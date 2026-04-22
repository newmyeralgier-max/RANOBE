#!/usr/bin/env python3
import os
import json
import time
import urllib.request
import sys

# === CONFIGURATION ===
FIREWORKS_API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
API_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

DATA_DIR = "d:/1. Project/Ranobe/data_infinite"
RAW_DIR = os.path.join(DATA_DIR, "raw")
TRANS_DIR = os.path.join(DATA_DIR, "translated")

START_CH = 94
END_CH = 141

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
    
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        API_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {FIREWORKS_API_KEY}",
            "Content-Type": "application/json"
        }
    )
    
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"].strip()
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
    
    for ch_num in range(START_CH, END_CH + 1):
        raw_file = os.path.join(RAW_DIR, f"chapter_{ch_num:04d}.txt")
        trans_file = os.path.join(TRANS_DIR, f"chapter_{ch_num:04d}.txt")
        
        if os.path.exists(trans_file):
            print(f"Chapter {ch_num} already exists, skipping.")
            continue
            
        if not os.path.exists(raw_file):
            print(f"Chapter {ch_num} raw file not found, skipping.")
            continue
            
        with open(raw_file, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
            
        title_en = raw_data.get("title", "")
        text_en = raw_data.get("text", "")
        
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
