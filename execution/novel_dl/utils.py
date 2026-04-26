"""HTTP + text cleanup helpers shared by all adapters."""

from __future__ import annotations

import html as html_lib
import re
import threading
import time
import urllib.error
import urllib.request


def wait_if_paused(
    pause_event: "threading.Event | None",
    cancel_event: "threading.Event | None" = None,
    *,
    poll_interval: float = 0.2,
) -> None:
    """Block while ``pause_event`` is *clear*; respect cancellation.

    Convention: ``pause_event.is_set() == True`` means the worker may
    proceed. A clear event means "paused — wait here". This matches the
    threading.Event docs more naturally than the inverse.

    If ``cancel_event`` is set at any point we return immediately —
    callers are expected to check the cancel event right after.
    """
    if pause_event is None or pause_event.is_set():
        return
    while not pause_event.is_set():
        if cancel_event is not None and cancel_event.is_set():
            return
        # Short polling so a Resume click feels instant; the cost of a
        # 200ms idle wait is negligible compared to a network request.
        time.sleep(poll_interval)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


class FetchError(RuntimeError):
    """Network/HTTP error after all retries were exhausted."""


def fetch_html(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 25.0,
    retries: int = 3,
    backoff: float = 2.0,
) -> str:
    """GET ``url`` and return decoded HTML. Retries on transient errors."""
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update(headers)

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            return raw.decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise FetchError(f"GET {url} failed after {retries} attempts: {last_err}")


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def strip_tags(html: str) -> str:
    """Remove HTML tags and decode entities. Preserves paragraph breaks."""
    # <br> and <p> boundaries become newlines so text is readable.
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = html_lib.unescape(text)
    return text


def normalize_text(text: str) -> str:
    """Collapse runs of spaces/blank lines while keeping paragraph structure."""
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    joined = "\n".join(lines)
    joined = _MULTI_BLANK_RE.sub("\n\n", joined)
    return joined.strip()


_SAFE_FS_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


def safe_filename(name: str, *, max_len: int = 120) -> str:
    """Turn an arbitrary string into a filesystem-safe file or directory name."""
    cleaned = _SAFE_FS_RE.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip(" .")
    return cleaned


def parse_range_spec(spec: str, total: int) -> list[int]:
    """Parse a selection spec like ``"1-50,60,100-"`` into 1-based indices.

    ``total`` is the number of available chapters; it is used to resolve
    open-ended ranges (``"100-"``) and validate bounds. Returns a sorted,
    deduplicated list of 1-based indices.
    """
    if total <= 0:
        return []
    spec = spec.strip().lower()
    if not spec or spec == "all":
        return list(range(1, total + 1))

    out: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                lo_s, hi_s = part.split("-", 1)
                lo = int(lo_s) if lo_s.strip() else 1
                hi = int(hi_s) if hi_s.strip() else total
                if lo > hi:
                    lo, hi = hi, lo
                lo = max(1, lo)
                hi = min(total, hi)
                out.update(range(lo, hi + 1))
            else:
                idx = int(part)
                if 1 <= idx <= total:
                    out.add(idx)
        except ValueError:
            # Unparseable part (e.g. "abc" or "3-xyz"): skip it instead
            # of crashing the caller. Callers already treat an empty
            # result as "nothing selected" and surface a warning.
            continue
    return sorted(out)
