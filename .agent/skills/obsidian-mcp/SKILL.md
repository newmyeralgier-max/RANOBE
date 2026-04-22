---
name: obsidian-mcp
description: Interacts with the Obsidian vault via an MCP Server. Enables AI to read, search, write, and manage Markdown notes, tags, and frontmatter. Use when working with personal knowledge bases or generating documentation.
---

# Obsidian Knowledge Management Skill

## When to use this skill
- The user asks to search for notes or read specific documents in their Obsidian vault.
- The user wants to generate new markdown notes and save them directly to Obsidian.
- The user needs to extract knowledge from Obsidian notes to inform code logic or architecture (e.g., reading project specs from Obsidian).

## Required Setup
To use this skill, the user must have an Obsidian MCP server running. Recommended options:
1. `cyanheads/obsidian-mcp-server` (Requires the 'Local REST API' community plugin in Obsidian).
2. `smithery-ai/mcp-obsidian` (General vault connector).

**If the MCP server is not configured in the IDE, inform the user they need to add one of the above repositories to their MCP config.**

## Workflow
1. **Search Notes**: Use `mcp_obsidian_search_nodes` (or the equivalent tool provided by the active MCP server) to find relevant notes by keyword.
2. **Read Content**: Use `mcp_obsidian_read_note` to retrieve the full markdown content of a note before modifying it.
3. **Write/Update Notes**: Use `mcp_obsidian_write_note` or `mcp_obsidian_append_note` to document project progress, save code snippets, or create API documentation directly in the vault.
4. **Frontmatter Management**: Always include YAML frontmatter when creating new semantic notes (e.g., `tags`, `aliases`, `date`).

## Instructions
- Always respect the Markdown formatting standards of Obsidian (e.g., internal links `[[note name]]`).
- When writing technical docs into Obsidian, format code blocks with explicit languages (`python`, `javascript`) to ensure proper rendering.
- Do not overwrite existing notes entirely without reading them first and merging the content carefully.
