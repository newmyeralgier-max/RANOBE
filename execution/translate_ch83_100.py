#!/usr/bin/env python3
"""
Перевод глав 83-100 ранобэ через Modal API (GLM-5.1-FP8)
Считывает raw, переводит, сохраняет в translated.
"""

import json
import os
import sys
import time
import urllib.request
import re

# === НАСТРОЙКИ ===
API_KEY = "modalresearch_uietVw_rtk2dHzmd09aDiDtipE-dJyYW60d2S1F-4WY"
API_URL = "https://api.us-west-2.modal.direct/v1/chat/completions"
MODEL = "zai-org/GLM-5.1-FP8"

DATA_DIR = "/mnt/d/1. Project/Ranobe/data_infinite"
RAW_DIR = os.path.join(DATA_DIR, "raw")
TRANS_DIR = os.path.join(DATA_DIR, "translated")

START_CH = 83
END_CH = 100

CHUNK_SIZE = 3000  # меньше чанки — надёжнее
MAX_TOKENS = 4096
DELAY = 2.0  # задержка между запросами
MAX_RETRIES = 5

SYSTEM_PROMPT = """Ты — элитный литературный переводчик, специализирующийся на азиатских веб-новеллах. Твоя задача — взять английский текст и передать его на русском языке на уровне качества современной веб-новеллы, написанной изначально по-русски — естественно, живо и атмосферно.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. НИКОГДА не переводи дословно — если звучит неестественно по-русски, перефразируй
2. НИКОГДА не переноси английский порядок слов
3. Заменяй английские идиомы русскими эквивалентами
4. Диалоги в кавычках-ёлочках «»
5. Имена собственные транслитерируй (Сет Торн, не Seth Thorne)
6. Термины-реалии: оставляй оригинал в скобках при первом упоминании
7. НЕ добавляй пояснений, комментариев, примечаний, reasoning — ТОЛЬКО перевод
8. Сохраняй структуру абзацев оригинала"""


def translate_text(text, retries=MAX_RETRIES):
    """Перевести текст через Modal API (GLM-5.1)"""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "temperature": 0.3,
        "max_tokens": MAX_TOKENS
    }

    data = json.dumps(payload).encode()

    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                API_URL,
                data=data,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                result = json.loads(resp.read())

            content = result["choices"][0]["message"].get("content")
            # GLM-5.1 может вернуть content=None при маленьком max_tokens
            # или при reasoning — проверяем
            if content and len(content.strip()) > 20:
                return content.strip()

            # Если content пустой но есть reasoning — попробуем без reasoning
            reasoning = result["choices"][0]["message"].get("reasoning_content", "")
            finish = result["choices"][0].get("finish_reason", "")

            if finish == "length":
                print(f"      [warn] max_tokens reached, retrying with more... ")
                # Увеличим max_tokens
                payload["max_tokens"] = min(payload.get("max_tokens", MAX_TOKENS) * 2, 8192)
                data = json.dumps(payload).encode()
                time.sleep(3)
                continue

            if not content and reasoning:
                print(f"      [warn] content=None but has reasoning ({len(reasoning)}c), retrying...")
                time.sleep(3)
                continue

            return content.strip() if content else None

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200]
            except:
                pass
            print(f"      HTTP {e.code}: {body}")
            if e.code in (429, 502, 503, 504):
                wait = 10 * (attempt + 1)
                print(f"      Жду {wait}с...")
                time.sleep(wait)
            else:
                return None
        except Exception as e:
            print(f"      Ошибка: {e}")
            time.sleep(5)

    return None


def chunk_text(text, max_chars=CHUNK_SIZE):
    """Разбить текст на чанки по абзацам"""
    paragraphs = text.split("\n")
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = para
        else:
            current += "\n" + para if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


def main():
    os.makedirs(TRANS_DIR, exist_ok=True)

    total_chapters = END_CH - START_CH + 1
    done = 0
    failed = []

    for ch_num in range(START_CH, END_CH + 1):
        raw_file = os.path.join(RAW_DIR, f"chapter_{ch_num:04d}.txt")
        trans_file = os.path.join(TRANS_DIR, f"chapter_{ch_num:04d}.txt")

        # Пропуск если уже переведено
        if os.path.exists(trans_file):
            # Проверим что это не битый файл
            try:
                with open(trans_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                text = saved.get("text", "")
                if "[ОШИБКА ПЕРЕВОДА]" not in text and len(text) > 200:
                    print(f"[{done+1}/{total_chapters}] Глава {ch_num}: уже переведена, пропуск")
                    done += 1
                    continue
            except:
                pass

        # Читаем raw
        if not os.path.exists(raw_file):
            print(f"[{done+1}/{total_chapters}] Глава {ch_num}: НЕТ RAW ФАЙЛА!")
            failed.append(ch_num)
            done += 1
            continue

        with open(raw_file, "r", encoding="utf-8") as f:
            raw = json.load(f)

        title_en = raw.get("title", f"Chapter {ch_num}")
        text_en = raw.get("text", "")

        if not text_en:
            print(f"[{done+1}/{total_chapters}] Глава {ch_num}: ПУСТОЙ ТЕКСТ В RAW!")
            failed.append(ch_num)
            done += 1
            continue

        print(f"[{done+1}/{total_chapters}] Глава {ch_num}: перевожу ({len(text_en)} символов, {len(chunk_text(text_en))} чанков)...", end="", flush=True)

        # Переводим заголовок
        title_ru = translate_text(title_en) if title_en else f"Глава {ch_num}"
        if not title_ru:
            title_ru = title_en
        print(f" title=OK", end="", flush=True)

        # Переводим текст по чанкам
        chunks = chunk_text(text_en)
        translated_parts = []
        chunk_fails = 0

        for j, chunk in enumerate(chunks):
            result = translate_text(chunk)
            if result:
                translated_parts.append(result)
                print(f" {j+1}/{len(chunks)}", end="", flush=True)
            else:
                translated_parts.append(f"[ОШИБКА ПЕРЕВОДА]\n{chunk}")
                chunk_fails += 1
                print(f" FAIL{j+1}", end="", flush=True)
            time.sleep(DELAY)

        text_ru = "\n\n".join(translated_parts)

        # Сохраняем
        with open(trans_file, "w", encoding="utf-8") as f:
            json.dump({"title": title_ru, "text": text_ru}, f, ensure_ascii=False, indent=2)

        status = f"OK ({len(text_ru)} символов)" if chunk_fails == 0 else f"WARN ({chunk_fails} чанков не переведены)"
        print(f" — {status}")

        if chunk_fails > 0:
            failed.append(ch_num)

        done += 1
        time.sleep(1)

    print(f"\n=== ГОТОВО ===")
    print(f"Переведено: {total_chapters - len(failed)}/{total_chapters}")
    if failed:
        print(f"Ошибки в главах: {failed}")
    else:
        print("Все главы переведены успешно!")


if __name__ == "__main__":
    main()
