---
name: working-with-github
description: Interacts with the GitHub MCP server to search repositories, list issues, pull requests, read file contents, and manage branches. Use when the user asks to analyze GitHub links, search for code, review PRs, or manage repository files.
---

# Working with GitHub MCP

## When to use this skill

- The user wants to read code or retrieve information from a GitHub repository.
- The user provides GitHub links and wants them summarized or ingested into NotebookLM.
- The user asks to analyze pull requests, issues, or commit histories.
- The user requests to create or update files, branches, or issues on GitHub.

## Workflow

### 1. Reading and Analyzing Repository Content
When asked to analyze a GitHub repository:
- [ ] Extract the `owner` and `repo` from the provided GitHub URL (e.g., `https://github.com/owner/repo`).
- [ ] Use `mcp_github-mcp-server_get_file_contents` to read specific files or list directory contents.
- [ ] If the user wants a broad overview, you might need to read `README.md` or search for specific code using `mcp_github-mcp-server_search_code`.
- [ ] If ingestion into NotebookLM is requested, copy the retrieved text and use `mcp_notebooklm_notebook_add_text` to append it as a new source in NotebookLM.

### 2. Searching and Exploring
- Use `mcp_github-mcp-server_search_repositories` to find repos matching a description.
- Use `mcp_github-mcp-server_search_code` to find symbols or exact text across a repo.
- Use `mcp_github-mcp-server_list_commits` or `mcp_github-mcp-server_get_commit` to understand recent changes.

### 3. Issues and Pull Requests
- Use `mcp_github-mcp-server_list_issues` or `mcp_github-mcp-server_search_issues` to find tasks.
- Use `mcp_github-mcp-server_list_pull_requests` or `mcp_github-mcp-server_pull_request_read` to analyze PR content and diffs.
- Use `mcp_github-mcp-server_issue_write` to create or update issues.

## Error Handling
- **Repository Not Found**: Ensure the URL matches a valid GitHub `owner/repo` format. If it's a private repo, ensure the user's GitHub token (configured globally or in the MCP server) has adequate permissions.
- **Rate Limits**: If you encounter GitHub API rate limits, slow down requests or inform the user.
- **Huge Repositories**: Do not attempt to read every single file. Use search or read specifically requested files (like `README.md`, `package.json`, or root documentation).

## Resources
- This functionality is provided by the `github-mcp-server`.
