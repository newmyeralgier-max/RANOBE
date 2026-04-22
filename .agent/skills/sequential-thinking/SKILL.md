---
name: sequential-thinking
description: A problem-solving framework that forces the AI to think step-by-step, analyze requirements, and design architecture before writing code for complex tasks.
---

# Sequential Thinking & Problem Solving

## When to use this skill
- When starting a new feature, a complex refactor, or debugging a multi-layered issue.
- Whenever the user asks to "build X" without providing a detailed technical specification.

## Core Directives
Do not rush into writing code. Follow this sequential thinking process:

1. **Information Gathering**:
   - What is the user's ultimate goal?
   - What are the input constraints and expected outputs?
   - Read the relevant existing files to understand the current architecture.

2. **Analysis & Edge Cases**:
   - Identify potential pitfalls, race conditions, or performance bottlenecks.
   - What happens when external dependencies fail or time out?
   - Are there missing requirements that need clarification from the user?

3. **Architecture Mapping**:
   - Write a short, step-by-step implementation plan (usually in an artifact or thought block).
   - Decide which files need to be modified, created, or deleted.
   - Outline the data structures or database schema changes needed.

4. **Execution**:
   - Only begin writing code *after* the plan is clear.
   - Implement the solution in logical chunks, checking functionality along the way.
   - Ask for user feedback if a critical architectural decision needs to be made mid-flight.

## Why this matters
Writing code without planning leads to spaghetti code, broken tests, and time wasted rewriting. Measure twice, cut once.
