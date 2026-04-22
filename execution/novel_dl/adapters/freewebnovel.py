"""Adapter for freewebnovel.com."""

from __future__ import annotations

import html as html_lib
import re
import urllib.parse

from ..core import Book, Chapter, SiteAdapter
from ..registry import register_adapter
from ..utils import FetchError, fetch_html, normalize_text, strip_tags

_DOMAINS = ("freewebnovel.com", "freewebnovel.net")

_CHAPTER_LINK_RE = re.compile(r'href="(/novel/([^"/]+)/chapter-(\d+))"', re.IGNORECASE)
_META_RE = re.compile(
    r'<meta\s+(?:name|property)="([^"]+)"\s+content="([^"]*)"', re.IGNORECASE
)
_ARTICLE_RE = re.compile(
    r'<div[^>]*id=["\']article["\'][^>]*>(.*?)</div>\s*<', re.DOTALL | re.IGNORECASE
)
_ARTICLE_FALLBACK_RE = re.compile(
    r'<div[^>]*class=["\'][^"\']*txt[^"\']*["\'][^>]*>(.*?)</div>',
    re.DOTALL | re.IGNORECASE,
)


@register_adapter
class FreeWebNovelAdapter(SiteAdapter):
    """Scraper for freewebnovel.com book + chapter pages."""

    site_id = "freewebnovel"

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _DOMAINS)

    def fetch_book(self, url: str) -> Book:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        html = fetch_html(url, headers={"Referer": base + "/"})

        meta = {
            name.lower(): html_lib.unescape(content)
            for name, content in _META_RE.findall(html)
        }
        title = meta.get("og:title") or meta.get("og:novel:novel_name") or "Unknown"
        author = meta.get("og:novel:author", "")
        cover = meta.get("og:image", "")
        description = meta.get("og:description", "")
        # Also unescape meta values we assigned above.
        title = html_lib.unescape(title)
        author = html_lib.unescape(author)
        description = html_lib.unescape(description)

        slug = ""
        chapters: list[Chapter] = []
        seen: set[int] = set()
        for href_match in _CHAPTER_LINK_RE.finditer(html):
            href, link_slug, num_str = href_match.groups()
            num = int(num_str)
            if num in seen:
                continue
            seen.add(num)
            if not slug:
                slug = link_slug
            chapters.append(
                Chapter(
                    num=num,
                    title=f"Chapter {num}",
                    url=urllib.parse.urljoin(base, href),
                )
            )

        if not chapters:
            raise FetchError(
                "freewebnovel: chapter list not found on book page. "
                "Site layout may have changed."
            )

        chapters.sort(key=lambda c: c.num or 0)
        for i, ch in enumerate(chapters):
            ch.index = i

        return Book(
            title=title.strip(),
            author=author.strip(),
            slug=slug or "novel",
            source_url=url,
            cover_url=cover,
            description=description.strip(),
            chapters=chapters,
        )

    def fetch_chapter(self, chapter: Chapter) -> Chapter:
        html = fetch_html(chapter.url, headers={"Referer": chapter.url})
        meta = {name.lower(): content for name, content in _META_RE.findall(html)}
        title = (
            meta.get("og:novel:chapter_name")
            or chapter.title
            or f"Chapter {chapter.num or chapter.index + 1}"
        )

        m = _ARTICLE_RE.search(html) or _ARTICLE_FALLBACK_RE.search(html)
        if not m:
            chapter.title = title.strip()
            chapter.text = ""
            return chapter

        body = m.group(1)
        text = normalize_text(strip_tags(body))
        # The chapter body often starts with a duplicate of the title line.
        if title and text.lower().startswith(title.lower()):
            text = text[len(title):].lstrip("\n :.-")

        chapter.title = title.strip()
        chapter.text = normalize_text(text)
        return chapter
