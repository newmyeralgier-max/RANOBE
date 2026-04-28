"""Adapter for royalroad.com (English original web fiction).

Royal Road serves the chapter list inline on the fiction page as a
JSON-flavoured table; chapters render as plain HTML with a
``<div class="chapter-content">`` body. We don't try to parse the
fancy table — a simple regex over ``href="/fiction/<id>/<slug>/chapter/<chid>/<chslug>"``
catches every chapter link in document order, which is also reading
order on RR.
"""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from ..core import Book, Chapter, SiteAdapter
from ..registry import register_adapter
from ..utils import FetchError, fetch_html, normalize_text, strip_tags

_DOMAINS = ("royalroad.com",)

_CHAPTER_HREF_RE = re.compile(
    r'href="(/fiction/(\d+)/[^"/]+/chapter/(\d+)/[^"]+)"',
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(
    r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"',
    re.IGNORECASE,
)
_FICTION_TITLE_RE = re.compile(
    r'<h1[^>]*class="[^"]*\bfont-white\b[^"]*"[^>]*>(.*?)</h1>',
    re.IGNORECASE | re.DOTALL,
)
_AUTHOR_RE = re.compile(
    r'<a[^>]*href="/profile/\d+"[^>]*>([^<]+)</a>',
    re.IGNORECASE,
)
_CHAPTER_BODY_RE = re.compile(
    r'<div[^>]*class="[^"]*chapter-content[^"]*"[^>]*>(.*?)</div>\s*<div',
    re.IGNORECASE | re.DOTALL,
)
_CHAPTER_TITLE_HEAD_RE = re.compile(
    r'<h1[^>]*>([^<]+)</h1>',
    re.IGNORECASE,
)


@register_adapter
class RoyalRoadAdapter(SiteAdapter):
    """Scraper for royalroad.com fiction + chapter pages."""

    site_id = "royalroad"

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _DOMAINS)

    def fetch_book(self, url: str, *, cancel_event=None) -> Book:
        if cancel_event is not None and cancel_event.is_set():
            raise FetchError("Отменено пользователем.")
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        html = fetch_html(url, headers={"Referer": base + "/"})

        meta = {
            name.lower(): html_lib.unescape(content)
            for name, content in _META_RE.findall(html)
        }
        # Prefer the on-page H1 (clean, no "| Royal Road" suffix). Fall
        # back to og:title which carries the same string but with the
        # site brand appended.
        h1 = _FICTION_TITLE_RE.search(html)
        if h1:
            title = normalize_text(strip_tags(h1.group(1))).strip()
        else:
            title = (
                meta.get("og:title")
                or meta.get("twitter:title")
                or "Unknown"
            )
            title = re.sub(r"\s*\|\s*Royal Road.*$", "", title).strip()
        author_match = _AUTHOR_RE.search(html)
        author = (
            html_lib.unescape(author_match.group(1)).strip()
            if author_match else ""
        )
        cover = meta.get("og:image", "")
        description = meta.get("og:description", "")

        # Slug is the fiction-id segment of the URL: /fiction/<id>/<slug>
        path_parts = [p for p in parsed.path.split("/") if p]
        slug = path_parts[2] if len(path_parts) >= 3 else "royalroad-novel"

        chapters: list[Chapter] = []
        seen_chids: set[int] = set()
        for m in _CHAPTER_HREF_RE.finditer(html):
            href, _fid, chid_str = m.groups()
            chid = int(chid_str)
            if chid in seen_chids:
                continue
            seen_chids.add(chid)
            chapters.append(Chapter(
                num=len(chapters) + 1,
                title=f"Chapter {len(chapters) + 1}",
                url=urllib.parse.urljoin(base, href),
            ))

        if not chapters:
            raise FetchError(
                "royalroad: chapter list not found. "
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
        title_match = _CHAPTER_TITLE_HEAD_RE.search(html)
        title = (
            html_lib.unescape(title_match.group(1)).strip()
            if title_match else (chapter.title or f"Chapter {chapter.num}")
        )

        body_match = _CHAPTER_BODY_RE.search(html)
        if not body_match:
            chapter.title = title
            chapter.text = ""
            return chapter
        text = normalize_text(strip_tags(body_match.group(1)))
        # The chapter body sometimes starts with a "Note: …" anti-piracy
        # paragraph injected by RR for free users; we keep it because
        # stripping it requires JS-aware logic and the translator
        # handles it gracefully.
        if title and text.lower().startswith(title.lower()):
            text = text[len(title):].lstrip("\n :.-")

        chapter.title = title
        chapter.text = text
        return chapter
