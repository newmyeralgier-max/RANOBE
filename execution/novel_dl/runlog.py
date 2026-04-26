"""Per-run log file writer.

Every launch of the GUI (or CLI) creates a fresh log file under
``$HOME/.novel_dl/runs/<YYYY-MM-DD-HHMMSS>.log`` and mirrors every line
the user sees in the GUI Log panel into that file. This way, when
something breaks five days later, the user can send the file instead of
trying to scroll back through a window that was already closed.

Old log files are pruned to ``MAX_LOG_FILES`` on each launch so the
folder doesn't grow forever.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

from .settings import CONFIG_DIR

LOG_DIR = CONFIG_DIR / "runs"
MAX_LOG_FILES = 30


class RunLog:
    """Append-only line-buffered file logger.

    Designed to be cheap to construct and cheap to write to. The file
    handle is line-buffered so a crash leaves the log on disk up to the
    last newline. Failures to open/write the file are swallowed — the
    GUI must keep working even if the user's HOME is read-only.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path: Path | None = None
        self._fh = None
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            if path is None:
                stamp = _dt.datetime.now().strftime("%Y-%m-%d-%H%M%S")
                path = LOG_DIR / f"{stamp}.log"
            # buffering=1 ⇒ line-buffered.
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
            self.path = path
            header = (
                f"=== novel_dl run started "
                f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
            )
            self._fh.write(header)
        except OSError:
            self._fh = None
            self.path = None

    def write(self, line: str) -> None:
        """Append one line. ``line`` may or may not end with newline."""
        if self._fh is None:
            return
        try:
            if not line.endswith("\n"):
                line = line + "\n"
            self._fh.write(line)
        except (OSError, ValueError):
            # Disk full / removable drive yanked / permissions changed /
            # ValueError if the underlying file got closed out from under us.
            # Stop trying to write so we don't spam errors every line.
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
            self.path = None

    def close(self) -> None:
        if self._fh is None:
            return
        try:
            self._fh.write(
                f"=== run ended "
                f"{_dt.datetime.now().isoformat(timespec='seconds')} ===\n"
            )
            self._fh.close()
        except OSError:
            pass
        finally:
            self._fh = None


def prune_old_logs(max_files: int = MAX_LOG_FILES) -> None:
    """Delete oldest .log files until at most ``max_files`` remain."""
    if not LOG_DIR.is_dir():
        return
    try:
        files = sorted(
            (p for p in LOG_DIR.iterdir() if p.suffix == ".log" and p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
    except OSError:
        return
    excess = len(files) - max_files
    if excess <= 0:
        return
    for p in files[:excess]:
        try:
            p.unlink()
        except OSError:
            pass


def open_in_system_editor(path: Path) -> bool:
    """Open ``path`` in the user's default text editor.

    Returns ``True`` if we kicked off something, ``False`` if we couldn't.
    The caller is responsible for showing a friendly fallback message.
    """
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
            return True
        # macOS / Linux
        import shutil
        import subprocess
        opener = "open" if shutil.which("open") else "xdg-open"
        if shutil.which(opener):
            subprocess.Popen([opener, str(path)])
            return True
    except OSError:
        pass
    return False
