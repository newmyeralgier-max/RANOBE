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
_ARTICLE_OPEN_RE = re.compile(
    r'<div[^>]*id=["\']arrticle["\'][^>]*>', re.IGNORECASE,
)
_DIV_TAG_RE = re.compile(r"<(/?)div\b[^>]*>", re.IGNORECASE)
_ARTICLE_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)

# Strip out anything we definitely do NOT want in the translated body:
# inline ad scripts, style blocks, noscript fallbacks, and ad-service
# <ins>/<div class="adsb..."> placeholders ranobes sprinkles between
# paragraphs.
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_NOSCRIPT_RE = re.compile(r"<noscript\b[^>]*>.*?</noscript>",
                          re.DOTALL | re.IGNORECASE)
_INS_RE = re.compile(r"<ins\b[^>]*>.*?</ins>", re.DOTALL | re.IGNORECASE)
# Paragraphs whose textual content is obvious JavaScript rather than
# story text. The patterns demand actual JS syntax after the keyword so
# we don't eat legitimate English paragraphs starting with "Let's go" or
# "Window. The light poured in.".
_JS_LINE_RE = re.compile(
    r"^\s*("
    r"var\s+[\w$]+\s*="      # var x =
    r"|let\s+[\w$]+\s*="      # let x =
    r"|const\s+[\w$]+\s*="    # const x =
    r"|function\s+[\w$]+\s*\("   # function foo(
    r"|window\.[\w$]+\s*[=.(\[]"  # window.foo = / .bar / (
    r"|document\.[\w$]+\s*[=.(\[]"
    r"|adx_id[\w$]*\b"        # ad-injector globals
    r"|pubadxtag\b"
    r"|yaContextCb\b"
    r")",
    re.IGNORECASE,
)
_CHAPTER_NUM_HINT_RE = re.compile(r"chapter\s+(\d+)", re.IGNORECASE)

# Heuristics for "this page is a Cloudflare challenge, not a chapter".
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cloudflare",
    "attention required",
    "turnstile",
    "challenge-platform",
    "checking your browser",
)


def _looks_like_challenge(html: str) -> bool:
    low = html.lower()
    return any(m in low for m in _CHALLENGE_MARKERS)


@register_adapter
class RanobesAdapter(SiteAdapter):
    """Scraper for the ranobes.net family of sites."""

    site_id = "ranobes"

    @classmethod
    def matches(cls, url: str) -> bool:
        host = urllib.parse.urlparse(url).netloc.lower()
        return any(host == d or host.endswith("." + d) for d in _DOMAINS)

    # ---- book ------------------------------------------------------------
    def fetch_book(self, url: str, *, cancel_event=None) -> Book:
        parsed = urllib.parse.urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        book_id, book_page_url = self._resolve_book_id(url, base)
        if cancel_event is not None and cancel_event.is_set():
            raise FetchError("Отменено пользователем.")
        html = fetch_html(book_page_url)
        meta = _extract_meta(html)

        title = meta.get("og:title") or _extract_book_title(html) or f"Novel {book_id}"
        author = meta.get("og:novel:author", "") or meta.get("author", "")
        cover = meta.get("og:image", "")
        description = meta.get("og:description", "") or meta.get("description", "")
        slug = _slug_from_book_url(book_page_url) or f"novel-{book_id}"

        chapters = self._list_chapters(base, book_id, cancel_event=cancel_event)
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

    def _list_chapters(
        self, base: str, book_id: str, *, cancel_event=None,
    ) -> list[Chapter]:
        chapters: list[Chapter] = []
        seen_ids: set[str] = set()
        page = 1
        pages_total: int | None = None
        referer = f"{base}/chapters/{book_id}/"

        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise FetchError(
                    f"Отменено пользователем на странице {page} "
                    f"списка глав. Скачано частично: {len(chapters)} шт."
                )
            list_url = f"{base}/chapters/{book_id}/page/{page}/"
            # Pass Referer so ranobes' anti-bot / CDN is less likely to serve
            # us a challenge page that lacks window.__DATA__.
            html = fetch_html(list_url, headers={"Referer": referer})
            data = _extract_data_json(html)
            if data is None:
                if page == 1:
                    raise FetchError(
                        "ranobes вернул страницу без списка глав "
                        f"(window.__DATA__ отсутствует в {list_url}). "
                        "Скорее всего сработала защита Cloudflare / региональный "
                        "фильтр. Попробуй: 1) открыть ссылку в браузере один раз "
                        "(чтобы получить cookie), 2) включить VPN, 3) повторить."
                    )
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
        if not result:
            raise FetchError(
                "ranobes не вернул ни одной главы. Возможные причины: книга "
                "снята с сайта, временная ошибка на стороне ranobes, либо "
                "антибот-защита. Открой ссылку в браузере и попробуй ещё раз."
            )
        return result

    # ---- chapter ---------------------------------------------------------
    def fetch_chapter(self, chapter: Chapter) -> Chapter:
        """Fetch a single chapter, retrying with long backoff on Cloudflare.

        Ranobes intermittently serves an anti-bot / challenge page after
        ~50 rapid requests from the same IP. The page returns HTTP 200
        but has no ``<div id="arrticle">``. We used to take that as
        "empty chapter" and abort the whole run; now we sleep 30/60/120 s
        between attempts and try up to 3 times. The downloader treats a
        subsequent empty body as fatal, so if we still can't get through
        after the retries, the user gets a clear "Cloudflare — wait and
        retry" error rather than a silent stop mid-book.
        """
        sleeps = (30, 60, 120)
        last_html = ""
        for attempt, wait in enumerate((0, *sleeps), start=1):
            if wait:
                time.sleep(wait)
            last_html = fetch_html(chapter.url)
            block = _extract_article_block(last_html)
            if block is None:
                continue

            title = chapter.title or _extract_chapter_title(last_html) or "Untitled"
            block = _strip_ads(block)
            paragraphs = _ARTICLE_PARA_RE.findall(block)
            if paragraphs:
                parts = [normalize_text(strip_tags(p)) for p in paragraphs]
            else:
                parts = [normalize_text(strip_tags(block))]

            # Drop paragraphs that are plainly leftover JS / ad snippets.
            parts = [p for p in parts if p and not _JS_LINE_RE.match(p)]
            body = normalize_text("\n\n".join(p for p in parts if p))
            if not body.strip():
                # Parsed the article block but ended up with nothing —
                # treat like a challenge page and retry.
                continue

            chapter.title = title.strip()
            chapter.text = body
            return chapter

        # All attempts returned a challenge / empty article. Surface it
        # as a FetchError so the downloader can show the user an
        # actionable message instead of silently writing an empty file.
        if _looks_like_challenge(last_html):
            raise FetchError(
                "Ranobes включил антибот-защиту (Cloudflare). "
                f"За 3 попытки с паузами не удалось получить текст главы "
                f"({chapter.url}). Открой ссылку в браузере один раз, "
                "подожди 10-15 минут, и запусти скачку снова — уже "
                "скачанные главы пропустятся."
            )
        chapter.text = ""
        return chapter


# ---- helpers -------------------------------------------------------------

def _strip_ads(block: str) -> str:
    """Remove script / style / noscript / ins blocks from an article chunk."""
    block = _SCRIPT_RE.sub("", block)
    block = _STYLE_RE.sub("", block)
    block = _NOSCRIPT_RE.sub("", block)
    block = _INS_RE.sub("", block)
    return block


def _extract_article_block(html: str) -> str | None:
    """Return the inner HTML of ``<div id="arrticle">`` with balanced ``<div>``.

    Ranobes wraps the chapter in ``#arrticle`` but sprinkles nested ``<div>``
    blocks (ads, share buttons) inside it, so a non-greedy regex stops at the
    first ``</div>`` and only captures the opening paragraphs. We instead walk
    every ``<div>`` / ``</div>`` token after the opening tag, counting depth,
    and cut the block off at the matching close tag.
    """
    open_match = _ARTICLE_OPEN_RE.search(html)
    if open_match is None:
        return None
    start = open_match.end()
    depth = 1
    pos = start
    for tag_match in _DIV_TAG_RE.finditer(html, pos):
        is_close = tag_match.group(1) == "/"
        if is_close:
            depth -= 1
            if depth == 0:
                return html[start:tag_match.start()]
        else:
            depth += 1
    # Unbalanced markup — return whatever we have so the caller still gets text.
    return html[start:]


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
