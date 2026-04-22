---
name: smart-problem-search
description: Automatically triggers comprehensive problem-solving search using NotebookLM, GitHub, and Google Search when the user asks "найди" (find).
---

# Smart Problem Search ("Найди")

## Trigger Condition
- **Activate this skill** whenever the user inputs the word "найди" (find/search) in the context of solving a technical problem, researching a concept, or looking for a solution.
- **DO NOT activate this skill** if the user restricts the context to local files (e.g., "найди в файлах", "найди эту строку кода у нас").

## Core Directives
When triggered, you must **not** rely solely on your internal knowledge. You are required to actively query all three of the following external sources to synthesize the best possible solution:

1. **Google Search (`search_web`)**
   - **Goal:** Find current documentation, recent articles, StackOverflow answers, and community discussions.
   - **Action:** Execute targeted web searches related to the user's query.

2. **GitHub (`github-mcp-server`)**
   - **Goal:** Find real-world code implementations, open issues, architectural examples, or libraries.
   - **Action:** Use tools like `mcp_github-mcp-server_search_code` or `mcp_github-mcp-server_search_repositories`.

3. **NotebookLM (`notebooklm` MCP)**
   - **Goal:** Perform deep research, summarize dense documentation, or query an existing codebase knowledge base if applicable.
   - **Action:** Use `mcp_notebooklm_research_start` (with `mode="fast"` or `mode="deep"`) or query existing notebooks to synthesize findings.

## Execution Strategy
1. **Understand:** Identify the exact technical problem the user is trying to solve.
2. **Execute Parallel Searches:** 
   - Launch a `search_web` query.
   - Launch a GitHub search.
   - Launch a NotebookLM research task.
3. **Synthesize:** Analyze the results from all three sources.
4. **Respond:** Provide a comprehensive answer that includes conceptual understanding (from Web), code examples (from GitHub), and deep synthesis (from NotebookLM). Explicitly mention the sources you used.
