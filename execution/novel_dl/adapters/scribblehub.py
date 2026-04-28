"""Adapter for scribblehub.com (community web fiction).

Scribblehub paginates the chapter list (50 per page) and exposes a
"Get Chapter List" AJAX endpoint, but the simplest correct path is to
walk the public ``/series/<id>/<slug>/?toc=1&pg=N`` pages. Each chapter
is rendered as ``<div id="chp_raw">…</div>`` on its own URL.
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from ..core import Book, Chapter, SiteAdapter
from ..registry import register_adapter
from ..utils import FetchError, fetch_html, normalize_text, strip_tags

_DOMAINS = ("scribblehub.com",)

_SERIES_TITLE_RE = re.compile(
    r'<div[^>]*class="[^"]*fic_title[^"]*"[^>]*title="([^"]+)"',
    re.IGNORECASE,
)
_AUTHOR_RE = re.compile(
    r'<span[^>]*class="[^"]*auth_name_fic[^"]*"[^>]*>([^<]+)</span>',
    re.IGNORECASE,
)
_META_RE = re.compile(
    r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"',
    re.IGNORECASE,
)
_CHAPTER_HREF_RE = re.compile(
    r'href="(https?://www\.scribblehub\.com/read/\d+-[^"/]+/chapter/\d+/?)"\s+title="([^"]+)"',
    re.IGNORECASE,
)
_CHAPTER_BODY_RE = re.compile(
    r'<div[^>]*id="chp_raw"[^>]*>(.*?)</div>\s*<div',
    re.IGNORECASE | re.DOTALL,
)
_CHAPTER_TITLE_RE = re.compile(
    r'<div[^>]*class="[^"]*chapter-title[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TOC_LAST_PAGE_RE = re.compile(
    r'class="page-numbers"[^>]*>(\d+)</a>',
    re.IGNORECASE,
)


@register_adapter
class ScribbleHubAdapter(SiteAdapter):
    """Scraper for scribblehub.com series + chapter pages."""

    site_id = "scribblehub"

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _DOMAINS)

    def fetch_book(self, url: str, *, cancel_event=None) -> Book:
        if cancel_event is not None and cancel_event.is_set():
            raise FetchError("Отменено пользователем.")
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        # Always pull page 1 of the TOC explicitly so we get the same
        # markup whether the user pasted /series/.../ or /series/.../?…
        first_url = self._toc_url(url, page=1)
        html = fetch_html(first_url, headers={"Referer": base + "/"})

        meta = {
            name.lower(): html_lib.unescape(content)
            for name, content in _META_RE.findall(html)
        }
        title_match = _SERIES_TITLE_RE.search(html)
        title = (
            html_lib.unescape(title_match.group(1)).strip()
            if title_match else (meta.get("og:title") or "Unknown")
        )
        author_match = _AUTHOR_RE.search(html)
        author = (
            html_lib.unescape(author_match.group(1)).strip()
            if author_match else ""
        )
        cover = meta.get("og:image", "")
        description = meta.get("og:description", "")

        # Slug is the series-slug segment.
        path_parts = [p for p in parsed.path.split("/") if p]
        slug = (
            path_parts[2] if len(path_parts) >= 3 else "scribblehub-series"
        )

        # Walk every page of the TOC, collecting chapters in document
        # order. The site shows chapters from oldest to newest by
        # default — that's what we want.
        chapters: list[Chapter] = []
        seen_urls: set[str] = set()

        def collect_from(page_html: str) -> None:
            for m in _CHAPTER_HREF_RE.finditer(page_html):
                ch_url = m.group(1).rstrip("/")
                ch_title = html_lib.unescape(m.group(2)).strip()
                if ch_url in seen_urls:
                    continue
                seen_urls.add(ch_url)
                chapters.append(Chapter(
                    num=len(chapters) + 1,
                    title=ch_title or f"Chapter {len(chapters) + 1}",
                    url=ch_url,
                ))

        collect_from(html)
        last_pages = [int(n) for n in _TOC_LAST_PAGE_RE.findall(html)]
        last_page = max(last_pages) if last_pages else 1
        for page in range(2, last_page + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise FetchError("Отменено пользователем.")
            page_html = fetch_html(
                self._toc_url(url, page=page),
                headers={"Referer": first_url},
            )
            collect_from(page_html)

        if not chapters:
            raise FetchError(
                "scribblehub: chapter list not found. "
                "Site layout may have changed."
            )

        for i, ch in enumerate(chapters):
            ch.index = i

        return Book(
            title=title or "Unknown",
            author=author,
            slug=slug,
            source_url=url,
            cover_url=cover,
            description=description.strip(),
            chapters=chapters,
        )

    def fetch_chapter(self, chapter: Chapter) -> Chapter:
        html = fetch_html(chapter.url, headers={"Referer": chapter.url})
        title_match = _CHAPTER_TITLE_RE.search(html)
        title = (
            normalize_text(strip_tags(title_match.group(1))).strip()
            if title_match else (chapter.title or f"Chapter {chapter.num}")
        )
        body_match = _CHAPTER_BODY_RE.search(html)
        if not body_match:
            chapter.title = title
            chapter.text = ""
            return chapter
        text = normalize_text(strip_tags(body_match.group(1)))
        if title and text.lower().startswith(title.lower()):
            text = text[len(title):].lstrip("\n :.-")
        chapter.title = title
        chapter.text = text
        return chapter

    @staticmethod
    def _toc_url(series_url: str, *, page: int) -> str:
        """Build the canonical TOC URL for a given page number.

        We rebuild the URL from scratch instead of just appending so we
        don't end up with ``?…&toc=1&pg=2&pg=3`` if the user pastes a
        URL that already had query params.
        """
        parsed = urllib.parse.urlparse(series_url)
        return urllib.parse.urlunparse((
            parsed.scheme, parsed.netloc, parsed.path,
            "", f"toc=1&pg={page}", "",
        ))
