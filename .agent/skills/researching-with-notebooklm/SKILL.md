---
name: researching-with-notebooklm
description: Interacts with the NotebookLM MCP server to manage notebooks, ingest sources, conduct deep research, query knowledge bases, and generate study artifacts. Use when the user asks to analyze documents, search the web or Drive for research, or create overviews via NotebookLM.
---

# Researching with NotebookLM

## When to use this skill

- The user wants to query, summarize, or extract content from their existing NotebookLM notebooks.
- The user requests deep research or fast web/Drive research on a specific topic.
- The user needs to add URLs, Google Drive documents, or raw text to a notebook.
- The user asks to generate structural artifacts like Audio Overviews, Video Overviews, Study Guides, Flashcards, Quizzes, or Mind Maps.
- The user needs to manage (list, create, rename, delete) notebooks and sources.

## Workflow

### 1. Research & Content Ingestion (Asynchronous Pattern)
When asked to research a new topic:
- [ ] Start the research task using `research_start` (e.g., `mode="deep"` or `mode="fast"`).
- [ ] Poll for completion using `research_status`, waiting for the status to show "completed".
  - **Rule:** For deep research (`mode="deep"`), automatically wait ~5 minutes (polling periodically) and proactively import the discovered sources via `research_import` without requiring additional user prompting.
- [ ] Import the discovered sources into the notebook using `research_import`.

### 2. Artifact Generation (Studio Polling Pattern)
When creating audio, video, infographics, or slides:
- [ ] Request user confirmation first (as these actions consume resources).
- [ ] Call the respective create tool (e.g., `audio_overview_create`) with `confirm=True`.
- [ ] Poll the studio using `studio_status` until the artifact is generated and the URL is available.
- [ ] Provide the final URL to the user.

### 3. Source Management
- **List existing drive sources:** Use `source_list_drive` to identify stale context.
- **Sync drive sources:** Use `source_sync_drive` to fetch the latest changes (requires `confirm=True`).
- **Get raw content:** Use `source_get_content` to quickly read original text without AI processing overhead.

## Instructions

- **Authentication Fallback**: If you encounter authentication errors, first use the `refresh_auth` tool to reload tokens. If it still fails, instruct the user to run `notebooklm-mcp-auth` in their terminal.
- **Querying Existing Knowledge**: Use `notebook_query` to ask questions ONLY about existing sources. Do not use this for web searches (use `research_start` instead). 
- **Destructive Actions**: Always verify intent before using `notebook_delete`, `source_delete`, or `studio_delete` and pass `confirm=True`.
- **Custom Chat Goals**: Use `chat_configure` to adjust the notebook's AI behavior (e.g., `goal="learning_guide"` or `goal="custom"` with a prompt).

## Error Handling

- **Missing Notebook ID**: If the user doesn't specify a notebook, use `notebook_list` to find the relevant one, or create one with `notebook_create` if this is a new topic.
- **Timeout on Queries**: If `notebook_query` times out, try splitting the prompt or focusing on fewer `source_ids`.
- **CLI Fallback**: The `save_auth_tokens` tool is a strict fallback. Always prefer the automated `notebooklm-mcp-auth` bash command.

## Resources

- None required. All functionality is provided dynamically via the NotebookLM MCP tools.
