"""Cohere-backed chapter translator.

Calls ``POST https://api.cohere.com/v2/chat`` with a user-supplied system
prompt and returns the translated text. Built on top of ``urllib`` so it
has zero extra dependencies beyond the stdlib, matching the rest of
``novel_dl``.

The translator splits long chapters into paragraph-sized batches so we
stay within a sensible prompt size and, more importantly, so a single
dropped request only re-runs one chunk rather than the whole chapter.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DEFAULT_COHERE_MODEL = "command-a-03-2025"
COHERE_CHAT_URL = "https://api.cohere.com/v2/chat"

# Approximate character budget per API call. Command models comfortably
# take >100k tokens, but shorter chunks give better retry behaviour and
# make progress visible per-chunk rather than per-chapter.
DEFAULT_CHUNK_CHARS = 6000


class TranslationError(RuntimeError):
    """Translation failed permanently (after retries)."""

    def __init__(self, msg: str, *, translated: int = 0,
                 last_ok: int | None = None) -> None:
        super().__init__(msg)
        self.translated = translated
        self.last_ok = last_ok


class TranslationCancelled(TranslationError):  # noqa: N818 — reads better this way
    """User asked us to stop translating."""


@dataclass
class TranslatorConfig:
    api_key: str
    model: str = DEFAULT_COHERE_MODEL
    system_prompt: str = ""
    chunk_chars: int = DEFAULT_CHUNK_CHARS
    request_timeout: float = 120.0
    retries: int = 3
    retry_backoff: float = 3.0


# ---- translation primitives ----------------------------------------------

def translate_text(text: str, cfg: TranslatorConfig) -> str:
    """Translate a single block of text with one Cohere request."""
    if not text.strip():
        return ""
    body = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": cfg.system_prompt},
            {"role": "user", "content": text},
        ],
        # Leave temperature/top_p at model defaults — the prompt already
        # pins the style and we want deterministic-ish translation.
    }
    data = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(1, cfg.retries + 1):
        req = urllib.request.Request(COHERE_CHAT_URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=cfg.request_timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            return _extract_text_from_cohere(payload)
        except urllib.error.HTTPError as exc:
            # Read server body so the user sees "model X removed on ..." etc.
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            msg = f"HTTP {exc.code}: {err_body or exc.reason}"
            last_err = TranslationError(msg)
            # 4xx (except 408/429) is not worth retrying.
            if exc.code in (401, 402, 403, 404, 422):
                raise TranslationError(
                    f"Cohere отклонил запрос: {msg}"
                ) from exc
            if attempt < cfg.retries:
                time.sleep(cfg.retry_backoff * attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if attempt < cfg.retries:
                time.sleep(cfg.retry_backoff * attempt)
    raise TranslationError(
        f"Cohere request failed after {cfg.retries} attempts: {last_err}"
    )


def _extract_text_from_cohere(payload: dict) -> str:
    """Pull assistant text out of a v2 /chat response."""
    msg = payload.get("message") or {}
    parts = msg.get("content") or []
    if isinstance(parts, list):
        out: list[str] = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text") or "")
        if out:
            return "\n".join(p for p in out if p).strip()
    # Fallback for single-string responses (older/other shapes).
    if isinstance(parts, str):
        return parts.strip()
    # Last-ditch: v1-style "text" field.
    if "text" in payload and isinstance(payload["text"], str):
        return payload["text"].strip()
    raise TranslationError(
        f"Cohere response has no text content: {json.dumps(payload)[:400]}"
    )


# ---- chunking -------------------------------------------------------------

def split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split ``text`` by paragraphs into chunks no longer than ``max_chars``.

    We never break a paragraph in half — if a single paragraph is longer
    than ``max_chars`` we emit it alone as its own chunk, so the API call
    will be bigger than the budget but the text stays coherent.
    """
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in paragraphs:
        plen = len(p)
        if buf and buf_len + plen + 2 > max_chars:
            chunks.append("\n\n".join(buf))
            buf, buf_len = [], 0
        buf.append(p)
        buf_len += plen + 2
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


# ---- high-level: translate a whole folder --------------------------------

def translate_chapter_file(
    src: Path, dst: Path, cfg: TranslatorConfig,
    *, progress: Callable[[str], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> None:
    """Translate one ``chapter_NNNN.txt`` file into ``dst``.

    Preserves the ``# Title`` header (we translate the title separately so
    it ends up in Russian too) and the paragraph structure of the body.
    Refuses to write an empty translated file if the source had text —
    that way a silent model refusal surfaces as an error rather than as
    a corrupted library.
    """
    raw = src.read_text(encoding="utf-8")
    title, body = _split_title(raw)
    source_had_text = bool(body.strip())

    if cancel_event is not None and cancel_event.is_set():
        raise TranslationCancelled("Отменено до начала главы.")

    translated_title = translate_text(title, cfg) if title else ""
    chunks = split_into_chunks(body, cfg.chunk_chars)
    translated_parts: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled(
                f"Отменено на чанке {i}/{len(chunks)} файла {src.name}."
            )
        if progress:
            progress(f"    chunk {i}/{len(chunks)} ({len(chunk)} chars)")
        translated_parts.append(translate_text(chunk, cfg))

    translated_body = "\n\n".join(p for p in translated_parts if p).strip()
    if source_had_text and not translated_body:
        # Cohere returned nothing meaningful — don't ship an empty .txt.
        raise TranslationError(
            f"Cohere вернул пустой перевод для {src.name}. Возможно, "
            "модель отказалась переводить контент или промпт спорный."
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    header = f"# {translated_title or title or 'Без названия'}"
    dst.write_text(f"{header}\n\n{translated_body}\n", encoding="utf-8")


def translate_folder(
    src_dir: Path, dst_dir: Path, cfg: TranslatorConfig,
    *, force: bool = False,
    progress: Callable[[str], None] | None = None,
    cancel_event: "threading.Event | None" = None,
) -> list[Path]:
    """Translate every ``chapter_*.txt`` in ``src_dir`` into ``dst_dir``."""
    files = sorted(src_dir.glob("chapter_*.txt"))
    if not files:
        raise TranslationError(
            f"В папке {src_dir} нет файлов chapter_*.txt — сначала скачай главы."
        )

    dst_dir.mkdir(parents=True, exist_ok=True)
    done: list[Path] = []
    last_ok_idx: int | None = None

    for i, src in enumerate(files, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled(
                f"Остановлено пользователем на {i}/{len(files)}.",
                translated=len(done),
                last_ok=last_ok_idx,
            )
        dst = dst_dir / src.name
        if dst.exists() and not force:
            if progress:
                progress(f"[{i}/{len(files)}] skip (exists): {src.name}")
            done.append(dst)
            last_ok_idx = i
            continue
        if progress:
            progress(f"[{i}/{len(files)}] translating: {src.name}")
        try:
            translate_chapter_file(
                src, dst, cfg,
                progress=progress, cancel_event=cancel_event,
            )
        except TranslationCancelled:
            raise
        except TranslationError as exc:
            raise TranslationError(
                f"Перевод упал на {i}/{len(files)} ({src.name}): {exc}",
                translated=len(done),
                last_ok=last_ok_idx,
            ) from exc
        done.append(dst)
        last_ok_idx = i
    return done


def _split_title(raw: str) -> tuple[str, str]:
    lines = raw.splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = "\n".join(lines[1:]).lstrip("\n")
        return title, body
    return "", raw
