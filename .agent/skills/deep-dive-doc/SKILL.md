---
name: deep-dive-doc
description: Automatically generates study guides, cheat sheets, and deeply analyzes external documentation when the user asks to learn or use a new library.
---

# Deep Dive Documentation (Глубокое изучение библиотек)

## Trigger Condition
- **Activate this skill** whenever the user asks to research, learn, or figure out how to use a specific library/framework we are not currently familiar with (e.g., "изучи библиотеку X", "разберись как работает Y", "как сделать Z с помощью новой либы").

## Core Directives
When triggered, do not guess APIs or rely on outdated internal knowledge. You must pull current documentation and synthesize it.

1. **Locate the Source:**
   - Use `search_web` to find the official documentation site or GitHub repository of the target library.
2. **Ingest the Documentation:**
   - Call `mcp_github-mcp-server_get_file_contents` to fetch the `README.md` and core markdown files from the `docs/` folder in the repository.
   - If documentation is heavily web-based, use `read_url_content` to scrape the getting started guide and API reference.
3. **Analyze via NotebookLM:**
   - Copy the retrieved text and add it as a source using `mcp_notebooklm_notebook_add_text` (or just parse it yourself if it's brief).
4. **Generate the Artifact:**
   - Produce a concise "Cheat Sheet" or "Quick Start Guide" tailored to the specific problem the user is trying to solve.
   - Emphasize modern recommended patterns (e.g., v2 APIs vs legacy v1).
   - Provide working code examples that fit seamlessly into the user's current project architecture (e.g., converting callbacks to `async/await` if the user works in FastAPI).
