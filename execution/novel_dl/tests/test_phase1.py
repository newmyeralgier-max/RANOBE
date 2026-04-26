"""Tests for Phase 1 foundation: settings recents, runlog, backup.

Phase 2 sanity checks live here too where they're tiny enough not to
deserve their own file (e.g. wait_if_paused). Heavier Phase 2 work
gets its own file when it grows.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve()
EXECUTION_DIR = HERE.parents[2]
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from novel_dl import settings as settings_mod  # noqa: E402
from novel_dl.backup import snapshot_dir  # noqa: E402
from novel_dl.glossary import (  # noqa: E402
    format_glossary_for_prompt,
    glossary_path_for,
    load_glossary,
    save_glossary,
)
from novel_dl.runlog import RunLog, prune_old_logs  # noqa: E402
from novel_dl.settings import push_recent  # noqa: E402
from novel_dl.translator import (  # noqa: E402
    TranslatorConfig,
    _system_prompt_with_glossary,
)
from novel_dl.update_check import (  # noqa: E402
    UpdateStatus,
    check_for_updates,
)
from novel_dl.utils import wait_if_paused  # noqa: E402

# ---- settings.push_recent -----------------------------------------------

def test_push_recent_adds_to_front():
    assert push_recent([], "a") == ["a"]
    assert push_recent(["a"], "b") == ["b", "a"]
    assert push_recent(["b", "a"], "c") == ["c", "b", "a"]


def test_push_recent_dedup_moves_to_front():
    assert push_recent(["a", "b", "c"], "b") == ["b", "a", "c"]


def test_push_recent_strips_and_drops_empty():
    assert push_recent(["a"], "  ") == ["a"]
    assert push_recent(["a"], "") == ["a"]
    assert push_recent([" a "], "a") == ["a"]


def test_push_recent_caps_length():
    out = push_recent(["a", "b", "c", "d", "e"], "f", max_items=5)
    assert out == ["f", "a", "b", "c", "d"]


def test_push_recent_ignores_non_strings():
    out = push_recent(["a", 42, None, "b"], "c")  # type: ignore[list-item]
    assert out == ["c", "a", "b"]


# ---- settings load/save round-trip --------------------------------------

def test_settings_round_trip_with_recents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")

    settings_mod.save_settings({
        "url": "https://example.com/1",
        "save_api_key": True,
        "api_key": "k1",
        "recent_urls": ["https://example.com/1", "https://example.com/2"],
        "recent_api_keys": ["k1", "k2"],
    })
    loaded = settings_mod.load_settings()
    assert loaded["recent_urls"] == ["https://example.com/1",
                                     "https://example.com/2"]
    assert loaded["recent_api_keys"] == ["k1", "k2"]


def test_settings_strips_keys_when_save_api_key_off(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")

    settings_mod.save_settings({
        "save_api_key": False,
        "api_key": "secret",
        "recent_api_keys": ["secret", "other"],
    })
    loaded = settings_mod.load_settings()
    assert loaded["api_key"] == ""
    assert loaded["recent_api_keys"] == []


def test_settings_migrates_legacy_url_into_recents(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "CONFIG_PATH", tmp_path / "config.json")

    # Hand-write an "old" config with no recent_urls.
    (tmp_path / "config.json").write_text(
        '{"url": "https://example.com/legacy"}', encoding="utf-8",
    )
    loaded = settings_mod.load_settings()
    assert "https://example.com/legacy" in loaded["recent_urls"]


# ---- runlog --------------------------------------------------------------

def test_runlog_writes_lines(tmp_path):
    log_path = tmp_path / "x.log"
    rl = RunLog(path=log_path)
    rl.write("hello")
    rl.write("world\n")
    rl.close()
    text = log_path.read_text(encoding="utf-8")
    assert "hello" in text
    assert "world" in text
    # Header + footer markers.
    assert "run started" in text
    assert "run ended" in text


def test_runlog_survives_disk_errors(tmp_path):
    rl = RunLog(path=tmp_path / "ok.log")
    rl.write("first")
    # Simulate a disk failure by closing the underlying handle ourselves.
    if rl._fh is not None:  # type: ignore[attr-defined]
        rl._fh.close()  # type: ignore[attr-defined]
    # Subsequent writes must not raise.
    rl.write("second")
    rl.close()


def test_prune_old_logs_keeps_only_max(tmp_path, monkeypatch):
    import novel_dl.runlog as runlog_mod
    monkeypatch.setattr(runlog_mod, "LOG_DIR", tmp_path)

    # Make 7 log files with strictly increasing mtimes so ordering is
    # deterministic on filesystems with coarse mtime granularity.
    for i in range(7):
        p = tmp_path / f"{i:02d}.log"
        p.write_text("x", encoding="utf-8")
        os.utime(p, (1700000000 + i, 1700000000 + i))

    prune_old_logs(max_files=3)
    surviving = sorted(p.name for p in tmp_path.iterdir())
    assert surviving == ["04.log", "05.log", "06.log"]


# ---- backup --------------------------------------------------------------

def test_snapshot_creates_backup_with_chapters(tmp_path):
    (tmp_path / "chapter_0001_test.txt").write_text("body", encoding="utf-8")
    (tmp_path / "chapter_0002_test.txt").write_text("body", encoding="utf-8")
    (tmp_path / "ignore_me.json").write_text("{}", encoding="utf-8")

    snap = snapshot_dir(tmp_path, label="test")
    assert snap is not None
    files = sorted(p.name for p in snap.iterdir())
    assert files == ["chapter_0001_test.txt", "chapter_0002_test.txt"]


def test_snapshot_returns_none_for_empty_dir(tmp_path):
    assert snapshot_dir(tmp_path, label="test") is None


def test_wait_if_paused_returns_immediately_when_unpaused():
    ev = threading.Event()
    ev.set()
    t = time.monotonic()
    wait_if_paused(ev)
    assert time.monotonic() - t < 0.05


def test_wait_if_paused_blocks_until_set():
    ev = threading.Event()  # cleared = paused

    released = threading.Event()

    def releaser():
        time.sleep(0.1)
        ev.set()
        released.set()

    threading.Thread(target=releaser, daemon=True).start()
    t = time.monotonic()
    wait_if_paused(ev, poll_interval=0.02)
    elapsed = time.monotonic() - t
    assert released.is_set()
    assert 0.05 < elapsed < 1.0  # actually waited, but not forever


def test_wait_if_paused_returns_on_cancel_even_when_still_paused():
    pause = threading.Event()  # cleared = paused, never set
    cancel = threading.Event()

    def cancel_after():
        time.sleep(0.05)
        cancel.set()

    threading.Thread(target=cancel_after, daemon=True).start()
    t = time.monotonic()
    wait_if_paused(pause, cancel, poll_interval=0.02)
    elapsed = time.monotonic() - t
    assert cancel.is_set()
    assert elapsed < 1.0


def test_update_check_handles_non_git_dir(tmp_path):
    status = check_for_updates(tmp_path, do_fetch=False)
    assert isinstance(status, UpdateStatus)
    assert status.error is not None
    assert not status.has_updates


def test_update_status_has_updates_property():
    assert UpdateStatus(behind=2).has_updates is True
    assert UpdateStatus(behind=0).has_updates is False
    assert UpdateStatus(behind=5, error="boom").has_updates is False


def test_glossary_round_trip(tmp_path, monkeypatch):
    import novel_dl.glossary as gloss_mod
    monkeypatch.setattr(gloss_mod, "GLOSSARY_DIR", tmp_path / "g")
    pairs = [("John", "Джон"), ("Sword Saint", "Святой Меча")]
    save_glossary("test-book", pairs)
    assert load_glossary("test-book") == pairs


def test_glossary_missing_file_returns_empty(tmp_path, monkeypatch):
    import novel_dl.glossary as gloss_mod
    monkeypatch.setattr(gloss_mod, "GLOSSARY_DIR", tmp_path / "g")
    assert load_glossary("does-not-exist") == []


def test_glossary_path_safe_for_weird_slugs(tmp_path, monkeypatch):
    import novel_dl.glossary as gloss_mod
    monkeypatch.setattr(gloss_mod, "GLOSSARY_DIR", tmp_path / "g")
    p = glossary_path_for("../../etc/passwd")
    # Sanitiser must collapse path-traversal characters.
    assert ".." not in p.name
    assert "/" not in p.name


def test_glossary_format_empty_returns_empty_string():
    assert format_glossary_for_prompt([]) == ""


def test_glossary_format_renders_pairs():
    out = format_glossary_for_prompt([("John", "Джон"), ("Saint", "Святой")])
    assert "John" in out and "Джон" in out
    assert "Saint" in out and "Святой" in out
    # Header should be present so the model treats the list as
    # instructions, not as example output.
    assert "Глоссарий" in out


def test_translator_appends_glossary_to_system_prompt():
    cfg = TranslatorConfig(
        api_key="x", model="m",
        system_prompt="Ты переводчик.",
        glossary=[("John", "Джон")],
    )
    full = _system_prompt_with_glossary(cfg)
    assert full.startswith("Ты переводчик.")
    assert "John" in full and "Джон" in full


def test_translator_no_glossary_keeps_prompt_unchanged():
    cfg = TranslatorConfig(api_key="x", model="m", system_prompt="P")
    assert _system_prompt_with_glossary(cfg) == "P"


def test_tail_paragraphs_returns_last_n():
    from novel_dl.translator import _tail_paragraphs
    text = "Para A.\n\nPara B.\n\nPara C.\n\nPara D."
    assert _tail_paragraphs(text, 2) == "Para C.\n\nPara D."
    assert _tail_paragraphs(text, 1) == "Para D."
    # n > total just returns everything.
    assert _tail_paragraphs(text, 10).startswith("Para A.")


def test_tail_paragraphs_handles_empty_and_whitespace():
    from novel_dl.translator import _tail_paragraphs
    assert _tail_paragraphs("", 2) == ""
    assert _tail_paragraphs("\n\n   \n\n", 2) == ""


def test_wrap_for_translation_includes_prior_context():
    from novel_dl.translator import _wrap_for_translation
    out = _wrap_for_translation(
        "Hello world.", prior_context="Это контекст.",
    )
    assert "Это контекст." in out
    # Source markers must still wrap the actual text to translate, not
    # the context — otherwise the model would re-translate the context.
    assert "Hello world." in out
    src_begin = out.index("<<<ENGLISH_SOURCE_BEGIN>>>")
    ctx_pos = out.index("Это контекст.")
    assert ctx_pos < src_begin


def test_wrap_for_translation_no_context_unchanged():
    from novel_dl.translator import _wrap_for_translation
    out = _wrap_for_translation("Hello.")
    assert "Hello." in out
    # No context block header should appear.
    assert "контекст" not in out.lower()


def test_translate_folder_threads_context_between_chapters(tmp_path, monkeypatch):
    """Each chapter after the first should see the prior chapter's tail."""
    import novel_dl.translator as tmod

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "chapter_0001_a.txt").write_text(
        "# A\n\nFirst body para.\n\nSecond body para.\n",
        encoding="utf-8",
    )
    (src_dir / "chapter_0002_b.txt").write_text(
        "# B\n\nThird body para.\n\nFourth body para.\n",
        encoding="utf-8",
    )

    seen_contexts: list[str] = []

    def fake_translate(text, cfg, *, progress=None, pause_event=None,
                       cancel_event=None, prior_context=""):
        seen_contexts.append(prior_context)
        # Echo the source so the dst body matches the src body — that
        # keeps "tail of dst = tail of src" predictable for the test.
        return text

    monkeypatch.setattr(tmod, "translate_text", fake_translate)

    cfg = tmod.TranslatorConfig(
        api_key="x", model="m",
        system_prompt="p",
        use_prior_context=True,
        prior_context_paragraphs=2,
    )
    tmod.translate_folder(src_dir, dst_dir, cfg)

    # Calls: ch1 title, ch1 body, ch2 title, ch2 body. Title calls go
    # with empty context (we don't pass it for titles); ch2 body call
    # should include tail of ch1 body.
    body_contexts = [c for c in seen_contexts if c]
    assert any("Second body para." in c for c in body_contexts), (
        f"expected ch1 tail in ch2 context; saw {seen_contexts!r}"
    )


def test_bilingual_epub_pairs_chapters_by_number(tmp_path):
    import zipfile

    from novel_dl.epub import build_bilingual_epub_from_folders

    en = tmp_path / "en"
    ru = tmp_path / "ru"
    en.mkdir()
    ru.mkdir()
    (en / "chapter_0001_a.txt").write_text(
        "# Title One\n\nHello world.\n\nSecond para.\n",
        encoding="utf-8",
    )
    (en / "chapter_0002_b.txt").write_text(
        "# Title Two\n\nMore english.\n",
        encoding="utf-8",
    )
    (ru / "chapter_0001_a.txt").write_text(
        "# Заголовок Один\n\nПривет мир.\n\nВторой абзац.\n",
        encoding="utf-8",
    )
    # Chapter 2 in russian deliberately missing — only ch1 has both.
    out = tmp_path / "bilingual.epub"
    build_bilingual_epub_from_folders(
        en, ru, out, book_title="Test", author="A",
    )
    assert out.exists()
    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
        # mimetype first; only ONE chapter file (ch0001) since only
        # chapter 1 had both english and russian sources.
        assert names[0] == "mimetype"
        chs = [n for n in names if n.startswith("OEBPS/ch") and n.endswith(".xhtml")]
        assert chs == ["OEBPS/ch0001.xhtml"]
        body = zf.read("OEBPS/ch0001.xhtml").decode("utf-8")
        assert "Hello world." in body
        assert "Привет мир." in body
        # English paragraph should appear before russian paragraph.
        assert body.index("Hello world.") < body.index("Привет мир.")
        assert 'class="bi-en"' in body and 'class="bi-ru"' in body


def test_bilingual_epub_raises_when_no_overlap(tmp_path):
    from novel_dl.epub import build_bilingual_epub_from_folders

    en = tmp_path / "en"
    ru = tmp_path / "ru"
    en.mkdir()
    ru.mkdir()
    (en / "chapter_0001_a.txt").write_text("# A\n\nbody\n", encoding="utf-8")
    (ru / "chapter_0007_q.txt").write_text("# Б\n\nтело\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="и английская, и русская"):
        build_bilingual_epub_from_folders(
            en, ru, tmp_path / "x.epub", book_title="x",
        )


def test_translate_folder_skips_context_when_disabled(tmp_path, monkeypatch):
    import novel_dl.translator as tmod

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    src_dir.mkdir()
    (src_dir / "chapter_0001_a.txt").write_text(
        "# A\n\nP1.\n\nP2.\n", encoding="utf-8",
    )
    (src_dir / "chapter_0002_b.txt").write_text(
        "# B\n\nP3.\n\nP4.\n", encoding="utf-8",
    )

    seen_contexts: list[str] = []

    def fake_translate(text, cfg, *, progress=None, pause_event=None,
                       cancel_event=None, prior_context=""):
        seen_contexts.append(prior_context)
        return text

    monkeypatch.setattr(tmod, "translate_text", fake_translate)

    cfg = tmod.TranslatorConfig(
        api_key="x", model="m", system_prompt="p",
        use_prior_context=False,
    )
    tmod.translate_folder(src_dir, dst_dir, cfg)
    assert all(c == "" for c in seen_contexts)


def test_snapshot_prunes_excess_backups(tmp_path):
    (tmp_path / "chapter_0001_test.txt").write_text("body", encoding="utf-8")
    # Create more than MAX_BACKUPS snapshots; pruning must keep the
    # latest 5 only.
    snaps = []
    for i in range(8):
        s = snapshot_dir(tmp_path, label=f"x{i}")
        assert s is not None
        os.utime(s, (1700000000 + i, 1700000000 + i))
        snaps.append(s)

    surviving = sorted(p.name for p in (tmp_path / "backups").iterdir())
    # 5 most-recent labels are x3..x7 (we made 8 = indices 0..7).
    assert len(surviving) == 5
    assert all(any(name.endswith(f"-x{i}") for i in range(3, 8))
               for name in surviving)
