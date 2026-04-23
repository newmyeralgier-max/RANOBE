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

CONFIG_DIR = Path.home() / ".novel_dl"
CONFIG_PATH = CONFIG_DIR / "config.json"

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
    "translate_range": "all",
    "retranslate": False,
    "epub_range": "all",
    "epub_source": "ru (перевод)",
}


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
    return merged


def save_settings(values: dict[str, object]) -> None:
    """Write settings atomically. Keys not in DEFAULTS are dropped."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {k: values.get(k, DEFAULTS[k]) for k in DEFAULTS}
    # If the caller asked us not to save the API key, zero it out on
    # disk. The caller is still responsible for keeping it out of RAM
    # when the checkbox is off.
    if not payload.get("save_api_key"):
        payload["api_key"] = ""
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
