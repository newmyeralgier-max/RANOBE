"""Pre-operation snapshot of already-downloaded chapter files.

Before each big GUI action (Download / Translate / EPUB) we snapshot
the existing ``chapter_*.txt`` (and ``*.ru.txt``) files into
``<output_dir>/backups/<YYYY-MM-DD-HHMM>/``. We use hardlinks where the
filesystem supports them so a snapshot is instant and free in disk
space; on filesystems that can't hardlink (FAT32, network shares,
cross-volume) we fall back to ``shutil.copy2``.

Only the most recent ``MAX_BACKUPS`` snapshots per output folder are
kept.
"""

from __future__ import annotations

import datetime as _dt
import os
import shutil
from pathlib import Path

# 5 = enough to recover from "I clicked the wrong thing five times in a
# row" but not so many that disk usage grows unbounded for users with
# slow non-hardlink filesystems.
MAX_BACKUPS = 5

# File patterns that we consider worth snapshotting. Keep this list
# narrow — config / log / __pycache__ should never end up in a backup.
SNAPSHOT_PATTERNS = ("chapter_*.txt", "*.ru.txt", "*.epub")


def _matches_any(name: str, patterns: tuple[str, ...]) -> bool:
    from fnmatch import fnmatch
    return any(fnmatch(name, p) for p in patterns)


def snapshot_dir(src_dir: Path, *, label: str = "auto") -> Path | None:
    """Snapshot files under ``src_dir`` into ``src_dir/backups/<stamp>/``.

    Returns the snapshot path on success, or ``None`` if there was
    nothing to snapshot (empty source) or the snapshot couldn't be
    created (read-only HOME, etc.).
    """
    if not src_dir.is_dir():
        return None

    files = [
        p for p in src_dir.iterdir()
        if p.is_file() and _matches_any(p.name, SNAPSHOT_PATTERNS)
    ]
    if not files:
        return None

    backups_root = src_dir / "backups"
    stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
    snap_dir = backups_root / f"{stamp}-{label}"
    try:
        snap_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    for src in files:
        dst = snap_dir / src.name
        try:
            os.link(src, dst)
        except OSError:
            # Hardlink not supported / cross-device / dst exists / etc.
            try:
                shutil.copy2(src, dst)
            except OSError:
                # Skip individual file errors — one bad file shouldn't
                # nuke the whole snapshot.
                continue

    _prune_old(backups_root)
    return snap_dir


def _prune_old(backups_root: Path, max_keep: int = MAX_BACKUPS) -> None:
    if not backups_root.is_dir():
        return
    try:
        snaps = sorted(
            (p for p in backups_root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    excess = len(snaps) - max_keep
    if excess <= 0:
        return
    for p in snaps[:excess]:
        try:
            shutil.rmtree(p)
        except OSError:
            pass
