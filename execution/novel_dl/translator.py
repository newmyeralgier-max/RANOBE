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
import re
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

# Very low temperature — we're translating, not generating. Anything
# higher lets the model drift toward "write something in this style"
# which has produced completely hallucinated output in the past.
DEFAULT_TEMPERATURE = 0.1

# Firm markers around the source text. The model is instructed to only
# translate what's between them; this + a clear per-request instruction
# is what keeps it from echoing examples from the system prompt or
# free-styling an unrelated scene.
_SRC_BEGIN = "<<<ENGLISH_SOURCE_BEGIN>>>"
_SRC_END = "<<<ENGLISH_SOURCE_END>>>"


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
    temperature: float = DEFAULT_TEMPERATURE


def _wrap_for_translation(text: str) -> str:
    """Frame the source text so the model can't mistake it for a prompt.

    Cohere's command-a models will happily continue in the style of any
    in-prompt examples instead of translating the user's text, and will
    quietly improvise whole scenes if the temperature is above ~0.2.
    Wrapping the source between explicit markers and repeating the
    'translate THIS' instruction at the user-message level fixed both
    failure modes in testing.

    The "keep interjections short" rule is not decorative — command-a at
    low temperature goes into a repetition loop on long screams
    ("Ahhhhhhhh!" -> "А-а-а-а-а-а..." × 8000 chars), blowing the output
    budget and truncating the rest of the chapter.
    """
    return (
        "Переведи приведённый ниже английский отрывок на русский язык "
        "литературно и точно. Переводи ИМЕННО тот текст, что находится "
        f"между маркерами {_SRC_BEGIN} и {_SRC_END}. Не добавляй ничего "
        "от себя, не пересказывай, не сокращай, не добавляй комментариев, "
        "не повторяй примеры из системного промпта. Междометия и крики "
        "(«Ahhh!», «Waaaah!» и т.п.) переводи КОРОТКО — не длиннее 15 "
        "символов, даже если в оригинале они растянуты. Не зацикливайся "
        "на повторах одного и того же звука. Выведи только готовый "
        "русский перевод.\n\n"
        f"{_SRC_BEGIN}\n{text}\n{_SRC_END}"
    )


# Detect runs of a single non-space character longer than 40 chars,
# which is a tell-tale sign of command-a's scream-repetition loop.
_RUNAWAY_CHAR_RE = re.compile(r"(?:(\S)(?:[- ]?\1){40,})")
# Detect a single short fragment (1-4 non-space chars) repeated >=12 times
# with optional separators — catches loops like "Бах-Бах-Бах-..." as well
# as "ха ха ха ха ..." that aren't a single-char run.
_RUNAWAY_FRAGMENT_RE = re.compile(
    r"((?:\S{1,4}))(?:[\s\-—.,!?]+\1){12,}", re.IGNORECASE,
)


def _looks_runaway(text: str) -> bool:
    if _RUNAWAY_CHAR_RE.search(text):
        return True
    if _RUNAWAY_FRAGMENT_RE.search(text):
        return True
    return False


def _finish_reason_is_length(payload: dict) -> bool:
    """Cohere v2 uses finish_reason ``"MAX_TOKENS"`` / ``"LENGTH"`` when
    the output was truncated. Tolerate either spelling.
    """
    fr = payload.get("finish_reason")
    if isinstance(fr, str) and fr.upper() in {"MAX_TOKENS", "LENGTH"}:
        return True
    # Some shapes nest it under message.
    msg = payload.get("message")
    if isinstance(msg, dict):
        fr2 = msg.get("finish_reason")
        if isinstance(fr2, str) and fr2.upper() in {"MAX_TOKENS", "LENGTH"}:
            return True
    return False


def _too_long_vs_source(translated: str, source: str) -> bool:
    """Translated Russian is typically ~1.3–2.1× the English source.
    If we come back with >3.5× the source length it's almost always
    because the model looped — fail fast and retry.
    """
    if len(source) < 200:
        return False  # too short to trust the ratio heuristic
    return len(translated) > len(source) * 3.5


# ---- translation primitives ----------------------------------------------

def translate_text(text: str, cfg: TranslatorConfig) -> str:
    """Translate a single block of text with one Cohere request.

    Retries on network failure AND on pathological output (scream
    repetition loops, or output far longer than the source). The retry
    nudges temperature up slightly — at 0.1 the model sometimes locks
    into a bad loop it can't break out of.
    """
    if not text.strip():
        return ""
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    last_err: Exception | None = None
    for attempt in range(1, cfg.retries + 1):
        temp = cfg.temperature + 0.1 * (attempt - 1)
        body = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": cfg.system_prompt},
                {"role": "user", "content": _wrap_for_translation(text)},
            ],
            "temperature": temp,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(COHERE_CHAT_URL, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=cfg.request_timeout) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            translated = _extract_text_from_cohere(payload)
            # Cohere signals 'hit max output tokens' via finish_reason.
            # In that case the reply is truncated mid-chapter — retry
            # from scratch, usually the next attempt finishes cleanly.
            if _finish_reason_is_length(payload):
                last_err = TranslationError(
                    "Cohere обрезал перевод по лимиту токенов (finish_reason=LENGTH)."
                )
                if attempt < cfg.retries:
                    time.sleep(cfg.retry_backoff * attempt)
                    continue
                raise last_err
            if _looks_runaway(translated):
                last_err = TranslationError(
                    "Модель зациклилась на повторах (скрим-петля)."
                )
                if attempt < cfg.retries:
                    time.sleep(cfg.retry_backoff * attempt)
                    continue
                raise last_err
            if _too_long_vs_source(translated, text):
                last_err = TranslationError(
                    f"Перевод длиннее оригинала в "
                    f"{len(translated) / max(1, len(text)):.1f}× "
                    "раз — вероятно, модель зациклилась."
                )
                if attempt < cfg.retries:
                    time.sleep(cfg.retry_backoff * attempt)
                    continue
                raise last_err
            return translated
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
    """Pull assistant text out of a v2 /chat response.

    Cohere error payloads sometimes put the error message in ``message``
    as a plain string (``{"message": "invalid api token"}``) instead of
    the usual ``{"message": {"content": [...]}}``. In that case calling
    ``.get`` on a ``str`` would blow up with AttributeError; we handle it
    explicitly and raise a clean TranslationError instead.
    """
    raw_msg = payload.get("message")
    if isinstance(raw_msg, str):
        raise TranslationError(f"Cohere вернул ошибку: {raw_msg}")
    msg = raw_msg if isinstance(raw_msg, dict) else {}
    parts = msg.get("content") or []
    if isinstance(parts, list):
        out: list[str] = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text") or "")
        if out:
            # Join with a blank line so paragraph structure survives
            # multi-part responses (Cohere sometimes splits a long reply
            # into several text parts).
            return "\n\n".join(p for p in out if p).strip()
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
    wanted_numbers: "set[int] | None" = None,
) -> list[Path]:
    """Translate every ``chapter_*.txt`` in ``src_dir`` into ``dst_dir``.

    If ``wanted_numbers`` is given, only files whose leading 4-digit
    chapter number is in the set are translated.
    """
    all_files = sorted(src_dir.glob("chapter_*.txt"))
    if not all_files:
        raise TranslationError(
            f"В папке {src_dir} нет файлов chapter_*.txt — сначала скачай главы."
        )
    if wanted_numbers is not None:
        files = [f for f in all_files if _chapter_number(f) in wanted_numbers]
        if not files:
            raise TranslationError(
                f"По указанному диапазону в {src_dir} не нашлось глав. "
                f"Доступны номера "
                f"{_format_available_numbers(all_files)}."
            )
    else:
        files = all_files

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


_CHAPTER_NUM_RE = re.compile(r"^chapter_(\d{1,6})_")


def _chapter_number(path: Path) -> int:
    m = _CHAPTER_NUM_RE.match(path.name)
    return int(m.group(1)) if m else -1


def _format_available_numbers(files: list[Path]) -> str:
    nums = sorted({n for n in (_chapter_number(f) for f in files) if n >= 0})
    if not nums:
        return "(нет)"
    if len(nums) <= 6:
        return ", ".join(str(n) for n in nums)
    return f"{nums[0]}..{nums[-1]} ({len(nums)} файлов)"
