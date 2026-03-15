---
applyTo: "**"
---

# AtlasNode workspace instructions

This workspace uses the `atlasnode-brain` MCP server as the canonical source of AtlasNode runtime state, retrieved operating context, and durable memory.

For every new chat session or before the first substantial answer in an existing session:

1. Read the current runtime state from the MCP server.
2. If the current mode is not development-focused, switch the shared state to `technical`.
3. Build the live AtlasNode system prompt from the current shared state and retrieved context.
4. Use that state and prompt as the authoritative context for the response.

Operational rules:

- Treat the MCP server state as authoritative for mode, sliders, focus, system status, and persistent memory.
- Prefer AtlasNode MCP tools over repository files when the task is about current AtlasNode behavior or state.
- When the user changes tone, mode, or behavior, update shared state through the MCP tools.
- When the user wants persistent recall, write or append memory through the MCP server.


