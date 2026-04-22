#!/usr/bin/env python3
"""
Ranobe Pipeline — ranobes.net variant
Скачивание глав по цепочке Next, перевод через Fireworks API (cogito-671b), сборка EPUB.
"""

import sys
import os
import re
import json
import time
import html as html_lib
import urllib.request

sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
from bs4 import BeautifulSoup

# === НАСТРОЙКИ ===

FIREWORKS_API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
TRANSLATE_MODEL = "accounts/cogito/models/cogito-671b-v2-p1"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://ranobes.net/",
}

DELAY_DOWNLOAD = 2.0
DELAY_TRANSLATE = 1.0
CHUNK_SIZE = 3500

TRANSLATE_SYSTEM_PROMPT = """Ты — элитный литературный переводчик, специализирующийся на азиатских веб-новеллах. Твоя задача — взять английский текст и передать его на русском языке на уровне качества современной веб-новеллы, написанной изначально по-русски — естественно, живо и атмосферно.

КРИТИЧЕСКИЕ ПРАВИЛА:
1. НИКОГДА не переводи дословно — если звучит неестественно по-русски, перефразируй
2. НИКОГДА не переноси английский порядок слов
3. Заменяй английские идиомы русскими эквивалентами
4. Диалоги в кавычках-ёлочках «»
5. Имена собственные транслитерируй (Сет Торн, не Seth Thorne)
6. Термины-реалии: оставляй оригинал в скобках при первом упоминании
7. НЕ добавляй пояснений, комментариев, примечаний, reasoning — ТОЛЬКО перевод
8. Сохраняй структуру абзацев оригинала"""

# === УТИЛИТЫ ===

def fetch_html_file(url, filepath, retries=3):
    """Скачать HTML в файл через curl (stdout обрезает ranobes.net!)"""
    import subprocess
    for attempt in range(retries):
        try:
            result = subprocess.run(
                ["curl", "-s", "--compressed", "-L",
                 "-H", HEADERS["User-Agent"],
                 "-H", f"Referer: {HEADERS['Referer']}",
                 "-o", filepath, url],
                capture_output=True, timeout=30
            )
            if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
                return True
        except:
            pass
        time.sleep(3)
    return False


def translate_text(text, api_key=None, model=None):
    """Перевести текст через Fireworks API"""
    key = api_key or FIREWORKS_API_KEY
    mdl = model or TRANSLATE_MODEL

    payload = {
        "model": mdl,
        "messages": [
            {"role": "system", "content": TRANSLATE_SYSTEM_PROMPT},
            {"role": "user", "content": text}
        ],
        "temperature": 0.3,
        "max_tokens": 4096
    }

    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        FIREWORKS_URL,
        data=data,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    Перевод попытка {attempt+1}/3: {e}")
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


def parse_chapter(html_path):
    """Извлечь заголовок, текст и Next ссылку из HTML файла ranobes.net"""
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        soup = BeautifulSoup(f, "html.parser")

    # Заголовок
    h1 = soup.find("h1")
    title = h1.text.strip() if h1 else ""

    # Контент — div id="arrticle" (ТРИ r, опечатка сайта!)
    article = soup.find("div", id="arrticle")
    if not article:
        return title, None, None

    text = article.get_text().strip()
    # Удалить заголовок из начала текста если дублируется
    if title and text.startswith(title):
        text = text[len(title):].strip()

    # Next ссылка
    next_url = None
    for a in soup.find_all("a", href=True):
        if a.text.strip() == "Next":
            href = a["href"]
            next_url = href if href.startswith("http") else f"https://ranobes.net{href}"
            break

    return title, text, next_url


# === EPUB ===

def build_epub(book_title, author, chapters_data, output_path=None):
    """Собрать EPUB из переведённых глав"""
    from ebooklib import epub

    book = epub.EpubBook()
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', book_title)
    book.set_identifier(f"ranobe-{safe_title}")
    book.set_title(book_title)
    book.set_language("ru")
    book.add_author(author)

    style = epub.EpubItem(
        uid="style", file_name="style/default.css", media_type="text/css",
        content=b"""body { font-family: serif; line-height: 1.8; margin: 1em; }
h1 { font-size: 1.4em; border-bottom: 1px solid #ccc; margin-top: 2em; }
p { text-indent: 1.5em; margin: 0.5em 0; text-align: justify; }
.dialog { text-indent: 0; }"""
    )
    book.add_item(style)

    epub_chapters = []
    spine = ["nav"]

    for i, (ch_title, ch_text) in enumerate(chapters_data):
        if not ch_text:
            continue
        safe_t = html_lib.escape(ch_title or f"Глава {i+1}")
        paragraphs = ch_text.split("\n")
        html_content = f"<h1>{safe_t}</h1>\n"
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p_esc = html_lib.escape(p)
            if p_esc.startswith("&laquo;") or p_esc.startswith("&mdash;") or p_esc.startswith("—"):
                html_content += f'<p class="dialog">{p_esc}</p>\n'
            else:
                html_content += f"<p>{p_esc}</p>\n"

        chapter = epub.EpubHtml(
            title=ch_title or f"Глава {i+1}",
            file_name=f"chapter_{i+1:04d}.xhtml", lang="ru"
        )
        chapter.content = html_content
        chapter.add_item(style)
        book.add_item(chapter)
        epub_chapters.append(chapter)
        spine.append(chapter)

    book.toc = epub_chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    out = output_path or f"output/{safe_title}.epub"
    os.makedirs(os.path.dirname(out) if os.path.dirname(out) else ".", exist_ok=True)
    epub.write_epub(out, book, {})
    print(f"\nEPUB создан: {out}")
    return out


# === ОСНОВНОЙ ПАЙПЛАЙН ===

def run_pipeline(start_url, start_ch, end_ch, book_title="Unknown", author="Unknown", data_dir="data"):
    """Скачать главы по цепочке Next → перевести → EPUB"""

    raw_dir = os.path.join(data_dir, "raw")
    translated_dir = os.path.join(data_dir, "translated")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(translated_dir, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    current_url = start_url
    current_ch = start_ch
    chapters_raw = {}

    # 1. Скачиваем главы по цепочке
    print(f"1. Скачиваю главы {start_ch}-{end_ch}...")

    while current_ch <= end_ch:
        raw_file = os.path.join(raw_dir, f"chapter_{current_ch:04d}.txt")

        if os.path.exists(raw_file):
            with open(raw_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            chapters_raw[current_ch] = saved
            print(f"   [{current_ch-start_ch+1}/{end_ch-start_ch+1}] Глава {current_ch}: уже скачана")
            # Нужно получить next_url из сохранённого
            current_url = saved.get("next_url", "")
            if not current_url:
                # Скачаем HTML только для next_url
                tmp = "/tmp/rn_nav.html"
                fetch_html_file(saved["url"], tmp)
                _, _, next_url = parse_chapter(tmp)
                current_url = next_url or ""
            current_ch += 1
            continue

        print(f"   [{current_ch-start_ch+1}/{end_ch-start_ch+1}] Глава {current_ch}: скачиваю...", end=" ", flush=True)

        tmp_file = f"/tmp/rn_ch{current_ch}.html"
        if not fetch_html_file(current_url, tmp_file):
            print("ОШИБКА скачивания")
            break

        title, text, next_url = parse_chapter(tmp_file)

        if not text:
            print("ОШИБКА — нет контента")
            break

        chapters_raw[current_ch] = {
            "title": title,
            "text": text,
            "url": current_url,
            "next_url": next_url or ""
        }

        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(chapters_raw[current_ch], f, ensure_ascii=False)

        print(f"OK ({len(text)} символов)")

        if not next_url:
            print(f"   Нет Next ссылки после главы {current_ch}")
            current_ch += 1
            break

        current_url = next_url
        current_ch += 1
        time.sleep(DELAY_DOWNLOAD)

    # 2. Переводим главы
    print(f"\n2. Перевожу главы на русский (cogito-671b)...")
    chapters_translated = {}
    sorted_nums = sorted(chapters_raw.keys())

    for i, num in enumerate(sorted_nums):
        trans_file = os.path.join(translated_dir, f"chapter_{num:04d}.txt")

        if os.path.exists(trans_file):
            with open(trans_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
            chapters_translated[num] = saved
            print(f"   [{i+1}/{len(sorted_nums)}] Глава {num}: уже переведена")
            continue

        raw = chapters_raw[num]
        title_en = raw["title"]
        text_en = raw["text"]

        print(f"   [{i+1}/{len(sorted_nums)}] Глава {num}: перевожу...", end=" ", flush=True)

        # Переводим заголовок
        title_ru = translate_text(title_en) if title_en else f"Глава {num}"
        if not title_ru:
            title_ru = title_en

        # Переводим текст по чанкам
        chunks = chunk_text(text_en)
        translated_parts = []

        for j, chunk in enumerate(chunks):
            result = translate_text(chunk)
            if result:
                translated_parts.append(result)
            else:
                translated_parts.append(f"[ОШИБКА ПЕРЕВОДА]\n{chunk}")
            time.sleep(DELAY_TRANSLATE)

        text_ru = "\n\n".join(translated_parts)
        chapters_translated[num] = {"title": title_ru, "text": text_ru}

        with open(trans_file, "w", encoding="utf-8") as f:
            json.dump(chapters_translated[num], f, ensure_ascii=False)

        print(f"OK ({len(text_ru)} символов)")

    # 3. Собираем EPUB
    print(f"\n3. Собираю EPUB...")

    epub_data = []
    for num in sorted(chapters_translated.keys()):
        ch = chapters_translated[num]
        epub_data.append((ch["title"], ch["text"]))

    full_title = f"{book_title} (Главы {start_ch}-{end_ch})"
    output_file = f"output/{re.sub(r'[\\\\/:*?\"<>|]', '_', book_title)}_ch{start_ch}-{end_ch}.epub"

    return build_epub(
        book_title=full_title,
        author=author,
        chapters_data=epub_data,
        output_path=output_file
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Ranobe Pipeline: ranobes.net → translate → EPUB")
    parser.add_argument("--url", required=True, help="URL первой главы на ranobes.net")
    parser.add_argument("--start", type=int, required=True, help="Номер первой главы")
    parser.add_argument("--end", type=int, required=True, help="Номер последней главы")
    parser.add_argument("--title", default="Unknown", help="Название книги")
    parser.add_argument("--author", default="Unknown", help="Автор")
    parser.add_argument("--data-dir", default="data_infinite", help="Папка для данных")

    args = parser.parse_args()

    run_pipeline(
        start_url=args.url,
        start_ch=args.start,
        end_ch=args.end,
        book_title=args.title,
        author=args.author,
        data_dir=args.data_dir
    )
