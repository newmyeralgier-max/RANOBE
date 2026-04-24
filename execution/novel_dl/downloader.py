"""High-level download orchestration: chapters -> text files on disk."""

from __future__ import annotations

import json
import random
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from .core import Book, Chapter, SiteAdapter
from .utils import FetchError, safe_filename


class DownloadError(RuntimeError):
    """Raised when a chapter cannot be downloaded after retries.

    Carries progress stats so callers (CLI / GUI) can report how far the
    download got before giving up: ``downloaded`` counts chapters that
    landed on disk in this run, ``last_ok`` is the 1-based index into the
    book's chapter list of the last successfully saved chapter (or ``None``
    if nothing was saved yet).
    """

    def __init__(self, msg: str, *, downloaded: int = 0,
                 last_ok: int | None = None) -> None:
        super().__init__(msg)
        self.downloaded = downloaded
        self.last_ok = last_ok


class DownloadCancelled(DownloadError):  # noqa: N818 — "cancelled" reads better than "Error"
    """Raised when the user asked to stop the download."""


def download_chapters(
    adapter: SiteAdapter,
    book: Book,
    indices: Iterable[int],
    out_dir: Path,
    *,
    delay: float = 1.0,
    force: bool = False,
    progress: Callable[[str], None] | None = None,
    combined_path: Path | None = None,
    cancel_event: "threading.Event | None" = None,
) -> list[Chapter]:
    """Download the chapters at the given 1-based ``indices``.

    - Writes one ``chapter_NNNN.txt`` per chapter into ``out_dir`` (already
      existing files are skipped unless ``force`` is true).
    - Each file starts with the chapter title as a single Markdown H1 line,
      followed by a blank line and the paragraph-separated body. This format is
      optimised for pasting into local LLM translators.
    - If the adapter returns an empty body or the network fetch errors out, we
      **stop immediately** (``DownloadError``) instead of writing empty files.
      The exception carries ``.downloaded`` and ``.last_ok`` so the caller can
      tell the user how far we got.
    - If ``cancel_event`` is supplied and set mid-loop, stop with
      :class:`DownloadCancelled`.
    - After all chapters succeed, writes ``meta.json`` and optionally a single
      combined ``combined_path`` text file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    indices_list = list(indices)
    total = len(indices_list)
    results: list[Chapter] = []
    last_ok_idx: int | None = None

    for i, idx in enumerate(indices_list, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled(
                f"Остановлено пользователем на {i}/{total}.",
                downloaded=len(results),
                last_ok=last_ok_idx,
            )

        chapter = book.chapters[idx - 1]
        file_path = _chapter_file_path(out_dir, chapter)
        label = _chapter_label(chapter)

        if file_path.exists() and not force:
            if progress:
                progress(f"[{i}/{total}] skip (exists): {label}")
            chapter.text = _read_body(file_path)
            results.append(chapter)
            last_ok_idx = idx
            continue

        if progress:
            progress(f"[{i}/{total}] fetching: {label}")
        try:
            filled = adapter.fetch_chapter(chapter)
        except FetchError as exc:
            raise DownloadError(
                f"Сеть упала на главе {i}/{total} ({label}): {exc}",
                downloaded=len(results),
                last_ok=last_ok_idx,
            ) from exc
        except Exception as exc:  # pragma: no cover - adapter-specific
            raise DownloadError(
                f"Ошибка на главе {i}/{total} ({label}): {exc!r}",
                downloaded=len(results),
                last_ok=last_ok_idx,
            ) from exc

        if not filled.text or not filled.text.strip():
            # Don't write empty files — treat as a fatal error and stop.
            raise DownloadError(
                f"Пустое тело главы {i}/{total} ({label}). Сайт не вернул "
                f"текст (возможно, Cloudflare или региональный фильтр). "
                f"Стоп на главе {i}.",
                downloaded=len(results),
                last_ok=last_ok_idx,
            )

        _write_chapter_file(file_path, filled)
        results.append(filled)
        last_ok_idx = idx
        if delay > 0 and i < total:
            # Jitter ±30% so we don't look like a metronome to the site's
            # rate-limiter — the fixed-interval pattern is what typically
            # triggers ranobes' anti-bot after ~50 fast requests.
            jitter = delay * 0.3
            time.sleep(max(0.0, delay + random.uniform(-jitter, jitter)))

    _write_meta(out_dir, book, results)
    if combined_path is not None:
        _write_combined(combined_path, book, results)
    return results


# ---- formatting & I/O ----------------------------------------------------

def _chapter_label(chapter: Chapter) -> str:
    if chapter.num is not None:
        return f"Ch {chapter.num}: {chapter.title}"
    return chapter.title or chapter.url


def _chapter_file_path(out_dir: Path, chapter: Chapter) -> Path:
    number = chapter.num if chapter.num is not None else chapter.index + 1
    slug = safe_filename(chapter.title, max_len=80) if chapter.title else ""
    suffix = f"_{slug}" if slug else ""
    return out_dir / f"chapter_{number:04d}{suffix}.txt"


def _write_chapter_file(path: Path, chapter: Chapter) -> None:
    title = chapter.title or "Untitled"
    body = chapter.text or ""
    content = f"# {title}\n\n{body}\n"
    path.write_text(content, encoding="utf-8")


def _read_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # Strip the leading "# Title\n\n" header that _write_chapter_file added.
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join(lines[2:]).strip()
    return text


def _write_meta(out_dir: Path, book: Book, chapters: list[Chapter]) -> None:
    meta = {
        "title": book.title,
        "author": book.author,
        "slug": book.slug,
        "source_url": book.source_url,
        "cover_url": book.cover_url,
        "description": book.description,
        "total_chapters": len(book.chapters),
        "downloaded_chapters": [
            {"num": c.num, "title": c.title, "url": c.url} for c in chapters
        ],
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _write_combined(path: Path, book: Book, chapters: list[Chapter]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = []
    header = f"# {book.title}"
    if book.author:
        header += f"\n*{book.author}*"
    if book.source_url:
        header += f"\n\nSource: {book.source_url}"
    parts.append(header)

    for ch in chapters:
        title = ch.title or f"Chapter {ch.num or ch.index + 1}"
        body = ch.text or ""
        parts.append(f"\n\n## {title}\n\n{body.strip()}")

    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


# ---- misc ----------------------------------------------------------------

def book_summary_dict(book: Book) -> dict:
    """Serialisable summary useful for ``--dump-book`` / debugging output."""
    return {
        "title": book.title,
        "author": book.author,
        "slug": book.slug,
        "source_url": book.source_url,
        "total_chapters": len(book.chapters),
        "chapters": [asdict(c) for c in book.chapters],
    }
