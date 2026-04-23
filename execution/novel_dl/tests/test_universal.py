"""Unit tests for the universal downloader: parsing, registry, file output."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve()
EXECUTION_DIR = HERE.parents[2]
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from novel_dl.adapters.freewebnovel import FreeWebNovelAdapter  # noqa: E402
from novel_dl.adapters.ranobes import RanobesAdapter  # noqa: E402
from novel_dl.core import Book, Chapter, SiteAdapter, UnsupportedSiteError  # noqa: E402
from novel_dl.downloader import (  # noqa: E402
    DownloadCancelled,
    DownloadError,
    download_chapters,
)
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


# ---- downloader: fail-fast on empty body & cancellation -------------------

class _StubAdapter(SiteAdapter):
    """Minimal adapter for unit-testing download_chapters behaviour."""

    site_id = "stub"

    def __init__(self, bodies: dict[str, str]) -> None:
        # Map of chapter URL -> body text the "adapter" will return.
        self._bodies = bodies

    @classmethod
    def matches(cls, url: str) -> bool:  # pragma: no cover - unused
        return False

    def fetch_book(self, url: str) -> Book:  # pragma: no cover - unused
        raise NotImplementedError

    def fetch_chapter(self, chapter: Chapter) -> Chapter:
        chapter.text = self._bodies.get(chapter.url, "")
        return chapter


def _stub_book() -> Book:
    chapters = [
        Chapter(num=i, title=f"Chapter {i}", url=f"https://x/{i}", index=i - 1)
        for i in range(1, 6)
    ]
    return Book(
        title="Stub Book", author="", slug="stub", source_url="https://x",
        cover_url="", description="", chapters=chapters,
    )


def test_download_stops_on_empty_body_and_reports_progress(tmp_path):
    """The old code silently wrote '# Chapter X\\n\\n\\n' for empty bodies.
    We now fail fast with stats so the user sees how far we got."""
    book = _stub_book()
    # Chapters 1..3 have text, chapter 4 is empty, chapter 5 never runs.
    adapter = _StubAdapter({
        "https://x/1": "body one",
        "https://x/2": "body two",
        "https://x/3": "body three",
        "https://x/4": "",
    })

    out_dir = tmp_path / "out"
    try:
        download_chapters(adapter, book, [1, 2, 3, 4, 5], out_dir, delay=0)
    except DownloadError as exc:
        assert not isinstance(exc, DownloadCancelled)
        assert exc.downloaded == 3
        assert exc.last_ok == 3
        assert "Пустое тело" in str(exc)
        # Only 1..3 made it to disk.
        files = sorted(p.name for p in out_dir.glob("chapter_*.txt"))
        assert len(files) == 3
        for f in files:
            body = (out_dir / f).read_text(encoding="utf-8")
            # No empty bodies on disk.
            assert "body" in body
        return
    raise AssertionError("expected DownloadError on empty chapter body")


def test_download_respects_cancel_event(tmp_path):
    """User-triggered Stop must interrupt the loop and surface stats."""
    book = _stub_book()
    adapter = _StubAdapter({f"https://x/{i}": f"body {i}" for i in range(1, 6)})

    cancel = threading.Event()

    captured: list[str] = []

    def progress(msg: str) -> None:
        captured.append(msg)
        # Cancel after 2 successful fetches.
        if msg.startswith("[2/5] fetching"):
            cancel.set()

    try:
        download_chapters(
            adapter, book, [1, 2, 3, 4, 5], tmp_path / "out",
            delay=0, progress=progress, cancel_event=cancel,
        )
    except DownloadCancelled as exc:
        # After progress("[2/5] fetching"), chapter 2 finishes, then the
        # loop checks cancel at the top of iteration 3 -> stop.
        assert exc.downloaded == 2
        assert exc.last_ok == 2
        return
    raise AssertionError("expected DownloadCancelled")


# ---- ranobes adapter: strip ad JS from chapter body -----------------------

def test_ranobes_js_regex_does_not_eat_english_prose():
    """The JS-paragraph heuristic must not munch lines like 'Let's go.' or
    'Window. The sun poured in.' that happen to start with a JS keyword."""
    from novel_dl.adapters.ranobes import _JS_LINE_RE

    false_positives = [
        "Let's go.",
        "Let it be.",
        "Var was his name, not a variable.",
        "Window. The sun poured in through the blinds.",
        "Document your work, then move on.",
        "Function over form, always.",
        "Const folks called him Old.",
    ]
    for s in false_positives:
        assert not _JS_LINE_RE.match(s), f"regex wrongly stripped: {s!r}"

    # True positives — these MUST match.
    true_positives = [
        "var adx_id_10448 = document.getElementById('bg-ssp-10448');",
        "let x = 1;",
        "const y = 2;",
        "function foo(a, b) {",
        "window.pubadxtag.push({zoneid: 10448});",
        "document.getElementById('bg-ssp');",
        "adx_id_10448.something",
        "pubadxtag.push(...)",
        "yaContextCb.push(...);",
    ]
    for s in true_positives:
        assert _JS_LINE_RE.match(s), f"regex missed: {s!r}"


def test_ranobes_adapter_strips_ad_scripts(monkeypatch):
    """Ranobes injects ``<script>`` ad blocks and, occasionally, paragraphs
    whose content is pure JavaScript. None of that should survive into the
    chapter .txt (otherwise the translator happily translates it)."""
    from novel_dl.adapters import ranobes as mod

    polluted_html = """
    <html><body>
    <h1 class="title">Chapter 7: Tainted</h1>
    <div id="arrticle">
      <p>Real paragraph one.</p>
      <script>var leaky = 1;</script>
      <p>var adx_id_10448 = document.getElementById('bg-ssp-10448');</p>
      <p>window.pubadxtag.push({zoneid: 10448});</p>
      <ins class="adsbygoogle"></ins>
      <p>Real paragraph two.</p>
    </div>
    </body></html>
    """

    monkeypatch.setattr(mod, "fetch_html", lambda url, **kw: polluted_html)
    adapter = mod.RanobesAdapter()
    ch = Chapter(num=7, title="Chapter 7: Tainted", url="https://x/7", index=0)
    filled = adapter.fetch_chapter(ch)
    text = filled.text or ""
    assert "Real paragraph one." in text
    assert "Real paragraph two." in text
    assert "adx_id" not in text
    assert "pubadxtag" not in text
    assert "var " not in text
    assert "<script" not in text


# ---- translator chunking & response parsing -------------------------------

def test_translator_split_respects_paragraph_boundaries():
    from novel_dl.translator import split_into_chunks

    text = "\n\n".join(["para one " * 50, "para two " * 50, "para three " * 50])
    chunks = split_into_chunks(text, max_chars=600)
    # Each chunk must end on a paragraph boundary — i.e. splitting on \n\n
    # inside a chunk should give exactly 1 or more complete paragraphs.
    for c in chunks:
        for p in c.split("\n\n"):
            assert p.strip()
    # All input paragraphs are accounted for in order.
    joined = "\n\n".join(chunks)
    assert joined.count("para one") == 50
    assert joined.count("para three") == 50


def test_translator_refuses_to_write_empty_translation(tmp_path, monkeypatch):
    """Cohere occasionally returns an empty string for a filter trip. We
    must raise rather than produce a .txt with just the header and blank
    body — otherwise the user's library gets silently corrupted."""
    from novel_dl import translator as mod

    (tmp_path / "src").mkdir()
    src = tmp_path / "src" / "chapter_0001_x.txt"
    src.write_text("# Title\n\nReal English paragraph here.\n", encoding="utf-8")
    dst = tmp_path / "dst" / "chapter_0001_x.txt"

    # Stub out the network. Title translates, body translates to "" (silent
    # refusal) — this is the scenario we need to catch.
    def fake_translate(text, cfg):
        return "Заголовок" if text.strip() == "Title" else ""

    monkeypatch.setattr(mod, "translate_text", fake_translate)

    cfg = mod.TranslatorConfig(api_key="x", model="m", system_prompt="p")
    try:
        mod.translate_chapter_file(src, dst, cfg)
    except mod.TranslationError as exc:
        assert "пуст" in str(exc).lower()
        assert not dst.exists(), "empty translation must NOT be written"
        return
    raise AssertionError("expected TranslationError on empty translation body")


def test_translator_extract_v2_response_shape():
    from novel_dl.translator import _extract_text_from_cohere

    payload = {
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Привет!"}],
        }
    }
    assert _extract_text_from_cohere(payload) == "Привет!"


# ---- epub writer ----------------------------------------------------------

def test_epub_builder_produces_valid_archive(tmp_path):
    import zipfile

    from novel_dl.epub import build_epub_from_folder

    src = tmp_path / "chs"
    src.mkdir()
    (src / "chapter_0001_Prologue.txt").write_text(
        "# Prologue\n\nFirst paragraph.\n\nSecond paragraph.\n",
        encoding="utf-8",
    )
    (src / "chapter_0002_A Start.txt").write_text(
        "# A Start\n\nOnce upon a time.\n",
        encoding="utf-8",
    )

    out = tmp_path / "book.epub"
    build_epub_from_folder(src, out, book_title="My Book", author="Тест")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        # mimetype must be first and uncompressed per EPUB spec.
        assert names[0] == "mimetype"
        info = zf.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert zf.read("mimetype") == b"application/epub+zip"
        # Required files for a minimal EPUB 3.
        assert "META-INF/container.xml" in names
        assert "OEBPS/content.opf" in names
        assert "OEBPS/nav.xhtml" in names
        assert "OEBPS/ch0001.xhtml" in names
        assert "OEBPS/ch0002.xhtml" in names
        opf = zf.read("OEBPS/content.opf").decode("utf-8")
        assert "My Book" in opf
        assert "Тест" in opf
        ch1 = zf.read("OEBPS/ch0001.xhtml").decode("utf-8")
        assert "First paragraph." in ch1
        assert "Second paragraph." in ch1


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
