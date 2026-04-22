---
name: universal-coding-standards
description: Core programming guidelines covering variable naming, typings, comments, and DRY principles applicable to any language (Python, JS/TS, Lua, etc.).
---

# Universal Coding Standards

## When to use this skill
- ALWAYS. This is the baseline standard for writing self-documenting, maintainable code.

## 1. Naming & Readability
- Variables and functions must have highly descriptive names. Avoid `temp`, `data`, `res`, `obj` unless contextually obvious.
- Boolean variables should sound like questions (e.g., `is_active`, `has_permission`, `should_render`).
- Favor readable code over unnecessarily clever one-liners. If a complex list comprehension or ternary takes more than a few seconds to understand, break it down.

## 2. Typing & Signatures
- **Python**: Always use type hints (`def process_user(user_id: int) -> dict:`).
- **JavaScript**: Use TypeDoc comments or TypeScript interfaces whenever possible to clarify object shapes.
- Function signatures should be explicit about what is required versus optional.

## 3. Comments & Documentation
- **Do not state the obvious**: Avoid `// initialize counter` above `int count = 0;`.
- **Comment the "Why", not the "What"**: Explain the business logic, edge cases, and architectural decisions. E.g., `// We fetch 101 items to have 1 extra for pagination logic`.
- Add docstrings to all public modules, classes, and complex functions. Provide a brief description, Args, and Returns.

## 4. DRY & SOLID Principles
- If you copy-paste code more than once, extract it into a reusable function or utility.
- Keep functions small and focused on a single responsibility.
- Write pure functions where possible (functions that don't mutate external state and always return the same output for the same input).
