"""Lightweight git-based update check for the GUI launcher.

The repo is the source of truth — users run from a fresh ``git pull``
checkout. This module exposes one function the GUI can call from a
background thread on startup; it asks ``git`` whether the local clone
is behind its tracked upstream and, optionally, performs a fast-forward
pull.

We deliberately stay inside the stdlib (``subprocess``) — the project's
explicit constraint is "no external deps for core flows". Anything that
goes wrong (no git installed, not a clone, network down, detached HEAD,
local dirty changes) is swallowed into a structured ``UpdateStatus``
result so the GUI can render a sensible message instead of crashing.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

# 8-second cap for any individual git invocation. ``git fetch`` over a
# slow link can otherwise stall startup; the GUI just falls back to
# "no update info" in that case.
_GIT_TIMEOUT_S = 8.0


@dataclass
class UpdateStatus:
    """Result of a single update check. Fields are best-effort."""

    behind: int = 0
    ahead: int = 0
    upstream: str | None = None
    head_short: str | None = None
    upstream_short: str | None = None
    error: str | None = None

    @property
    def has_updates(self) -> bool:
        return self.error is None and self.behind > 0


def _run_git(repo_root: Path, *args: str) -> str:
    """Run ``git`` and return stdout stripped, raising on non-zero exit.

    Captures stderr too so error messages we surface to the user are
    informative (``not a git repository`` etc.) instead of an opaque
    CalledProcessError.
    """
    proc = subprocess.run(  # noqa: S603 — args are static, repo path validated
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "git failed").strip().splitlines()
        raise RuntimeError(msg[0] if msg else f"git exited {proc.returncode}")
    return (proc.stdout or "").strip()


def check_for_updates(repo_root: Path, *, do_fetch: bool = True) -> UpdateStatus:
    """Compare local HEAD against its tracked upstream branch.

    ``do_fetch=True`` (default) runs ``git fetch --quiet`` first so the
    answer reflects what's actually on the server. Tests can pass
    ``do_fetch=False`` to keep them offline + fast.
    """
    repo_root = Path(repo_root)
    if not (repo_root / ".git").exists():
        return UpdateStatus(error="не git-репозиторий — авто-обновление недоступно")

    try:
        if do_fetch:
            _run_git(repo_root, "fetch", "--quiet")
        upstream = _run_git(
            repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}",
        )
        head = _run_git(repo_root, "rev-parse", "--short", "HEAD")
        upstream_sha = _run_git(repo_root, "rev-parse", "--short", upstream)
        # left-right counts: behind\tahead
        counts = _run_git(
            repo_root, "rev-list", "--left-right", "--count", f"HEAD...{upstream}",
        )
        ahead_str, behind_str = counts.split("\t", 1)
        return UpdateStatus(
            behind=int(behind_str),
            ahead=int(ahead_str),
            upstream=upstream,
            head_short=head,
            upstream_short=upstream_sha,
        )
    except subprocess.TimeoutExpired:
        return UpdateStatus(error="git fetch не уложился в 8 секунд")
    except RuntimeError as exc:
        return UpdateStatus(error=str(exc))


def fast_forward_pull(repo_root: Path) -> tuple[bool, str]:
    """Try ``git pull --ff-only``; returns (ok, human_message).

    Refuses to do anything destructive — if the local clone has diverged
    or has local commits, the pull will fail and we surface the error
    so the user can sort it out manually instead of silently rebasing.
    """
    repo_root = Path(repo_root)
    try:
        out = _run_git(repo_root, "pull", "--ff-only")
        return True, out or "Обновлено."
    except subprocess.TimeoutExpired:
        return False, "git pull не уложился в 8 секунд"
    except RuntimeError as exc:
        return False, str(exc)
