#!/usr/bin/env python3
"""Скачать Horror Game Developer с FreeWebNovel (главы 1-613)"""
import sys, os, re, json, time, subprocess
sys.path.insert(0, os.path.expanduser("~/.local/lib/python3.12/site-packages"))
from bs4 import BeautifulSoup

RAW_DIR = "/mnt/d/1. Project/Ranobe/data/raw"
SLUG = "horror-game-developer-my-games-arent-that-scary"

# Existing chapters
already = set()
for f in os.listdir(RAW_DIR):
    m = re.match(r"chapter_(\d+)\.txt", f)
    if m:
        already.add(int(m.group(1)))

print(f"Already have: {len(already)} chapters", flush=True)

def fetch(url, retries=3):
    for i in range(retries):
        try:
            r = subprocess.run(
                ["curl", "-s", "-L", "-m", "20",
                 "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                 url],
                capture_output=True, timeout=25, encoding="utf-8", errors="replace"
            )
            if r.returncode == 0 and len(r.stdout) > 500:
                return r.stdout
        except Exception as e:
            print(f"  retry {i+1}: {e}", flush=True)
            time.sleep(2)
    return None

def download_chapter(ch_num):
    url = f"https://freewebnovel.com/{SLUG}/chapter-{ch_num}.html"
    html = fetch(url)
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    # Title
    h1 = soup.find("h1", class_="tit") or soup.find("h1")
    title = h1.get_text(strip=True) if h1 else f"Chapter {ch_num}"

    # Content
    div = soup.find("div", id="article") or soup.find("div", class_="text")
    if not div:
        return {"title": title, "text": ""}

    paragraphs = []
    for p in div.find_all("p"):
        t = p.get_text(strip=True)
        if t:
            paragraphs.append(t)

    text = "\n\n".join(paragraphs)
    return {"title": title, "text": text}

# Download chapters 1-613, skipping existing
to_download = [n for n in range(1, 614) if n not in already]
print(f"To download: {len(to_download)} chapters", flush=True)
print(flush=True)

ok = 0
fail = 0
for i, num in enumerate(to_download, 1):
    print(f"[{i}/{len(to_download)}] Ch {num}...", end=" ", flush=True)
    result = download_chapter(num)
    if result and result.get("text"):
        fname = os.path.join(RAW_DIR, f"chapter_{num:04d}.txt")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        print(f"OK ({len(result['text'])}c)", flush=True)
        ok += 1
    else:
        print("FAIL", flush=True)
        fail += 1

    # Rate limit — be nice to the server
    if i % 10 == 0:
        time.sleep(2)
    else:
        time.sleep(1)

print(f"\nDone! OK: {ok}, Fail: {fail}", flush=True)
