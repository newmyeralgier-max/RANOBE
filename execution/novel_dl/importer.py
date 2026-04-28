"""Import a local novel file (.txt / .epub / .fb2) into the downloader layout.

The translator in :mod:`novel_dl.translator` reads ``chapter_NNNN.txt``
files from a directory and produces ``chapter_NNNN.txt`` translations in
another directory. By converting an arbitrary local novel into that same
on-disk shape we let the user translate any text they have lying around
— bought EPUBs, fan TLs, scraped txt dumps — without writing a new
translator path. The importer:

1. Detects the format from the extension.
2. Splits the content into chapters using format-appropriate cues.
3. Writes one ``chapter_NNNN.txt`` per chapter into ``out_dir``, plus a
   ``meta.json`` so the downloader's existing UI logic (chapter list,
   status ticks) keeps working.

When the source has no obvious chapter boundaries (a single .txt with
no headings) we fall back to a single chapter of the whole text. Better
to translate "all of it as one chapter" than refuse the input.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict
from pathlib import Path

from .core import Book, Chapter
from .utils import normalize_text, safe_filename, strip_tags


class LocalImportError(RuntimeError):  # noqa: N818 — match DownloadError naming
    """Raised when a local file can't be imported as a novel."""


# Heuristics for splitting a freeform .txt into chapters. Picks up the
# common shapes we've seen in the wild: numbered "Chapter 12" lines (any
# case), Russian "Глава 12", "Часть 1", and Markdown H1/H2 lines.
_TXT_CHAPTER_PATTERNS = [
    re.compile(r"^\s*chapter\s+\d+(?:[:.\s].*)?$", re.IGNORECASE),
    re.compile(r"^\s*глава\s+\d+(?:[:.\s].*)?$", re.IGNORECASE),
    re.compile(r"^\s*часть\s+\d+(?:[:.\s].*)?$", re.IGNORECASE),
    re.compile(r"^#{1,2}\s+.+$"),  # Markdown H1/H2
]


def import_local_file(src: Path, out_dir: Path) -> Book:
    """Convert a local file into the downloader's on-disk layout.

    Returns a :class:`Book` whose chapters have ``text`` already filled
    in (each chapter file is also written to ``out_dir``). The returned
    book mirrors what an adapter would have produced, so the rest of
    the GUI can treat imported and scraped novels identically.
    """
    suffix = src.suffix.lower()
    if suffix == ".txt":
        chapters = _split_txt(src)
    elif suffix == ".epub":
        chapters = _split_epub(src)
    elif suffix == ".fb2":
        chapters = _split_fb2(src)
    else:
        raise LocalImportError(
            f"Не поддерживаемый формат: {suffix!r}. "
            f"Можно .txt, .epub, .fb2."
        )

    if not chapters:
        raise LocalImportError(
            f"В {src.name} не нашлось ни одной главы."
        )

    title = src.stem
    book = Book(
        title=title,
        author="",
        slug=safe_filename(title),
        source_url=f"file://{src}",
        cover_url="",
        description="",
        chapters=chapters,
    )
    _write_to_out_dir(book, out_dir)
    return book


# ---- format-specific splitters -------------------------------------------


def _split_txt(src: Path) -> list[Chapter]:
    """Split a free-form .txt by chapter-heading heuristics.

    Falls back to a single chapter named after the file when no headings
    are found — useful for translating short-stories or book excerpts the
    user dropped in as one big blob.
    """
    raw = src.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    headings: list[tuple[int, str]] = []  # (line index, heading text)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        for pat in _TXT_CHAPTER_PATTERNS:
            if pat.match(stripped):
                # Strip leading '#' for markdown headings so the chapter
                # title doesn't carry its formatting marker.
                clean = re.sub(r"^#{1,3}\s*", "", stripped).strip()
                headings.append((i, clean))
                break

    if not headings:
        # No headings — whole file is one chapter.
        body = normalize_text(raw)
        return [
            Chapter(num=1, title=src.stem, url="", index=0, text=body),
        ]

    # First heading might not be at line 0; if there's any text before it,
    # treat it as a "Foreword" chapter so we don't silently drop content.
    chapters: list[Chapter] = []
    if headings[0][0] > 0:
        intro = "\n".join(lines[: headings[0][0]]).strip()
        if intro:
            chapters.append(Chapter(
                num=0, title="Foreword", url="", index=0,
                text=normalize_text(intro),
            ))

    for ci, (line_idx, heading) in enumerate(headings):
        end = headings[ci + 1][0] if ci + 1 < len(headings) else len(lines)
        body_lines = lines[line_idx + 1: end]
        body = normalize_text("\n".join(body_lines))
        chapters.append(Chapter(
            num=len(chapters) + 1 if chapters and chapters[0].num == 0
            else ci + 1,
            title=heading,
            url="",
            index=len(chapters),
            text=body,
        ))
    return chapters


def _split_epub(src: Path) -> list[Chapter]:
    """Pull chapters out of an EPUB by walking its spine.

    EPUB = a zip containing an OPF manifest and HTML/XHTML chapter
    files. We read the manifest to map ids → file paths, the spine to
    get chapter ORDER, then strip HTML to plain text. We don't try to
    be a full-fidelity reader (no images, no styling) — just text good
    enough to feed to the translator.
    """
    with zipfile.ZipFile(src) as zf:
        names = zf.namelist()
        # container.xml tells us where the OPF lives. Some EPUBs put the
        # OPF at the root; some bury it under OEBPS/. Always look it up.
        try:
            container_xml = zf.read("META-INF/container.xml").decode(
                "utf-8", errors="replace",
            )
        except KeyError as exc:
            raise LocalImportError(
                "EPUB без META-INF/container.xml — файл повреждён."
            ) from exc
        m = re.search(r'full-path="([^"]+)"', container_xml)
        if not m:
            raise LocalImportError("В container.xml не нашёлся OPF.")
        opf_path = m.group(1)
        opf_dir = str(Path(opf_path).parent).replace("\\", "/")
        opf_xml = zf.read(opf_path).decode("utf-8", errors="replace")

        # Manifest: id -> href. Spine: ordered list of idrefs.
        manifest: dict[str, str] = {}
        for m in re.finditer(
            r'<item\s[^>]*id="([^"]+)"[^>]*href="([^"]+)"', opf_xml,
        ):
            manifest[m.group(1)] = m.group(2)
        spine_ids = [
            m.group(1)
            for m in re.finditer(r'<itemref\s[^>]*idref="([^"]+)"', opf_xml)
        ]

        chapters: list[Chapter] = []
        idx = 1
        for idref in spine_ids:
            href = manifest.get(idref)
            if not href:
                continue
            # Resolve relative to OPF dir, but case-insensitively because
            # some packagers ship mismatched case in the manifest vs zip.
            candidate = (
                f"{opf_dir}/{href}" if opf_dir and opf_dir != "."
                else href
            )
            try:
                data = zf.read(candidate)
            except KeyError:
                # Fallback: case-insensitive match
                lower = candidate.lower()
                match = next(
                    (n for n in names if n.lower() == lower), None,
                )
                if not match:
                    continue
                data = zf.read(match)
            html = data.decode("utf-8", errors="replace")
            # Title heuristic: first <h1>…</h1> or <title>…</title>.
            title_match = re.search(
                r"<h1[^>]*>(.*?)</h1>", html,
                flags=re.IGNORECASE | re.DOTALL,
            ) or re.search(
                r"<title[^>]*>(.*?)</title>", html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            title = (
                normalize_text(strip_tags(title_match.group(1))).strip()
                if title_match else f"Chapter {idx}"
            )
            text = normalize_text(strip_tags(html)).strip()
            if not text:
                continue
            # If the title is duplicated as the first line of the body,
            # drop it so we don't render it twice.
            if title and text.startswith(title):
                text = text[len(title):].lstrip("\n").strip()
            chapters.append(Chapter(
                num=idx, title=title or f"Chapter {idx}",
                url="", index=idx - 1, text=text,
            ))
            idx += 1
        return chapters


def _split_fb2(src: Path) -> list[Chapter]:
    """Walk every <section><title>…</title>…</section> as a chapter.

    FB2 is XML; we use ElementTree which keeps memory low for the rare
    multi-MB book. Nested sections (sub-chapters) are flattened — the
    translator doesn't care about hierarchy, just about chapter
    boundaries that fit a single Cohere request.
    """
    try:
        tree = ET.parse(src)
    except ET.ParseError as exc:
        raise LocalImportError(f"FB2 не парсится как XML: {exc}") from exc
    root = tree.getroot()
    # FB2 uses default namespace; strip namespaces for simpler queries.
    for elem in root.iter():
        if "}" in elem.tag:
            elem.tag = elem.tag.split("}", 1)[1]

    chapters: list[Chapter] = []
    for idx, section in enumerate(root.iter("section"), start=1):
        title_elem = section.find("title")
        title = (
            normalize_text(_text_of(title_elem)).strip()
            if title_elem is not None else f"Chapter {idx}"
        )
        # Body = everything in the section EXCEPT the <title>. We strip
        # the title before extracting text so the output isn't
        # "Chapter 1\n\nChapter 1\n\n…".
        body_parts: list[str] = []
        for child in section:
            if child.tag == "title":
                continue
            txt = _text_of(child)
            if txt:
                body_parts.append(txt)
        body = normalize_text("\n\n".join(body_parts)).strip()
        if not body:
            continue
        chapters.append(Chapter(
            num=idx, title=title or f"Chapter {idx}",
            url="", index=idx - 1, text=body,
        ))
    return chapters


def _text_of(elem) -> str:
    """Recursively gather text from an Element tree node, joining paragraphs."""
    if elem is None:
        return ""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_text_of(child))
        if child.tail:
            parts.append(child.tail)
    joined = "".join(parts)
    # FB2 wraps each paragraph in <p>; we lost that boundary by joining,
    # so reinstate paragraph separators from <p> tags via text-of recursion
    # caller. Here we just trim whitespace.
    return joined.strip()


# ---- writing the imported book to disk -----------------------------------


def _write_to_out_dir(book: Book, out_dir: Path) -> None:
    """Write each chapter to ``chapter_NNNN.txt`` and a meta.json sidecar.

    The output layout matches what :func:`novel_dl.downloader.download_chapters`
    produces, so the translator and EPUB builder don't need to know the
    book was imported instead of scraped.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for chapter in book.chapters:
        n = chapter.num if chapter.num is not None else chapter.index + 1
        path = out_dir / f"chapter_{n:04d}.txt"
        body = (chapter.text or "").rstrip()
        # Same on-disk format as downloader: '# Title\n\nbody\n'.
        path.write_text(
            f"# {chapter.title}\n\n{body}\n", encoding="utf-8",
        )
    meta_path = out_dir / "meta.json"
    meta = {
        "book": {
            "title": book.title,
            "author": book.author,
            "slug": book.slug,
            "source_url": book.source_url,
            "cover_url": book.cover_url,
            "description": book.description,
        },
        "chapters": [
            {k: v for k, v in asdict(ch).items() if k != "text"}
            for ch in book.chapters
        ],
    }
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8",
    )
