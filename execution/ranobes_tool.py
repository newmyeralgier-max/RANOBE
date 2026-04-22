#!/usr/bin/env python3
"""
Ranobes.net scraper + translator
Скачивает все главы ранобэ с ranobes.net и переводит на русский через DeepL/Fireworks API

Использование:
  python3 ranobes_tool.py scrape URL              — скачать всё в HTML
  python3 ranobes_tool.py translate FILE           — перевести HTML файл на русский
  python3 ranobes_tool.py full URL                 — скачать + перевести
  python3 ranobes_tool.py chapters URL             — список глав
  
  URL = ссылка на любую главу ранобэ
"""

import sys
import re
import time
import json
import html as html_lib
import urllib.request
import urllib.parse
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Установи: pip install beautifulsoup4")
    sys.exit(1)


# === СКАЧИВАНИЕ ===

def fetch_page(url, retries=3):
    """Получить HTML страницы через urllib (без зависимостей)"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            })
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.read().decode('utf-8', errors='replace')
        except Exception as e:
            print(f"  Попытка {attempt+1}/{retries} ошибка: {e}")
            time.sleep(2)
    return None


def get_story_name(soup):
    """Получить название ранобэ из навигации"""
    nav = soup.find(id="dle-speedbar")
    if nav:
        links = nav.find_all("a")
        if len(links) >= 2:
            return links[1].text.strip()
    # Фолбэк — из title
    title = soup.find("title")
    if title:
        name = title.text.split("|")[-1].strip()
        return name
    return "unknown"


def get_chapter_title(soup, story_name=""):
    """Получить название главы"""
    # Ищем h1 с классом title
    h1 = soup.find("h1", class_="title")
    if h1:
        title = h1.text.strip()
        # Убираем название ранобэ из конца
        if story_name and title.endswith(f"| {story_name}"):
            title = title[:-len(f"| {story_name}")].strip()
        return title
    # Фолбэк
    h1 = soup.find("h1")
    if h1:
        return h1.text.strip()
    return "Unknown Chapter"


def get_chapter_text(soup):
    """Получить текст главы из arrticle (да, опечатка на сайте)"""
    article = soup.find(id="arrticle")
    if not article:
        # Фолбэк — ищем div с классом text внутри dle-content
        content = soup.find(id="dle-content")
        if content:
            article = content.find("div", class_="text")
    if not article:
        return None
    
    paragraphs = article.find_all("p")
    if not paragraphs:
        # Если нет <p>, берём весь текст
        return article.get_text(separator="\n").strip()
    
    parts = []
    for p in paragraphs:
        text = p.get_text().strip()
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def get_next_url(soup):
    """Найти ссылку на следующую главу"""
    # Ищем все ссылки на главы этого ранобэ
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Кнопка Next
        if "Next" in a.get_text() or "Вперед" in a.get_text() or "Далее" in a.get_text():
            return href
    
    # Фолбэк — ищем ссылку с большим номером главы
    # Извлекаем текущий номер из URL
    return None


def get_nav_urls(soup, current_url):
    """Получить ссылки навигации (prev, next, chapters_list)"""
    prev_url = None
    next_url = None
    chapters_url = None
    
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text().strip()
        
        if "Back" in text or "Назад" in text:
            if "horror-game-developer" in href or "/chapters/" not in href:
                prev_url = href
        elif "Next" in text or "Вперед" in text or "Далее" in text:
            if "horror-game-developer" in href:
                next_url = href
        elif "/chapters/" in href:
            chapters_url = href
    
    return prev_url, next_url, chapters_url


def get_chapters_list(story_id):
    """Получить список всех глав через API ranobes"""
    chapters = []
    page = 1
    while True:
        url = f"https://ranobes.net/chapters/{story_id}/?offset={500*(page-1)}"
        print(f"  Загружаю список глав, страница {page}...")
        html = fetch_page(url)
        if not html:
            break
        soup = BeautifulSoup(html, "html.parser")
        
        # Ищем ссылки на главы
        found = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            match = re.search(r'/horror-game-developer-\d+/(\d+)\.html', href)
            if match:
                title = a.get_text().strip()
                found.append({"url": href if href.startswith("http") else f"https://ranobes.net{href}", "title": title, "id": match.group(1)})
        
        if not found:
            break
        chapters.extend(found)
        page += 1
        time.sleep(1)
    
    return chapters


def scrape_story(start_url, output_dir="/home/hermes/ranobes_output"):
    """Скачать все главы ранобэ начиная с указанной"""
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    
    # Получаем первую страницу
    print(f"Загружаю начальную страницу: {start_url}")
    html = fetch_page(start_url)
    if not html:
        print("Не удалось загрузить страницу!")
        return None
    
    soup = BeautifulSoup(html, "html.parser")
    story_name = get_story_name(soup)
    safe_name = re.sub(r'[^\w\s-]', '', story_name).strip().replace(' ', '_')
    
    print(f"Ранобэ: {story_name}")
    
    # Пытаемся получить список глав
    story_id_match = re.search(r'(\d+)-horror-game-developer', start_url) or re.search(r'horr-game-developer-(\d+)', start_url)
    story_id = story_id_match.group(1) if story_id_match else None
    
    if story_id:
        chapters = get_chapters_list(story_id)
        if chapters:
            print(f"Найдено глав: {len(chapters)}")
        else:
            print("Список глав не найден, буду идти по Next-кнопкам")
            chapters = None
    else:
        chapters = None
    
    output_file = Path(output_dir) / f"{safe_name}.html"
    all_chapters = []
    
    if chapters:
        # Скачиваем по списку
        for i, ch in enumerate(chapters, 1):
            print(f"  Глава {i}/{len(chapters)}: {ch['title'][:60]}...")
            html = fetch_page(ch['url'])
            if not html:
                print(f"    ОШИБКА: не загружена")
                continue
            soup = BeautifulSoup(html, "html.parser")
            title = get_chapter_title(soup, story_name)
            text = get_chapter_text(soup)
            if text:
                all_chapters.append({"title": title, "text": text, "url": ch['url']})
            time.sleep(1.5)  # Не DDOSим сайт
    else:
        # Идём по Next-кнопкам
        url = start_url
        ch_num = 0
        while url:
            ch_num += 1
            print(f"  Глава {ch_num}: {url}")
            html = fetch_page(url)
            if not html:
                print(f"    ОШИБКА: не загружена, стоп")
                break
            soup = BeautifulSoup(html, "html.parser")
            title = get_chapter_title(soup, story_name)
            text = get_chapter_text(soup)
            if text:
                all_chapters.append({"title": title, "text": text, "url": url})
            else:
                print(f"    Нет текста, стоп")
                break
            
            _, next_url, _ = get_nav_urls(soup, url)
            url = next_url
            time.sleep(1.5)
    
    # Сохраняем HTML
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{story_name}</title>
<style>
body {{ font-family: Georgia, serif; max-width: 800px; margin: 0 auto; padding: 20px; line-height: 1.7; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 10px; margin-top: 60px; font-size: 1.4em; }}
.chapter-text {{ margin-bottom: 40px; }}
p {{ text-indent: 1.5em; margin: 0.5em 0; }}
</style>
</head>
<body>
<h1 style="text-align:center; font-size:2em; border:none;">{story_name}</h1>
<p style="text-align:center; color:gray;">Скачано с ranobes.net | {len(all_chapters)} глав</p>
<hr>
""")
        for ch in all_chapters:
            f.write(f'<h1 class="chapter">{ch["title"]}</h1>\n')
            f.write(f'<div class="chapter-text">')
            for para in ch["text"].split("\n\n"):
                f.write(f'<p>{html_lib.escape(para)}</p>\n')
            f.write(f'</div>\n')
            f.write(f'<!-- {ch["url"]} -->\n\n')
        f.write("</body></html>")
    
    # Также сохраняем чистый текст
    txt_file = Path(output_dir) / f"{safe_name}.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        for ch in all_chapters:
            f.write(f'=== {ch["title"]} ===\n\n')
            f.write(ch["text"])
            f.write("\n\n\n")
    
    print(f"\nГотово! Скачано {len(all_chapters)} глав")
    print(f"HTML: {output_file}")
    print(f"TXT:  {txt_file}")
    return output_file, txt_file


# === ПЕРЕВОД ===

def translate_deepl(text, api_key, target_lang="RU"):
    """Перевод через DeepL API Free"""
    url = "https://api-free.deepl.com/v2/translate"
    data = urllib.parse.urlencode({
        "auth_key": api_key,
        "text": text,
        "target_lang": target_lang,
        "source_lang": "EN",
        "split_sentences": "nonewlines",
    }).encode()
    
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode())
        return result["translations"][0]["text"]


def translate_fireworks(text, api_key, model="accounts/fireworks/models/llama-v3p3-70b-instruct"):
    """Перевод через Fireworks API (OpenAI-совместимый)"""
    url = "https://api.fireworks.ai/inference/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Ты профессиональный переводчик художественной литературы с английского на русский. Переводи литературно, сохраняя стиль и атмосферу оригинала. Не добавляй пояснений. Возвращай только перевод."},
            {"role": "user", "content": f"Переведи на русский:\n\n{text}"}
        ],
        "temperature": 0.3,
        "max_tokens": 4096,
    }
    
    req = urllib.request.Request(url, 
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        })
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode())
        return result["choices"][0]["message"]["content"]


def translate_text_chunks(text, api_type="deepl", api_key="", chunk_size=4000):
    """Разбить текст на чанки и перевести каждый"""
    # Разбиваем по абзацам, не разрывая предложения
    paragraphs = text.split("\n\n")
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
    
    translated = []
    for i, chunk in enumerate(chunks):
        print(f"  Чанк {i+1}/{len(chunks)} ({len(chunk)} символов)...")
        try:
            if api_type == "deepl":
                result = translate_deepl(chunk, api_key)
            else:
                result = translate_fireworks(chunk, api_key)
            translated.append(result)
        except Exception as e:
            print(f"    Ошибка: {e}")
            translated.append(f"[ОШИБКА ПЕРЕВОДА: {e}]\n\n{chunk}")
        time.sleep(1)
    
    return "\n\n".join(translated)


def translate_file(input_path, api_type="fireworks", api_key="fw_5CAe1FD9DRaBZpWDDTZzhN"):
    """Перевести TXT файл на русский"""
    input_path = Path(input_path)
    text = input_path.read_text(encoding="utf-8")
    
    # Разбиваем на главы
    chapters = re.split(r'(=== .+? ===)', text)
    
    output_path = input_path.with_suffix('.ru.txt')
    
    with open(output_path, "w", encoding="utf-8") as f:
        i = 0
        while i < len(chapters):
            if chapters[i].startswith("==="):
                # Заголовок главы — переводим
                header = chapters[i].strip("= ").strip()
                print(f"Перевод: {header[:60]}...")
                f.write(chapters[i] + "\n\n")
                i += 1
                # Текст главы
                if i < len(chapters):
                    body = chapters[i].strip()
                    if body:
                        translated = translate_text_chunks(body, api_type, api_key)
                        f.write(translated + "\n\n\n")
                    i += 1
            else:
                i += 1
    
    print(f"\nПеревод сохранён: {output_path}")
    return output_path


# === ГЛАВНАЯ ===

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "scrape":
        url = sys.argv[2]
        scrape_story(url)
    
    elif command == "translate":
        filepath = sys.argv[2]
        api = sys.argv[3] if len(sys.argv) > 3 else "fireworks"
        translate_file(filepath, api)
    
    elif command == "full":
        url = sys.argv[2]
        result = scrape_story(url)
        if result:
            txt_file = result[1]
            translate_file(txt_file)
    
    elif command == "chapters":
        url = sys.argv[2]
        html = fetch_page(url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            story_id = re.search(r'(\d+)-horror-game-developer', url)
            if story_id:
                chapters = get_chapters_list(story_id.group(1))
                print(f"\nНайдено {len(chapters)} глав:")
                for i, ch in enumerate(chapters, 1):
                    print(f"  {i}. {ch['title'][:80]}")
            else:
                print("Не удалось определить ID ранобэ")
    
    else:
        print(f"Неизвестная команда: {command}")
        print(__doc__)


if __name__ == "__main__":
    main()
