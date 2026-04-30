import os
import json
import time
import re
import html as html_lib
from pathlib import Path
from dotenv import load_dotenv

# Загружаем ключи из .env
load_dotenv()

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY")
TRANSLATE_MODEL = os.getenv("TRANSLATE_MODEL", "accounts/fireworks/models/llama-v3p3-70b-instruct")
FIREWORKS_URL = "https://api.fireworks.ai/inference/v1/chat/completions"

# Директории
SOURCE_DIR = Path(r"d:\2. Areas\Ranobe\execution\downloads\1206913-this-dungeon-grew-mushrooms")
DATA_DIR = Path("data/translated/mushrooms")
OUTPUT_DIR = Path("output")

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TRANSLATE_SYSTEM_PROMPT = """Ты профессиональный переводчик художественной литературы с английского на русский.

ПРАВИЛА:
1. Переводи литературно, сохраняя стиль и атмосферу оригинала
2. НЕ добавляй пояснений, комментариев или примечаний
3. НЕ пропускай предложения — переводи всё
4. Диалоги в кавычках-ёлочках «»
5. Имена собственные транслитерируй (Линь Цзюнь, не Lin Jun)
6. Термины-реалии: оставляй оригинал в скобках при первом упоминании
7. Возвращай ТОЛЬКО перевод, ничего лишнего
8. Сохраняй структуру абзацев оригинала"""

def translate_text(text):
    import urllib.request
    
    payload = {
        "model": TRANSLATE_MODEL,
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
            "Authorization": f"Bearer {FIREWORKS_API_KEY}",
            "Content-Type": "application/json"
        }
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"    Ошибка API (попытка {attempt+1}): {e}")
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

def build_epub(chapters_data, book_title="This Dungeon Grew Mushrooms"):
    from ebooklib import epub
    
    book = epub.EpubBook()
    book.set_identifier("mushrooms-3-44")
    book.set_title(book_title)
    book.set_language("ru")
    book.add_author("Unknown")

    style = epub.EpubItem(
        uid="style",
        file_name="style/default.css",
        media_type="text/css",
        content=b"""body { font-family: serif; line-height: 1.6; margin: 1em; }
h1 { font-size: 1.5em; text-align: center; margin-bottom: 1em; }
p { text-indent: 1.5em; margin: 0.5em 0; }"""
    )
    book.add_item(style)

    epub_chapters = []
    spine = ["nav"]

    for i, (title, text) in enumerate(chapters_data):
        c = epub.EpubHtml(title=title, file_name=f"chap_{i+1:03d}.xhtml", lang="ru")
        paragraphs = text.split("\n")
        html_content = f"<h1>{html_lib.escape(title)}</h1>"
        for p in paragraphs:
            if p.strip():
                html_content += f"<p>{html_lib.escape(p.strip())}</p>"
        
        c.content = html_content
        c.add_item(style)
        book.add_item(c)
        epub_chapters.append(c)
        spine.append(c)

    book.toc = epub_chapters
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = spine
    
    out_path = OUTPUT_DIR / "This_Dungeon_Grew_Mushrooms_3-44.epub"
    epub.write_epub(out_path, book, {})
    print(f"EPUB создан: {out_path}")

def main():
    files = sorted(list(SOURCE_DIR.glob("chapter_*.txt")))
    
    # Отфильтруем главы 3-44
    target_files = []
    for f in files:
        match = re.search(r"chapter_(\d+)_", f.name)
        if match:
            num = int(match.group(1))
            if 3 <= num <= 44:
                target_files.append(f)

    print(f"Найдено {len(target_files)} глав для перевода.")
    
    translated_data = []
    
    for i, f in enumerate(target_files):
        num_match = re.search(r"chapter_(\d+)_", f.name)
        num = int(num_match.group(1))
        
        save_path = DATA_DIR / f"chapter_{num:04d}.json"
        
        if save_path.exists():
            with open(save_path, "r", encoding="utf-8") as sf:
                data = json.load(sf)
                translated_data.append((data["title"], data["text"]))
                print(f"[{i+1}/{len(target_files)}] Глава {num}: уже переведена.")
                continue

        print(f"[{i+1}/{len(target_files)}] Глава {num}: перевод...", end="", flush=True)
        
        with open(f, "r", encoding="utf-8") as src:
            content = src.read()
        
        # Первая строка обычно "# Chapter N"
        lines = content.split("\n")
        orig_title = lines[0].replace("#", "").strip()
        body = "\n".join(lines[1:]).strip()
        
        title_ru = translate_text(orig_title) or f"Глава {num}"
        
        chunks = chunk_text(body)
        translated_chunks = []
        for chunk in chunks:
            res = translate_text(chunk)
            if res:
                translated_chunks.append(res)
            else:
                translated_chunks.append(f"[Ошибка перевода]\n{chunk}")
            time.sleep(1)
        
        full_text_ru = "\n\n".join(translated_chunks)
        
        with open(save_path, "w", encoding="utf-8") as sf:
            json.dump({"title": title_ru, "text": full_text_ru}, sf, ensure_ascii=False)
        
        translated_data.append((title_ru, full_text_ru))
        print(f" OK ({len(full_text_ru)} симв.)")
        time.sleep(1)

    print("\nСборка EPUB...")
    build_epub(translated_data)

if __name__ == "__main__":
    main()
