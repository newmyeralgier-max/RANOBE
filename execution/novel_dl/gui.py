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

from .core import Book, UnsupportedSiteError
from .downloader import DownloadCancelled, DownloadError, download_chapters
from .epub import build_epub_from_folder
from .registry import get_adapter
from .settings import load_settings, save_settings
from .translator import (
    DEFAULT_COHERE_MODEL,
    TranslationCancelled,
    TranslationError,
    TranslatorConfig,
    translate_folder,
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


# ---- app ------------------------------------------------------------------

class NovelDownloaderApp:
    """Tkinter front-end. One instance == one window."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Novel Downloader")
        self.root.geometry("900x820")
        self.root.minsize(760, 640)

        self._messages: queue.Queue[object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._book: Book | None = None
        self._adapter = None
        self._last_out_dir: Path | None = None

        self._build_widgets()
        self._apply_settings(load_settings())
        self._set_state_idle()
        _install_layout_agnostic_clipboard_bindings(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_messages)

    # ---- UI layout ------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 10, "pady": 6}

        top = ttk.LabelFrame(self.root, text="1. Ссылка на книгу")
        top.pack(fill="x", **pad)
        self.url_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.url_var).pack(
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
        self.stop_btn = ttk.Button(action, text="Остановить",
                                   command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.open_btn = ttk.Button(action, text="Открыть папку с результатом",
                                   command=self._on_open_folder, state="disabled")
        self.open_btn.pack(side="left", padx=(8, 0))

        tr = ttk.LabelFrame(self.root, text="5. Перевод (Cohere)")
        tr.pack(fill="x", **pad)
        key_row = ttk.Frame(tr)
        key_row.pack(fill="x", padx=8, pady=(8, 2))
        ttk.Label(key_row, text="API-ключ:").pack(side="left")
        self.cohere_key_var = tk.StringVar()
        self.cohere_key_entry = ttk.Entry(
            key_row, textvariable=self.cohere_key_var, show="•",
        )
        self.cohere_key_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
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

        prompt_frame = ttk.Frame(tr)
        prompt_frame.pack(fill="x", padx=8, pady=(4, 4))
        ttk.Label(prompt_frame, text="Промпт (редактируемый):",
                  anchor="w").pack(fill="x")
        self.prompt_text = tk.Text(prompt_frame, height=8, wrap="word")
        self.prompt_text.pack(side="left", fill="both", expand=True)
        self.prompt_text.insert("1.0", DEFAULT_TRANSLATOR_PROMPT)
        prompt_sb = ttk.Scrollbar(prompt_frame, orient="vertical",
                                  command=self.prompt_text.yview)
        prompt_sb.pack(side="right", fill="y")
        self.prompt_text.configure(yscrollcommand=prompt_sb.set)

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

        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=(0, 4))
        self.status_var = tk.StringVar(value="Готов.")
        ttk.Label(self.root, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=10,
        )

        log_frame = ttk.LabelFrame(self.root, text="Лог")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log = tk.Text(log_frame, height=10, wrap="word", state="disabled")
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        log_sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        log_sb.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.log.configure(yscrollcommand=log_sb.set)

    # ---- state transitions ---------------------------------------------
    def _set_state_idle(self) -> None:
        self.load_btn.config(state="normal")
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.translate_btn.config(state="normal")
        self.epub_btn.config(state="normal")

    def _set_state_loading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="disabled")
        self.translate_btn.config(state="disabled")
        self.epub_btn.config(state="disabled")
        self.status_var.set("Загружаю список глав...")

    def _set_state_ready(self) -> None:
        self.load_btn.config(state="normal")
        self.download_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.translate_btn.config(state="normal")
        self.epub_btn.config(state="normal")

    def _set_state_downloading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
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
        self._set_state_loading()
        self._append_log(f"[site={self._adapter.site_id}] загружаю {url}")
        self._save_current_settings()
        self._spawn(lambda: self._worker_fetch_book(url))

    def _on_pick_dir(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or str(Path.cwd()))
        if chosen:
            self.output_var.set(chosen)

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
        self._set_state_downloading()
        self.progress.config(maximum=len(indices), value=0)
        self._append_log(
            f"Скачиваю {len(indices)} глав (первая {indices[0]}, последняя {indices[-1]}) "
            f"в {out_dir}"
        )
        self._save_current_settings()
        self._spawn(lambda: self._worker_download(adapter, book, indices,
                                                  out_dir, combined, force,
                                                  self._cancel_event))

    def _on_stop(self) -> None:
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()
            self.stop_btn.config(state="disabled")
            self.status_var.set("Останавливаю...")
            self._append_log("Запрошена остановка. Дожидаюсь текущей задачи...")

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
            "translate_range": self.translate_range_var.get(),
            "retranslate": bool(self.retranslate_var.get()),
            "epub_range": self.epub_range_var.get(),
            "epub_source": self.epub_source_var.get(),
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
        if _s("translate_range"):
            self.translate_range_var.set(_s("translate_range"))
        self.retranslate_var.set(_b("retranslate", False))
        if _s("epub_range"):
            self.epub_range_var.set(_s("epub_range"))
        if _s("epub_source"):
            self.epub_source_var.set(_s("epub_source"))

    def _save_current_settings(self) -> None:
        try:
            save_settings(self._current_settings())
        except Exception as exc:  # pragma: no cover
            self._append_log(f"Не удалось сохранить настройки: {exc}")

    def _on_close(self) -> None:
        self._save_current_settings()
        self.root.destroy()

    # ---- translate / epub --------------------------------------------
    def _src_dir_for_postprocess(self) -> Path | None:
        """Where the raw English chapter_*.txt files live."""
        if self._last_out_dir is not None and self._last_out_dir.exists():
            return self._last_out_dir
        # Fall back to computing it from the current book + output field.
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
        cfg = TranslatorConfig(api_key=api_key, model=model, system_prompt=prompt)

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
        self._set_state_downloading()
        files_count = len(wanted)
        self.progress.config(maximum=files_count, value=0)
        self._append_log(
            f"Перевод {files_count} глав (из {len(available_nums)}) "
            f"→ {dst_dir} (модель {model})"
        )
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
        use_ru = self.epub_source_var.get().startswith("ru")
        src_dir = raw_dir.parent / (raw_dir.name + "_ru") if use_ru else raw_dir
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
        if use_ru:
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
        self._set_state_downloading()
        self.stop_btn.config(state="disabled")
        self.status_var.set("Собираю EPUB...")
        self.progress.config(maximum=1, value=0)
        self._append_log(
            f"Собираю EPUB из {src_dir} ({len(wanted)} глав) → {epub_path}"
        )
        self._save_current_settings()
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

    def _worker_fetch_book(self, url: str) -> None:
        try:
            book = self._adapter.fetch_book(url)
        except FetchError as exc:
            self._messages.put(_Error(f"Ошибка загрузки книги: {exc}"))
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
                delay=0.5, force=force,
                progress=progress,
                combined_path=combined,
                cancel_event=cancel_event,
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
        counter = {"i": 0}
        total = len(wanted)

        def progress(msg: str) -> None:
            self._messages.put(_LogMsg(msg))
            # Count only top-level "[i/N]" file transitions, not chunk logs.
            if msg.lstrip().startswith("["):
                counter["i"] += 1
                self._messages.put(_ProgressMsg(counter["i"], total))

        try:
            done = translate_folder(
                src_dir, dst_dir, cfg,
                force=force, progress=progress, cancel_event=cancel_event,
                wanted_numbers=wanted,
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
        elif isinstance(msg, _ProgressMsg):
            self.progress.config(value=msg.current, maximum=msg.total)
            self.status_var.set(f"Скачано {msg.current}/{msg.total}")
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
            self._set_state_ready()
        elif isinstance(msg, _DownloadDone):
            self._last_out_dir = msg.out_dir
            self.open_btn.config(state="normal")
            self.status_var.set("Готово!")
            self._append_log(f"Готово. Папка: {msg.out_dir}")
            if msg.combined_path is not None:
                self._append_log(f"Файл combined: {msg.combined_path}")
            self._set_state_ready()
            messagebox.showinfo("Готово",
                                f"Скачано в:\n{msg.out_dir}")
        elif isinstance(msg, _TranslateDone):
            self.status_var.set("Перевод готов.")
            self._append_log(f"Переведено {msg.count} глав в {msg.out_dir}")
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


_CHAPTER_NUM_RE = re.compile(r"^chapter_(\d{1,6})_")


def _chapter_numbers_in(src_dir: Path) -> set[int]:
    """Return the integer indexes of chapter_NNNN_*.txt files in ``src_dir``."""
    out: set[int] = set()
    for p in src_dir.glob("chapter_*.txt"):
        m = _CHAPTER_NUM_RE.match(p.name)
        if m:
            out.add(int(m.group(1)))
    return out


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
