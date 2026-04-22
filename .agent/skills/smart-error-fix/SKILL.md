---
name: smart-error-fix
description: Automatically triggers an intelligent error-resolution workflow using Web Search, GitHub Issues, and NotebookLM when the user encounters a bug, crash, or explicitly asks for an error fix.
---

# Smart Error Fix (Умное исправление ошибок)

## Trigger Condition
- **Activate this skill** whenever the user inputs words related to crashes, errors, exceptions, or explicitly provides a stack trace (e.g., "ошибка", "упало", "crashed", "почини", "помоги с ошибкой", or pastes a Python/JS traceback).

## Core Directives
When triggered, do not simply guess the solution based on your internal knowledge, as libraries and APIs change frequently. You must gather context dynamically:

1. **Understand & Identify:** Extract the exact error message, stack trace, and specific library/framework involved (e.g., `FastAPI ValueError`, `React useEffect warning`).
2. **Web Search (`search_web`):**
   - Search the exact error text or key parts of the stack trace.
   - Look for StackOverflow discussions and recent blog posts/tutorials solving similar issues.
3. **GitHub Search (`github-mcp-server`):**
   - Use `mcp_github-mcp-server_search_issues` focusing on the specific library's repository. Format the search as `repo:org/lib "exact error snippet"`.
   - Check if the issue is a known bug, if there is a pending PR, or if a recent version fixes it.
4. **NotebookLM Synthesis (`notebooklm`):**
   - If the error is complex or architecture-related, use `mcp_notebooklm_research_start` (`mode="fast"`) to find discussions on best practices to refactor away from the error.
5. **Formulate the Solution:** Provide the user with a definitive answer:
   - **Diagnosis:** Why it happens.
   - **Immediate Fix:** Code snippet to patch the error.
   - **Root Cause & Source:** Provide the URLs to the GitHub Issue or StackOverflow thread where the fix was found.
