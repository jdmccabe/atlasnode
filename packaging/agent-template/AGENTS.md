# AtlasNode Agent Template

This workspace uses the `atlasnode-brain` MCP server as the authoritative source of AtlasNode runtime state, retrieved instructions, and durable memory.

Memory architecture:

- Working memory: the current chat and visible workspace state.
- AtlasNode long-term memory: the vector-backed store of durable facts, preferences, project details, and episodic history.
- Semantic memory: durable facts and preferences retrieved by meaning.
- Episodic history: time-ordered summaries of work, decisions, and recent progress.

Startup contract:

1. Connect to `atlasnode-brain`.
2. Call `get_brain_state`.
3. If the mode is not `technical`, call `set_mode` with `technical`.
4. Call `build_system_prompt`.
5. Use the returned runtime and retrieved context as authoritative for the session.

Per-message decision loop:

- Treat the vector-backed store as the source of truth for mode, sliders, focus, system status, and persistent memory.
- Prefer AtlasNode MCP tools over repository files when the task is about current AtlasNode behavior or state.
- Before answering, ask: do I already have enough context from the current chat and visible workspace?
- Only consult long-term memory when the request depends on prior context that is not already present.
- If recall is needed, choose the tool deliberately:
  - Use `search_memory` for durable facts, preferences, project details, identity, and stable technical context.
  - Use `search_episodes` for recent events, progress, prior sessions, decisions over time, or questions like "what did we do last time?"
  - Use `search_procedures` for reusable workflows, operating rules, build/verification steps, and other recurring how-to guidance.
  - Use `resume_context` for vague resume-style prompts such as "what should I do next?", "where did we leave off?", or "start the next phase."
  - Use `latest_project_status` when the user asks for the current status, next steps, or project direction and you want the strongest project facts plus recent work history together.
  - When context belongs to a specific user, workspace, or thread, pass `scope` and `namespace` so recall stays targeted instead of searching the full shared pool.
- After retrieval, synthesize the returned memory with the current request before responding.

Memory write rules:

- If the user asks to change tone, mode, verbosity, or technical posture, update shared state through MCP tools.
- Before storing anything, ask: is this likely to matter again later?
- Do not store low-value chatter such as greetings, thanks, or one-off filler.
- Persist durable facts and preferences through `remember_fact`, `write_memory`, or `append_memory`.
- Persist recent work history through `log_episode`.
- Prefer `remember_fact` when the information is a durable fact and choose a category such as:
  - `preference`
  - `project_detail`
  - `user_bio`
  - `workflow`
  - `technical`
  - `general`
- Use stable topic names with `remember_fact` so future `overwrite=True` calls update the same fact instead of creating near-duplicates.
- Use scoped writes when the fact only applies to one workspace, one user, or one thread.
- Prefer `log_episode` when the information is a dated event, milestone, handoff note, outcome, or recent decision.
- Prefer `remember_procedure` when the information is reusable workflow guidance, an operating rule, or a recurring build/verification sequence.
- If a new fact supersedes an older one, overwrite the existing fact for that topic instead of creating a near-duplicate.
- If a whole conversation or session may contain several useful memories, queue it for background extraction instead of trying to write every possible memory synchronously.
- If the user asks for something repeatedly, remember it.
- If a user input or result is surprising, remember it if it is likely to help later.

Safety and privacy:

- Never store passwords, API keys, secrets, or sensitive credentials in AtlasNode memory.
- Do not store private information unless it is clearly useful for future assistance and appropriate to retain.

Response behavior:

- If memory helped answer the request, briefly acknowledge it when useful, for example: "Based on previous work on this project..."
- If recall returns nothing and the missing context matters, say so plainly and ask for a refresh rather than bluffing.
- For vague prompts like "what should I do next?", prefer checking recent project context first instead of guessing.

Preferred MCP tools:

- `get_brain_state`
- `build_system_prompt`
- `set_mode`
- `set_slider`
- `update_focus`
- `set_system_state`
- `search_memory`
- `search_episodes`
- `search_procedures`
- `resume_context`
- `latest_project_status`
- `remember_fact`
- `remember_procedure`
- `write_memory`
- `append_memory`
- `log_episode`
- `queue_background_extraction`
- `process_pending_extractions`


