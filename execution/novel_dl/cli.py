"""Command-line interface for the universal novel downloader."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import UnsupportedSiteError
from .downloader import DownloadError, download_chapters
from .registry import get_adapter
from .utils import FetchError, parse_range_spec, safe_filename


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="novel_dl",
        description=(
            "Universal ranobe/webnovel downloader. Paste a book URL, pick "
            "chapters, get clean .txt files ready for local translation."
        ),
    )
    parser.add_argument("url", help="Book URL (e.g. https://ranobes.net/novels/<id>-<slug>.html)")
    parser.add_argument(
        "-o", "--output",
        default="downloads",
        help="Output root directory (default: ./downloads)",
    )
    parser.add_argument(
        "--range",
        dest="range_spec",
        default=None,
        help=(
            "Chapter selection, e.g. '1-50', '500-', '1,5,10-20'. "
            "If omitted together with --all, the CLI asks interactively."
        ),
    )
    parser.add_argument(
        "--all", action="store_true", help="Download every chapter non-interactively.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Only fetch and print the chapter list, then exit.",
    )
    parser.add_argument(
        "--combined", action="store_true",
        help="Also write a single combined .txt with all selected chapters.",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Seconds to wait between chapter downloads (default: 1.0).",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-download chapters even if their file already exists.",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Don't prompt for confirmation before starting the download.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        adapter = get_adapter(args.url)
    except UnsupportedSiteError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    print(f"[1/3] Site: {adapter.site_id}. Fetching book info...")
    try:
        book = adapter.fetch_book(args.url)
    except FetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3

    total = len(book.chapters)
    print(f"      Title:    {book.title}")
    if book.author:
        print(f"      Author:   {book.author}")
    print(f"      Chapters: {total}")
    if not total:
        print("No chapters found on this page.", file=sys.stderr)
        return 4

    if args.list:
        _print_chapter_list(book.chapters)
        return 0

    print("[2/3] Selecting chapters...")
    indices = _resolve_indices(args, total, book_chapters=book.chapters)
    if not indices:
        print("Nothing selected; exiting.")
        return 0
    print(f"      Selected {len(indices)} of {total} chapters "
          f"(first {indices[0]}, last {indices[-1]}).")

    if not args.yes and not args.all and args.range_spec is None:
        # Interactive flow already confirmed by the user; no extra prompt.
        pass

    out_dir = Path(args.output) / safe_filename(book.slug or book.title)
    out_dir.mkdir(parents=True, exist_ok=True)
    combined = (out_dir / "combined.txt") if args.combined else None

    print(f"[3/3] Downloading to {out_dir} ...")
    try:
        download_chapters(
            adapter,
            book,
            indices,
            out_dir,
            delay=args.delay,
            force=args.force,
            progress=lambda msg: print("    " + msg),
            combined_path=combined,
        )
    except DownloadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 5

    print("Done.")
    print(f"Chapters: {out_dir}")
    if combined:
        print(f"Combined: {combined}")
    return 0


def _resolve_indices(
    args: argparse.Namespace, total: int, *, book_chapters
) -> list[int]:
    if args.all:
        return list(range(1, total + 1))
    if args.range_spec:
        return parse_range_spec(args.range_spec, total)
    return _interactive_select(book_chapters)


def _interactive_select(chapters) -> list[int]:
    total = len(chapters)
    print()
    print(f"      Available: {total} chapters.")
    if chapters:
        first = chapters[0]
        last = chapters[-1]
        print(f"        #1    : {first.title}")
        print(f"        #{total:<4}: {last.title}")
    print()
    print("      Enter a range (e.g. '1-50', '500-', '1,5,10-20'),")
    print("      'all' for everything, or blank to cancel.")
    try:
        raw = input("      Selection: ").strip()
    except EOFError:
        return []
    if not raw:
        return []
    return parse_range_spec(raw, total)


def _print_chapter_list(chapters) -> None:
    for ch in chapters:
        num = str(ch.num) if ch.num is not None else "-"
        print(f"  #{num:>5}  {ch.title}  {ch.url}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
