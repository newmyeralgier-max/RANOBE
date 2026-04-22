---
name: automated-code-review
description: Automatically triggers a comprehensive multi-layered code review using coding standards, open-source best practices, and security rules when the user asks for a review.
---

# Automated Code Review (Умное ревью кода)

## Trigger Condition
- **Activate this skill** whenever the user explicitly asks you to review code, analyze a file, or says "проревьюй", "/review-code", "чекни код", "сделай ревью".

## Core Directives
When performing a review, analyze the code across multiple dimensions instead of just looking for syntax errors. Follow this strict checklist:

1. **Standards Verification:**
   - Mentally verify the code against rules defined in the `universal-coding-standards`, `security-and-defensive-programming`, and framework-specific guidelines (like `fastapi-expert`).
   - Look for hardcoded secrets, injection vulnerabilities, missing types, and poor naming conventions.

2. **Open-Source Benchmarking (`github-mcp-server`):**
   - If the code implements a complex pattern (e.g., custom authentication flow, rate-limiting logic, state management), use `mcp_github-mcp-server_search_code` to find how similar logic is implemented in highly-starred open-source repositories.
   - Compare the user's implementation with industry standards and propose structural improvements based on what you find.

3. **Contextual Analysis (`notebooklm`):**
   - If the project has an existing NotebookLM knowledge base, query it to ensure the current design aligns with previous architectural decisions or overarching goals.

4. **Review Report Format:** Provide a structured report categorized by priority:
   - 🔴 **CRITICAL:** Security vulnerabilities, severe bugs, architectural flaws.
   - 🟡 **WARNING:** Performance issues, edge cases not handled, missing validation.
   - 🟢 **SUGGESTION:** Stylistic improvements, typing, DRY enhancements.
   - Provide concrete code blocks (`diff` format) showing exactly how to fix the identified issues.
