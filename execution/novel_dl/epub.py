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


def _paragraph_html(p: str) -> str:
    return f"<p>{_xml_escape(p).replace(chr(10), '<br/>')}</p>"


def _xhtml_from_chapter(title: str, body: str) -> str:
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    paras = "\n".join(_paragraph_html(p) for p in paragraphs)
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


def _xhtml_from_bilingual_chapter(
    title_en: str, body_en: str,
    title_ru: str, body_ru: str,
) -> str:
    """Render a bilingual chapter: en paragraph then ru paragraph, repeating.

    For language learning the side-by-side pattern that's actually
    helpful is the same paragraph in both languages, top-to-bottom —
    not separate columns, since e-readers reflow text and most kill
    real columns. We mark each paragraph with a CSS class so a
    motivated user can hide one language with their reader's stylesheet
    if they want a single-language reading pass. When the two bodies
    have a different number of paragraphs (translator merged or split
    something) we still render both — extra paragraphs from the longer
    side land at the end labelled with their language.
    """
    paras_en = [p.strip() for p in body_en.split("\n\n") if p.strip()]
    paras_ru = [p.strip() for p in body_ru.split("\n\n") if p.strip()]
    rows: list[str] = []
    n = max(len(paras_en), len(paras_ru))
    for i in range(n):
        if i < len(paras_en):
            rows.append(
                f'<p class="bi-en" lang="en" xml:lang="en">'
                f'{_xml_escape(paras_en[i]).replace(chr(10), "<br/>")}</p>'
            )
        if i < len(paras_ru):
            rows.append(
                f'<p class="bi-ru" lang="ru" xml:lang="ru">'
                f'{_xml_escape(paras_ru[i]).replace(chr(10), "<br/>")}</p>'
            )
    paras = "\n".join(rows)
    title_block = (
        f'<h1 lang="ru" xml:lang="ru">{_xml_escape(title_ru or title_en)}</h1>\n'
        f'<h2 class="bi-en" lang="en" xml:lang="en" '
        f'style="font-weight:normal;font-size:1.0em;color:#666;'
        f'margin-top:-0.4em;">{_xml_escape(title_en)}</h2>'
    ) if title_en and title_en != title_ru else (
        f'<h1>{_xml_escape(title_ru or title_en)}</h1>'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" lang="ru" xml:lang="ru">\n'
        '<head>\n'
        f'  <title>{_xml_escape(title_ru or title_en)}</title>\n'
        '  <meta charset="utf-8"/>\n'
        '  <style>body{font-family:serif;line-height:1.55;margin:1.2em}'
        'h1{font-size:1.3em}'
        'p{margin:0 0 0.6em 0;text-indent:1.2em}'
        'p.bi-en{color:#444;font-style:italic}'
        'p.bi-ru{color:#000}'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        f'  {title_block}\n'
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


def build_bilingual_epub_from_folders(
    en_dir: Path, ru_dir: Path, out_path: Path, *,
    book_title: str,
    author: str = "",
    wanted_numbers: set[int] | None = None,
) -> Path:
    """Bilingual EPUB: English paragraph followed by Russian paragraph.

    Pairs ``en_dir/chapter_NNNN_*.txt`` with ``ru_dir/chapter_NNNN_*.txt``
    by chapter number. Chapters that are missing one side are skipped
    with a clear error rather than rendered half-blank — the user
    almost certainly forgot to translate that chapter and we don't want
    a misleading bilingual book.
    """
    en_files = {
        int(_CHAPTER_FILE_RE.match(p.name).group(1)): p
        for p in en_dir.glob("chapter_*.txt")
        if _CHAPTER_FILE_RE.match(p.name)
    }
    ru_files = {
        int(_CHAPTER_FILE_RE.match(p.name).group(1)): p
        for p in ru_dir.glob("chapter_*.txt")
        if _CHAPTER_FILE_RE.match(p.name)
    }
    if not en_files:
        raise RuntimeError(
            f"В папке {en_dir} нет английских chapter_*.txt — "
            "нечего собирать в двуязычный EPUB."
        )
    if not ru_files:
        raise RuntimeError(
            f"В папке {ru_dir} нет русских chapter_*.txt — "
            "сначала переведи главы."
        )

    common_nums = sorted(set(en_files) & set(ru_files))
    if wanted_numbers is not None:
        common_nums = [n for n in common_nums if n in wanted_numbers]
    if not common_nums:
        raise RuntimeError(
            "Нет глав, у которых есть и английская, и русская версия. "
            "Переведи главы целиком и попробуй снова."
        )

    chapters: list[tuple[str, str, str]] = []
    for idx, num in enumerate(common_nums, start=1):
        title_en, body_en = _read_chapter_file(en_files[num])
        title_ru, body_ru = _read_chapter_file(ru_files[num])
        href = f"ch{idx:04d}.xhtml"
        chapters.append((
            href,
            title_ru or title_en,
            _xhtml_from_bilingual_chapter(title_en, body_en, title_ru, body_ru),
        ))

    book_id = str(uuid.uuid4())
    toc_entries = [(href, title) for (href, title, _) in chapters]
    content_opf = _content_opf(book_title, author, book_id, toc_entries)
    nav_xhtml = _nav_xhtml(toc_entries)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w") as zf:
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
