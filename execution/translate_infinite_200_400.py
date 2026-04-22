#!/usr/bin/env python3
import os
import json
import time
import requests
import sys

# === CONFIGURATION ===
FIREWORKS_API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

DATA_DIR = "d:/1. Project/Ranobe/data_infinite"
RAW_DIR = os.path.join(DATA_DIR, "raw")
TRANS_DIR = os.path.join(DATA_DIR, "translated")

START_CH = 201
END_CH = 400

# Glossary from INIT.md
GLOSSARY = "Чэнь Янь, Линь Цифэн, Чу Сияо, Цинь Юэ, Цинь Цинъюй, Ху Тяньюань, Юнь Ичэнь, Фу Цянь, Бай Цимин, Чжун Инь, Юэ Чи, Лю Яньтан, Ли Хаовэнь, Цзинь Аньчэн, Дин Цю, Нянь Юнь."

SYSTEM_PROMPT = f"""РОЛЬ: Ты — элитный литературный переводчик, специализирующийся на азиатских веб-новеллах. Твоя задача — взять существующий английский перевод и передать его на русском языке на уровне качества современной веб-новеллы, написанной изначально по-русски — естественно, живо и атмосферно.

КЛЮЧЕВЫЕ ПРИНЦИПЫ СТИЛЯ:
- Голос прежде всего: каждое предложение должно звучать так, будто это бестселлер веб-новеллы, написанный изначально по-русски.
- Естественные идиомы: заменяй английские устойчивые выражения их русскими эквивалентами.
- Показывай, не рассказывай: сохраняй физические реакции, подтекст и детали окружения.
- Оформление диалогов: используй русские типографские кавычки «», тире — для реплик персонажей и многоточие…

ГЛОССАРИЙ ПЕРСОНАЖЕЙ (СОБЛЮДАЙ СТРОГО):
{GLOSSARY}

КРИТИЧЕСКИЕ ПРАВИЛА:
1. НИКОГДА не переводи дословно.
2. НИКОГДА не переноси английский порядок слов.
3. НИКОГДА не сокращай и не упрощай детали.
4. Выводи только переведённый отрывок — без комментариев, пояснений и заметок."""

def translate_text(text, retries=3):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "temperature": 0.3,
        "max_tokens": 4096
    }
    
    headers = {
        "Authorization": f"Bearer {FIREWORKS_API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    for attempt in range(retries):
        try:
            response = requests.post(API_URL, json=payload, headers=headers, timeout=120)
            if response.status_code == 200:
                result = response.json()
                return result["choices"][0]["message"]["content"].strip()
            else:
                print(f"      [!] HTTP Error {response.status_code}: {response.text[:200]}")
                time.sleep(5)
        except Exception as e:
            print(f"      [!] Error (attempt {attempt+1}): {e}")
            time.sleep(5)
    return None

def chunk_text(text, max_chars=3500):
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
    
    # Get sorted list of raw files to determine actual range
    if not os.path.exists(RAW_DIR):
        print(f"Error: Raw directory {RAW_DIR} does not exist.")
        return

    raw_files = [f for f in os.listdir(RAW_DIR) if f.startswith("chapter_") and f.endswith(".txt")]
    available_chapters = sorted([int(f.split("_")[1].split(".")[0]) for f in raw_files])
    
    target_chapters = [ch for ch in available_chapters if START_CH <= ch <= END_CH]
    
    if not target_chapters:
        print("No raw chapters found in the specified range.")
        return

    print(f"Found {len(target_chapters)} chapters to translate.")
    
    for ch_num in target_chapters:
        raw_file = os.path.join(RAW_DIR, f"chapter_{ch_num:04d}.txt")
        trans_file = os.path.join(TRANS_DIR, f"chapter_{ch_num:04d}.txt")
        
        if os.path.exists(trans_file):
            print(f"Chapter {ch_num} already exists, skipping.")
            continue
            
        with open(raw_file, "r", encoding="utf-8") as f:
            try:
                raw_data = json.load(f)
            except Exception as e:
                print(f"Error loading {raw_file}: {e}")
                continue
            
        title_en = raw_data.get("title", "")
        text_en = raw_data.get("text", "")
        
        if not text_en:
            print(f"Chapter {ch_num} has no text, skipping.")
            continue

        print(f"[*] Translating Chapter {ch_num}...", end="", flush=True)
        
        # Translate title
        title_ru = translate_text(f"Translate this book chapter title: {title_en}")
        if not title_ru: title_ru = title_en
        
        # Translate body
        chunks = chunk_text(text_en)
        translated_parts = []
        for i, chunk in enumerate(chunks):
            res = translate_text(chunk)
            if res:
                translated_parts.append(res)
                print(".", end="", flush=True)
            else:
                translated_parts.append(f"[ОШИБКА ПЕРЕВОДА]\n{chunk}")
                print("F", end="", flush=True)
            time.sleep(1)
            
        text_ru = "\n\n".join(translated_parts)
        
        with open(trans_file, "w", encoding="utf-8") as f:
            json.dump({"title": title_ru, "text": text_ru}, f, ensure_ascii=False, indent=2)
            
        print(" DONE")

if __name__ == "__main__":
    main()
