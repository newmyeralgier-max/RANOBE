"""Unit tests for the universal downloader: parsing, registry, file output."""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
EXECUTION_DIR = HERE.parents[2]
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from novel_dl.adapters.freewebnovel import FreeWebNovelAdapter  # noqa: E402
from novel_dl.adapters.ranobes import RanobesAdapter  # noqa: E402
from novel_dl.core import UnsupportedSiteError  # noqa: E402
from novel_dl.downloader import download_chapters  # noqa: E402
from novel_dl.registry import get_adapter  # noqa: E402
from novel_dl.utils import normalize_text, parse_range_spec, strip_tags  # noqa: E402

# ---- utils ---------------------------------------------------------------

def test_parse_range_basic():
    assert parse_range_spec("1-3", 10) == [1, 2, 3]
    assert parse_range_spec("all", 5) == [1, 2, 3, 4, 5]
    assert parse_range_spec("", 5) == [1, 2, 3, 4, 5]
    assert parse_range_spec("2,4,6", 10) == [2, 4, 6]
    assert parse_range_spec("1-3,8-", 10) == [1, 2, 3, 8, 9, 10]
    assert parse_range_spec("-3", 10) == [1, 2, 3]
    # Out-of-bounds gets clamped / ignored.
    assert parse_range_spec("9-20", 10) == [9, 10]
    assert parse_range_spec("100", 10) == []


def test_strip_tags_preserves_paragraphs():
    html = "<p>Hello <b>world</b>.</p><p>Second &amp; last.</p>"
    text = normalize_text(strip_tags(html))
    assert text == "Hello world.\n\nSecond & last."


# ---- registry ------------------------------------------------------------

def test_registry_resolves_supported_sites():
    a = get_adapter("https://ranobes.net/novels/1206834-horror-game-developer.html")
    assert a.site_id == "ranobes"
    b = get_adapter("https://ranobes.com/ranobe/foo.html")
    assert b.site_id == "ranobes"
    c = get_adapter("https://freewebnovel.com/some-novel.html")
    assert c.site_id == "freewebnovel"


def test_registry_rejects_unknown_site():
    try:
        get_adapter("https://example.com/unknown")
    except UnsupportedSiteError:
        return
    raise AssertionError("expected UnsupportedSiteError")


# ---- ranobes adapter (offline parsing of canned HTML) -------------------

RANOBES_LIST_HTML = """
<html><body>
<script>window.__DATA__ = {"pages_count":1,"chapters":[
  {"id":"200","title":"Chapter 2: Beta","link":"https://ranobes.net/slug-42/200.html"},
  {"id":"100","title":"Chapter 1: Alpha","link":"https://ranobes.net/slug-42/100.html"}
]};</script>
</body></html>
"""

RANOBES_CHAPTER_HTML = """
<html><head><meta property="og:title" content="Chapter 1: Alpha"/></head><body>
<h1 class="title">Chapter 1: Alpha</h1>
<div class="text" id="arrticle">
  <p>First paragraph.</p>
  <div class="ad-block"><p>AD: buy premium</p></div>
  <p>Second <i>paragraph</i>.</p>
  <div class="share"><span>share</span><div class="inner"><p>ignored-too</p></div></div>
  <p>Third paragraph after the ads.</p>
</div>
<div class="footer">footer that should not be part of the chapter</div>
</body></html>
"""

RANOBES_BOOK_HTML = """
<html><head>
<meta property="og:title" content="Horror Game Developer"/>
<meta property="og:novel:author" content="Test Author"/>
<meta property="og:image" content="https://example.com/cover.jpg"/>
</head><body><h1 class="title">Horror Game Developer</h1></body></html>
"""


def test_ranobes_adapter_end_to_end(monkeypatch, tmp_path):
    from novel_dl.adapters import ranobes as mod

    calls: list[str] = []

    def fake_fetch(url, **kwargs):
        calls.append(url)
        if "/chapters/" in url:
            return RANOBES_LIST_HTML
        if url.endswith("/100.html") or url.endswith("/200.html"):
            return RANOBES_CHAPTER_HTML
        return RANOBES_BOOK_HTML

    monkeypatch.setattr(mod, "fetch_html", fake_fetch)

    adapter = RanobesAdapter()
    book = adapter.fetch_book(
        "https://ranobes.net/novels/42-horror-game-developer.html"
    )
    assert book.title == "Horror Game Developer"
    assert book.author == "Test Author"
    assert len(book.chapters) == 2
    # Sorted by chapter number ascending.
    assert book.chapters[0].num == 1
    assert book.chapters[1].num == 2
    assert book.chapters[0].index == 0

    out_dir = tmp_path / "out"
    download_chapters(adapter, book, [1, 2], out_dir, delay=0)

    files = sorted(p.name for p in out_dir.glob("chapter_*.txt"))
    assert files == ["chapter_0001_Chapter 1_ Alpha.txt", "chapter_0002_Chapter 2_ Beta.txt"]
    body = (out_dir / files[0]).read_text(encoding="utf-8")
    assert body.startswith("# Chapter 1: Alpha")
    assert "First paragraph." in body
    assert "Second paragraph." in body
    # Regression: nested <div> blocks (ads, share widgets) inside #arrticle
    # used to truncate the match at the first </div>. We now walk balanced
    # div depth, so paragraphs AFTER the nested block must still be captured.
    assert "Third paragraph after the ads." in body
    # And the footer outside #arrticle must NOT leak in.
    assert "footer that should not be part" not in body

    meta = json.loads((out_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Horror Game Developer"
    assert meta["total_chapters"] == 2
    assert meta["downloaded_chapters"][0]["num"] == 1


def test_ranobes_adapter_raises_on_empty_chapter_list(monkeypatch):
    """If page 1 has no window.__DATA__ we must raise a clear error rather
    than silently reporting ``0 глав загружено из списка``."""
    from novel_dl.adapters import ranobes as mod
    from novel_dl.utils import FetchError

    def fake_fetch(url, **kwargs):
        if "/chapters/" in url:
            # Simulate a Cloudflare / anti-bot challenge page.
            return "<html><body>Just a moment...</body></html>"
        return RANOBES_BOOK_HTML

    monkeypatch.setattr(mod, "fetch_html", fake_fetch)
    adapter = RanobesAdapter()
    try:
        adapter.fetch_book(
            "https://ranobes.net/novels/42-horror-game-developer.html"
        )
    except FetchError as exc:
        assert "window.__DATA__" in str(exc) or "глав" in str(exc)
        return
    raise AssertionError("expected FetchError when chapter list is empty")


# ---- freewebnovel adapter -----------------------------------------------

FWN_BOOK_HTML = """
<html><head>
<meta property="og:title" content="Sample Novel"/>
<meta property="og:novel:author" content="Author X"/>
</head><body>
<a href="/novel/sample-novel/chapter-1">Chapter 1</a>
<a href="/novel/sample-novel/chapter-2">Chapter 2</a>
<a href="/novel/sample-novel/chapter-2">Chapter 2 dup</a>
</body></html>
"""

FWN_CHAPTER_HTML = """
<html><head>
<meta property="og:novel:chapter_name" content="Chapter 1: Intro"/>
</head><body>
<div id="article">
  Chapter 1: Intro<br>
  Hello world.<br>
  Second line.
</div>
<div class="footer">ignored</div>
</body></html>
"""


def test_freewebnovel_adapter(monkeypatch, tmp_path):
    from novel_dl.adapters import freewebnovel as mod

    def fake_fetch(url, **kwargs):
        return FWN_BOOK_HTML if url.endswith(".html") and "chapter-" not in url else FWN_CHAPTER_HTML

    monkeypatch.setattr(mod, "fetch_html", fake_fetch)

    adapter = FreeWebNovelAdapter()
    book = adapter.fetch_book("https://freewebnovel.com/sample-novel.html")
    assert book.title == "Sample Novel"
    assert book.slug == "sample-novel"
    assert len(book.chapters) == 2

    ch = adapter.fetch_chapter(book.chapters[0])
    assert ch.title == "Chapter 1: Intro"
    assert "Hello world." in (ch.text or "")
    # The duplicated title line at the start of the body is stripped.
    assert not (ch.text or "").lower().startswith("chapter 1: intro")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
