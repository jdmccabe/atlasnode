# Codex Workspace Instructions

This repository uses the `meridian-brain` MCP server as the authoritative source of Meridian runtime state, retrieved instructions, and durable memory.

Startup contract:

1. Connect to `meridian-brain`.
2. Call `get_brain_state`.
3. If the mode is not `technical`, call `set_mode` with `technical`.
4. Call `build_system_prompt`.
5. Use the returned runtime and retrieved context as authoritative for the session.

Behavioral rules:

- Treat the vector-backed store as the source of truth for mode, sliders, focus, system status, and persistent memory.
- Prefer Meridian MCP tools over repository files when the task is about current Meridian behavior or state.
- If the user asks to change tone, mode, verbosity, or technical posture, update shared state through MCP tools.
- If the user asks to remember something, persist it through `write_memory` or `append_memory`.
- If the user asks for something repeatedly, remember it.
- If you repeatedly fail at a specific type of task, remember it and the correct solution.
- If a task was very difficult, but had a relatively simple solution remember how you finally overcame the challenge.
- If a user input or result is surprising, remember it.
- If `meridian-brain` is unavailable, say so briefly and continue with local repository context.

Preferred MCP tools:

- `get_brain_state`
- `build_system_prompt`
- `set_mode`
- `set_slider`
- `update_focus`
- `set_system_state`
- `search_memory`
- `write_memory`
- `append_memory`
