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
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .glossary import format_glossary_for_prompt
from .utils import wait_if_paused


def _system_prompt_with_glossary(cfg: "TranslatorConfig") -> str:
    """Splice the glossary fragment after the user's system prompt.

    Glossary goes AFTER the user's prompt so user customisation still
    sets the overall style/voice; the glossary then constrains specific
    terms inside that voice. Empty glossary returns the user's prompt
    unchanged so we don't add an empty paragraph that wastes tokens.
    """
    base = cfg.system_prompt or ""
    fragment = format_glossary_for_prompt(cfg.glossary)
    if not fragment:
        return base
    if not base:
        return fragment
    return f"{base}\n\n{fragment}"

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
    # Phase 3: per-book glossary of fixed translations. Pairs are
    # (source-language term, target-language rendering). Joined into
    # the system prompt at request time so the model can't drift
    # between Джон / Иван / Юджин for the same character across
    # chapters. Empty list ⇒ no glossary fragment is appended.
    glossary: "list[tuple[str, str]]" = field(default_factory=list)
    # Phase 3.2: feed the last N paragraphs of the previously-translated
    # chapter as inline context into the next chapter's request. Helps
    # the model keep pronouns, tense, narrative voice, and character
    # names consistent across chapter boundaries. ``False`` keeps the
    # old behaviour (no context) for users who want strictly chapter-
    # local translations or want to save tokens.
    use_prior_context: bool = True
    prior_context_paragraphs: int = 2


def _tail_paragraphs(text: str, n: int = 2) -> str:
    """Return the last ``n`` non-empty paragraphs of ``text``.

    Used to feed cross-chapter context (a couple of trailing paragraphs
    of the previous chapter's translation) into the next request so the
    model keeps pronouns, tense, and character voice consistent. Returns
    an empty string when ``text`` has no usable paragraphs — callers
    treat empty as "no context, behave as before".
    """
    if not text:
        return ""
    paragraphs = [
        p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()
    ]
    if not paragraphs:
        return ""
    return "\n\n".join(paragraphs[-n:])


def _wrap_for_translation(text: str, *, prior_context: str = "") -> str:
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
    context_block = ""
    if prior_context:
        context_block = (
            "Для согласованности перевода — последние абзацы предыдущей "
            "главы (это уже готовый русский, НЕ переводи их обратно, "
            "просто учти стиль/тон/имена/род/время):\n\n"
            f"{prior_context}\n\n---\n\n"
        )
    return (
        f"{context_block}"
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


def _format_cohere_4xx(code: int, raw_msg: str) -> str:
    """Turn a Cohere 4xx error into a user-actionable message.

    The flavour users hit most often is HTTP 403 from api.cohere.com's
    edge (Cloudflare), not Cohere's application layer. The edge refuses
    connections from a handful of regions (Russia, Iran, China, etc.)
    and the body is an HTML page saying "Your client does not have
    permission". No API key change can fix that — only a VPN exit in an
    allowed region.
    """
    low = raw_msg.lower()
    if code == 403 and (
        "does not have permission" in low
        or "cloudflare" in low
        or "<html" in low
    ):
        return (
            "Cohere закрыл доступ с твоего IP (HTTP 403, блок на стороне "
            "Cloudflare у api.cohere.com).\n\n"
            "Это не ключ и не промпт — Cohere гео-блокирует ряд регионов "
            "(Россия, Иран, Китай и др.).\n\n"
            "Решение: включи VPN (любой узел US/EU/Израиль), перезапусти "
            "окно, нажми «Перевести» ещё раз — уже переведённые главы "
            "пропустятся."
        )
    if code == 401:
        return (
            "Cohere не принял ключ (HTTP 401). Проверь, что скопировал "
            "ключ целиком (без пробелов) с https://dashboard.cohere.com/api-keys "
            "и что он ещё активен."
        )
    if code == 402:
        return (
            "Cohere отклонил запрос: закончился кредит/лимит (HTTP 402). "
            "Проверь баланс на dashboard.cohere.com."
        )
    if code == 404:
        return (
            "Cohere не знает такой модели (HTTP 404). Проверь, что имя "
            "модели в поле «Модель» верное (например, command-a-03-2025)."
        )
    return f"Cohere отклонил запрос: {raw_msg}"


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

def translate_text(
    text: str, cfg: TranslatorConfig,
    *, progress: Callable[[str], None] | None = None,
    pause_event: "threading.Event | None" = None,
    cancel_event: "threading.Event | None" = None,
    prior_context: str = "",
) -> str:
    """Translate a single block of text with one Cohere request.

    Retries on network failure AND on pathological output (scream
    repetition loops, or output far longer than the source). The retry
    nudges temperature up slightly — at 0.1 the model sometimes locks
    into a bad loop it can't break out of.

    ``progress`` is called with short status strings before each HTTP
    attempt and after each response. When this function is called from
    the GUI the caller threads those strings straight into the log so
    the user sees "sending 6000 chars…" / "got 8200 chars" / "retry 2
    (temp=0.2)" instead of a minute of silence.
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
        # Honour pause/cancel before every attempt — including the
        # first — so a user who clicks Pause right after Start gets
        # immediate effect instead of having to wait through one
        # whole round-trip first.
        wait_if_paused(pause_event, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled("Отменено перед запросом к Cohere.")

        temp = cfg.temperature + 0.1 * (attempt - 1)
        if progress:
            progress(
                f"    → Cohere: отправляю {len(text)} символов "
                f"(попытка {attempt}/{cfg.retries}, temp={temp:.1f})"
            )
        t_start = time.monotonic()
        body = {
            "model": cfg.model,
            "messages": [
                {"role": "system", "content": _system_prompt_with_glossary(cfg)},
                {
                    "role": "user",
                    "content": _wrap_for_translation(
                        text, prior_context=prior_context,
                    ),
                },
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
            if progress:
                elapsed = time.monotonic() - t_start
                progress(
                    f"    ← ответ: {len(translated)} символов "
                    f"за {elapsed:.1f} сек"
                )
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
                raise TranslationError(_format_cohere_4xx(exc.code, msg)) from exc
            if progress:
                progress(f"    ✗ HTTP {exc.code} — ретрай через {cfg.retry_backoff * attempt:.0f} сек")
            if attempt < cfg.retries:
                time.sleep(cfg.retry_backoff * attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_err = exc
            if progress:
                progress(
                    f"    ✗ сеть: {type(exc).__name__} ({exc}) — "
                    f"ретрай через {cfg.retry_backoff * attempt:.0f} сек"
                )
            if attempt < cfg.retries:
                time.sleep(cfg.retry_backoff * attempt)
    # Exhausted retries without ever reaching Cohere successfully.
    # Distinguish pure network failure (can't even open TCP / HTTPS) from
    # server-side errors — on Windows + split-tunnel VPN, urllib often
    # can't reach api.cohere.com even when the browser can. That looks
    # identical to "server is down" unless we say it out loud.
    reason = str(last_err) if last_err is not None else "нет подробностей"
    is_network = isinstance(
        last_err, (urllib.error.URLError, TimeoutError, ConnectionError)
    )
    if is_network:
        raise TranslationError(
            f"Не удалось достучаться до Cohere за {cfg.retries} попыток "
            f"({reason}).\n\n"
            "Проверь в PowerShell:\n"
            "    python -c \"import urllib.request; "
            "print(urllib.request.urlopen('https://api.cohere.com/', "
            "timeout=10).status)\"\n"
            "Если команда зависает или пишет timeout/URLError — это "
            "значит твой VPN пропускает только браузер (split-tunnel), "
            "а Python идёт напрямую и его режет провайдер. Решение: "
            "переключи VPN в режим «весь трафик» (Full tunnel) и "
            "перезапусти окно. Уже переведённые главы пропустятся."
        )
    raise TranslationError(
        f"Cohere request failed after {cfg.retries} attempts: {reason}"
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
    pause_event: "threading.Event | None" = None,
    chunk_done: Callable[[int], None] | None = None,
    prior_context: str = "",
) -> str:
    """Translate one ``chapter_NNNN.txt`` file into ``dst``.

    Preserves the ``# Title`` header (we translate the title separately so
    it ends up in Russian too) and the paragraph structure of the body.
    Refuses to write an empty translated file if the source had text —
    that way a silent model refusal surfaces as an error rather than as
    a corrupted library.

    Returns the translated body (without the title header) so the caller
    can feed its tail paragraphs as ``prior_context`` into the next
    chapter for cross-chapter consistency.
    """
    raw = src.read_text(encoding="utf-8")
    title, body = _split_title(raw)
    source_had_text = bool(body.strip())

    if cancel_event is not None and cancel_event.is_set():
        raise TranslationCancelled("Отменено до начала главы.")

    translated_title = (
        translate_text(
            title, cfg, progress=progress,
            pause_event=pause_event, cancel_event=cancel_event,
            # Title is short and stylistically distinct — passing prior
            # context here just confuses the model into echoing.
        )
        if title else ""
    )
    chunks = split_into_chunks(body, cfg.chunk_chars)
    translated_parts: list[str] = []
    # Only the FIRST chunk of a chapter gets cross-chapter context —
    # subsequent chunks of the same chapter already have local
    # continuity from the source itself, and including the previous
    # chapter's tail in every chunk would inflate token use linearly.
    first_chunk_context = prior_context if cfg.use_prior_context else ""
    for i, chunk in enumerate(chunks, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled(
                f"Отменено на чанке {i}/{len(chunks)} файла {src.name}."
            )
        if progress:
            progress(
                f"  чанк {i}/{len(chunks)} ({len(chunk)} символов)"
            )
        translated_parts.append(translate_text(
            chunk, cfg, progress=progress,
            pause_event=pause_event, cancel_event=cancel_event,
            prior_context=first_chunk_context if i == 1 else "",
        ))
        # Report char-level progress AFTER a chunk lands successfully so
        # a failed/retried chunk doesn't double-count. The callback may
        # update a global progress bar that spans the whole folder.
        if chunk_done is not None:
            try:
                chunk_done(len(chunk))
            except Exception:  # pragma: no cover — callback is GUI-side
                pass

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
    return translated_body


def translate_folder(
    src_dir: Path, dst_dir: Path, cfg: TranslatorConfig,
    *, force: bool = False,
    progress: Callable[[str], None] | None = None,
    cancel_event: "threading.Event | None" = None,
    pause_event: "threading.Event | None" = None,
    wanted_numbers: "set[int] | None" = None,
    chunk_done: Callable[[int], None] | None = None,
    max_workers: int = 1,
) -> list[Path]:
    """Translate every ``chapter_*.txt`` in ``src_dir`` into ``dst_dir``.

    If ``wanted_numbers`` is given, only files whose leading 4-digit
    chapter number is in the set are translated.

    ``max_workers`` (default 1 = strictly sequential) caps how many chapter
    translations can be in flight at once. >1 dispatches to a parallel
    path that runs whole chapters concurrently. The cross-chapter context
    feature is fundamentally sequential (each chapter's translation needs
    the *previous* chapter's tail), so when ``cfg.use_prior_context`` is
    true we transparently fall back to serial regardless of
    ``max_workers`` — the consistency win is worth more than the speed.
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
    # Parallel path: only safe to take when cross-chapter context is OFF,
    # since context inherently chains chapter N's translation onto N-1's
    # output. With context ON we silently fall back to serial — the
    # alternative is "fast but inconsistent character names", which
    # defeats the whole point of the feature.
    if max_workers > 1 and not cfg.use_prior_context:
        return _translate_folder_parallel(
            files, dst_dir, cfg,
            force=force,
            progress=progress,
            cancel_event=cancel_event,
            pause_event=pause_event,
            chunk_done=chunk_done,
            max_workers=max_workers,
        )

    done: list[Path] = []
    last_ok_idx: int | None = None
    # Cross-chapter context: tail of the previous chapter's translation,
    # threaded into the next chapter's first chunk. Survives skipped
    # (cached) chapters by reading the tail off the existing dst file
    # so a partial run still gets continuity.
    prior_context = ""
    n_ctx = max(0, cfg.prior_context_paragraphs)

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
            # Even for cached files, capture the tail so the NEXT
            # chapter sees consistent context. Cheap (one read).
            if cfg.use_prior_context and n_ctx > 0:
                try:
                    cached_raw = dst.read_text(encoding="utf-8")
                    _, cached_body = _split_title(cached_raw)
                    prior_context = _tail_paragraphs(cached_body, n_ctx)
                except OSError:
                    prior_context = ""
            continue
        if progress:
            progress(f"[{i}/{len(files)}] translating: {src.name}")
        try:
            translated_body = translate_chapter_file(
                src, dst, cfg,
                progress=progress, cancel_event=cancel_event,
                pause_event=pause_event,
                chunk_done=chunk_done,
                prior_context=prior_context if cfg.use_prior_context else "",
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
        # Refresh context for the next chapter from what we just wrote.
        if cfg.use_prior_context and n_ctx > 0:
            prior_context = _tail_paragraphs(translated_body, n_ctx)
    return done


def _translate_folder_parallel(
    files: list[Path], dst_dir: Path, cfg: TranslatorConfig,
    *, force: bool,
    progress: Callable[[str], None] | None,
    cancel_event: "threading.Event | None",
    pause_event: "threading.Event | None",
    chunk_done: Callable[[int], None] | None,
    max_workers: int,
) -> list[Path]:
    """Parallel translation across chapters (no cross-chapter context).

    Each chapter is still translated chunk-by-chunk on its own worker
    thread, so intra-chapter continuity is preserved. Only the
    *between-chapter* bottleneck goes away. Cached chapters (dst file
    already exists) are skipped inline on the dispatcher thread.

    On the first per-chapter failure we stop accepting new work and
    drain the in-flight set, then re-raise — exactly like the serial
    path so the GUI's existing error-handling stays intact.

    Progress / chunk_done callbacks fire from worker threads, so the
    caller has to be thread-safe (the GUI's queue-of-messages already
    is — that's what we built it for).
    """
    log_lock = threading.Lock()

    def safe_progress(msg: str) -> None:
        if progress is None:
            return
        # Serialise prints so two concurrent chunks don't interleave their
        # log lines into half-readable garbage.
        with log_lock:
            progress(msg)

    safe_chunk_done: Callable[[int], None] | None = None
    if chunk_done is not None:
        chunk_lock = threading.Lock()

        def _safe_chunk_done(n_chars: int) -> None:
            # chunk_done in the GUI just enqueues a message — already
            # thread-safe — but lock for paranoia in case a future caller
            # mutates shared state directly.
            with chunk_lock:
                chunk_done(n_chars)

        safe_chunk_done = _safe_chunk_done

    done_set: dict[int, Path] = {}  # 1-based index -> dst path
    last_ok_idx: int | None = None
    failure: BaseException | None = None
    cancelled = False

    # Process cached chapters synchronously up front. They're free and
    # there's no point queuing them.
    pending: list[tuple[int, Path]] = []
    for i, src in enumerate(files, start=1):
        dst = dst_dir / src.name
        if dst.exists() and not force:
            done_set[i] = dst
            last_ok_idx = i
            safe_progress(f"[{i}/{len(files)}] skip (exists): {src.name}")
        else:
            pending.append((i, src))

    def translate_one(i: int, src: Path) -> tuple[int, Path]:
        if cancel_event is not None and cancel_event.is_set():
            raise TranslationCancelled("cancel before translate")
        wait_if_paused(pause_event, cancel_event)
        dst = dst_dir / src.name
        safe_progress(f"[{i}/{len(files)}] translating: {src.name}")
        translate_chapter_file(
            src, dst, cfg,
            progress=safe_progress,
            cancel_event=cancel_event,
            pause_event=pause_event,
            chunk_done=safe_chunk_done,
            # Parallel path is only entered when use_prior_context is
            # False, so prior_context stays empty. Passing it explicitly
            # keeps behaviour symmetric with the serial path.
            prior_context="",
        )
        return i, dst

    pending_iter = iter(pending)
    in_flight: dict = {}  # future -> i

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for _ in range(min(max_workers, len(pending))):
            try:
                i, src = next(pending_iter)
            except StopIteration:
                break
            in_flight[pool.submit(translate_one, i, src)] = i

        while in_flight and failure is None and not cancelled:
            ready, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in ready:
                i = in_flight.pop(fut)
                exc = fut.exception()
                if isinstance(exc, TranslationCancelled):
                    cancelled = True
                    continue
                if exc is not None:
                    failure = exc
                    continue
                _, dst = fut.result()
                done_set[i] = dst
                if last_ok_idx is None or i > last_ok_idx:
                    last_ok_idx = i
                if failure is None and not cancelled and (
                    cancel_event is None or not cancel_event.is_set()
                ):
                    try:
                        ni, nsrc = next(pending_iter)
                    except StopIteration:
                        continue
                    in_flight[pool.submit(translate_one, ni, nsrc)] = ni
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True

        # Drain remainder so pool shutdown doesn't block on stuck workers.
        if in_flight:
            for fut in list(in_flight):
                try:
                    res = fut.result(timeout=60)
                except Exception:
                    continue
                idx, dst = res
                done_set.setdefault(idx, dst)

    ordered = [done_set[i] for i in range(1, len(files) + 1) if i in done_set]

    if cancelled or (cancel_event is not None and cancel_event.is_set()):
        raise TranslationCancelled(
            f"Остановлено пользователем. Готово {len(ordered)}/{len(files)}.",
            translated=len(ordered),
            last_ok=last_ok_idx,
        )
    if failure is not None:
        if isinstance(failure, TranslationError):
            raise TranslationError(
                str(failure),
                translated=len(ordered),
                last_ok=last_ok_idx,
            )
        raise TranslationError(
            f"Перевод упал: {failure!r}",
            translated=len(ordered),
            last_ok=last_ok_idx,
        )
    return ordered


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
