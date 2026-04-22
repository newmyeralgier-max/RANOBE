---
name: git-workflow
description: Standard operating procedures for version control, commit messages, and branching.
---

# Git & Version Control Workflow

## When to use this skill
- When saving progress.
- When the user asks you to commit your changes or push to a branch.
- Before embarking on a heavy refactor where we might need to rollback.

## Core Directives

1. **Commit Often, Commit Small**:
   - Don't wait until a massive feature is 100% complete. Commit logical chunks (e.g., "Setup database schema", "Implement authentication middleware").

2. **Commit Message Format (Conventional Commits)**:
   - Use structured prefixes:
     - `feat:` for new features.
     - `fix:` for bug fixes.
     - `docs:` for documentation changes.
     - `refactor:` for code changes that neither fix a bug nor add a feature.
     - `test:` for adding or fixing tests.
     - `chore:` for updating dependencies, build processes, etc.
   - The summary should be written in the imperative mood (e.g., `feat: add user login endpoint`, not `feat: added user login endpoint`).

3. **Branching Strategy**:
   - Do not commit directly to `main` or `master` if working on a large or breaking change.
   - Always verify the current branch before committing.
   - If asked to create a feature, create a new branch like `feature/short-description`.

4. **Review Before Commit**:
   - Always run `git diff` or `git status` before committing to ensure no unintended files (like `.env` or `.tmp/`) are being added.
