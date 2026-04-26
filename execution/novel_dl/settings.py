"""Persistent user settings for the GUI.

We persist in ``$HOME/.novel_dl/config.json`` (never inside the repo) so
URL / output folder / API key / prompt / range fields survive across
launches. The file is written atomically and the API key can be
suppressed from persistence via a GUI checkbox.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

CONFIG_DIR = Path.home() / ".novel_dl"
CONFIG_PATH = CONFIG_DIR / "config.json"

# Maximum entries kept in the "recent X" history lists. Keeping it short
# (5) means the dropdowns stay scannable; the user said they want
# "the last 5 URLs/keys" specifically.
MAX_RECENT = 5

# Known keys + their default values. Anything not in here is ignored on
# load so we don't blow up on a config written by a newer version.
DEFAULTS: dict[str, object] = {
    "url": "",
    "output_dir": "",
    "range_spec": "all",
    "combined": True,
    "force": False,
    "api_key": "",
    "save_api_key": False,
    "model": "command-a-03-2025",
    "prompt": "",
    "translate_src_dir": "",
    "translate_range": "all",
    "retranslate": False,
    "epub_range": "all",
    "epub_source": "ru (перевод)",
    # History lists for the "recent N" dropdowns. Latest first.
    # ``recent_api_keys`` is only populated when ``save_api_key`` is on
    # (matching the privacy contract for the single ``api_key`` field).
    "recent_urls": [],
    "recent_api_keys": [],
}


def _normalize_recent(values: object) -> list[str]:
    """Coerce a stored 'recent X' value back into a clean string list.

    We tolerate older configs that may have stored the wrong type or
    duplicates: empty strings drop out, duplicates collapse to first
    occurrence, list is trimmed to ``MAX_RECENT``.
    """
    if not isinstance(values, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if not isinstance(v, str):
            continue
        v = v.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
        if len(out) >= MAX_RECENT:
            break
    return out


def push_recent(history: Iterable[str], value: str, *,
                max_items: int = MAX_RECENT) -> list[str]:
    """Return ``history`` with ``value`` moved to the front, deduped/trimmed.

    Pure function — does not touch disk. Caller decides when to persist.
    """
    value = (value or "").strip()
    out: list[str] = []
    if value:
        out.append(value)
    for v in history or ():
        if not isinstance(v, str):
            continue
        v = v.strip()
        if not v or v == value or v in out:
            continue
        out.append(v)
        if len(out) >= max_items:
            break
    return out


def load_settings() -> dict[str, object]:
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return dict(DEFAULTS)
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    for k, v in data.items():
        if k in DEFAULTS:
            merged[k] = v
    # Coerce list-typed history keys back into clean lists, even if the
    # file on disk was hand-edited or written by an older build.
    merged["recent_urls"] = _normalize_recent(merged.get("recent_urls"))
    merged["recent_api_keys"] = _normalize_recent(merged.get("recent_api_keys"))

    # One-time migration: if the user has an existing ``url`` / ``api_key``
    # from before recent-lists were introduced, seed those into the new
    # history so the dropdowns aren't empty on first launch after upgrade.
    legacy_url = str(merged.get("url") or "").strip()
    if legacy_url and legacy_url not in merged["recent_urls"]:
        merged["recent_urls"] = push_recent(merged["recent_urls"], legacy_url)
    legacy_key = str(merged.get("api_key") or "").strip()
    if (
        legacy_key
        and merged.get("save_api_key")
        and legacy_key not in merged["recent_api_keys"]
    ):
        merged["recent_api_keys"] = push_recent(
            merged["recent_api_keys"], legacy_key,
        )
    return merged


def save_settings(values: dict[str, object]) -> None:
    """Write settings atomically. Keys not in DEFAULTS are dropped."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: values.get(k, DEFAULTS[k]) for k in DEFAULTS}
    # Always normalize history lists before persisting so we never grow
    # a corrupted entry over time.
    payload["recent_urls"] = _normalize_recent(payload.get("recent_urls"))
    payload["recent_api_keys"] = _normalize_recent(
        payload.get("recent_api_keys"),
    )
    # If the caller asked us not to save the API key, zero out both the
    # single field and the history list. The caller is still responsible
    # for keeping the key out of RAM when the checkbox is off.
    if not payload.get("save_api_key"):
        payload["api_key"] = ""
        payload["recent_api_keys"] = []
    fd, tmp_name = tempfile.mkstemp(
        prefix=".config.", suffix=".json.tmp", dir=str(CONFIG_DIR),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp_name, CONFIG_PATH)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
