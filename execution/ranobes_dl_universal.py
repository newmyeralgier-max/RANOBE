#!/usr/bin/env python3
"""
Универсальный скачиватель ранобэ с ranobes.net
Использование:
  python3 ranobes_dl_universal.py NOVEL_ID [START_CH] [END_CH]
  python3 ranobes_dl_universal.py 1207051           — скачать все главы
  python3 ranobes_dl_universal.py 1207051 142 200   — главы 142-200

Результат: папка raw/ с файлами chapter_NNNN.txt (JSON {"title":"...","text":"..."})
"""

import sys, os, re, json, time, subprocess

RAW_DIR = None


def fetch(url, retries=3):
    """Скачать страницу через curl (надёжнее urllib)."""
    for i in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-m", "20",
                 "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                 url],
                capture_output=True, timeout=25, encoding='utf-8', errors='replace'
            )
            if r.returncode == 0 and len(r.stdout) > 500:
                return r.stdout
        except Exception as e:
            print(f"  curl retry {i+1}: {e}")
            time.sleep(2)
    return None


def get_chapter_list(novel_id):
    """Собрать список всех глав через __DATA__ JSON."""
    all_ch = []
    page = 1
    seen_ids = set()

    while True:
        url = f"https://ranobes.net/chapters/{novel_id}/page/{page}/"
        print(f"  Стр {page}...", end=" ", flush=True)
        html = fetch(url)
        if not html:
            print("ошибка загрузки")
            break

        m = re.search(r'window\.__DATA__\s*=\s*(\{.+?\})\s*;?\s*<', html, re.DOTALL)
        if not m:
            print("нет __DATA__")
            break

        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            print("JSON ошибка")
            break

        chapters = data.get("chapters", [])
        if not chapters:
            print("пусто")
            break

        new = 0
        for ch in chapters:
            ch_id = ch.get("id", "")
            if ch_id in seen_ids:
                continue
            seen_ids.add(ch_id)
            title = ch.get("title", "")
            link = ch.get("link", "")
            num_m = re.search(r'Chapter\s+(\d+)', title)
            if num_m:
                all_ch.append({
                    "num": int(num_m.group(1)),
                    "title": title,
                    "link": link,
                })
                new += 1

        if new == 0:
            print("дубликаты, стоп")
            break

        nums = [c["num"] for c in all_ch[-new:]]
        print(f"ch {min(nums)}-{max(nums)} (+{new})")
        page += 1
        if page > 30:
            break
        time.sleep(0.5)

    all_ch.sort(key=lambda x: x["num"])
    return all_ch


def download_chapter(url):
    """Скачать текст одной главы."""
    html = fetch(url)
    if not html:
        return None

    # Заголовок из <h1 class="title"> или из текста
    title = "Unknown"
    title_m = re.search(r'<h1[^>]*class="title"[^>]*>(.*?)</h1>', html, re.DOTALL)
    if title_m:
        title = re.sub(r'<[^>]+>', '', title_m.group(1)).strip()
    else:
        title_m2 = re.search(r'Chapter\s+\d+:[^<\n]+', html)
        if title_m2:
            title = title_m2.group(0).strip()

    # Текст: <div class="text" id="arrticle">
    idx = html.find('id="arrticle"')
    if idx < 0:
        idx = html.find("id='arrticle'")
    if idx < 0:
        return {"title": title, "text": ""}

    start = html.rfind('<div', 0, idx)
    chunk = html[start:]

    paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', chunk, re.DOTALL)
    text_parts = []
    for p in paragraphs:
        clean = re.sub(r'<[^>]+>', '', p).strip()
        if clean:
            text_parts.append(clean)

    text = '\n\n'.join(text_parts)
    return {"title": title, "text": text}


def main():
    global RAW_DIR

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    novel_id = sys.argv[1]
    start_ch = int(sys.argv[2]) if len(sys.argv) > 2 else None
    end_ch = int(sys.argv[3]) if len(sys.argv) > 3 else None

    # Папка проекта — ищем data_*/raw/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(script_dir)

    for subdir in ["data_infinite", "data", ""]:
        d = os.path.join(project_dir, subdir, "raw") if subdir else os.path.join(project_dir, "raw")
        if os.path.isdir(d):
            RAW_DIR = d
            break

    if not RAW_DIR:
        RAW_DIR = os.path.join(project_dir, "raw")
        os.makedirs(RAW_DIR, exist_ok=True)

    print(f"Novel ID: {novel_id}")
    print(f"Output: {RAW_DIR}")
    if start_ch and end_ch:
        print(f"Range: {start_ch}-{end_ch}")
    print()

    # 1. Список глав
    print("=== Собираю список глав ===")
    all_ch = get_chapter_list(novel_id)
    print(f"Всего: {len(all_ch)}")

    if not all_ch:
        print("Главы не найдены!")
        sys.exit(1)

    # Фильтр
    if start_ch and end_ch:
        target = [c for c in all_ch if start_ch <= c["num"] <= end_ch]
    else:
        target = all_ch
    print(f"В диапазоне: {len(target)}")

    # Убираем скачанные
    already = set()
    for f in os.listdir(RAW_DIR):
        m = re.match(r'chapter_(\d+)\.txt', f)
        if m:
            already.add(int(m.group(1)))

    to_download = [c for c in target if c["num"] not in already]
    print(f"Уже есть: {len(target) - len(to_download)}, качать: {len(to_download)}")

    if not to_download:
        print("Всё скачано!")
        return

    # 2. Скачивание
    print(f"\n=== Скачиваю {len(to_download)} глав ===")
    ok = 0
    fail = 0
    for i, ch in enumerate(to_download, 1):
        num = ch["num"]
        print(f"  [{i}/{len(to_download)}] Ch {num}: {ch['title'][:55]}...", end=" ", flush=True)

        result = download_chapter(ch["link"])
        if result and result.get("text"):
            fname = os.path.join(RAW_DIR, f"chapter_{num:04d}.txt")
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False)
            print(f"OK ({len(result['text'])}c)")
            ok += 1
        else:
            print("FAIL")
            fail += 1

        time.sleep(2)

    print(f"\n=== Готово! OK: {ok}, Fail: {fail} ===")


if __name__ == "__main__":
    main()
