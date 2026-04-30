"""High-level download orchestration: chapters -> text files on disk."""

from __future__ import annotations

import json
import random
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from .core import Book, Chapter, SiteAdapter
from .utils import FetchError, safe_filename, wait_if_paused


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
    pause_event: "threading.Event | None" = None,
    max_workers: int = 1,
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
    - ``max_workers`` (default 1 = strictly sequential) caps how many chapter
      fetches can be in flight at once. >1 uses a ThreadPoolExecutor, keeps
      cancel/pause semantics, and still applies the same jittered ``delay``
      between successive requests **per worker** to be polite. Output is
      always sorted by source order regardless of completion order.
    """
    if max_workers > 1:
        return _download_chapters_parallel(
            adapter, book, indices, out_dir,
            delay=delay, force=force, progress=progress,
            combined_path=combined_path,
            cancel_event=cancel_event, pause_event=pause_event,
            max_workers=max_workers,
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    indices_list = list(indices)
    total = len(indices_list)
    results: list[Chapter] = []
    last_ok_idx: int | None = None

    for i, idx in enumerate(indices_list, start=1):
        # Pause first so a user clicking Pause mid-loop doesn't have to
        # wait for the next chapter to be queued before the worker actually
        # stops. Cancel is checked again right after the pause returns —
        # a Stop click during a pause should still take effect.
        wait_if_paused(pause_event, cancel_event)
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


def _download_chapters_parallel(
    adapter: SiteAdapter,
    book: Book,
    indices: Iterable[int],
    out_dir: Path,
    *,
    delay: float,
    force: bool,
    progress: Callable[[str], None] | None,
    combined_path: Path | None,
    cancel_event: "threading.Event | None",
    pause_event: "threading.Event | None",
    max_workers: int,
) -> list[Chapter]:
    """Parallel cousin of :func:`download_chapters` for max_workers > 1.

    Strategy: run up to ``max_workers`` ``fetch_chapter`` calls in flight at
    the same time. Cached chapters (file already on disk) are handled inline
    on the main thread — they're cheap and would otherwise occupy a worker
    slot for nothing. After each successful fetch the worker sleeps the
    same jittered ``delay`` we use in the serial path so we still aren't
    pummelling the site flat-out.

    Cancellation: any worker that observes ``cancel_event`` set returns
    immediately, the executor is shut down, and all in-flight chapters
    that completed before cancel are kept on disk + counted into
    ``DownloadCancelled.downloaded``. Pause is honoured per-worker; when
    paused, workers spin in :func:`wait_if_paused` so they don't burn CPU.

    Output ordering: results are sorted by source order before
    ``_write_meta``/``_write_combined`` so the combined file reads top to
    bottom even though completion can come back any order.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    indices_list = list(indices)
    total = len(indices_list)
    if total == 0:
        _write_meta(out_dir, book, [])
        if combined_path is not None:
            _write_combined(combined_path, book, [])
        return []

    # Resolve cached vs to-fetch up front so we can report both kinds of
    # progress accurately and not waste a worker slot on a no-op.
    cached: dict[int, Chapter] = {}  # idx -> chapter (loaded from disk)
    pending: list[int] = []
    for idx in indices_list:
        chapter = book.chapters[idx - 1]
        path = _chapter_file_path(out_dir, chapter)
        if path.exists() and not force:
            chapter.text = _read_body(path)
            cached[idx] = chapter
        else:
            pending.append(idx)

    completed: dict[int, Chapter] = dict(cached)
    last_ok_idx: int | None = None
    if cached:
        # Cached chapters land in 'completed order' = original index order.
        last_ok_idx = max(cached)
        if progress:
            for idx in sorted(cached):
                ch = cached[idx]
                progress(
                    f"[{len(completed)}/{total}] skip (exists): "
                    f"{_chapter_label(ch)}"
                )

    def fetch_one(idx: int) -> tuple[int, Chapter]:
        # Honour pause/cancel before each fetch so a worker waiting in the
        # pool's queue still picks up a Stop click within ~1s instead of
        # racing through whatever was queued.
        wait_if_paused(pause_event, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise DownloadCancelled("cancel before fetch")
        chapter = book.chapters[idx - 1]
        filled = adapter.fetch_chapter(chapter)
        if not filled.text or not filled.text.strip():
            raise DownloadError(
                f"Пустое тело главы {idx} ({_chapter_label(chapter)}). "
                "Сайт не вернул текст (Cloudflare или регион)."
            )
        path = _chapter_file_path(out_dir, filled)
        _write_chapter_file(path, filled)
        if delay > 0:
            jitter = delay * 0.3
            time.sleep(max(0.0, delay + random.uniform(-jitter, jitter)))
        return idx, filled

    failure: BaseException | None = None
    cancelled = False
    pending_iter = iter(pending)
    in_flight: dict = {}  # future -> idx

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        # Prime the pool with up to max_workers tasks.
        for _ in range(min(max_workers, len(pending))):
            try:
                idx = next(pending_iter)
            except StopIteration:
                break
            in_flight[pool.submit(fetch_one, idx)] = idx

        while in_flight and failure is None and not cancelled:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in done:
                idx = in_flight.pop(fut)
                exc = fut.exception()
                if isinstance(exc, DownloadCancelled):
                    cancelled = True
                    continue
                if exc is not None:
                    failure = exc
                    continue
                _, chapter = fut.result()
                completed[idx] = chapter
                if progress:
                    progress(
                        f"[{len(completed)}/{total}] fetched: "
                        f"{_chapter_label(chapter)}"
                    )
                if last_ok_idx is None or idx > last_ok_idx:
                    last_ok_idx = idx
                # Top up the pool only if we're still healthy.
                if failure is None and not cancelled and (
                    cancel_event is None or not cancel_event.is_set()
                ):
                    try:
                        nxt = next(pending_iter)
                    except StopIteration:
                        continue
                    in_flight[pool.submit(fetch_one, nxt)] = nxt
            # External cancel observed mid-batch — stop accepting new
            # work and let the in-flight batch drain.
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True

        # Drain whatever's still running so we don't deadlock the pool
        # at shutdown. Their exceptions are accepted but ignored — we
        # already have the user's intent (cancel or first failure).
        if in_flight:
            for fut in list(in_flight):
                try:
                    res = fut.result(timeout=30)
                except Exception:
                    continue
                idx, chapter = res
                completed.setdefault(idx, chapter)

    # Build the final ordered result list in source-order even though
    # completion order was arbitrary.
    results = [completed[idx] for idx in indices_list if idx in completed]

    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        # Even when cancelled, persist meta.json for what we DID save so
        # a follow-up run can pick up cleanly.
        _write_meta(out_dir, book, results)
        if combined_path is not None:
            _write_combined(combined_path, book, results)
        raise DownloadCancelled(
            f"Остановлено пользователем. Сохранено {len(results)}/{total}.",
            downloaded=len(results),
            last_ok=last_ok_idx,
        )
    if failure is not None:
        # Same here — keep what landed on disk so the user doesn't lose
        # progress to one bad chapter mid-batch.
        _write_meta(out_dir, book, results)
        if combined_path is not None:
            _write_combined(combined_path, book, results)
        if isinstance(failure, DownloadError):
            raise DownloadError(
                str(failure),
                downloaded=len(results),
                last_ok=last_ok_idx,
            )
        if isinstance(failure, FetchError):
            raise DownloadError(
                f"Сеть упала: {failure}",
                downloaded=len(results),
                last_ok=last_ok_idx,
            )
        raise DownloadError(
            f"Ошибка скачки: {failure!r}",
            downloaded=len(results),
            last_ok=last_ok_idx,
        )

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
