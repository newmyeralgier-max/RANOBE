"""Tkinter GUI for the universal novel downloader.

Launch via ``python3 -m novel_dl.gui`` or double-click one of the bundled
launcher scripts (``execution/launchers/novel_dl.bat`` on Windows,
``execution/launchers/novel_dl.sh`` on Linux/macOS).

Design goals:

* Single self-contained window, no extra dependencies beyond the Python
  standard library. Tkinter ships with CPython on Windows and macOS.
* Non-blocking UI: every network call runs on a worker thread; results reach
  the UI through a thread-safe :class:`queue.Queue`.
* Minimal state machine: ``idle`` -> ``loading book`` -> ``ready`` ->
  ``downloading`` -> ``ready``.
"""

from __future__ import annotations

import queue
import re
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .backup import snapshot_dir
from .core import Book, UnsupportedSiteError
from .downloader import DownloadCancelled, DownloadError, download_chapters
from .epub import build_bilingual_epub_from_folders, build_epub_from_folder
from .glossary import load_glossary, save_glossary
from .registry import get_adapter
from .runlog import RunLog, open_in_system_editor, prune_old_logs
from .settings import load_settings, push_recent, save_settings
from .translator import (
    DEFAULT_COHERE_MODEL,
    TranslationCancelled,
    TranslationError,
    TranslatorConfig,
    translate_folder,
)
from .update_check import (
    UpdateStatus,
    check_for_updates,
    fast_forward_pull,
)
from .utils import FetchError, parse_range_spec, safe_filename

DEFAULT_TRANSLATOR_PROMPT = (
    "Ты — элитный литературный переводчик, специализирующийся на азиатских "
    "веб-новеллах. Твоя задача — взять существующий английский перевод и "
    "передать его на русском языке на уровне качества современной веб-новеллы, "
    "написанной изначально по-русски — естественно, живо и атмосферно.\n\n"
    "КРИТИЧЕСКИЕ ПРАВИЛА:\n"
    "1. НИКОГДА не переводи дословно — перефразируй, сохраняя смысл.\n"
    "2. НИКОГДА не переноси английский порядок слов, если режет слух.\n"
    "3. НИКОГДА не добавляй сюжет или действия, которых нет в оригинале.\n"
    "4. НИКОГДА не сокращай детали, описания, образы и нюансы.\n"
    "5. ВСЕГДА сохраняй исходный смысл, структуру сцены и тон автора.\n\n"
    "ОФОРМЛЕНИЕ: русские кавычки « », тире — для реплик, многоточие … для "
    "незавершённых фраз. Имена собственные транслитерируй естественно.\n\n"
    "ВЫВОДИ ТОЛЬКО ПЕРЕВЕДЁННЫЙ ОТРЫВОК — без комментариев и пояснений."
)

# ---- worker messages -----------------------------------------------------

@dataclass
class _LogMsg:
    text: str


@dataclass
class _BookReady:
    book: Book


@dataclass
class _DownloadDone:
    out_dir: Path
    combined_path: Path | None


@dataclass
class _TranslateDone:
    out_dir: Path
    count: int


@dataclass
class _EpubDone:
    epub_path: Path


@dataclass
class _Error:
    text: str
    downloaded: int = 0
    last_ok: int | None = None
    cancelled: bool = False


@dataclass
class _ProgressMsg:
    current: int
    total: int


@dataclass
class _UpdateMsg:
    """Background-thread result of the startup ``git fetch`` check."""

    status: UpdateStatus


@dataclass
class _CharsMsg:
    """Char-level translation progress: ``done`` of ``total`` chars completed.

    The translator emits one of these per finished chunk so the bar moves
    smoothly even inside a single long chapter, instead of jumping by
    whole chapters every 30+ seconds.
    """

    done: int
    total: int
    chapters_done: int = 0
    chapters_total: int = 0


# ---- app ------------------------------------------------------------------

class NovelDownloaderApp:
    """Tkinter front-end. One instance == one window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Novel Downloader")
        # Bigger default height so the Log panel + status line are visible
        # without the user having to resize. On Windows with small 1366×768
        # laptops 820 was enough to hide the log completely behind the
        # translate controls.
        self.root.geometry("960x980")
        self.root.minsize(800, 720)

        self._messages: queue.Queue[object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        # Pause is implemented as a single long-lived Event: set = run,
        # clear = pause. Always lives so worker code can pass it to the
        # downloader/translator unconditionally; .set() at construction
        # so a fresh worker isn't paused by default.
        self._pause_event: threading.Event = threading.Event()
        self._pause_event.set()
        self._book: Book | None = None
        self._adapter = None
        self._last_out_dir: Path | None = None
        # Human-readable label for the currently running background op.
        # Lets the status line say "Переведено 3/50" instead of the
        # generic "Скачано N/M" regardless of which op is running.
        self._operation_label: str = ""

        # Open the per-run log file before any other init so even early
        # exceptions during widget construction get captured.
        prune_old_logs()
        self._runlog = RunLog()

        # In-memory copies of the "last 5" history lists. Persisted via
        # the same atomic save_settings call as everything else.
        self._recent_urls: list[str] = []
        self._recent_api_keys: list[str] = []
        # Per-book glossary, loaded fresh whenever a book finishes
        # loading. Pairs are (src, dst) — src is the term in the source
        # language, dst is what the translator must output. Lives in
        # ``~/.novel_dl/glossary/<slug>.json`` so different novels keep
        # separate name dictionaries.
        self._glossary: list[tuple[str, str]] = []
        self._glossary_slug: str | None = None

        # Debounce token for autosave-on-change. tkinter "after" returns
        # an id we can cancel so multiple keystrokes coalesce into one
        # write 500ms after the last keystroke.
        self._autosave_after_id: str | None = None
        self._autosave_armed = False

        self._build_widgets()
        self._apply_settings(load_settings())
        self._install_autosave_traces()
        self._set_state_idle()
        _install_layout_agnostic_clipboard_bindings(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)

        # Kick off the update check in a daemon thread. Cheap (one git
        # fetch), runs once at startup. The result lands as an
        # _UpdateMsg in the regular message queue so we don't touch tk
        # from the worker thread.
        threading.Thread(
            target=self._update_check_worker,
            name="update-check",
            daemon=True,
        ).start()

    # ---- UI layout ------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}

        # Update banner. Hidden until the background check finishes.
        # Lives at the very top so it doesn't bump the rest of the
        # layout when it appears.
        self._update_banner = ttk.Frame(self.root)
        self._update_banner_label = ttk.Label(
            self._update_banner,
            text="",
            foreground="#0078d7",
            anchor="w",
        )
        self._update_banner_label.pack(side="left", padx=(8, 6), pady=4)
        self._update_btn = ttk.Button(
            self._update_banner, text="Обновить",
            command=self._on_update_pull,
        )
        self._update_btn.pack(side="right", padx=(0, 8), pady=4)
        # _update_banner is *not* packed yet — _show_update_banner does that.

        top = ttk.LabelFrame(self.root, text="1. Ссылка на книгу")
        top.pack(fill="x", **pad)
        self.url_var = tk.StringVar()
        # Combobox = Entry + dropdown of last 5 URLs the user has loaded.
        # ``state="normal"`` keeps it editable; the dropdown is just a
        # convenience over freeform typing. ``height`` clamps the popup to
        # MAX_RECENT entries so it doesn't sprawl.
        self._url_combo = ttk.Combobox(
            top, textvariable=self.url_var, height=5,
        )
        self._url_combo.pack(
            side="left", fill="x", expand=True, padx=(8, 6), pady=8,
        )
        self.load_btn = ttk.Button(top, text="Загрузить список глав",
                                   command=self._on_load_book)
        self.load_btn.pack(side="right", padx=(0, 8), pady=8)

        info = ttk.LabelFrame(self.root, text="2. Книга")
        info.pack(fill="x", **pad)
        self.book_title_var = tk.StringVar(value="— ещё ничего не загружено —")
        self.book_info_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.book_title_var, font=("TkDefaultFont", 11, "bold"),
                  anchor="w").pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(info, textvariable=self.book_info_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 6),
        )

        sel = ttk.LabelFrame(self.root, text="3. Какие главы скачать")
        sel.pack(fill="x", **pad)
        row = ttk.Frame(sel)
        row.pack(fill="x", padx=8, pady=6)
        ttk.Label(row, text="Диапазон:").pack(side="left")
        self.range_var = tk.StringVar(value="all")
        self.range_entry = ttk.Entry(row, textvariable=self.range_var, width=30)
        self.range_entry.pack(side="left", padx=(6, 10))
        ttk.Label(
            row, foreground="#555",
            text="примеры: 1-50  •  500-  •  1,5,10-20  •  all",
        ).pack(side="left")
        ttk.Button(
            row, text="Обновить статусы",
            command=self._refresh_chapter_status,
        ).pack(side="right", padx=(0, 4))

        # Per-chapter status grid: visible after a book has loaded. Shows
        # which chapters are downloaded (.txt exists), translated (matching
        # file in <out>/translated_ru/), and which packaged into the most
        # recent EPUB. Clicking a row inserts the chapter number into the
        # range field as a quick selection helper.
        tree_frame = ttk.Frame(sel)
        tree_frame.pack(fill="both", expand=False, padx=8, pady=(0, 6))
        self.chapter_tree = ttk.Treeview(
            tree_frame, columns=("num", "title", "dl", "tr"),
            show="headings", height=8, selectmode="extended",
        )
        self.chapter_tree.heading("num", text="№")
        self.chapter_tree.heading("title", text="Название")
        self.chapter_tree.heading("dl", text="Скачано")
        self.chapter_tree.heading("tr", text="Переведено")
        self.chapter_tree.column("num", width=60, anchor="e", stretch=False)
        self.chapter_tree.column("title", width=460, anchor="w")
        self.chapter_tree.column("dl", width=80, anchor="center", stretch=False)
        self.chapter_tree.column("tr", width=100, anchor="center", stretch=False)
        self.chapter_tree.pack(side="left", fill="both", expand=True)
        tree_sb = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.chapter_tree.yview,
        )
        tree_sb.pack(side="right", fill="y")
        self.chapter_tree.configure(yscrollcommand=tree_sb.set)
        self.chapter_tree.bind(
            "<Double-1>", self._on_chapter_tree_double_click,
        )

        out = ttk.LabelFrame(self.root, text="4. Куда сохранять")
        out.pack(fill="x", **pad)
        out_row = ttk.Frame(out)
        out_row.pack(fill="x", padx=8, pady=6)
        self.output_var = tk.StringVar(value=str(Path.cwd() / "downloads"))
        ttk.Entry(out_row, textvariable=self.output_var).pack(
            side="left", fill="x", expand=True,
        )
        ttk.Button(out_row, text="Выбрать...",
                   command=self._on_pick_dir).pack(side="left", padx=(6, 0))

        opts = ttk.Frame(out)
        opts.pack(fill="x", padx=8, pady=(0, 6))
        self.combined_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="Склеить все выбранные главы в combined.txt",
                        variable=self.combined_var).pack(side="left")
        self.force_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Перезагружать уже скачанные",
                        variable=self.force_var).pack(side="left", padx=(16, 0))

        action = ttk.Frame(self.root)
        action.pack(fill="x", **pad)
        self.download_btn = ttk.Button(action, text="Скачать",
                                       command=self._on_download)
        self.download_btn.pack(side="left")
        # Pause toggles between "Пауза" and "Продолжить" depending
        # on _pause_event state. Disabled until a worker is actually
        # running (same lifecycle as Stop).
        self.pause_btn = ttk.Button(
            action, text="Пауза",
            command=self._on_pause, state="disabled",
        )
        self.pause_btn.pack(side="left", padx=(8, 0))
        self.stop_btn = ttk.Button(action, text="Остановить",
                                   command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.open_btn = ttk.Button(action, text="Открыть папку с результатом",
                                   command=self._on_open_folder, state="disabled")
        self.open_btn.pack(side="left", padx=(8, 0))

        tr = ttk.LabelFrame(self.root, text="5. Перевод (Cohere)")
        tr.pack(fill="x", **pad)
        # Provider selector. Right now only Cohere is wired through to
        # the actual translator; the rest are visible-but-disabled stubs
        # so the UI shows users what's planned without giving them a
        # broken option to click. When we add a real provider we just
        # remove the "future" tag from its label and add a branch in
        # _on_translate.
        provider_row = ttk.Frame(tr)
        provider_row.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(provider_row, text="Провайдер:").pack(side="left")
        self.provider_var = tk.StringVar(value="Cohere (command-a)")
        provider_combo = ttk.Combobox(
            provider_row, textvariable=self.provider_var,
            state="readonly", height=5,
            values=(
                "Cohere (command-a)",
                "OpenAI (на будущее)",
                "Anthropic (на будущее)",
                "DeepSeek (на будущее)",
            ),
        )
        provider_combo.pack(side="left", padx=(6, 0))
        provider_combo.bind(
            "<<ComboboxSelected>>", self._on_provider_changed,
        )

        key_row = ttk.Frame(tr)
        key_row.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(key_row, text="API-ключ:").pack(side="left")
        self.cohere_key_var = tk.StringVar()
        # Combobox so the user can pick a previously-used key from the
        # dropdown. ``show="•"`` masks the typed/picked value the same
        # way the old plain Entry did. We keep a reference under the
        # legacy attribute name so the rest of the code (state toggling,
        # _push_recent_key) doesn't need to change.
        self._key_combo = ttk.Combobox(
            key_row, textvariable=self.cohere_key_var, show="•", height=5,
        )
        self._key_combo.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.cohere_key_entry = self._key_combo
        ttk.Label(key_row, text="Модель:").pack(side="left")
        self.cohere_model_var = tk.StringVar(value=DEFAULT_COHERE_MODEL)
        ttk.Entry(key_row, textvariable=self.cohere_model_var,
                  width=22).pack(side="left", padx=(6, 0))

        key_opt_row = ttk.Frame(tr)
        key_opt_row.pack(fill="x", padx=8, pady=(0, 2))
        self.save_api_key_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_opt_row,
            text="Запомнить ключ на этом ПК (в ~/.novel_dl/config.json)",
            variable=self.save_api_key_var,
        ).pack(side="left")
        # Dark-mode toggle. The checkbox lives next to "save api key" so
        # users see all the on/off knobs in one row instead of hunting
        # for an Options menu.
        self.dark_mode_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            key_opt_row,
            text="Тёмная тема",
            variable=self.dark_mode_var,
            command=self._on_dark_mode_toggle,
        ).pack(side="right")

        prompt_frame = ttk.Frame(tr)
        prompt_frame.pack(fill="x", padx=8, pady=(4, 4))
        ttk.Label(prompt_frame, text="Промпт (редактируемый):",
                  anchor="w").pack(fill="x")
        # height=5 keeps the prompt editable but doesn't steal vertical
        # space from the log panel below. Scrollable inside the widget
        # if the prompt is longer.
        self.prompt_text = tk.Text(prompt_frame, height=5, wrap="word")
        self.prompt_text.pack(side="left", fill="both", expand=True)
        self.prompt_text.insert("1.0", DEFAULT_TRANSLATOR_PROMPT)
        prompt_sb = ttk.Scrollbar(prompt_frame, orient="vertical",
                                  command=self.prompt_text.yview)
        prompt_sb.pack(side="right", fill="y")
        self.prompt_text.configure(yscrollcommand=prompt_sb.set)

        src_row = ttk.Frame(tr)
        src_row.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Label(
            src_row, text="Папка с английскими главами:",
        ).pack(side="left")
        self.translate_src_var = tk.StringVar(value="")
        ttk.Entry(
            src_row, textvariable=self.translate_src_var,
        ).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ttk.Button(
            src_row, text="Выбрать...", command=self._on_pick_translate_src,
        ).pack(side="left")
        ttk.Label(
            tr, foreground="#555",
            text="(пусто = папка последней скачки; иначе указываем вручную)",
        ).pack(fill="x", padx=8)

        tr_range_row = ttk.Frame(tr)
        tr_range_row.pack(fill="x", padx=8, pady=(0, 2))
        ttk.Label(tr_range_row, text="Переводить главы:").pack(side="left")
        self.translate_range_var = tk.StringVar(value="all")
        ttk.Entry(
            tr_range_row, textvariable=self.translate_range_var, width=22,
        ).pack(side="left", padx=(6, 10))
        ttk.Label(
            tr_range_row, foreground="#555",
            text="номера глав: 3-200  •  3,5,10-20  •  all",
        ).pack(side="left")

        # Glossary editor — per-book terms the translator must respect.
        # Loaded automatically when the book is loaded; persists to
        # ``~/.novel_dl/glossary/<slug>.json``. Living inside the
        # translation frame keeps the translator's knobs in one place.
        gloss_frame = ttk.LabelFrame(tr, text="Глоссарий (исправляет дрейф имён между главами)")
        gloss_frame.pack(fill="x", padx=8, pady=(4, 4))
        gloss_top = ttk.Frame(gloss_frame)
        gloss_top.pack(fill="x", padx=4, pady=(4, 2))
        ttk.Label(
            gloss_top, foreground="#555",
            text="двойной клик по строке — изменить; добавляй имена/термины которые модель путает",
        ).pack(side="left")
        ttk.Button(
            gloss_top, text="+ Добавить", command=self._on_glossary_add,
        ).pack(side="right", padx=(4, 0))
        ttk.Button(
            gloss_top, text="− Удалить", command=self._on_glossary_delete,
        ).pack(side="right", padx=(4, 0))

        gloss_tree_frame = ttk.Frame(gloss_frame)
        gloss_tree_frame.pack(fill="x", padx=4, pady=(0, 4))
        self.glossary_tree = ttk.Treeview(
            gloss_tree_frame, columns=("src", "dst"),
            show="headings", height=4, selectmode="extended",
        )
        self.glossary_tree.heading("src", text="Source (en)")
        self.glossary_tree.heading("dst", text="Перевод (ru)")
        self.glossary_tree.column("src", width=260, anchor="w")
        self.glossary_tree.column("dst", width=260, anchor="w")
        self.glossary_tree.pack(side="left", fill="x", expand=True)
        gloss_sb = ttk.Scrollbar(
            gloss_tree_frame, orient="vertical",
            command=self.glossary_tree.yview,
        )
        gloss_sb.pack(side="right", fill="y")
        self.glossary_tree.configure(yscrollcommand=gloss_sb.set)
        self.glossary_tree.bind(
            "<Double-1>", self._on_glossary_double_click,
        )

        tr_btns = ttk.Frame(tr)
        tr_btns.pack(fill="x", padx=8, pady=(2, 4))
        self.translate_btn = ttk.Button(tr_btns, text="Перевести скачанные",
                                        command=self._on_translate)
        self.translate_btn.pack(side="left")
        self.retranslate_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(tr_btns, text="Переводить заново (игнор кэша)",
                        variable=self.retranslate_var).pack(
            side="left", padx=(12, 0),
        )
        # Phase 3.2: context memory. On by default — keeps pronouns,
        # tense, and character names consistent across chapter
        # boundaries by feeding the previous chapter's tail into the
        # next request. Costs ~150–300 extra tokens per chapter.
        self.use_context_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            tr_btns,
            text="Память контекста (последние абзацы прошлой главы)",
            variable=self.use_context_var,
        ).pack(side="left", padx=(12, 0))

        epub_row = ttk.Frame(tr)
        epub_row.pack(fill="x", padx=8, pady=(2, 8))
        self.epub_btn = ttk.Button(epub_row, text="Собрать EPUB",
                                   command=self._on_build_epub)
        self.epub_btn.pack(side="left")
        ttk.Label(epub_row, text="Источник:").pack(side="left", padx=(12, 4))
        self.epub_source_var = tk.StringVar(value="ru (перевод)")
        ttk.Combobox(
            epub_row, textvariable=self.epub_source_var, width=16,
            values=["ru (перевод)", "en (оригинал)"], state="readonly",
        ).pack(side="left")
        ttk.Label(epub_row, text="Главы:").pack(side="left", padx=(12, 4))
        self.epub_range_var = tk.StringVar(value="all")
        ttk.Entry(
            epub_row, textvariable=self.epub_range_var, width=20,
        ).pack(side="left")
        ttk.Label(
            epub_row, foreground="#555",
            text="напр. 3-200 или all",
        ).pack(side="left", padx=(6, 0))

        # Phase 3.4: bilingual EPUB. User explicitly asked for a
        # SEPARATE prominent checkbox so it doesn't interfere with the
        # default flow. Wrapped in its own LabelFrame with a coloured
        # title so it visually stands out from the regular epub row.
        bi_frame = ttk.LabelFrame(
            tr,
            text="📚 Двуязычный EPUB (английский + русский для изучения языка)",
        )
        bi_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.bilingual_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            bi_frame,
            text=(
                "Включить — собрать EPUB где после каждого английского "
                "абзаца идёт русский (вместо обычного однолингвального)"
            ),
            variable=self.bilingual_var,
        ).pack(side="left", padx=8, pady=4)

        # Pack bottom-up so progress + status + log always have guaranteed
        # space at the bottom of the window. Use the Text widget's own
        # height (in rows) instead of a pixel-based frame height — Tkinter
        # respects rows reliably across DPI scales and themes, while pixel
        # sizes on a ttk.LabelFrame sometimes collapse to one line.
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.status_var = tk.StringVar(value="Готов.")
        self.status_label = ttk.Label(
            self.root, textvariable=self.status_var, anchor="w",
            wraplength=900, justify="left",
        )
        log_frame = ttk.LabelFrame(self.root, text="Лог")

        # Order: progress (very bottom), status (above progress), log (above
        # status, expands to fill the remaining vertical space).
        self.progress.pack(side="bottom", fill="x", padx=10, pady=(0, 4))
        self.status_label.pack(side="bottom", fill="x", padx=10, pady=(0, 2))
        log_frame.pack(side="bottom", fill="both", expand=True, **pad)

        # Toolbar row inside the log frame: "Open log file" button so the
        # user can hand the .log straight to me when something breaks
        # without having to find ~/.novel_dl/runs themselves.
        log_toolbar = ttk.Frame(log_frame)
        log_toolbar.pack(side="top", fill="x", padx=8, pady=(6, 0))
        ttk.Button(
            log_toolbar, text="Открыть файл лога", command=self._on_open_log,
        ).pack(side="right")
        ttk.Label(
            log_toolbar,
            foreground="#555",
            text="полный лог пишется в ~/.novel_dl/runs/",
        ).pack(side="left")

        # height=15 rows ≈ 280px on default font — well above the "1 line"
        # collapse the user reported.
        self.log = tk.Text(
            log_frame, wrap="word", state="disabled", height=15,
        )
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(4, 8))
        log_sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_sb.pack(side="right", fill="y", pady=(4, 8), padx=(0, 8))
        self.log.configure(yscrollcommand=log_sb.set)

    # ---- state transitions ---------------------------------------------
    def _set_state_idle(self) -> None:
        self.load_btn.config(state="normal")
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled", text="Пауза")
        self.translate_btn.config(state="normal")
        self.epub_btn.config(state="normal")
        # Reset the pause event whenever we transition to idle so the
        # next operation starts in the "running" state.
        self._pause_event.set()

    def _set_state_loading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        # Allow cancelling the chapter-list fetch — long books paginate
        # and the user shouldn't have to wait for every page to finish.
        self.stop_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="Пауза")
        self.translate_btn.config(state="disabled")
        self.epub_btn.config(state="disabled")
        self.status_var.set("Загружаю список глав...")

    def _set_state_ready(self) -> None:
        self.load_btn.config(state="normal")
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.pause_btn.config(state="disabled", text="Пауза")
        self.translate_btn.config(state="normal")
        self.epub_btn.config(state="normal")
        self._pause_event.set()

    def _set_state_downloading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.pause_btn.config(state="normal", text="Пауза")
        self.translate_btn.config(state="disabled")
        self.epub_btn.config(state="disabled")
        self.status_var.set("Работаю...")

    # ---- button handlers -----------------------------------------------
    def _on_load_book(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Ссылка", "Вставь ссылку на книгу.")
            return

        try:
            self._adapter = get_adapter(url)
        except UnsupportedSiteError as exc:
            messagebox.showerror("Сайт не поддержан", str(exc))
            return

        # Loading a new book invalidates the previous "last download"
        # folder — otherwise Translate/EPUB/Open-folder would still
        # target the old book while the new one is displayed.
        self._last_out_dir = None
        self.open_btn.config(state="disabled")
        self._book = None
        self.book_title_var.set("— загружаю... —")
        self.book_info_var.set("")
        # Create the cancel event BEFORE flipping UI state so that a
        # user who clicks Stop the instant the button becomes active
        # can't race ahead of the event's creation.
        self._cancel_event = threading.Event()
        self._set_state_loading()
        self._append_log(f"[site={self._adapter.site_id}] загружаю {url}")
        self._push_recent_url(url)
        self._save_current_settings()
        cancel_event = self._cancel_event
        self._spawn(lambda: self._worker_fetch_book(url, cancel_event))

    def _on_pick_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or str(Path.cwd()))
        if chosen:
            self.output_var.set(chosen)

    def _on_pick_translate_src(self) -> None:
        """Let the user pick an existing folder of chapter_*.txt files.

        Useful when the previous download happened in an earlier run and
        ``_last_out_dir`` is no longer set, or when the user has an
        external folder from elsewhere.
        """
        start = (
            self.translate_src_var.get()
            or (str(self._last_out_dir) if self._last_out_dir else "")
            or self.output_var.get()
            or str(Path.cwd())
        )
        chosen = filedialog.askdirectory(initialdir=start)
        if chosen:
            self.translate_src_var.set(chosen)

    def _on_download(self) -> None:
        if not self._book or not self._adapter:
            messagebox.showwarning("Нет книги", "Сначала нажми «Загрузить список глав».")
            return
        total = len(self._book.chapters)
        indices = parse_range_spec(self.range_var.get() or "all", total)
        if not indices:
            messagebox.showwarning("Диапазон",
                                   "Ничего не выбрано. Примеры: 1-50, 500-, all")
            return

        out_root = Path(self.output_var.get() or "downloads")
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Папка", f"Не могу создать папку: {exc}")
            return

        out_dir = out_root / safe_filename(self._book.slug or self._book.title)
        combined = (out_dir / "combined.txt") if self.combined_var.get() else None

        adapter = self._adapter
        book = self._book
        force = self.force_var.get()
        # Create the cancel event BEFORE flipping UI state — otherwise a
        # very quick Stop click between "state=downloading" (stop_btn
        # enabled) and the assignment below would set a stale / missing
        # event that the worker never sees.
        self._cancel_event = threading.Event()
        self._operation_label = "Скачано"
        self._set_state_downloading()
        self.progress.config(maximum=len(indices), value=0)
        self._append_log(
            f"Скачиваю {len(indices)} глав (первая {indices[0]}, последняя {indices[-1]}) "
            f"в {out_dir}"
        )
        snap = snapshot_dir(out_dir, label="download")
        if snap is not None:
            self._append_log(f"Бэкап перед скачкой: {snap}")
        self._save_current_settings()
        self._spawn(lambda: self._worker_download(adapter, book, indices,
                                                  out_dir, combined, force,
                                                  self._cancel_event))

    def _on_stop(self) -> None:
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()
            # If we were paused, resume the event so the worker wakes up
            # and observes the cancel flag instead of staying parked.
            self._pause_event.set()
            self.stop_btn.config(state="disabled")
            self.pause_btn.config(state="disabled")
            self.status_var.set("Останавливаю...")
            self._append_log("Запрошена остановка. Дожидаюсь текущей задачи...")

    def _on_pause(self) -> None:
        """Toggle the worker's pause state.

        Because pause is cooperative — the worker only blocks at safe
        checkpoints — a click here may take a few seconds to visibly
        "land". The status line is updated immediately so the user gets
        feedback even before the worker observes the change.
        """
        if self._pause_event.is_set():
            # Currently running → pause.
            self._pause_event.clear()
            self.pause_btn.config(text="Продолжить")
            self._append_log(
                "Пауза. Работа приостановлена на ближайшем безопасном шаге."
            )
            self.status_var.set("На паузе.")
        else:
            # Currently paused → resume.
            self._pause_event.set()
            self.pause_btn.config(text="Пауза")
            self._append_log("Продолжаю работу.")
            self.status_var.set("Работаю...")

    # ---- persistent settings -------------------------------------------
    def _current_settings(self) -> dict[str, object]:
        return {
            "url": self.url_var.get(),
            "output_dir": self.output_var.get(),
            "range_spec": self.range_var.get(),
            "combined": bool(self.combined_var.get()),
            "force": bool(self.force_var.get()),
            "api_key": self.cohere_key_var.get(),
            "save_api_key": bool(self.save_api_key_var.get()),
            "model": self.cohere_model_var.get(),
            "prompt": self.prompt_text.get("1.0", "end").rstrip("\n"),
            "translate_src_dir": self.translate_src_var.get(),
            "translate_range": self.translate_range_var.get(),
            "retranslate": bool(self.retranslate_var.get()),
            "epub_range": self.epub_range_var.get(),
            "epub_source": self.epub_source_var.get(),
            "recent_urls": list(self._recent_urls),
            "recent_api_keys": list(self._recent_api_keys),
            "dark_mode": bool(self.dark_mode_var.get()),
            "use_prior_context": bool(self.use_context_var.get()),
        }

    def _apply_settings(self, s: dict[str, object]) -> None:
        def _s(key: str, default: str = "") -> str:
            v = s.get(key, default)
            return str(v) if v is not None else default

        def _b(key: str, default: bool = False) -> bool:
            v = s.get(key, default)
            return bool(v) if v is not None else default

        if _s("url"):
            self.url_var.set(_s("url"))
        if _s("output_dir"):
            self.output_var.set(_s("output_dir"))
        if _s("range_spec"):
            self.range_var.set(_s("range_spec"))
        self.combined_var.set(_b("combined", True))
        self.force_var.set(_b("force", False))
        if _s("model"):
            self.cohere_model_var.set(_s("model"))
        self.save_api_key_var.set(_b("save_api_key", False))
        if _b("save_api_key") and _s("api_key"):
            self.cohere_key_var.set(_s("api_key"))
        prompt = _s("prompt")
        if prompt:
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", prompt)
        if _s("translate_src_dir"):
            self.translate_src_var.set(_s("translate_src_dir"))
        if _s("translate_range"):
            self.translate_range_var.set(_s("translate_range"))
        self.retranslate_var.set(_b("retranslate", False))
        if _s("epub_range"):
            self.epub_range_var.set(_s("epub_range"))
        if _s("epub_source"):
            self.epub_source_var.set(_s("epub_source"))
        # Restore recent-history lists. These never get "unset" — empty
        # list is fine, just means there's nothing in the dropdowns yet.
        ru = s.get("recent_urls")
        if isinstance(ru, list):
            self._recent_urls = [str(x) for x in ru if isinstance(x, str)]
        rk = s.get("recent_api_keys")
        if isinstance(rk, list):
            self._recent_api_keys = [str(x) for x in rk if isinstance(x, str)]
        # Push them into the live Combobox dropdowns so they appear
        # immediately after launch.
        if hasattr(self, "_url_combo"):
            try:
                self._url_combo["values"] = list(self._recent_urls)
            except tk.TclError:
                pass
        if hasattr(self, "_key_combo"):
            try:
                self._key_combo["values"] = list(self._recent_api_keys)
            except tk.TclError:
                pass
        # Apply dark mode AFTER widgets exist; on the first call this is
        # a no-op for the (default) light theme, but it makes the
        # post-launch toggle and the persisted-config restore symmetric.
        self.dark_mode_var.set(_b("dark_mode", False))
        self._apply_theme(self.dark_mode_var.get())
        self.use_context_var.set(_b("use_prior_context", True))

    def _save_current_settings(self) -> None:
        try:
            save_settings(self._current_settings())
        except Exception as exc:  # pragma: no cover
            self._append_log(f"Не удалось сохранить настройки: {exc}")

    # ---- autosave-on-change --------------------------------------------
    def _install_autosave_traces(self) -> None:
        """Trace every persistent tk variable; debounced save on change.

        Without this, settings only get written when the user runs an
        operation or closes the window. A crash mid-edit would lose any
        URL/key/prompt the user just typed. Now we save 500ms after the
        last keystroke / checkbox flip.
        """
        traced_vars = (
            self.url_var, self.output_var, self.range_var,
            self.combined_var, self.force_var,
            self.cohere_key_var, self.save_api_key_var,
            self.cohere_model_var,
            self.translate_src_var, self.translate_range_var,
            self.retranslate_var,
            self.epub_source_var, self.epub_range_var,
        )
        for var in traced_vars:
            try:
                var.trace_add("write", self._on_setting_changed)
            except (AttributeError, tk.TclError):
                continue
        # The prompt is a Text widget, not a Var — bind to <KeyRelease>.
        try:
            self.prompt_text.bind(
                "<KeyRelease>", lambda _e: self._on_setting_changed(),
            )
        except (AttributeError, tk.TclError):
            pass
        self._autosave_armed = True

    def _on_setting_changed(self, *_args: object) -> None:
        if not self._autosave_armed:
            return
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except (tk.TclError, ValueError):
                pass
        self._autosave_after_id = self.root.after(
            500, self._autosave_flush,
        )

    def _autosave_flush(self) -> None:
        self._autosave_after_id = None
        try:
            save_settings(self._current_settings())
        except Exception:  # pragma: no cover — best-effort
            pass

    def _on_close(self) -> None:
        if self._autosave_after_id is not None:
            try:
                self.root.after_cancel(self._autosave_after_id)
            except (tk.TclError, ValueError):
                pass
            self._autosave_after_id = None
        self._save_current_settings()
        try:
            self._runlog.close()
        except Exception:  # pragma: no cover
            pass
        self.root.destroy()

    # ---- translate / epub --------------------------------------------
    def _src_dir_for_postprocess(self) -> Path | None:
        """Where the raw English chapter_*.txt files live.

        Precedence:
          1. The explicit "Папка с английскими главами" field, if the
             user has typed / picked something. This is what lets the
             user translate / build EPUB from chapters downloaded in a
             previous run without having to click "Скачать" again.
          2. The folder the most recent in-session download produced.
          3. A path computed from the current book + output-root field.
        """
        explicit = self.translate_src_var.get().strip()
        if explicit:
            p = Path(explicit)
            if p.exists():
                return p
            # Fall through — maybe the book-derived fallback exists.
        if self._last_out_dir is not None and self._last_out_dir.exists():
            return self._last_out_dir
        if self._book is None:
            return None
        out_root = Path(self.output_var.get() or "downloads")
        return out_root / safe_filename(self._book.slug or self._book.title)

    def _on_translate(self) -> None:
        src_dir = self._src_dir_for_postprocess()
        if src_dir is None or not src_dir.exists():
            messagebox.showwarning(
                "Нечего переводить",
                "Сначала скачай главы или укажи папку, где лежат chapter_*.txt.",
            )
            return

        api_key = self.cohere_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "Нет ключа Cohere",
                "Вставь API-ключ Cohere. Получить: "
                "https://dashboard.cohere.com/api-keys",
            )
            return
        model = self.cohere_model_var.get().strip() or DEFAULT_COHERE_MODEL
        prompt = self.prompt_text.get("1.0", "end").strip()
        if not prompt:
            messagebox.showwarning(
                "Пустой промпт",
                "В поле промпта должен быть текст. Сбрось на дефолтный, "
                "если случайно удалил.",
            )
            return

        dst_dir = src_dir.parent / (src_dir.name + "_ru")
        cfg = TranslatorConfig(
            api_key=api_key, model=model, system_prompt=prompt,
            glossary=list(self._glossary),
            use_prior_context=bool(self.use_context_var.get()),
        )

        available_nums = sorted(_chapter_numbers_in(src_dir))
        if not available_nums:
            messagebox.showwarning(
                "Нечего переводить",
                f"В {src_dir} нет файлов chapter_*.txt.",
            )
            return

        wanted = _parse_chapter_number_spec(
            self.translate_range_var.get() or "all", available_nums,
        )
        if wanted is None:
            messagebox.showwarning(
                "Диапазон перевода",
                "Не понял диапазон. Примеры: 3-200, 3,5,10-20, all.",
            )
            return
        if not wanted:
            messagebox.showwarning(
                "Диапазон перевода",
                f"По указанному диапазону в папке нет глав. "
                f"Доступны: {available_nums[0]}..{available_nums[-1]} "
                f"({len(available_nums)} файлов).",
            )
            return

        force = self.retranslate_var.get()
        # Create cancel event BEFORE flipping state — see _on_download.
        self._cancel_event = threading.Event()
        self._operation_label = "Переведено"
        self._set_state_downloading()
        files_count = len(wanted)
        self.progress.config(maximum=files_count, value=0)
        self._append_log(
            f"Перевод {files_count} глав (из {len(available_nums)}) "
            f"→ {dst_dir} (модель {model})"
        )
        snap = snapshot_dir(dst_dir, label="translate")
        if snap is not None:
            self._append_log(f"Бэкап перед переводом: {snap}")
        self._push_recent_key(cfg.api_key)
        self._save_current_settings()
        self._spawn(lambda: self._worker_translate(
            src_dir, dst_dir, cfg, force, self._cancel_event, wanted,
        ))

    def _on_build_epub(self) -> None:
        raw_dir = self._src_dir_for_postprocess()
        if raw_dir is None or not raw_dir.exists():
            messagebox.showwarning(
                "Нечего собирать",
                "Сначала скачай (и при желании переведи) главы.",
            )
            return
        bilingual = bool(self.bilingual_var.get())
        ru_dir = raw_dir.parent / (raw_dir.name + "_ru")
        if bilingual:
            # Bilingual mode needs BOTH the english source dir and the
            # russian translation dir to exist. We compute the chapter
            # range against the russian dir (translation is the bottle-
            # neck — if a chapter isn't translated yet, we can't bind
            # it bilingually) and pass both to the builder.
            if not ru_dir.exists() or not any(ru_dir.glob("chapter_*.txt")):
                messagebox.showwarning(
                    "Нет данных для двуязычного EPUB",
                    "В папке "
                    f"{ru_dir} нет переведённых глав. Сначала переведи.",
                )
                return
            if not any(raw_dir.glob("chapter_*.txt")):
                messagebox.showwarning(
                    "Нет данных для двуязычного EPUB",
                    f"В папке {raw_dir} нет английских глав.",
                )
                return
            src_dir = ru_dir  # used only for chapter-number range parsing
            use_ru = True
        else:
            use_ru = self.epub_source_var.get().startswith("ru")
            src_dir = ru_dir if use_ru else raw_dir
            if not src_dir.exists() or not any(src_dir.glob("chapter_*.txt")):
                missing = "переведённых" if use_ru else "скачанных"
                messagebox.showwarning(
                    "Нет данных",
                    f"В папке {src_dir} нет {missing} глав. "
                    f"Сначала {'переведи' if use_ru else 'скачай'}.",
                )
                return

        available_nums = sorted(_chapter_numbers_in(src_dir))
        wanted = _parse_chapter_number_spec(
            self.epub_range_var.get() or "all", available_nums,
        )
        if wanted is None:
            messagebox.showwarning(
                "Диапазон EPUB",
                "Не понял диапазон. Примеры: 3-200, 3,5,10-20, all.",
            )
            return
        if not wanted:
            messagebox.showwarning(
                "Диапазон EPUB",
                f"По указанному диапазону в папке нет глав. "
                f"Доступны: {available_nums[0]}..{available_nums[-1]} "
                f"({len(available_nums)} файлов).",
            )
            return

        title = (self._book.title if self._book else raw_dir.name) or raw_dir.name
        author = (self._book.author if self._book else "") or ""
        suffix_bits: list[str] = []
        if bilingual:
            suffix_bits.append("EN+RU")
        elif use_ru:
            suffix_bits.append("перевод")
        if wanted != set(available_nums):
            lo, hi = min(wanted), max(wanted)
            suffix_bits.append(
                f"главы {lo}-{hi}" if lo != hi else f"глава {lo}",
            )
        if suffix_bits:
            title = f"{title} ({', '.join(suffix_bits)})"
        epub_path = raw_dir.parent / f"{safe_filename(title)}.epub"
        # EPUB build is fast synchronous stdlib zipfile work; there's no
        # meaningful point to cancel it, so we null out the cancel event
        # and force the Stop button off after the usual state transition.
        self._cancel_event = None
        self._operation_label = "Собрано EPUB"
        self._set_state_downloading()
        self.stop_btn.config(state="disabled")
        self.status_var.set("Собираю EPUB...")
        self.progress.config(maximum=1, value=0)
        self._append_log(
            f"Собираю EPUB из {src_dir} ({len(wanted)} глав) → {epub_path}"
        )
        snap = snapshot_dir(src_dir, label="epub")
        if snap is not None:
            self._append_log(f"Бэкап перед сборкой EPUB: {snap}")
        self._save_current_settings()
        if bilingual:
            self._spawn(lambda: self._worker_build_bilingual_epub(
                raw_dir, ru_dir, epub_path, title, author, wanted,
            ))
        else:
            self._spawn(lambda: self._worker_build_epub(
                src_dir, epub_path, title, author, wanted,
            ))

    def _on_open_folder(self) -> None:
        if not self._last_out_dir or not self._last_out_dir.exists():
            return
        _open_in_file_manager(self._last_out_dir)

    # ---- background work ----------------------------------------------
    def _spawn(self, target: Callable[[], None]) -> None:
        self._worker = threading.Thread(target=target, daemon=True)
        self._worker.start()

    def _worker_fetch_book(
        self, url: str, cancel_event: threading.Event,
    ) -> None:
        try:
            book = self._adapter.fetch_book(url, cancel_event=cancel_event)
        except FetchError as exc:
            self._messages.put(_Error(
                f"Ошибка загрузки книги: {exc}",
                cancelled=cancel_event.is_set(),
            ))
            return
        except Exception as exc:  # pragma: no cover - defensive
            self._messages.put(_Error(f"Неожиданная ошибка: {exc!r}"))
            return
        self._messages.put(_BookReady(book))

    def _worker_download(self, adapter, book: Book, indices: list[int],
                          out_dir: Path, combined: Path | None,
                          force: bool,
                          cancel_event: threading.Event) -> None:
        counter = {"i": 0}
        total = len(indices)

        def progress(msg: str) -> None:
            counter["i"] += 1
            self._messages.put(_LogMsg(msg))
            self._messages.put(_ProgressMsg(counter["i"], total))

        try:
            download_chapters(
                adapter, book, indices, out_dir,
                delay=1.5, force=force,
                progress=progress,
                combined_path=combined,
                cancel_event=cancel_event,
                pause_event=self._pause_event,
            )
        except DownloadCancelled as exc:
            self._messages.put(_Error(
                str(exc),
                downloaded=exc.downloaded,
                last_ok=exc.last_ok,
                cancelled=True,
            ))
            return
        except DownloadError as exc:
            self._messages.put(_Error(
                str(exc),
                downloaded=exc.downloaded,
                last_ok=exc.last_ok,
            ))
            return
        except Exception as exc:  # pragma: no cover
            self._messages.put(_Error(f"Неожиданная ошибка: {exc!r}"))
            return
        self._messages.put(_DownloadDone(out_dir=out_dir, combined_path=combined))

    def _worker_translate(self, src_dir: Path, dst_dir: Path,
                           cfg: TranslatorConfig, force: bool,
                           cancel_event: threading.Event,
                           wanted: set[int]) -> None:
        files_total = len(wanted)

        # Pre-scan source files to compute total char count across the
        # selection. This lets the progress bar advance smoothly per chunk
        # instead of jumping every chapter. Cached files (dst already
        # exists) count as "already done" so the bar starts at the right
        # offset and finishes at 100% even if half the work was skipped.
        chars_total = 0
        chars_pre_done = 0
        chapters_pre_done = 0
        for src in sorted(src_dir.glob("chapter_*.txt")):
            num = _chapter_number_for(src)
            if num is None or num not in wanted:
                continue
            try:
                body_len = _approx_body_chars(src)
            except OSError:
                body_len = 0
            chars_total += body_len
            dst = dst_dir / src.name
            if dst.exists() and not force:
                chars_pre_done += body_len
                chapters_pre_done += 1

        counter = {
            "chars": chars_pre_done,
            "chapters": chapters_pre_done,
        }

        def progress(msg: str) -> None:
            self._messages.put(_LogMsg(msg))
            stripped = msg.lstrip()
            if stripped.startswith("[") and (
                "skip" in stripped or "translating" in stripped
            ):
                # Count only top-level "[i/N]" file transitions.
                if "skip" in stripped:
                    # Already counted in pre-scan; still publish so the
                    # status line moves.
                    self._messages.put(_CharsMsg(
                        done=counter["chars"],
                        total=max(chars_total, 1),
                        chapters_done=counter["chapters"],
                        chapters_total=files_total,
                    ))
                else:
                    self._messages.put(_CharsMsg(
                        done=counter["chars"],
                        total=max(chars_total, 1),
                        chapters_done=counter["chapters"],
                        chapters_total=files_total,
                    ))

        def chunk_done(n_chars: int) -> None:
            counter["chars"] += n_chars
            self._messages.put(_CharsMsg(
                done=counter["chars"],
                total=max(chars_total, 1),
                chapters_done=counter["chapters"],
                chapters_total=files_total,
            ))

        # Bump chapters_done after each top-level file transition message.
        # We piggyback on the existing progress() callback by scanning
        # for the "translating: chapter_NNNN" prefix.
        original_progress = progress

        def progress_with_chapter_count(msg: str) -> None:
            stripped = msg.lstrip()
            if stripped.startswith("[") and (
                "translating" in stripped or "skip" in stripped
            ):
                counter["chapters"] += 1
            original_progress(msg)

        try:
            done = translate_folder(
                src_dir, dst_dir, cfg,
                force=force, progress=progress_with_chapter_count,
                cancel_event=cancel_event,
                pause_event=self._pause_event,
                wanted_numbers=wanted,
                chunk_done=chunk_done,
            )
        except TranslationCancelled as exc:
            self._messages.put(_Error(
                str(exc),
                downloaded=exc.translated,
                last_ok=exc.last_ok,
                cancelled=True,
            ))
            return
        except TranslationError as exc:
            self._messages.put(_Error(
                str(exc),
                downloaded=exc.translated,
                last_ok=exc.last_ok,
            ))
            return
        except Exception as exc:  # pragma: no cover
            self._messages.put(_Error(f"Неожиданная ошибка перевода: {exc!r}"))
            return
        self._messages.put(_TranslateDone(out_dir=dst_dir, count=len(done)))

    def _worker_build_epub(self, src_dir: Path, epub_path: Path,
                            title: str, author: str,
                            wanted: set[int]) -> None:
        try:
            build_epub_from_folder(
                src_dir, epub_path, book_title=title, author=author,
                wanted_numbers=wanted,
            )
        except Exception as exc:  # pragma: no cover
            self._messages.put(_Error(f"Не удалось собрать EPUB: {exc}"))
            return
        self._messages.put(_EpubDone(epub_path=epub_path))

    def _worker_build_bilingual_epub(
        self, en_dir: Path, ru_dir: Path, epub_path: Path,
        title: str, author: str, wanted: set[int],
    ) -> None:
        try:
            build_bilingual_epub_from_folders(
                en_dir, ru_dir, epub_path,
                book_title=title, author=author,
                wanted_numbers=wanted,
            )
        except Exception as exc:  # pragma: no cover
            self._messages.put(_Error(
                f"Не удалось собрать двуязычный EPUB: {exc}"
            ))
            return
        self._messages.put(_EpubDone(epub_path=epub_path))

    # ---- pump ----------------------------------------------------------
    def _drain_messages(self) -> None:
        try:
            while True:
                msg = self._messages.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_messages)

    def _handle_message(self, msg: object) -> None:
        if isinstance(msg, _LogMsg):
            self._append_log(msg.text)
            # Mirror the latest log line into the status label so the user
            # sees live activity even when the Лог panel is clipped by a
            # small window or hidden behind a scrollbar. Trim to one line.
            single = msg.text.splitlines()[0] if msg.text else ""
            if single:
                self.status_var.set(single[:300])
        elif isinstance(msg, _ProgressMsg):
            self.progress.config(value=msg.current, maximum=msg.total)
            label = self._operation_label or "Прогресс"
            self.status_var.set(f"{label}: {msg.current}/{msg.total}")
        elif isinstance(msg, _UpdateMsg):
            # Pulled-back from the daemon update-check thread. If there
            # are updates, show the banner; if we already showed it and
            # the new status says "up to date", hide it.
            if msg.status.has_updates:
                self._show_update_banner(msg.status)
            else:
                try:
                    self._update_banner.pack_forget()
                except tk.TclError:
                    pass
                if msg.status.error:
                    self._append_log(f"Update check: {msg.status.error}")
                else:
                    self._append_log("Update check: up to date.")
        elif isinstance(msg, _CharsMsg):
            # Char-based bar for translation: smooth motion even inside a
            # single 12000-character chapter that takes 30+ seconds.
            self.progress.config(value=msg.done, maximum=msg.total)
            done_k = msg.done / 1000
            total_k = msg.total / 1000
            self.status_var.set(
                f"Переведено: {done_k:.1f}K/{total_k:.1f}K символов "
                f"({msg.chapters_done}/{msg.chapters_total} глав)"
            )
        elif isinstance(msg, _BookReady):
            self._book = msg.book
            total = len(msg.book.chapters)
            self.book_title_var.set(msg.book.title or "(без названия)")
            bits: list[str] = [f"Глав: {total}"]
            if msg.book.author:
                bits.append(f"Автор: {msg.book.author}")
            if msg.book.slug:
                bits.append(f"Slug: {msg.book.slug}")
            self.book_info_var.set("   •   ".join(bits))
            self.status_var.set(f"Готов к скачиванию. Глав: {total}.")
            self._append_log(f"ОК: {total} глав загружено из списка.")
            self._populate_chapter_tree()
            slug = msg.book.slug or safe_filename(msg.book.title or "book")
            self._load_glossary_for_book(slug)
            self._set_state_ready()
        elif isinstance(msg, _DownloadDone):
            self._last_out_dir = msg.out_dir
            self.open_btn.config(state="normal")
            self.status_var.set("Готово!")
            self._append_log(f"Готово. Папка: {msg.out_dir}")
            if msg.combined_path is not None:
                self._append_log(f"Файл combined: {msg.combined_path}")
            self._refresh_chapter_status()
            self._set_state_ready()
            messagebox.showinfo("Готово",
                                f"Скачано в:\n{msg.out_dir}")
        elif isinstance(msg, _TranslateDone):
            self.status_var.set("Перевод готов.")
            self._append_log(f"Переведено {msg.count} глав в {msg.out_dir}")
            self._refresh_chapter_status()
            self._set_state_ready()
            messagebox.showinfo(
                "Перевод готов",
                f"Переведено {msg.count} глав.\n\n{msg.out_dir}",
            )
        elif isinstance(msg, _EpubDone):
            self.status_var.set("EPUB готов.")
            self._append_log(f"EPUB: {msg.epub_path}")
            self._set_state_ready()
            messagebox.showinfo(
                "EPUB готов",
                f"Книга собрана:\n{msg.epub_path}",
            )
        elif isinstance(msg, _Error):
            last = (
                f"Последняя успешно скачанная глава: #{msg.last_ok}."
                if msg.last_ok is not None
                else "Ни одной главы не скачано в этом запуске."
            )
            summary = f"Скачано за запуск: {msg.downloaded}. {last}"
            if msg.cancelled:
                self.status_var.set("Остановлено.")
                self._append_log("СТОП: " + msg.text)
                self._append_log(summary)
                self._set_state_ready()
                messagebox.showinfo(
                    "Остановлено",
                    f"{msg.text}\n\n{summary}\n\nЧастично скачанные главы "
                    "остались в папке. При повторном запуске с тем же "
                    "диапазоном они пропустятся.",
                )
            else:
                self.status_var.set("Ошибка.")
                self._append_log("ERROR: " + msg.text)
                if msg.downloaded or msg.last_ok is not None:
                    self._append_log(summary)
                self._set_state_ready()
                body = msg.text
                if msg.downloaded or msg.last_ok is not None:
                    body = f"{msg.text}\n\n{summary}"
                messagebox.showerror("Ошибка", body)

    def _append_log(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")
        try:
            self._runlog.write(text)
        except Exception:  # pragma: no cover — file logger is best-effort
            pass

    def _on_open_log(self) -> None:
        """Open the current run's log file in the user's editor."""
        path = getattr(self._runlog, "path", None)
        if path is None or not path.exists():
            messagebox.showinfo(
                "Лог",
                "Файл лога ещё не создан или недоступен (см. ~/.novel_dl/runs).",
            )
            return
        if not open_in_system_editor(path):
            messagebox.showinfo(
                "Лог",
                f"Файл лога:\n{path}\n\nОткрой его вручную — "
                f"автоматически открыть не получилось.",
            )

    # ---- update check --------------------------------------------------
    def _repo_root(self) -> Path:
        """Return the git repo root, derived from this file's location."""
        # gui.py lives at <repo>/execution/novel_dl/gui.py.
        return Path(__file__).resolve().parents[2]

    def _update_check_worker(self) -> None:
        try:
            status = check_for_updates(self._repo_root(), do_fetch=True)
        except Exception as exc:  # pragma: no cover — defensive
            status = UpdateStatus(error=f"check failed: {exc}")
        self._messages.put(_UpdateMsg(status=status))

    def _show_update_banner(self, status: UpdateStatus) -> None:
        if status.has_updates:
            self._update_banner_label.config(
                text=(
                    f"Доступно обновление: {status.behind} "
                    f"коммит(ов) на {status.upstream}. "
                    f"({status.head_short} → {status.upstream_short})"
                ),
                foreground="#0078d7",
            )
            self._update_btn.config(state="normal")
            self._update_banner.pack(
                fill="x", side="top", before=self.root.winfo_children()[0],
            )
            self._append_log(
                f"Update check: behind by {status.behind} commit(s) on "
                f"{status.upstream}."
            )
        else:
            # No updates and no error — keep the banner hidden. If
            # there's an error, log it but don't pester the user with a
            # red bar; they can see it in the log file if curious.
            if status.error:
                self._append_log(f"Update check: {status.error}")
            else:
                self._append_log(
                    f"Update check: up to date ({status.head_short})."
                )

    def _on_update_pull(self) -> None:
        """User clicked 'Обновить' — try a fast-forward pull."""
        self._update_btn.config(state="disabled")
        self._update_banner_label.config(
            text="Обновляю…", foreground="#888888",
        )

        def worker() -> None:
            ok, msg = fast_forward_pull(self._repo_root())
            self._messages.put(_LogMsg(f"git pull: {msg}"))
            if ok:
                self._messages.put(_LogMsg(
                    "Обновление применено. Перезапусти приложение, "
                    "чтобы новый код вступил в силу."
                ))
            else:
                self._messages.put(_LogMsg(
                    "Обновление не удалось. Сделай pull вручную или "
                    "запусти заново."
                ))
            # Refresh status so the banner either disappears (now
            # up-to-date) or repaints with the remaining commits.
            self._messages.put(_UpdateMsg(
                status=check_for_updates(self._repo_root(), do_fetch=False),
            ))

        threading.Thread(target=worker, name="update-pull", daemon=True).start()

    # ---- theme ----------------------------------------------------------
    def _on_dark_mode_toggle(self) -> None:
        """Apply the new theme immediately and persist via the autosave path."""
        self._apply_theme(bool(self.dark_mode_var.get()))
        # Bypass debounce — theme is a deliberate user action, not a
        # keystroke flurry, so save right away.
        self._save_current_settings()

    def _apply_theme(self, dark: bool) -> None:
        """Switch ttk style + raw Text/Listbox colors to the requested theme.

        ttk's vista/aqua native themes can't be re-coloured via Style
        configure, so dark mode forces the ``clam`` theme (which is
        scriptable on every platform). Light mode goes back to the
        platform default if available, falling back to clam.

        Tkinter's ``tk.Text`` and the ``ttk.Combobox`` popup ListBox
        live outside ttk's style system — we paint them directly with
        ``configure(bg=..., fg=...)`` and option_add for the popup.
        """
        style = ttk.Style(self.root)
        if dark:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass
            palette = {
                "bg": "#1e1e1e",
                "fg": "#dcdcdc",
                "panel": "#252526",
                "entry_bg": "#2d2d30",
                "entry_fg": "#dcdcdc",
                "select_bg": "#264f78",
                "select_fg": "#ffffff",
                "muted": "#8a8a8a",
                "accent": "#3a8edb",
                "border": "#3f3f46",
            }
        else:
            for name in ("vista", "winnative", "aqua", "clam", "default"):
                try:
                    style.theme_use(name)
                    break
                except tk.TclError:
                    continue
            palette = {
                "bg": "#f0f0f0",
                "fg": "#000000",
                "panel": "#f0f0f0",
                "entry_bg": "#ffffff",
                "entry_fg": "#000000",
                "select_bg": "#0078d7",
                "select_fg": "#ffffff",
                "muted": "#555555",
                "accent": "#0078d7",
                "border": "#cccccc",
            }
        self.root.configure(bg=palette["bg"])
        # ttk widget colour overrides. On the native (vista/aqua)
        # themes most of these are no-ops, which is what we want — light
        # mode then keeps the OS look.
        for cls in (
            "TFrame", "TLabel", "TLabelframe", "TLabelframe.Label",
            "TCheckbutton", "TRadiobutton",
        ):
            style.configure(cls, background=palette["bg"], foreground=palette["fg"])
        style.configure(
            "TButton",
            background=palette["panel"], foreground=palette["fg"],
        )
        style.map(
            "TButton",
            background=[("active", palette["select_bg"])],
            foreground=[("active", palette["select_fg"])],
        )
        style.configure(
            "TEntry",
            fieldbackground=palette["entry_bg"],
            foreground=palette["entry_fg"],
            insertcolor=palette["fg"],
        )
        style.configure(
            "TCombobox",
            fieldbackground=palette["entry_bg"],
            background=palette["panel"],
            foreground=palette["entry_fg"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["entry_bg"])],
            foreground=[("readonly", palette["entry_fg"])],
        )
        # Treeview rows + headings.
        style.configure(
            "Treeview",
            background=palette["entry_bg"],
            fieldbackground=palette["entry_bg"],
            foreground=palette["entry_fg"],
            bordercolor=palette["border"],
        )
        style.map(
            "Treeview",
            background=[("selected", palette["select_bg"])],
            foreground=[("selected", palette["select_fg"])],
        )
        style.configure(
            "Treeview.Heading",
            background=palette["panel"],
            foreground=palette["fg"],
        )
        style.configure(
            "TProgressbar",
            background=palette["accent"],
            troughcolor=palette["panel"],
        )
        # tk.Text / tk.Listbox are not ttk-styled; configure directly.
        for text_widget in (
            getattr(self, "prompt_text", None),
            getattr(self, "log_text", None),
        ):
            if text_widget is None:
                continue
            try:
                text_widget.configure(
                    background=palette["entry_bg"],
                    foreground=palette["entry_fg"],
                    insertbackground=palette["fg"],
                    selectbackground=palette["select_bg"],
                    selectforeground=palette["select_fg"],
                )
            except tk.TclError:
                pass
        # Combobox dropdown popup is a tk Listbox inside the X server;
        # only option_add reaches it. Affects newly-opened popups; the
        # already-open one (if any) will pick up the colours next time
        # the user clicks the arrow.
        self.root.option_add("*TCombobox*Listbox.background", palette["entry_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", palette["entry_fg"])
        self.root.option_add(
            "*TCombobox*Listbox.selectBackground", palette["select_bg"],
        )
        self.root.option_add(
            "*TCombobox*Listbox.selectForeground", palette["select_fg"],
        )

    # ---- provider stub -------------------------------------------------
    def _on_provider_changed(self, _event: object) -> None:
        """Bounce non-Cohere choices back to Cohere with a friendly note.

        We deliberately keep the other providers visible in the dropdown
        (instead of hiding them) so the user knows what's planned —
        clicking one just snaps back to Cohere with a 'coming soon'
        toast. When OpenAI/Anthropic/DeepSeek get real implementations
        this method either gets removed or grows real branches.
        """
        choice = self.provider_var.get()
        if choice.startswith("Cohere"):
            return
        messagebox.showinfo(
            "Провайдер",
            f"{choice} — пока в планах. Сейчас работает только Cohere "
            "(command-a). Возвращаю выбор обратно.",
        )
        self.provider_var.set("Cohere (command-a)")

    # ---- glossary ------------------------------------------------------
    def _refresh_glossary_tree(self) -> None:
        """Repopulate the glossary Treeview from ``self._glossary``."""
        if not hasattr(self, "glossary_tree"):
            return
        for iid in self.glossary_tree.get_children():
            self.glossary_tree.delete(iid)
        for i, (src, dst) in enumerate(self._glossary):
            self.glossary_tree.insert(
                "", "end", iid=str(i), values=(src, dst),
            )

    def _persist_glossary(self) -> None:
        """Save the in-memory glossary to disk for the current book.

        Called after every add/edit/delete so the user never has to hit
        a Save button. No-op when no book is loaded yet — the slug is
        the filename and we have no way to disambiguate without one.
        """
        if not self._glossary_slug:
            return
        try:
            save_glossary(self._glossary_slug, self._glossary)
        except OSError as exc:
            self._append_log(f"Не удалось сохранить глоссарий: {exc}")

    def _load_glossary_for_book(self, slug: str) -> None:
        self._glossary_slug = slug
        try:
            self._glossary = load_glossary(slug)
        except Exception as exc:  # pragma: no cover — defensive
            self._glossary = []
            self._append_log(f"Не удалось загрузить глоссарий: {exc}")
        self._refresh_glossary_tree()
        if self._glossary:
            self._append_log(
                f"Глоссарий ({len(self._glossary)} записей) загружен "
                f"для книги «{slug}»."
            )

    def _prompt_glossary_pair(
        self, initial: tuple[str, str] = ("", ""),
    ) -> tuple[str, str] | None:
        """Pop a small modal asking for src + dst. Returns None on cancel."""
        win = tk.Toplevel(self.root)
        win.title("Запись глоссария")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)
        result: dict[str, tuple[str, str] | None] = {"value": None}

        ttk.Label(win, text="Source (en):").grid(
            row=0, column=0, padx=8, pady=(8, 4), sticky="w",
        )
        src_var = tk.StringVar(value=initial[0])
        src_entry = ttk.Entry(win, textvariable=src_var, width=40)
        src_entry.grid(row=0, column=1, padx=8, pady=(8, 4))

        ttk.Label(win, text="Перевод (ru):").grid(
            row=1, column=0, padx=8, pady=4, sticky="w",
        )
        dst_var = tk.StringVar(value=initial[1])
        dst_entry = ttk.Entry(win, textvariable=dst_var, width=40)
        dst_entry.grid(row=1, column=1, padx=8, pady=4)

        btn_row = ttk.Frame(win)
        btn_row.grid(row=2, column=0, columnspan=2, pady=(8, 8))

        def ok() -> None:
            s = src_var.get().strip()
            d = dst_var.get().strip()
            if not s or not d:
                messagebox.showwarning(
                    "Глоссарий",
                    "Оба поля обязательны.",
                    parent=win,
                )
                return
            result["value"] = (s, d)
            win.destroy()

        def cancel() -> None:
            win.destroy()

        ttk.Button(btn_row, text="OK", command=ok).pack(side="left", padx=4)
        ttk.Button(btn_row, text="Отмена", command=cancel).pack(side="left", padx=4)
        win.bind("<Return>", lambda _e: ok())
        win.bind("<Escape>", lambda _e: cancel())
        src_entry.focus_set()
        win.wait_window()
        return result["value"]

    def _on_glossary_add(self) -> None:
        if not self._glossary_slug:
            messagebox.showinfo(
                "Глоссарий",
                "Сначала загрузи книгу — глоссарий привязан к её slug.",
            )
            return
        pair = self._prompt_glossary_pair()
        if pair is None:
            return
        # Replace existing src match (case-sensitive) so the user can't
        # accidentally produce two entries for the same term.
        self._glossary = [
            (s, d) for (s, d) in self._glossary if s != pair[0]
        ]
        self._glossary.append(pair)
        self._refresh_glossary_tree()
        self._persist_glossary()

    def _on_glossary_delete(self) -> None:
        sel = self.glossary_tree.selection()
        if not sel:
            return
        indexes = sorted((int(iid) for iid in sel if iid.isdigit()), reverse=True)
        for idx in indexes:
            if 0 <= idx < len(self._glossary):
                del self._glossary[idx]
        self._refresh_glossary_tree()
        self._persist_glossary()

    def _on_glossary_double_click(self, _event: object) -> None:
        sel = self.glossary_tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return
        if not (0 <= idx < len(self._glossary)):
            return
        pair = self._prompt_glossary_pair(self._glossary[idx])
        if pair is None:
            return
        self._glossary[idx] = pair
        self._refresh_glossary_tree()
        self._persist_glossary()

    # ---- chapter status tree -------------------------------------------
    def _populate_chapter_tree(self) -> None:
        """Fill the Treeview from ``self._book.chapters`` and refresh statuses.

        Called once when a book finishes loading. After that the same
        rows live as long as the book stays loaded; only the dl/tr
        columns get updated (via ``_refresh_chapter_status``) when the
        on-disk state changes.
        """
        for iid in self.chapter_tree.get_children():
            self.chapter_tree.delete(iid)
        if self._book is None:
            return
        for i, chapter in enumerate(self._book.chapters, start=1):
            title = (chapter.title or f"Глава {i}").strip()
            self.chapter_tree.insert(
                "", "end", iid=str(i),
                values=(i, title, "", ""),
            )
        self._refresh_chapter_status()

    def _refresh_chapter_status(self) -> None:
        """Re-scan disk and update the dl/tr columns for every chapter row.

        Cheap enough (just two ``Path.exists`` per row) to call any time.
        Triggered automatically after each successful download/translate
        and manually by the toolbar button next to the range entry.
        """
        if self._book is None or not hasattr(self, "chapter_tree"):
            return
        out_root = Path(self.output_var.get() or "downloads")
        slug = safe_filename(self._book.slug or self._book.title)
        # Default download/translate locations. We trust the user's
        # current "output_dir" field; if they had a different one when
        # the data was actually written the indicators just won't match
        # — and that's the right thing to do (different config, different
        # state).
        src_dir = out_root / slug
        explicit_src = self.translate_src_var.get().strip()
        if explicit_src and Path(explicit_src).exists():
            src_dir = Path(explicit_src)
        dst_dir = src_dir.parent / (src_dir.name + "_ru")

        downloaded_nums = (
            _chapter_numbers_in(src_dir) if src_dir.exists() else set()
        )
        translated_nums = (
            _chapter_numbers_in(dst_dir) if dst_dir.exists() else set()
        )

        for i in range(1, len(self._book.chapters) + 1):
            iid = str(i)
            if not self.chapter_tree.exists(iid):
                continue
            current = self.chapter_tree.item(iid, "values")
            self.chapter_tree.item(
                iid,
                values=(
                    current[0],
                    current[1],
                    "✅" if i in downloaded_nums else "—",
                    "✅" if i in translated_nums else "—",
                ),
            )

    def _on_chapter_tree_double_click(self, _event: object) -> None:
        """Double-click on a row → write that chapter's number into the range field.

        Lets the user pick "translate just chapter 7" in two clicks
        without having to type. Multi-select + double-click writes a
        comma-separated list of the selected chapter numbers.
        """
        sel = self.chapter_tree.selection()
        if not sel:
            return
        nums = sorted(int(iid) for iid in sel if iid.isdigit())
        if not nums:
            return
        self.range_var.set(",".join(str(n) for n in nums))
        self.translate_range_var.set(self.range_var.get())
        self.epub_range_var.set(self.range_var.get())

    def _push_recent_url(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        self._recent_urls = push_recent(self._recent_urls, url)
        if hasattr(self, "_url_combo"):
            try:
                self._url_combo["values"] = list(self._recent_urls)
            except tk.TclError:
                pass

    def _push_recent_key(self, key: str) -> None:
        key = (key or "").strip()
        if not key:
            return
        # Privacy: only stash keys when the user has explicitly opted into
        # persisting them. Otherwise the in-memory list still tracks them
        # for this session's dropdown but we won't write to disk.
        if not bool(self.save_api_key_var.get()):
            return
        self._recent_api_keys = push_recent(self._recent_api_keys, key)
        if hasattr(self, "_key_combo"):
            try:
                self._key_combo["values"] = list(self._recent_api_keys)
            except tk.TclError:
                pass


_CHAPTER_NUM_RE = re.compile(r"^chapter_(\d{1,6})_")


def _chapter_numbers_in(src_dir: Path) -> set[int]:
    """Return the integer indexes of chapter_NNNN_*.txt files in ``src_dir``."""
    out: set[int] = set()
    for p in src_dir.glob("chapter_*.txt"):
        m = _CHAPTER_NUM_RE.match(p.name)
        if m:
            out.add(int(m.group(1)))
    return out


def _chapter_number_for(path: Path) -> int | None:
    """Return the chapter number embedded in a ``chapter_NNNN_*.txt`` filename."""
    m = _CHAPTER_NUM_RE.match(path.name)
    return int(m.group(1)) if m else None


def _approx_body_chars(src: Path) -> int:
    """Approximate the translatable-body length of a chapter file.

    Subtracts the leading ``# Title`` line + blank line so the char-progress
    bar matches what the translator actually feeds to Cohere. Falls back to
    the full file size on read errors so we still get a useful estimate
    without crashing the worker.
    """
    raw = src.read_text(encoding="utf-8", errors="replace")
    # Strip the header (we still translate it, but it's tiny — under 1%
    # of body chars on average — so excluding it makes the bar less
    # jittery on short chapters).
    if raw.startswith("# "):
        nl = raw.find("\n\n")
        if nl != -1:
            raw = raw[nl + 2:]
    return len(raw)


def _parse_chapter_number_spec(spec: str, available: list[int]) -> set[int] | None:
    """Parse ``3-200`` / ``all`` / ``3,5,10-20`` into a set of chapter numbers.

    Returns ``None`` if the spec is unparseable, and an empty set if the
    spec is valid but nothing in ``available`` matches it. The distinction
    matters — the GUI shows different error messages.
    """
    spec = (spec or "").strip().lower()
    if not spec or spec == "all":
        return set(available)
    available_set = set(available)
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, hi_s = part.split("-", 1)
            lo_s = lo_s.strip()
            hi_s = hi_s.strip()
            try:
                lo = int(lo_s) if lo_s else (min(available) if available else 1)
                hi = int(hi_s) if hi_s else (max(available) if available else 0)
            except ValueError:
                return None
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                if n in available_set:
                    result.add(n)
        else:
            try:
                n = int(part)
            except ValueError:
                return None
            if n in available_set:
                result.add(n)
    return result


def _open_in_file_manager(path: Path) -> None:
    import os
    import subprocess
    import sys

    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # pragma: no cover
        pass


def _install_layout_agnostic_clipboard_bindings(root: tk.Misc) -> None:
    """Make Ctrl+C/V/X/A work regardless of keyboard layout.

    Tk's default clipboard shortcuts are bound to Latin keysyms
    (``<Control-v>`` etc.), so when the user has Russian (or any non-Latin)
    layout active, the events arrive as ``<Control-Cyrillic_em>`` and Tk
    silently does nothing. The robust fix is to look at ``event.keycode``,
    which is the physical key scan code and therefore layout-independent.

    We dispatch on **both** keysym (Latin + Cyrillic twins) and keycode
    (Windows VK codes + X11 hardware codes for the row ``AVCX``) so the
    binding works on Windows, Linux/X11 and macOS X11 alike.
    """
    # Physical keys → virtual clipboard event.
    # Keys listed by keysym (layout-dependent text) and by keycode
    # (layout-independent physical code). We accept a match on either.
    specs = [
        # virt event,     latin keysyms,       cyrillic keysyms,        win vk, x11 code
        ("<<Paste>>",     {"v", "V"},          {"Cyrillic_em", "м", "М"},    86,   55),
        ("<<Copy>>",      {"c", "C"},          {"Cyrillic_es", "с", "С"},    67,   54),
        ("<<Cut>>",       {"x", "X"},          {"Cyrillic_che", "ч", "Ч"},   88,   53),
        ("<<SelectAll>>", {"a", "A"},          {"Cyrillic_ef", "ф", "Ф"},    65,   38),
    ]

    def on_ctrl_keypress(event: "tk.Event") -> "str | None":
        keysym = event.keysym or ""
        keycode = getattr(event, "keycode", 0)
        for virt, latin, cyr, win_vk, x11_code in specs:
            if keysym in latin or keysym in cyr \
                    or keycode == win_vk or keycode == x11_code:
                event.widget.event_generate(virt)
                return "break"
        return None

    for widget_class in ("TEntry", "Entry", "Text"):
        root.bind_class(widget_class, "<Control-KeyPress>", on_ctrl_keypress)

    # Tk doesn't ship a default <<SelectAll>> handler for ttk.Entry/Entry;
    # wire one that selects the whole content.
    def select_all_entry(event: "tk.Event") -> str:
        w = event.widget
        try:
            w.select_range(0, "end")
            w.icursor("end")
        except tk.TclError:
            pass
        return "break"

    for widget_class in ("TEntry", "Entry"):
        root.bind_class(widget_class, "<<SelectAll>>", select_all_entry)


def main() -> int:
    root = tk.Tk()
    # Prefer a more modern ttk theme when available.
    try:
        style = ttk.Style(root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass
    NovelDownloaderApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
