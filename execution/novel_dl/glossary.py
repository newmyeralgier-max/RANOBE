"""Per-book glossary of name/term translations.

A glossary is a small mapping of source-language terms to their fixed
target-language renderings — e.g. ``John -> Джон``, ``Sword Saint ->
Святой Меча``. Without it command-a (and any other LLM) drifts between
chapters: the same character ends up as Джон in chapter 1, Иван in
chapter 12, Юджин in chapter 30.

Storage is per-book — each book has its own slug-keyed JSON under
``~/.novel_dl/glossary/<slug>.json`` so different novels don't fight
over the same name. The format is intentionally simple so users can
hand-edit it in any text editor:

    {
        "version": 1,
        "entries": [
            {"src": "John", "dst": "Джон"},
            {"src": "Sword Saint", "dst": "Святой Меча"}
        ]
    }

Entries are stored as a list (not a dict) so case-sensitivity and
duplicate-source rules stay explicit + visible to the user, and so the
file order in the editor is preserved.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

GLOSSARY_DIR = Path.home() / ".novel_dl" / "glossary"

# Tight cap so a runaway prompt builder can't ship a 50KB glossary at
# Cohere on every request. Real-world books rarely need more than ~150
# terms; if a user goes beyond this we just drop the tail and log it.
MAX_GLOSSARY_ENTRIES = 200


def _safe_slug(slug: str) -> str:
    """Sanitize a book slug into a filesystem-safe filename stem.

    The on-disk filename is user-visible (we want them to be able to
    grep/edit it), so we keep alnum + dash/underscore/dot. Anything
    else collapses to ``_`` and the result is bounded to a sane length
    so a pathological URL doesn't blow ENAMETOOLONG.
    """
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", slug or "default").strip("._-")
    if not cleaned:
        cleaned = "default"
    return cleaned[:80]


def glossary_path_for(slug: str) -> Path:
    """Return the JSON file path for a given book slug."""
    return GLOSSARY_DIR / f"{_safe_slug(slug)}.json"


def load_glossary(slug: str) -> list[tuple[str, str]]:
    """Load ``[(src, dst), ...]`` pairs for the given book slug.

    Missing file or malformed JSON returns an empty list rather than
    raising — this is best-effort metadata, not part of the critical
    path. Callers shouldn't have to wrap every read in try/except.
    """
    path = glossary_path_for(slug)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    out: list[tuple[str, str]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        src = str(e.get("src") or "").strip()
        dst = str(e.get("dst") or "").strip()
        if src and dst:
            out.append((src, dst))
    return out[:MAX_GLOSSARY_ENTRIES]


def save_glossary(slug: str, pairs: list[tuple[str, str]]) -> Path:
    """Persist a glossary to disk atomically; returns the file path.

    Atomic-rename via tempfile + os.replace so a power loss mid-write
    can't leave the user with an empty/corrupt file. We mirror the
    pattern used in settings.save_settings().
    """
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    path = glossary_path_for(slug)
    payload = {
        "version": 1,
        "entries": [
            {"src": src.strip(), "dst": dst.strip()}
            for src, dst in pairs[:MAX_GLOSSARY_ENTRIES]
            if src.strip() and dst.strip()
        ],
    }
    fd, tmp = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        # mkstemp leaves the temp on disk if the rename above didn't
        # already consume it (e.g. an exception inside the with-block).
        if Path(tmp).exists():
            try:
                Path(tmp).unlink()
            except OSError:
                pass
    return path


def format_glossary_for_prompt(pairs: list[tuple[str, str]]) -> str:
    """Render glossary entries as a system-prompt fragment.

    Returns an empty string when the glossary is empty so callers can
    blindly concatenate without producing a useless ``\n\nГлоссарий:\n``
    header that would otherwise burn a few tokens for nothing.
    """
    if not pairs:
        return ""
    lines = [
        "Глоссарий — обязательные соответствия имён и терминов "
        "(используй ИМЕННО эти варианты в переводе, не придумывай "
        "альтернатив, не склоняй имена в нестандартные формы):",
    ]
    for src, dst in pairs:
        lines.append(f"  • {src} → {dst}")
    return "\n".join(lines)
