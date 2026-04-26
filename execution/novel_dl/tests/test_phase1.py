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

HERE = Path(__file__).resolve()
EXECUTION_DIR = HERE.parents[2]
if str(EXECUTION_DIR) not in sys.path:
    sys.path.insert(0, str(EXECUTION_DIR))

from novel_dl import settings as settings_mod  # noqa: E402
from novel_dl.backup import snapshot_dir  # noqa: E402
from novel_dl.runlog import RunLog, prune_old_logs  # noqa: E402
from novel_dl.settings import push_recent  # noqa: E402
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
