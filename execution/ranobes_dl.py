#!/usr/bin/env python3
"""
Ranobes.com downloader + translator
На основе логики из https://github.com/Taraflex/ranobe-ebook-loader

Скачивает все главы ранобэ с ranobes.com в TXT/HTML
Опционально переводит через DeepL или Fireworks API

Использование:
  python3 ranobes_dl.py download URL          — скачать книгу в TXT + HTML
  python3 ranobes_dl.py translate FILE        — перевести TXT на русский
  python3 ranobes_dl.py full URL              — скачать + перевести

  URL = ссылка на страницу книги на ranobes.com
  Пример: https://ranobes.com/ranobe/604059-chronicles-of-primitive-civilizations-growth.html
"""

import sys
import re
import json
import time
import html as html_lib
import urllib.request
import urllib.parse
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Нужен beautifulsoup4: pip install beautifulsoup4")
    sys.exit(1)


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
}

OUTPUT_DIR = Path("/home/hermes/ranobes_output")
DELAY = 1.5  # задержка между запросами (секунды)


def fetch(url, retries=3):
    """Получить HTML страницы"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            print(f"    Попытка {attempt+1}/{retries}: {e}")
            time.sleep(3)
    return None


def get_book_alias(html_content):
    """Извлечь alias книги из HTML страницы книги (логика из Ranobes.ts)"""
    soup = BeautifulSoup(html_content, "html.parser")
    # По логике оригинала: .r-fullstory-chapters-foot > a:nth-child(3n)
    foot = soup.find(class_="r-fullstory-chapters-foot")
    if foot:
        links = foot.find_all("a")
        for a in links:
            href = a.get("href", "")
            # Ищем ссылку вида /chapters/ALIAS/
            m = re.search(r'/chapters/([^/]+)/', href)
            if m:
                return m.group(1)
    
    # Фолбэк — из ссылок на главы
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r'ranobes\.com/chapters/([^/]+)/', href)
        if m:
            return m.group(1)
    
    return None


def get_book_info(html_content):
    """Получить метаданные книги"""
    soup = BeautifulSoup(html_content, "html.parser")
    
    info = {}
    
    # Название
    h1 = soup.find("h1")
    if h1:
        title_parts = h1.get_text().split("•")
        info['title'] = title_parts[0].strip()
        info['subtitle'] = title_parts[1].strip() if len(title_parts) > 1 else ""
    else:
        info['title'] = "Unknown"
        info['subtitle'] = ""
    
    # Описание
    desc = soup.find(itemprop="description")
    info['description'] = desc.get_text() if desc else ""
    
    # Авторы
    authors = soup.find_all(itemprop="creator")
    info['authors'] = [a.get_text().strip() for a in authors] if authors else []
    
    # Жанры
    genres = soup.find_all(itemprop="genre")
    info['genres'] = [g.get_text().strip() for g in genres] if genres else []
    
    # Обложка
    img = soup.find(itemprop="image")
    info['cover_url'] = img['href'] if img and img.get('href') else ""
    
    return info


def get_chapter_list(book_alias):
    """Получить список всех глав книги (пагинация по 25 на страницу)"""
    all_chapters = []
    page = 1
    
    while True:
        if page == 1:
            url = f"https://ranobes.com/chapters/{book_alias}/"
        else:
            url = f"https://ranobes.com/chapters/{book_alias}/page/{page}/"
        
        print(f"  Загружаю список глав, страница {page}...")
        html = fetch(url)
        if not html:
            break
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Ищем ссылки на главы в cat_block
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if f"/chapters/{book_alias}/" in href and href.endswith(".html"):
                title_el = a.find(class_="title")
                title = title_el.get_text().strip() if title_el else a.get_text().strip()
                # Убираем дату из заголовка
                title = re.sub(r'\d+ \w+ \d+ в \d+:\d+', '', title).strip()
                found.append({"url": href, "title": title})
        
        if not found:
            break
        
        all_chapters.extend(found)
        page += 1
        time.sleep(DELAY)
    
    # Реверсируем (на сайте главы от новых к старым)
    all_chapters.reverse()
    
    return all_chapters


def get_chapter_text(url):
    """Получить текст главы (учитывая разбивку на подстраницы)"""
    all_text = ""
    title = ""
    pages = [url]
    visited = set()
    
    for page_url in pages:
        if page_url in visited:
            continue
        visited.add(page_url)
        
        html = fetch(page_url)
        if not html:
            break
        
        soup = BeautifulSoup(html, "html.parser")
        
        # Текст из #arrticle (опечатка на сайте, так и есть)
        article = soup.find(id="arrticle")
        if not article:
            # Фолбэк
            article = soup.find(class_="text")
        if not article:
            break
        
        all_text += article.get_text(separator="\n").strip() + "\n"
        
        # Название главы (только первое)
        if not title:
            h1 = soup.find("h1", class_="title")
            if h1:
                raw = h1.get_text().strip()
                # Убираем название книги из конца
                title = raw.split("|")[0].strip() if "|" in raw else raw
        
        # Проверяем подстраницы главы (.splitnewsnavigation)
        nav = soup.find(class_="splitnewsnavigation")
        if nav:
            for a in nav.find_all("a", href=True):
                href = a["href"]
                if href not in visited and href not in pages:
                    pages.append(href)
        
        time.sleep(DELAY)
    
    return title, all_text.strip()


def download_book(book_url):
    """Скачать книгу целиком"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    print(f"Загружаю страницу книги: {book_url}")
    html = fetch(book_url)
    if not html:
        print("Не удалось загрузить страницу!")
        return None
    
    book_alias = get_book_alias(html)
    if not book_alias:
        print("Не удалось определить alias книги!")
        return None
    
    info = get_book_info(html)
    print(f"Книга: {info['title']}")
    print(f"Alias: {book_alias}")
    
    # Получаем список глав
    chapters = get_chapter_list(book_alias)
    print(f"Найдено глав: {len(chapters)}")
    
    if not chapters:
        print("Глав не найдено!")
        return None
    
    safe_name = re.sub(r'[^\w\s-]', '', info['title']).strip().replace(' ', '_')[:60]
    
    # Скачиваем каждую главу
    all_data = []
    for i, ch in enumerate(chapters, 1):
        print(f"  [{i}/{len(chapters)}] {ch['title'][:60]}...")
        title, text = get_chapter_text(ch['url'])
        if text:
            all_data.append({"title": title or ch['title'], "text": text, "url": ch['url']})
        else:
            print(f"    ПУСТАЯ ГЛАВА, пропускаю")
    
    # Сохраняем TXT
    txt_file = OUTPUT_DIR / f"{safe_name}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        # Заголовок с инфо
        f.write(f"# {info['title']}\n")
        if info['subtitle']:
            f.write(f"# {info['subtitle']}\n")
        if info['authors']:
            f.write(f"# Авторы: {', '.join(info['authors'])}\n")
        f.write(f"# Глав: {len(all_data)}\n")
        f.write(f"# Источник: {book_url}\n\n")
        
        for ch in all_data:
            f.write(f"=== {ch['title']} ===\n\n")
            f.write(ch['text'])
            f.write("\n\n\n")
    
    # Сохраняем HTML
    html_file = OUTPUT_DIR / f"{safe_name}.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>{html_lib.escape(info['title'])}</title>
<style>
body {{ font-family: Georgia, serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.8; background: #1a1a2e; color: #e0e0e0; }}
h1 {{ color: #e0e0e0; border-bottom: 1px solid #444; padding-bottom: 8px; margin-top: 50px; font-size: 1.3em; }}
h0 {{ text-align: center; font-size: 1.8em; border: none; }}
p {{ text-indent: 1.5em; margin: 0.4em 0; }}
a {{ color: #8cb4ff; }}
</style>
</head>
<body>
<h0>{html_lib.escape(info['title'])}</h0>
<p style="text-align:center; color:gray;">{len(all_data)} глав | Источник: ranobes.com</p>
<hr>
""")
        for ch in all_data:
            f.write(f'<h1>{html_lib.escape(ch["title"])}</h1>\n')
            for para in ch["text"].split("\n"):
                para = para.strip()
                if para:
                    f.write(f'<p>{html_lib.escape(para)}</p>\n')
            f.write(f'<!-- {ch["url"]} -->\n\n')
        f.write("</body></html>")
    
    print(f"\nГотово! Скачано {len(all_data)} глав")
    print(f"TXT:  {txt_file}")
    print(f"HTML: {html_file}")
    return txt_file


# === ПЕРЕВОД ===

def translate_deepl(text, api_key, target_lang="RU"):
    """Перевод через DeepL API Free"""
    url = "https://api-free.deepl.com/v2/translate"
    data = urllib.parse.urlencode({
        "auth_key": api_key,
        "text": text,
        "target_lang": target_lang,
    }).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
        return result["translations"][0]["text"]


def translate_fireworks(text, api_key, model="accounts/fireworks/models/llama-v3p3-70b-instruct"):
    """Перевод через Fireworks API"""
    url = "https://api.fireworks.ai/inference/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты профессиональный переводчик художественной литературы. Переводи литературно, сохраняя стиль. Возвращай ТОЛЬКО перевод."},
            {"role": "user", "content": f"Переведи на русский:\n\n{text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    req = urllib.request.Request(url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode())
        return result["choices"][0]["message"]["content"]


def translate_file(input_path, api_type="fireworks", api_key="fw_5CAe1FD9DRaBZpWDDTZzhN"):
    """Перевести TXT файл"""
    input_path = Path(input_path)
    text = input_path.read_text(encoding="utf-8")
    
    # Разбиваем на главы
    parts = re.split(r'(=== .+? ===)', text)
    
    output_path = input_path.with_name(input_path.stem + "_ru.txt")
    chunk_size = 4000
    
    with open(output_path, "w", encoding="utf-8") as f:
        i = 0
        while i < len(parts):
            if parts[i].startswith("==="):
                f.write(parts[i] + "\n\n")
                i += 1
                if i < len(parts):
                    body = parts[i].strip()
                    if body:
                        # Разбиваем на чанки
                        paragraphs = body.split("\n\n")
                        chunks = []
                        current = ""
                        for p in paragraphs:
                            if len(current) + len(p) > chunk_size and current:
                                chunks.append(current.strip())
                                current = p
                            else:
                                current += "\n\n" + p if current else p
                        if current.strip():
                            chunks.append(current.strip())
                        
                        for ci, chunk in enumerate(chunks):
                            print(f"    Чанк {ci+1}/{len(chunks)} ({len(chunk)} символов)...")
                            try:
                                if api_type == "deepl":
                                    translated = translate_deepl(chunk, api_key)
                                else:
                                    translated = translate_fireworks(chunk, api_key)
                                f.write(translated + "\n\n")
                            except Exception as e:
                                print(f"    Ошибка: {e}")
                                f.write(f"[ОШИБКА: {e}]\n\n{chunk}\n\n")
                            time.sleep(1)
                        f.write("\n")
                    i += 1
            else:
                i += 1
    
    print(f"\nПеревод сохранён: {output_path}")
    return output_path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "download":
        download_book(sys.argv[2])
    elif command == "translate":
        api = sys.argv[3] if len(sys.argv) > 3 else "fireworks"
        translate_file(sys.argv[2], api)
    elif command == "full":
        txt = download_book(sys.argv[2])
        if txt:
            translate_file(txt)
    else:
        print(f"Неизвестная команда: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
