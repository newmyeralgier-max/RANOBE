"""Telegram bot: paste a URL, get back an EPUB.

Self-contained Telegram bot — no external libraries (telegram-bot-api,
python-telegram-bot, aiogram, etc.). It talks to the Bot API through
``urllib.request`` directly, which keeps install footprint zero and
matches the rest of this project's stdlib-only philosophy.

Behaviour:
- ``/start`` and ``/help`` print a usage blurb.
- A bare URL message kicks off: load → download → translate → EPUB.
- Status messages stream into the chat as the job progresses so users
  aren't left looking at a frozen "typing…" indicator.
- The final EPUB is uploaded via ``sendDocument`` (multipart/form-data,
  hand-rolled because we don't want a ``requests`` dependency).
- Single global lock around the whole pipeline so we don't run two
  jobs at once on the same VM (would interleave logs, fight for the
  Cohere key, etc.). Multiple chats queue up.

Run:

    set TG_BOT_TOKEN=...
    set COHERE_API_KEY=...
    python -m novel_dl.bot

The bot reads chapters into ``~/.novel_dl/bot/<chat>/<slug>/`` so jobs
from different chats don't collide. Caches survive restarts so a
re-issued URL just rebuilds the EPUB without re-downloading.
"""

from __future__ import annotations

import argparse
import http.client
import json
import logging
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .downloader import DownloadError, download_chapters
from .epub import build_epub_from_folder
from .registry import UnsupportedSiteError, get_adapter
from .runlog import RunLog
from .translator import TranslationError, TranslatorConfig, translate_folder
from .utils import safe_filename

LOG = logging.getLogger("novel_dl.bot")

# Errors that signal a transient network blip rather than a real Telegram
# error. We retry these in long-poll instead of crashing the bot.
_TRANSIENT_NET_ERRORS: tuple[type[BaseException], ...] = (
    urllib.error.URLError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    socket.timeout,
)

_API_BASE = "https://api.telegram.org/bot{token}/{method}"
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_BOT_DIR = Path.home() / ".novel_dl" / "bot"


# ---- Telegram API helpers -------------------------------------------------


class BotConfigError(RuntimeError):
    """Raised when the bot can't start (missing token, etc.)."""


def _api_call(token: str, method: str, **params: Any) -> dict:
    """POST a JSON request to the Telegram Bot API."""
    url = _API_BASE.format(token=token, method=method)
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_send_document(
    token: str, chat_id: int, file_path: Path, caption: str = "",
) -> dict:
    """Upload a file via multipart/form-data using only stdlib.

    Most third-party Telegram libraries make this trivial. We build the
    multipart manually because adding a dependency for a single file
    upload isn't worth it.
    """
    boundary = "----novel_dl_" + secrets.token_hex(8)
    file_bytes = file_path.read_bytes()
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    )
    parts.append(f"{chat_id}\r\n".encode())

    if caption:
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            b'Content-Disposition: form-data; name="caption"\r\n\r\n'
        )
        parts.append(caption.encode("utf-8") + b"\r\n")

    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="document"; '
        f'filename="{file_path.name}"\r\n'.encode()
    )
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())

    body = b"".join(parts)
    url = _API_BASE.format(token=token, method="sendDocument")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---- Per-chat job pipeline ------------------------------------------------


_HELP = (
    "Привет! Я скачиваю и перевожу веб-новеллы.\n\n"
    "Просто пришли ссылку (freewebnovel, ranobes, royalroad, scribblehub) "
    "и я отправлю обратно EPUB на русском.\n\n"
    "Команды:\n"
    "  /help — это сообщение\n"
    "  /chapters 1-20 — ограничить диапазон глав в следующем задании\n"
    "  /lang en — отдать без перевода (английский оригинал)\n"
    "  /lang ru — переводить (по умолчанию)\n"
)


class _ChatState:
    """Per-chat preferences (range / language)."""

    def __init__(self) -> None:
        self.lang: str = "ru"
        self.range_spec: str = "all"


def _parse_message(text: str) -> tuple[str, str]:
    """Classify a Telegram message into (kind, payload).

    Returns ``("command", "/help")``, ``("url", "https://…")``,
    ``("range", "1-20")``, ``("lang", "en")``, or ``("other", text)``.
    Pulled out as a pure function so it can be unit-tested without
    spinning up a real bot.
    """
    text = text.strip()
    if not text:
        return ("other", "")

    # In groups, Telegram appends @botusername to commands ("/chapters@foo_bot
    # 1-3"). Strip that suffix so the rest of the parser doesn't see it.
    def _split_command(cmd: str) -> tuple[str, str] | None:
        head, _, tail = text.partition(" ")
        head_no_at = head.split("@", 1)[0]
        if head_no_at == cmd:
            return (head_no_at, tail.strip())
        return None

    if (parts := _split_command("/start")) is not None:
        return ("command", "/help")
    if (parts := _split_command("/help")) is not None:
        return ("command", "/help")
    if (parts := _split_command("/chapters")) is not None:
        return ("range", parts[1] or "all")
    if (parts := _split_command("/lang")) is not None:
        rest = parts[1].lower()
        return ("lang", rest if rest in ("en", "ru") else "ru")

    m = _URL_RE.search(text)
    if m:
        return ("url", m.group(0))
    return ("other", text)


def _process_url(
    *,
    token: str,
    chat_id: int,
    url: str,
    state: _ChatState,
    cohere_key: str,
    cohere_model: str,
    parallel_dl: int,
    parallel_tr: int,
) -> None:
    """Run the full download → translate → EPUB pipeline for one URL.

    Status updates flow through ``sendMessage`` after every meaningful
    milestone. On error we send a friendly explanation instead of
    leaving the user wondering. The whole function runs synchronously
    on the worker thread.
    """

    def say(msg: str) -> None:
        try:
            _api_call(token, "sendMessage", chat_id=chat_id, text=msg)
        except urllib.error.URLError as exc:
            # Telegram is unreachable — log but don't crash the worker.
            LOG.warning("sendMessage failed: %s", exc)

    chat_dir = _BOT_DIR / f"chat_{chat_id}"
    chat_dir.mkdir(parents=True, exist_ok=True)

    say(f"Беру ссылку: {url}")
    try:
        adapter = get_adapter(url)
    except UnsupportedSiteError as exc:
        say(f"Сайт не поддержан: {exc}")
        return

    say(f"Адаптер: {adapter.site_id}. Загружаю список глав…")
    try:
        book = adapter.fetch_book(url)
    except Exception as exc:  # noqa: BLE001 — surface anything to user
        say(f"Не смог загрузить список глав: {exc}")
        return

    out_dir = chat_dir / safe_filename(book.slug or book.title or "book")
    say(
        f"«{book.title}» — {len(book.chapters)} глав. "
        f"Скачиваю в {out_dir.name}/…"
    )

    # Translate the user-supplied range against the chapter count.
    from .utils import parse_range_spec

    indices = parse_range_spec(state.range_spec, len(book.chapters))
    if not indices:
        indices = list(range(1, len(book.chapters) + 1))

    def progress(msg: str) -> None:
        # Report only chapter-level milestones (skip per-chunk noise) so
        # we don't spam the chat with hundreds of messages.
        stripped = msg.lstrip()
        if stripped.startswith("[") and (
            "fetched" in stripped or "translating" in stripped
        ):
            say(stripped[:200])

    try:
        download_chapters(
            adapter, book, indices, out_dir,
            delay=1.0,
            progress=progress,
            max_workers=parallel_dl,
        )
    except DownloadError as exc:
        say(f"Скачка упала: {exc}")
        return

    if state.lang == "en":
        say("Сборка EPUB (английский, без перевода)…")
        epub_path = chat_dir / f"{safe_filename(book.title)} (en).epub"
        try:
            build_epub_from_folder(
                out_dir, epub_path,
                book_title=book.title,
                author=book.author,
                wanted_numbers=set(indices),
            )
        except Exception as exc:  # noqa: BLE001
            say(f"EPUB не собрался: {exc}")
            return
        say("Готово, отправляю файл.")
        _api_send_document(
            token, chat_id, epub_path,
            caption=f"{book.title} ({len(indices)} глав)",
        )
        return

    # Translate to Russian. Default = no cross-chapter context for the
    # bot path; the bot is meant to be fast, and a casual reader won't
    # notice the rare pronoun drift across chapter boundaries.
    say("Перевожу на русский (Cohere)…")
    cfg = TranslatorConfig(
        api_key=cohere_key,
        model=cohere_model or "command-a-03-2025",
        system_prompt=(
            "Ты литературный переводчик. Переводи на русский, сохраняя "
            "смысл и абзацы. Выводи ТОЛЬКО перевод."
        ),
        use_prior_context=False,
    )
    tr_dir = out_dir / "translated_ru"
    try:
        translate_folder(
            out_dir, tr_dir, cfg,
            progress=progress,
            wanted_numbers=set(indices),
            max_workers=parallel_tr,
        )
    except TranslationError as exc:
        say(f"Перевод упал: {exc}")
        return

    say("Сборка EPUB…")
    epub_path = chat_dir / f"{safe_filename(book.title)} (ru).epub"
    try:
        build_epub_from_folder(
            tr_dir, epub_path,
            book_title=book.title,
            author=book.author,
            wanted_numbers=set(indices),
        )
    except Exception as exc:  # noqa: BLE001
        say(f"EPUB не собрался: {exc}")
        return
    say("Готово, отправляю файл.")
    try:
        _api_send_document(
            token, chat_id, epub_path,
            caption=f"{book.title} (ru, {len(indices)} глав)",
        )
    except Exception as exc:  # noqa: BLE001
        say(f"Не смог загрузить EPUB в чат: {exc}")


# ---- Long-poll loop -------------------------------------------------------


def run_bot(
    token: str,
    *,
    cohere_key: str,
    cohere_model: str = "command-a-03-2025",
    parallel_dl: int = 2,
    parallel_tr: int = 1,
) -> None:
    """Long-poll Telegram for updates and dispatch each one.

    Single global lock serializes URL processing across chats so we
    don't double-up Cohere requests or have two downloaders fighting
    for the same Cloudflare cookie. Light-touch commands (/help, /lang,
    /chapters) bypass the lock since they're instant.
    """
    if not token:
        raise BotConfigError(
            "TG_BOT_TOKEN не задан. Получи токен у @BotFather и пропиши "
            "переменную окружения TG_BOT_TOKEN перед запуском."
        )

    # Mirror chat-side logging into the same per-run file the GUI uses.
    # Failures are swallowed inside RunLog itself.
    _run_log = RunLog()
    if _run_log.path:
        LOG.info("Bot run log: %s", _run_log.path)
    chat_states: dict[int, _ChatState] = {}
    job_lock = threading.Lock()
    offset = 0

    LOG.info("Bot started; long-polling Telegram…")
    backoff = 5.0
    while True:
        try:
            data = _api_call(
                token, "getUpdates",
                offset=offset, timeout=30,
            )
        except _TRANSIENT_NET_ERRORS as exc:
            wait = min(backoff, 60.0)
            LOG.warning(
                "getUpdates failed: %s (%s) — retrying in %.0fs",
                type(exc).__name__, exc, wait,
            )
            time.sleep(wait)
            backoff = min(backoff * 1.5, 60.0)
            continue
        except Exception as exc:  # noqa: BLE001
            # Don't let an unexpected exception kill the bot — log and retry.
            LOG.exception("Unexpected error in poll loop: %s", exc)
            time.sleep(5)
            continue
        backoff = 5.0
        if not data.get("ok"):
            LOG.warning("Telegram returned error: %s", data)
            time.sleep(5)
            continue

        for upd in data.get("result", []):
            offset = max(offset, int(upd["update_id"]) + 1)
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            chat_id = msg["chat"]["id"]
            text = msg.get("text", "")
            kind, payload = _parse_message(text)
            state = chat_states.setdefault(chat_id, _ChatState())

            if kind == "command":
                _api_call(token, "sendMessage", chat_id=chat_id, text=_HELP)
            elif kind == "range":
                state.range_spec = payload
                _api_call(
                    token, "sendMessage", chat_id=chat_id,
                    text=f"Диапазон следующего задания: {payload}",
                )
            elif kind == "lang":
                state.lang = payload
                label = "русский" if payload == "ru" else "оригинал"
                _api_call(
                    token, "sendMessage", chat_id=chat_id,
                    text=f"Язык вывода: {label}",
                )
            elif kind == "url":
                _api_call(
                    token, "sendMessage", chat_id=chat_id,
                    text="Принял. Жди в очереди если что-то ещё качается.",
                )

                def worker(
                    url: str = payload, st: _ChatState = state,
                    cid: int = chat_id,
                ) -> None:
                    with job_lock:
                        try:
                            _process_url(
                                token=token, chat_id=cid, url=url,
                                state=st, cohere_key=cohere_key,
                                cohere_model=cohere_model,
                                parallel_dl=parallel_dl,
                                parallel_tr=parallel_tr,
                            )
                        except Exception as exc:  # noqa: BLE001
                            LOG.exception("Job failed")
                            try:
                                _api_call(
                                    token, "sendMessage", chat_id=cid,
                                    text=f"Внутренняя ошибка: {exc}",
                                )
                            except Exception:
                                pass

                threading.Thread(target=worker, daemon=True).start()
            else:
                _api_call(
                    token, "sendMessage", chat_id=chat_id,
                    text="Не понял. Пришли ссылку или /help.",
                )


def _force_utf8_console() -> None:
    """Force stdout/stderr to UTF-8 so Cyrillic logs render correctly.

    Windows consoles default to CP866 / CP1251, which mangles UTF-8 text
    (the dreaded ``╨С╨╛╤В…`` mojibake). ``reconfigure`` is available in
    Python 3.7+ and silently no-ops if the stream doesn't support it.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: read token + Cohere key from env or args, start polling."""
    _force_utf8_console()
    parser = argparse.ArgumentParser(description="Telegram bot для novel_dl")
    parser.add_argument(
        "--token", default=os.environ.get("TG_BOT_TOKEN", ""),
        help="Токен бота от @BotFather (или TG_BOT_TOKEN env)",
    )
    parser.add_argument(
        "--cohere-key", default=os.environ.get("COHERE_API_KEY", ""),
        help="Ключ Cohere для перевода (или COHERE_API_KEY env)",
    )
    parser.add_argument(
        "--cohere-model", default=os.environ.get(
            "COHERE_MODEL", "command-a-03-2025",
        ),
    )
    parser.add_argument("--parallel-dl", type=int, default=2)
    parser.add_argument("--parallel-tr", type=int, default=1)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        run_bot(
            args.token,
            cohere_key=args.cohere_key,
            cohere_model=args.cohere_model,
            parallel_dl=args.parallel_dl,
            parallel_tr=args.parallel_tr,
        )
    except BotConfigError as exc:
        print(f"ERROR: {exc}")
        return 2
    except KeyboardInterrupt:
        print("Bot stopped.")
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
