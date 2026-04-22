---
name: automated-setup
description: Automatically scaffolds new projects (e.g., web scrapers, FastAPI backends, bots) by finding best-practice templates on GitHub and generating a tailored architecture. Activated via '/scaffold' or 'создай шаблон'.
---

# Automated Setup (/scaffold или "создай шаблон")

## Trigger Condition
- **Activate this skill** whenever the user asks to create a new project from scratch, says "создай шаблон проекта", or uses the command `/scaffold`.

## Core Directives
When triggered, do not simply write boilerplate code from memory. You must follow a structured approach to generate a project that adheres to modern standards.

1. **Understand Requirements:** Identify the stack the user wants (e.g., Python + Telegram Bot, TypeScript + Next.js, Python + FastAPI).
2. **Template Search (`github-mcp-server`):**
   - Use `mcp_github-mcp-server_search_repositories` to find minimalistic, highly-starred templates (e.g., `fastapi template`, `telegram bot boilerplate`).
   - If a good candidate is found, use `mcp_github-mcp-server_get_file_contents` to read its `README.md` or core structural files (`main.py`, `package.json`).
3. **Apply Internal Rules:**
   - Mentally cross-reference the planned structure with existing internal rules (e.g., `fastapi-expert`, `scripter-bot-maker`, `security-and-defensive-programming`).
4. **Generate the Project:**
   - Execute a series of terminal commands (`run_command`) to initialize the directory architecture (`mkdir`, `cd`, `git init`).
   - Use `write_to_file` to create the core files (e.g., `.gitignore`, `.env.example`, `requirements.txt`, `main.py`).
5. **Report to User:**
   - Present the finalized folder structure.
   - Provide exact commands the user needs to run to start the project (e.g., `pip install -r requirements.txt`, `uvicorn main:app --reload`).
