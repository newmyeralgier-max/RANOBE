"""Adapter for ranobes.net / ranobes.com novel sites.

Both domains expose the same JSON payload via ``window.__DATA__`` on the
``/chapters/<id>/page/<n>/`` listing pages, so a single adapter works for both.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import time
import urllib.parse

from ..core import Book, Chapter, SiteAdapter
from ..registry import register_adapter
from ..utils import FetchError, fetch_html, normalize_text, strip_tags

_DOMAINS = ("ranobes.net", "ranobes.com", "ranobes.top")

_BOOK_ID_RE = re.compile(r"/novels?/(\d+)(?:-[^/]*)?\.html", re.IGNORECASE)
_CHAPTER_ID_FROM_URL_RE = re.compile(r"/([a-z0-9-]+?)-(\d+)/\d+\.html", re.IGNORECASE)
_DATA_RE = re.compile(r"window\.__DATA__\s*=\s*(\{.+?\})\s*;?\s*<", re.DOTALL)
_TITLE_RE = re.compile(
    r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', re.DOTALL | re.IGNORECASE
)
_META_RE = re.compile(
    r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"', re.IGNORECASE
)
_ARTICLE_BLOCK_RE = re.compile(
    r'<div[^>]*id=["\']arrticle["\'][^>]*>(.*?)</div>\s*</div>',
    re.DOTALL | re.IGNORECASE,
)
_ARTICLE_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_CHAPTER_NUM_HINT_RE = re.compile(r"chapter\s+(\d+)", re.IGNORECASE)


@register_adapter
class RanobesAdapter(SiteAdapter):
    """Scraper for the ranobes.net family of sites."""

    site_id = "ranobes"

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _DOMAINS)

    # ---- book ------------------------------------------------------------
    def fetch_book(self, url: str) -> Book:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        book_id, book_page_url = self._resolve_book_id(url, base)
        html = fetch_html(book_page_url)
        meta = _extract_meta(html)

        title = meta.get("og:title") or _extract_book_title(html) or f"Novel {book_id}"
        author = meta.get("og:novel:author", "") or meta.get("author", "")
        cover = meta.get("og:image", "")
        description = meta.get("og:description", "") or meta.get("description", "")
        slug = _slug_from_book_url(book_page_url) or f"novel-{book_id}"

        chapters = self._list_chapters(base, book_id)
        return Book(
            title=title.strip(),
            author=author.strip(),
            slug=slug,
            source_url=book_page_url,
            cover_url=cover,
            description=description.strip(),
            chapters=chapters,
        )

    def _resolve_book_id(self, url: str, base: str) -> tuple[str, str]:
        """Return ``(book_id, canonical_book_page_url)`` for any ranobes URL."""
        m = _BOOK_ID_RE.search(url)
        if m:
            return m.group(1), url

        # Chapter URLs look like ``/<slug>-<book_id>/<chapter_id>.html``.
        m = _CHAPTER_ID_FROM_URL_RE.search(urllib.parse.urlparse(url).path)
        if m:
            slug, book_id = m.group(1), m.group(2)
            return book_id, f"{base}/novels/{book_id}-{slug}.html"

        raise FetchError(
            f"Could not extract ranobes book id from URL: {url}. "
            "Expected /novels/<id>-<slug>.html or a chapter URL."
        )

    def _list_chapters(self, base: str, book_id: str) -> list[Chapter]:
        chapters: list[Chapter] = []
        seen_ids: set[str] = set()
        page = 1
        pages_total: int | None = None

        while True:
            list_url = f"{base}/chapters/{book_id}/page/{page}/"
            html = fetch_html(list_url)
            data = _extract_data_json(html)
            if data is None:
                break

            if pages_total is None:
                pages_total = _coerce_int(data.get("pages_count")) or 1

            batch = data.get("chapters") or []
            new_in_page = 0
            for item in batch:
                ch_id = str(item.get("id") or "")
                if ch_id and ch_id in seen_ids:
                    continue
                if ch_id:
                    seen_ids.add(ch_id)
                title = (item.get("title") or "").strip()
                link = (item.get("link") or "").strip()
                if not link:
                    continue
                if link.startswith("//"):
                    link = "https:" + link
                elif link.startswith("/"):
                    link = base + link
                num_match = _CHAPTER_NUM_HINT_RE.search(title)
                num = int(num_match.group(1)) if num_match else None
                chapters.append(Chapter(num=num, title=title, url=link))
                new_in_page += 1

            if new_in_page == 0:
                break
            if page >= (pages_total or 1):
                break
            page += 1
            time.sleep(0.4)

        # Ranobes lists newest first; sort by chapter number where known, then
        # preserve site order for any chapters without a parseable number.
        ordered = sorted(
            enumerate(chapters),
            key=lambda pair: (
                pair[1].num if pair[1].num is not None else 10**9,
                -pair[0],  # fallback: earlier items in site order come later
            ),
        )
        result: list[Chapter] = []
        for new_idx, (_, ch) in enumerate(ordered):
            ch.index = new_idx
            result.append(ch)
        return result

    # ---- chapter ---------------------------------------------------------
    def fetch_chapter(self, chapter: Chapter) -> Chapter:
        html = fetch_html(chapter.url)
        title = chapter.title or _extract_chapter_title(html) or "Untitled"

        block_match = _ARTICLE_BLOCK_RE.search(html)
        if not block_match:
            chapter.text = ""
            return chapter

        block = block_match.group(1)
        paragraphs = _ARTICLE_PARA_RE.findall(block)
        if paragraphs:
            parts = [normalize_text(strip_tags(p)) for p in paragraphs]
        else:
            parts = [normalize_text(strip_tags(block))]

        chapter.title = title.strip()
        chapter.text = normalize_text("\n\n".join(p for p in parts if p))
        return chapter


# ---- helpers -------------------------------------------------------------

def _extract_data_json(html: str) -> dict | None:
    m = _DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _extract_meta(html: str) -> dict[str, str]:
    return {
        name.lower(): html_lib.unescape(content)
        for name, content in _META_RE.findall(html)
    }


def _extract_book_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    return normalize_text(strip_tags(m.group(1)))


def _extract_chapter_title(html: str) -> str | None:
    m = _TITLE_RE.search(html)
    if not m:
        return None
    return normalize_text(strip_tags(m.group(1)))


def _slug_from_book_url(url: str) -> str:
    m = _BOOK_ID_RE.search(url)
    if not m:
        return ""
    path = urllib.parse.urlparse(url).path
    tail = path.rsplit("/", 1)[-1].removesuffix(".html")
    return tail or ""


def _coerce_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
