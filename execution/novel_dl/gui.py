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
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .core import Book, UnsupportedSiteError
from .downloader import DownloadError, download_chapters
from .registry import get_adapter
from .utils import FetchError, parse_range_spec, safe_filename

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
class _Error:
    text: str


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
        self.root.geometry("780x620")
        self.root.minsize(640, 520)

        self._messages: queue.Queue[object] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._book: Book | None = None
        self._adapter = None
        self._last_out_dir: Path | None = None

        self._build_widgets()
        self._set_state_idle()
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
        self.open_btn = ttk.Button(action, text="Открыть папку с результатом",
                                   command=self._on_open_folder, state="disabled")
        self.open_btn.pack(side="left", padx=(8, 0))

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

    def _set_state_loading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.status_var.set("Загружаю список глав...")

    def _set_state_ready(self) -> None:
        self.load_btn.config(state="normal")
        self.download_btn.config(state="normal")

    def _set_state_downloading(self) -> None:
        self.load_btn.config(state="disabled")
        self.download_btn.config(state="disabled")
        self.status_var.set("Скачиваю...")

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

        self._set_state_loading()
        self._append_log(f"[site={self._adapter.site_id}] загружаю {url}")
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

        self._set_state_downloading()
        self.progress.config(maximum=len(indices), value=0)
        self._append_log(
            f"Скачиваю {len(indices)} глав (первая {indices[0]}, последняя {indices[-1]}) "
            f"в {out_dir}"
        )
        adapter = self._adapter
        book = self._book
        force = self.force_var.get()
        self._spawn(lambda: self._worker_download(adapter, book, indices,
                                                  out_dir, combined, force))

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
                          force: bool) -> None:
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
            )
        except DownloadError as exc:
            self._messages.put(_Error(f"Ошибка скачивания: {exc}"))
            return
        except Exception as exc:  # pragma: no cover
            self._messages.put(_Error(f"Неожиданная ошибка: {exc!r}"))
            return
        self._messages.put(_DownloadDone(out_dir=out_dir, combined_path=combined))

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
        elif isinstance(msg, _Error):
            self.status_var.set("Ошибка.")
            self._append_log("ERROR: " + msg.text)
            self._set_state_ready()
            messagebox.showerror("Ошибка", msg.text)

    def _append_log(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.config(state="disabled")


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
