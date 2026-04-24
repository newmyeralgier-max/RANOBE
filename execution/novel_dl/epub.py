"""Tiny stdlib-only EPUB 3 writer.

We keep this deliberately minimal: one book per call, one XHTML file per
chapter, no images (covers are skipped), no fancy styling. The output
validates as EPUB 3 in the Apple Books / calibre / Readium readers I
tested and that is enough for the "send it to my e-reader" use case.
"""

from __future__ import annotations

import datetime as _dt
import html
import re
import uuid
import zipfile
from pathlib import Path


def _xml_escape(text: str) -> str:
    return html.escape(text, quote=True)


def _xhtml_from_chapter(title: str, body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    paras = "\n".join(
        f"<p>{_xml_escape(p).replace(chr(10), '<br/>')}</p>"
        for p in paragraphs
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru">\n'
        '<head>\n'
        f'  <title>{_xml_escape(title)}</title>\n'
        '  <meta charset="utf-8"/>\n'
        '  <style>body{font-family:serif;line-height:1.55;margin:1.2em}'
        'h1{font-size:1.3em}p{margin:0 0 0.9em 0;text-indent:1.2em}</style>\n'
        '</head>\n'
        '<body>\n'
        f'  <h1>{_xml_escape(title)}</h1>\n'
        f'{paras}\n'
        '</body>\n</html>\n'
    )


def _nav_xhtml(chapters: list[tuple[str, str]]) -> str:
    lis = "\n".join(
        f'    <li><a href="{href}">{_xml_escape(title)}</a></li>'
        for href, title in chapters
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru">\n'
        '<head><title>Оглавление</title><meta charset="utf-8"/></head>\n'
        '<body>\n'
        '  <nav epub:type="toc" id="toc">\n'
        '    <h1>Оглавление</h1>\n'
        '    <ol>\n'
        f'{lis}\n'
        '    </ol>\n'
        '  </nav>\n'
        '</body>\n</html>\n'
    )


def _now_utc_iso() -> str:
    """Current UTC time as an EPUB-compliant ``dcterms:modified`` string."""
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)
    # Drop tzinfo and append explicit 'Z' — matches the reader-accepted form.
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def _content_opf(book_title: str, author: str, book_id: str,
                 chapters: list[tuple[str, str]]) -> str:
    manifest_items = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>',
    ]
    spine_items = []
    for idx, (href, _title) in enumerate(chapters, start=1):
        ident = f"ch{idx:04d}"
        manifest_items.append(
            f'    <item id="{ident}" href="{href}" '
            'media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'    <itemref idref="{ident}"/>')

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="ru">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="bookid">urn:uuid:{book_id}</dc:identifier>\n'
        f'    <dc:title>{_xml_escape(book_title)}</dc:title>\n'
        f'    <dc:creator>{_xml_escape(author or "Unknown")}</dc:creator>\n'
        '    <dc:language>ru</dc:language>\n'
        f'    <meta property="dcterms:modified">{_now_utc_iso()}</meta>\n'
        '  </metadata>\n'
        '  <manifest>\n'
        + "\n".join(manifest_items) + "\n"
        '  </manifest>\n'
        '  <spine>\n'
        '    <itemref idref="nav"/>\n'
        + "\n".join(spine_items) + "\n"
        '  </spine>\n'
        '</package>\n'
    )


_CONTAINER_XML = (
    '<?xml version="1.0"?>\n'
    '<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n'
    '    <rootfile full-path="OEBPS/content.opf" '
    'media-type="application/oebps-package+xml"/>\n'
    '  </rootfiles>\n'
    '</container>\n'
)


_CHAPTER_FILE_RE = re.compile(r"^chapter_(\d+)(?:_.*)?\.txt$", re.IGNORECASE)


def _read_chapter_file(path: Path) -> tuple[str, str]:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).lstrip("\n")
    else:
        title = path.stem
        body = raw
    return title, body


def build_epub_from_folder(
    src_dir: Path, out_path: Path, *,
    book_title: str,
    author: str = "",
    wanted_numbers: set[int] | None = None,
) -> Path:
    """Bundle every ``chapter_*.txt`` in ``src_dir`` into a single EPUB 3.

    Chapters are ordered by the 4-digit index in the filename.  If
    ``wanted_numbers`` is given, only chapters whose number is in the
    set are bundled.
    """
    all_files = [p for p in src_dir.glob("chapter_*.txt")
                 if _CHAPTER_FILE_RE.match(p.name)]
    if not all_files:
        raise RuntimeError(
            f"В папке {src_dir} нет chapter_*.txt — нечего собирать в EPUB."
        )
    if wanted_numbers is not None:
        files = [
            p for p in all_files
            if int(_CHAPTER_FILE_RE.match(p.name).group(1)) in wanted_numbers
        ]
        if not files:
            available = sorted({
                int(_CHAPTER_FILE_RE.match(p.name).group(1))
                for p in all_files
            })
            av_hint = (
                f"{available[0]}..{available[-1]} ({len(available)} глав)"
                if len(available) > 6 else ", ".join(str(n) for n in available)
            )
            raise RuntimeError(
                f"По указанному диапазону в {src_dir} нет глав. "
                f"Доступны: {av_hint}."
            )
    else:
        files = all_files
    files.sort(key=lambda p: int(_CHAPTER_FILE_RE.match(p.name).group(1)))

    chapters: list[tuple[str, str, str]] = []  # (href, title, xhtml)
    for idx, src in enumerate(files, start=1):
        title, body = _read_chapter_file(src)
        href = f"ch{idx:04d}.xhtml"
        chapters.append((href, title, _xhtml_from_chapter(title, body)))

    book_id = str(uuid.uuid4())
    toc_entries = [(href, title) for (href, title, _) in chapters]
    content_opf = _content_opf(book_title, author, book_id, toc_entries)
    nav_xhtml = _nav_xhtml(toc_entries)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as zf:
        # mimetype MUST be the first entry and stored uncompressed.
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", _CONTAINER_XML,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", content_opf,
                    compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav_xhtml,
                    compress_type=zipfile.ZIP_DEFLATED)
        for href, _title, xhtml in chapters:
            zf.writestr(f"OEBPS/{href}", xhtml,
                        compress_type=zipfile.ZIP_DEFLATED)
    return out_path
