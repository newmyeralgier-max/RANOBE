#!/usr/bin/env python3
"""
Ranobe Pipeline — Скачать + Перевести + EPUB
Единый скрипт для скачивания ранобэ с FreeWebNovel.com,
перевода через Fireworks API и сборки в EPUB.

Использование:
  python3 ranobe_pipeline.py --url BOOK_URL --start START_CH --end END_CH [--output FORMAT]

  Пример:
  python3 ranobe_pipeline.py \
    --url https://freewebnovel.com/horror-game-developer-my-games-arent-that-scary.html \
    --start 500 --end 607 --output epub

Поддержка резьюме: если в data/raw/ уже есть скачанные главы — пропускает их.
"""

import sys
import os
import re
import json
import time
import argparse
import urllib.request
import urllib.parse
import html as html_lib
from pathlib import Path

# Добавляем путь для bs4
sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))

from bs4 import BeautifulSoup

# === НАСТРОЙКИ ===

FIREWORKS_API_KEY = "fw_5CAe1FD9DRaBZpWDDTZzhN"
TRANSLATE_MODEL = "accounts/fireworks/models/llama-v3p3-70b-instruct"
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

BOOK_PAGE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://freewebnovel.com/",
}

CHAPTER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://freewebnovel.com/",
}

DELAY_DOWNLOAD = 2.0   # секунд между скачиванием глав
DELAY_TRANSLATE = 1.0  # секунд между запросами перевода
CHUNK_SIZE = 3800       # символов на чанк перевода

TRANSLATE_SYSTEM_PROMPT = """Ты профессиональный переводчик художественной литературы с английского на русский.

ПРАВИЛА:
1. Переводи литературно, сохраняя стиль и атмосферу оригинала
2. НЕ добавляй пояснений, комментариев или примечаний
3. НЕ пропускай предложения — переводи всё
4. Диалоги в кавычках-ёлочках «»
5. Имена собственные транслитерируй (Сет Торн, не Seth Thorne)
6. Термины-реалии: оставляй оригинал в скобках при первом упоминании
7. Возвращай ТОЛЬКО перевод, ничего лишнего
8. Сохраняй структуру абзацев оригинала"""

# === УТИЛИТЫ ===

def fetch_html(url, headers=None, retries=3):
    """Получить HTML страницы через urllib"""
    hdrs = headers or BOOK_PAGE_HEADERS
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"    Попытка {attempt+1}/{retries}: {e}")
            time.sleep(3)
    return None


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
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    Перевод попытка {attempt+1}/3: {e}")
            time.sleep(5)
    return None


def chunk_text(text, max_chars=CHUNK_SIZE):
    """Разбить текст на чанки по абзацам, не рвать предложения"""
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


# === СКАЧИВАНИЕ С FREEWEBNOVEL ===

def parse_book_page(html_content, book_url):
    """Извлечь slug и список глав из страницы книги"""
    soup = BeautifulSoup(html_content, "html.parser")

    # Извлечь slug из URL или мета-тегов
    # URL книги: https://freewebnovel.com/horror-game-developer-...html
    # URL глав:  https://freewebnovel.com/novel/SLUG/chapter-N
    parsed = urllib.parse.urlparse(book_url)
    path = parsed.path

    # Попробуем извлечь slug из ссылок на главы
    slug = None
    chapters = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.text.strip()

        # Ищем ссылки глав вида /novel/SLUG/chapter-N
        m = re.match(r"/novel/([^/]+)/chapter-(\d+)", href)
        if m:
            s = m.group(1)
            ch_num = int(m.group(2))
            if slug is None:
                slug = s
            chapters.append({
                "num": ch_num,
                "url": f"https://freewebnovel.com/novel/{s}/chapter-{ch_num}",
                "title": text
            })

    # Удаляем дубли, сортируем
    seen = set()
    unique = []
    for ch in sorted(chapters, key=lambda x: x["num"]):
        if ch["num"] not in seen:
            seen.add(ch["num"])
            unique.append(ch)

    # Метаданные книги
    title_meta = soup.find("meta", property="og:title")
    author_meta = soup.find("meta", property="og:novel:author")
    image_meta = soup.find("meta", property="og:image")

    book_title = title_meta.get("content", "Unknown") if title_meta else "Unknown"
    author = author_meta.get("content", "Unknown") if author_meta else "Unknown"
    cover_url = image_meta.get("content", "") if image_meta else ""

    return {
        "slug": slug,
        "title": book_title,
        "author": author,
        "cover_url": cover_url,
        "chapters": unique,
        "total_chapters": len(unique)
    }


def download_chapter(url):
    """Скачать текст одной главы с FreeWebNovel"""
    html = fetch_html(url, headers=CHAPTER_HEADERS)
    if not html:
        return None, None

    soup = BeautifulSoup(html, "html.parser")

    # Заголовок главы из мета-тега
    title_meta = soup.find("meta", property="og:novel:chapter_name")
    title = title_meta.get("content", "") if title_meta else ""

    # Текст из div id="article"
    article = soup.find("div", id="article")
    if not article:
        # Фолбэк — div class="txt"
        article = soup.find("div", class_="txt")

    if not article:
        print(f"    Не найден контент в {url}")
        return title, None

    # Очистить текст
    # Удалить заголовок главы из тела (он дублируется)
    text = article.get_text().strip()
    # Удалить строку заголовка из начала текста
    if title and text.startswith(title):
        text = text[len(title):].strip()

    return title, text


# === EPUB СБОРКА ===

def build_epub(book_title, author, chapters_data, cover_path=None, output_path=None):
    """Собрать EPUB из переведённых глав"""
    sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
    from ebooklib import epub

    book = epub.EpubBook()

    # Метаданные
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', book_title)
    book.set_identifier(f"ranobe-{safe_title}")
    book.set_title(book_title)
    book.set_language("ru")
    book.add_author(author)

    # Обложка
    if cover_path and os.path.exists(cover_path):
        with open(cover_path, "rb") as f:
            book.set_cover("cover.jpg", f.read())

    # CSS стиль
    style = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=b"""body { font-family: serif; line-height: 1.8; margin: 1em; }
h1 { font-size: 1.4em; border-bottom: 1px solid #ccc; margin-top: 2em; }
p { text-indent: 1.5em; margin: 0.5em 0; text-align: justify; }
.dialog { text-indent: 0; }"""
    )
    book.add_item(style)

    # Главы
    epub_chapters = []
    spine = ["nav"]

    for i, (ch_title, ch_text) in enumerate(chapters_data):
        if not ch_text:
            continue

        # HTML-экранирование
        safe_title = html_lib.escape(ch_title or f"Глава {i+1}")
        paragraphs = ch_text.split("\n")
        html_content = f"<h1>{safe_title}</h1>\n"
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            p_esc = html_lib.escape(p)
            # Диалоги (начинаются с « или —)
            if p_esc.startswith("&laquo;") or p_esc.startswith("&mdash;") or p_esc.startswith("—"):
                html_content += f'<p class="dialog">{p_esc}</p>\n'
            else:
                html_content += f"<p>{p_esc}</p>\n"

        chapter = epub.EpubHtml(
            title=ch_title or f"Глава {i+1}",
            file_name=f"chapter_{i+1:04d}.xhtml",
            lang="ru"
        )
        chapter.content = html_content
        chapter.add_item(style)

        book.add_item(chapter)
        epub_chapters.append(chapter)
        spine.append(chapter)

    # Оглавление
    book.toc = epub_chapters

    # Spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine

    # Запись
    out = output_path or f"output/{safe_title}.epub"
    os.makedirs(os.path.dirname(out) if os.path.dirname(out) else ".", exist_ok=True)
    epub.write_epub(out, book, {})
    print(f"\nEPUB создан: {out}")
    return out


# === ОСНОВНОЙ ПАЙПЛАЙН ===

def run_pipeline(book_url, start_ch, end_ch, output_format="epub", data_dir="data"):
    """Полный пайплайн: скачать → перевести → собрать"""

    # 1. Получить страницу книги
    print(f"1. Загружаю страницу книги: {book_url}")
    book_html = fetch_html(book_url)
    if not book_html:
        print("ОШИБКА: не удалось загрузить страницу книги")
        return None

    book_info = parse_book_page(book_html, book_url)
    print(f"   Книга: {book_info['title']}")
    print(f"   Автор: {book_info['author']}")
    print(f"   Глав найдено: {book_info['total_chapters']}")

    # 2. Фильтр по диапазону глав
    target_chapters = [ch for ch in book_info["chapters"] if start_ch <= ch["num"] <= end_ch]
    print(f"   Главы {start_ch}-{end_ch}: {len(target_chapters)} штук")

    if not target_chapters:
        print("ОШИБКА: нет глав в указанном диапазоне")
        return None

    # Создаём папки
    raw_dir = os.path.join(data_dir, "raw")
    translated_dir = os.path.join(data_dir, "translated")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(translated_dir, exist_ok=True)
    os.makedirs("output", exist_ok=True)

    # 3. Скачиваем главы
    print(f"\n2. Скачиваю главы {start_ch}-{end_ch}...")
    chapters_raw = {}  # num -> {"title": ..., "text": ...}

    for i, ch in enumerate(target_chapters):
        num = ch["num"]
        raw_file = os.path.join(raw_dir, f"chapter_{num:04d}.txt")

        # Резьюме: если файл уже есть — пропускаем
        if os.path.exists(raw_file):
            with open(raw_file, "r", encoding="utf-8") as f:
                saved = json.loads(f.read())
            chapters_raw[num] = saved
            print(f"   [{i+1}/{len(target_chapters)}] Глава {num}: уже скачана")
            continue

        print(f"   [{i+1}/{len(target_chapters)}] Глава {num}: скачиваю...", end=" ", flush=True)
        title, text = download_chapter(ch["url"])

        if not text:
            print(f"ОШИБКА скачивания")
            continue

        chapters_raw[num] = {"title": title, "text": text}
        # Сохраняем сырой текст
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump({"title": title, "text": text}, f, ensure_ascii=False)
        print(f"OK ({len(text)} символов)")

        time.sleep(DELAY_DOWNLOAD)

    # 4. Переводим главы
    print(f"\n3. Перевожу главы на русский...")
    chapters_translated = {}  # num -> {"title": ..., "text": ...}

    sorted_nums = sorted(chapters_raw.keys())

    for i, num in enumerate(sorted_nums):
        trans_file = os.path.join(translated_dir, f"chapter_{num:04d}.txt")

        # Резьюме: если перевод уже есть — пропускаем
        if os.path.exists(trans_file):
            with open(trans_file, "r", encoding="utf-8") as f:
                saved = json.loads(f.read())
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
            title_ru = title_en  # Фолбэк

        # Разбиваем на чанки и переводим
        chunks = chunk_text(text_en)
        translated_parts = []

        for j, chunk in enumerate(chunks):
            result = translate_text(chunk)
            if result:
                translated_parts.append(result)
            else:
                translated_parts.append(f"[ОШИБКА ПЕРЕВОДА]\n{chunk}")
                print(f"chunk error!", end=" ", flush=True)

            time.sleep(DELAY_TRANSLATE)

        text_ru = "\n\n".join(translated_parts)
        chapters_translated[num] = {"title": title_ru, "text": text_ru}

        # Сохраняем перевод
        with open(trans_file, "w", encoding="utf-8") as f:
            json.dump({"title": title_ru, "text": text_ru}, f, ensure_ascii=False)

        print(f"OK ({len(text_ru)} символов)")

    # 5. Собираем EPUB
    print(f"\n4. Собираю EPUB...")

    # Сортируем по номеру главы
    epub_data = []
    for num in sorted(chapters_translated.keys()):
        ch = chapters_translated[num]
        epub_data.append((ch["title"], ch["text"]))

    book_title = book_info["title"]
    if start_ch or end_ch < book_info["total_chapters"]:
        book_title += f" (Главы {start_ch}-{end_ch})"

    # Скачиваем обложку
    cover_path = None
    if book_info.get("cover_url"):
        try:
            cover_data = fetch_html_raw(book_info["cover_url"])
            if cover_data:
                cover_path = os.path.join(data_dir, "cover.jpg")
                with open(cover_path, "wb") as f:
                    f.write(cover_data)
        except:
            pass

    output_file = f"output/{re.sub(r'[\\\\/:*?\"<>|]', '_', book_info['title'])}_ch{start_ch}-{end_ch}.epub"

    result_path = build_epub(
        book_title=book_title,
        author=book_info["author"],
        chapters_data=epub_data,
        cover_path=cover_path,
        output_path=output_file
    )

    print(f"\n=== ГОТОВО ===")
    print(f"Файл: {result_path}")
    return result_path


def fetch_html_raw(url, retries=2):
    """Получить сырые байты (для обложки)"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=BOOK_PAGE_HEADERS)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.read()
        except:
            time.sleep(2)
    return None


# === CLI ===

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ranobe Pipeline: Скачать + Перевести + EPUB")
    parser.add_argument("--url", required=True, help="URL книги на FreeWebNovel")
    parser.add_argument("--start", type=int, default=1, help="Начальная глава")
    parser.add_argument("--end", type=int, default=9999, help="Конечная глава")
    parser.add_argument("--output", default="epub", choices=["epub", "txt"], help="Формат вывода")
    parser.add_argument("--data-dir", default="data", help="Папка для промежуточных данных")

    args = parser.parse_args()

    result = run_pipeline(
        book_url=args.url,
        start_ch=args.start,
        end_ch=args.end,
        output_format=args.output,
        data_dir=args.data_dir
    )
