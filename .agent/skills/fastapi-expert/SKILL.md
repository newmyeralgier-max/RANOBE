---
name: fastapi-expert
description: Architectural and coding rules tailored for building robust FastAPI backend applications. Ensures safety, validation, and modern Python standards.
---

# FastAPI Expert Rules

## When to use this skill
- Whenever creating or modifying API endpoints inside the `converter_app/backend` or any other FastAPI project.
- When designing database models, Pydantic schemas, or handling request validation.

## Coding Standards
1. **Pydantic V2**: Always use modern Pydantic features (`model_validate`, `Field`, `ConfigDict`). Avoid deprecated V1 methods like `.dict()` or `.parse_obj()`.
2. **Async by Default**: Use `async def` for endpoint definitions, unless performing heavy synchronous CPU blocking operations (in which case use `def` or `RunInThreadpool`).
3. **Dependency Injection**: Vigorously use `Depends()` for database sessions, authentication, and repetitive service extraction. Do not instantiate global state inside endpoints.
4. **Error Handling**: Never return raw Exceptions or 500s directly. Catch specific errors and raise `HTTPException` with clear, user-facing details. Document your error codes.

## Project Structure (Recommended for Converter App)
- `main.py` - Fast API setup and CORS middleware.
- `api/` - Routers and endpoint definitions.
- `schemas/` - Pydantic validation models.
- `services/` or `converters/` - Core business logic (e.g. `epub_parser.py`, `telegram_json.py`).

## Instructions for AI Agent
- Before modifying a Pydantic model, check all places where the model is instantiated to avoid breaking changes.
- Ensure that CORS is correctly configured for the Vue/Vanilla JS frontend layer.
- If importing `File` or `UploadFile` from `fastapi`, ensure proper handling of streaming large files to disk via `shutil.copyfileobj` instead of loading entire files into memory.
