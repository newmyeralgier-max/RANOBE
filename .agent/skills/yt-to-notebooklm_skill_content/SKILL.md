---
name: yt-to-notebooklm
description: Process YouTube videos and playlists into NotebookLM notebooks using yt-dlp and notebooklm-mcp. Use when user wants to (1) add YouTube videos/playlists to NotebookLM, (2) categorize playlist videos into themed notebooks, (3) extract and add YouTube comments as sources, (4) generate artifacts from notebooks (audio, video, slides, reports). Triggers on yt-dlp + NotebookLM, YouTube to notebook, video comments analysis, playlist categorization.
---

# YouTube to NotebookLM

Video/playlist → extract metadata → categorize → create notebooks → add sources + comments → generate artifacts → download.

## Prerequisites

- **yt-dlp** — installed and in PATH
- **notebooklm-mcp** — MCP server connected to Claude Code (`pip install notebooklm-mcp-cli`, then `nlm setup add claude-code`)

## Rules

- Store temp files in `./yt_nlm_tmp/` (working directory)
- Call MCP tools natively (NOT via bash/mcp-cli)
- Omit `--cookies-from-browser` by default; on access error ask user's browser name, retry with `--cookies-from-browser <browser>`
- **Russian Language Standard**: Always set `language="ru"` in all artifact generation tools (`audio_overview_create`, `report_create`, etc.). All text sources and AI responses must be in Russian.

## Workflow

### 1. Get URL and verify auth

Get YouTube URL from user. Call `notebook_list` silently — on auth error, suggest `nlm login`.

### 2. Extract metadata

**Single video:**
```bash
yt-dlp --skip-download --print "%(id)s|||%(title)s|||%(categories)s|||%(tags)s|||%(description).300s" "URL"
```

**Playlist** — first get video list, then metadata per video:
```bash
yt-dlp --flat-playlist --print "%(id)s|%(title)s" "PLAYLIST_URL"
```

On access error → ask user which browser, retry with `--cookies-from-browser <browser>`.

### 3. Categorize

- **Single video** → one notebook, skip to step 4
- **Playlist** → analyze titles/tags/categories, propose grouping. Show table:

| # | Video | YouTube Category | Proposed Category |
|---|-------|-----------------|-------------------|

**PAUSE.** Wait for user approval or adjustments.

### 4. Create or select notebooks

1. Call `notebook_list` — get existing notebooks
2. Match proposed categories against existing notebook titles
3. If matches found → ask user: add to existing or create new?
4. Create via `notebook_create(title=...)` or reuse existing `notebook_id`

### 5. Add videos as sources

For each video: `source_add(notebook_id, source_type="url", url="https://www.youtube.com/watch?v=ID", wait=true)`

**Fallback** if URL fails:
```bash
yt-dlp -x --audio-format mp3 -o "./yt_nlm_tmp/%(id)s.%(ext)s" "URL"
```
Then: `source_add(notebook_id, source_type="file", file_path="./yt_nlm_tmp/ID.mp3", wait=true)`

### 6. Collect and add comments

**Extract:**
```bash
yt-dlp --skip-download --write-comments \
  --extractor-args "youtube:max_comments=all,all,all,all" \
  --print "%(comments)j" "URL" > ./yt_nlm_tmp/comments_VIDEO_ID.json
```

**Process:**
1. Read JSON file, format each comment as `@author (likes: N):\ntext`, join with `\n\n---\n\n`
2. Check total size — if > 450K chars, split into chunks
3. Add via `source_add(notebook_id, source_type="text", title="Comments: <video_title>", text=..., wait=true)`
4. Title chunks as `"Comments: <video> (part N/M)"`

### 7. Artifacts

**PAUSE.** Show artifact types:

| Type | Key | Description |
|------|-----|-------------|
| Audio Overview | `audio` | Podcast-style discussion |
| Video Overview | `video` | Animated video summary |
| Slide Deck | `slide_deck` | Presentation (PDF/PPTX) |
| Report | `report` | Briefing doc / study guide |
| Infographic | `infographic` | Visual summary |
| Mind Map | `mind_map` | Concept map |
| Quiz | `quiz` | Multiple choice quiz |
| Flashcards | `flashcards` | Study cards |
| Data Table | `data_table` | Structured data |

Ask:
- Which artifacts and for which notebooks?
- **If notebook has multiple sources** → show source list, ask which to use (→ `source_ids`)
- Custom focus? (→ `focus_prompt`)

### 8. Create, poll, download

1. `studio_create(notebook_id, artifact_type, confirm=true, ...)` — save `artifact_id`
2. Poll `studio_status(notebook_id)` every 60-120s until `completed` or `failed`
3. `download_artifact(notebook_id, artifact_type, artifact_id, output_path="./yt_nlm_tmp/<name>.<ext>")`

If generation fails → retry with `source_ids` limited to 5-6 sources.

### 9. Cleanup

Ask user: delete `./yt_nlm_tmp/`?

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Playlist not found | Add `--cookies-from-browser <browser>` — ask user which browser |
| `source_add` URL fails | Fallback: download audio via `yt-dlp -x`, add as `source_type="file"` |
| Comments text > 450K chars | Split into chunks, add as separate text sources |
| Artifact generation fails | Retry with `source_ids` (max 5-6 sources) |
| NotebookLM auth error | Suggest `nlm login` (or `nlm login --clear` for re-auth) |
| Wrong Google account | `nlm login profile list` → `nlm login switch <profile>` |
