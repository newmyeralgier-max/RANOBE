# Test Plan — Universal Novel Downloader GUI

PR: https://github.com/newmyeralgier-max/RANOBE/pull/1
Feature under test: `execution/novel_dl/gui.py` (Tkinter window) reachable via
`python3 -m novel_dl.gui` from the `execution/` directory, or by double-clicking
`execution/launchers/novel_dl.sh` / `.bat`.

User intent (translated from Russian): "I want to paste a link a year from now,
having forgotten everything, and get the result" — so the single flow that
matters is: open window → paste ranobes URL → pick a range → click Download →
find clean `.txt` files on disk.

## Primary flow (one continuous recording)

### Setup (before recording)
- Launch the GUI: `cd execution && /usr/bin/python3 -m novel_dl.gui`
  (system Python has tkinter; pyenv Python does not).
- Maximize the window via `wmctrl -r :ACTIVE: -b add,maximized_vert,maximized_horz`.
- Open a terminal tab for post-hoc file verification (not on-screen during the
  UI recording).

### Step 1 — Paste URL and fetch chapter list
- Action: click the URL entry and type
  `https://ranobes.net/novels/1206834-horror-game-developer.html`.
  Click the button **«Загрузить список глав»**.
- Expected visible state within ~5 seconds:
  - The "2. Книга" panel's bold title changes from
    `— ещё ничего не загружено —` to
    `Horror Game Developer: My Games Aren't That Scary!` (exact text, apostrophe
    is a literal `'`, not `&#039;` — tests that HTML entities are decoded).
  - The info line contains the substring `Глав: 625`
    (as of 2026-04-22 the site listed 625 chapters; acceptable range 620–700 to
    allow for newly released chapters. Fewer than 100 or exactly 0 is a FAIL —
    pagination loop is broken).
  - Status bar text: `Готов к скачиванию. Глав: 625.` (digit may vary per above).
  - The **«Скачать»** button becomes enabled (was disabled).
- Adversarial check: if parsing were broken, the title would stay at
  `— ещё ничего не загружено —` or the count would be 25 (only first page).

### Step 2 — Select a small range and download
- Action: clear the Диапазон field, type `1-3`. Leave "Склеить в combined.txt"
  checked. Set output dir to `/tmp/novel_dl_gui_test` via the "Выбрать..." button
  (or type it). Click **«Скачать»**.
- Expected visible state:
  - The progress bar starts at 0/3 and advances in three discrete steps to 3/3.
  - The log panel gains three new lines matching the pattern
    `[N/3] fetching: Ch N: Chapter N: <title>` (or `skip (exists)` on reruns).
  - Status bar changes from `Скачиваю...` → `Скачано 1/3` → `2/3` → `3/3` → `Готово!`
  - A blocking modal `Готово` appears with text containing
    `/tmp/novel_dl_gui_test/1206834-horror-game-developer`.
  - The **«Открыть папку с результатом»** button becomes enabled.
- Adversarial check: a broken implementation would show `Готово` but the
  progress bar would jump 0→3 with no intermediate updates, or the log would
  be empty.

### Step 3 — Verify files on disk (external, via shell, still inside recording)
- Action: run `ls -la /tmp/novel_dl_gui_test/1206834-horror-game-developer/`
  in a terminal visible on the recording.
- Expected:
  - Files present: `chapter_0001_Chapter 1_ Prologue.txt`,
    `chapter_0002_Chapter 2_ The Jester [1].txt`,
    `chapter_0003_Chapter 3_ The Jester [2].txt`, `meta.json`, `combined.txt`.
  - `head -3 chapter_0001*.txt` begins with `# Chapter 1: Prologue\n\nClick.`
    (first body line). The presence of literal `Click.` is the canary — it
    proves the adapter extracted real chapter body text, not nav/ads/HTML.
  - `wc -l combined.txt` is ≥ 60 (three chapters concatenated).
  - `python3 -c "import json; d=json.load(open('.../meta.json')); print(d['title'], d['total_chapters'])"`
    prints `Horror Game Developer: My Games Aren't That Scary! 625` (no HTML
    entities).
- Adversarial check: empty files, or files whose body starts with `<div` /
  `<p>` tags, mean `strip_tags` / article extraction failed.

## Secondary (optional, same recording) — error path
- Action: paste `https://example.com/not-a-novel` into the URL field and click
  **«Загрузить список глав»**.
- Expected: a modal titled `Сайт не поддержан` appears with text containing
  `No adapter found for URL`. The book panel does NOT change its title.
- Adversarial check: if the registry silently fell through, the app would
  freeze on "Загружаю..." or attempt to download garbage.

## Out of scope
- freewebnovel adapter (covered only by unit tests — no live environment here).
- EPUB pipeline / translation (not changed in this PR).
- Very large downloads (>50 chapters) — not needed to prove the feature.

## Evidence artefacts
- One recording of the full primary flow + error path.
- `ls` / `head` shell output captured as text in the report.
- Unit-test results are NOT used as evidence in the runtime report (already
  green in CI-less mode locally; runtime proof is what matters here).
