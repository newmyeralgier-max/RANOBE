---
name: security-and-defensive-programming
description: Critical security rules to prevent common vulnerabilities, secrets leakage, and ensure robust error handling.
---

# Security & Defensive Programming

## When to use this skill
- ALWAYS, but especially when handling user input, database queries, file uploads, authentication, or external API calls.

## Core Directives

1. **Never Trust User Input**:
   - Validate and sanitize all incoming data (e.g., query parameters, body payloads, file names).
   - Use established validation frameworks (like Pydantic in Python or Zod in JS) instead of manual if-else checks.

2. **No Hardcoded Secrets**:
   - NEVER hardcode API keys, database passwords, or secret tokens in the code.
   - Always load them via environment variables (e.g., `os.environ.get("API_KEY")` or `dotenv`).
   - If generating example code, use obvious placeholders like `YOUR_API_KEY_HERE`.

3. **Secure File Operations**:
   - When saving user uploads, sanitize filenames to prevent path traversal attacks (e.g., stripping `../` or `\`).
   - Do not execute user-uploaded files or treat them as scripts.

4. **Defensive Database Queries**:
   - Use ORMs (like SQLAlchemy or Prisma) or parameterized queries to prevent SQL Injection.
   - Never use string formatting or concatenation to build SQL queries with user input.

5. **Safe Logging**:
   - Ensure that sensitive data (passwords, auth tokens, personal user info) is never printed to `stdout` or written to log files.
