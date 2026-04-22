#!/usr/bin/env python3
"""Скачать Horror Game Developer — докачка пропущенных глав"""
import sys, os, re, json, time, subprocess
sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
from bs4 import BeautifulSoup

RAW_DIR = "/mnt/d/1. Project/Ranobe/data/raw"
SLUG = "horror-game-developer-my-games-arent-that-scary"
MAX_CH = 613

# Existing
already = set()
for f in os.listdir(RAW_DIR):
    m = re.match(r"chapter_(\d+)\.txt", f)
    if m:
        already.add(int(m.group(1)))

to_download = [n for n in range(1, MAX_CH + 1) if n not in already]
print(f"Already: {len(already)}, To download: {len(to_download)}", flush=True)

def fetch(url, retries=3):
    for i in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-m", "25",
                 "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                 "-H", "Accept-Language: en-US,en;q=0.9",
                 url],
                capture_output=True, timeout=30, encoding="utf-8", errors="replace"
            )
            if r.returncode == 0 and len(r.stdout) > 300:
                return r.stdout
        except:
            time.sleep(3)
    return None

def download_chapter(ch_num):
    url = f"https://freewebnovel.com/{SLUG}/chapter-{ch_num}.html"
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1", class_="tit") or soup.find("h1")
    title = h1.get_text(strip=True) if h1 else f"Chapter {ch_num}"
    div = soup.find("div", id="article") or soup.find("div", class_="text")
    if not div:
        return {"title": title, "text": ""}
    paragraphs = [p.get_text(strip=True) for p in div.find_all("p") if p.get_text(strip=True)]
    text = "\n\n".join(paragraphs)
    return {"title": title, "text": text}

ok = 0
fail = 0
fails_in_row = 0
for i, num in enumerate(to_download, 1):
    print(f"[{i}/{len(to_download)}] Ch {num}...", end=" ", flush=True)
    result = download_chapter(num)
    if result and result.get("text"):
        fname = os.path.join(RAW_DIR, f"chapter_{num:04d}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"OK ({len(result['text'])}c)", flush=True)
        ok += 1
        fails_in_row = 0
    else:
        print("FAIL", flush=True)
        fail += 1
        fails_in_row += 1
        if fails_in_row >= 5:
            print("5 fails in a row, sleeping 30s...", flush=True)
            time.sleep(30)
            fails_in_row = 0

    # Rate limit
    if i % 10 == 0:
        time.sleep(2)
    else:
        time.sleep(1)

    # Progress save every 50
    if i % 50 == 0:
        print(f"--- Progress: {ok} ok, {fail} fail, {len(to_download)-i} left ---", flush=True)

print(f"\nDONE! OK: {ok}, Fail: {fail}", flush=True)
