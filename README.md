# Project Meridian

Meridian is a local vector-backed runtime for agent state, retrieved operating context, and durable memory.

The markdown brain has been removed. Runtime data now lives in `.meridian/meridian.sqlite3`, and the MCP server retrieves the most relevant operational documents and memory records directly from that store.

## Architecture

- SQLite stores runtime state, operational documents, and memory.
- Each stored document is split into chunks, embedded into dense vectors, and indexed for hybrid retrieval.
- Search combines vector similarity with SQLite FTS lexical ranking.
- Prompt assembly is retrieval-driven instead of concatenating static markdown files.
- Meridian now defaults to local `BAAI/bge-m3` embeddings via `sentence-transformers`.
- OpenAI embeddings remain available as an explicit opt-in backend.
- The deterministic hash embedder remains available as a lightweight fallback for tests or constrained environments.
- Reindexing is selective: only documents affected by content or embedding-config changes are re-embedded.

## What the server exposes

- Shared runtime state for mode, sliders, focus, and system status
- Hybrid search over operational documents
- Hybrid search over semantic memory
- Hybrid search over episodic history
- Semantic memory write/update operations
- Episodic history logging and resume-context retrieval
- Prompt assembly from the live store

## Setup

```powershell
python -m pip install -e .
```

## Run

```powershell
python -m meridian_mcp.server
```

Or:

```powershell
python -m meridian_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000
```

## Dashboard

Run the local dashboard:

```powershell
python -m meridian_mcp.dashboard
```

Or:

```powershell
meridian-dashboard
```

By default the dashboard is served at `http://127.0.0.1:8765` and manages a dedicated Meridian HTTP service at `http://127.0.0.1:8000/mcp`.

The dashboard shows:

- Whether the managed Meridian service is running
- Start and stop controls
- Database file size and stored content size
- The active embedding backend and model signature
- Daily bytes added and daily bytes retrieved
- A dynamic category map of stored documents

Retrieval history is only available from the point this build started recording usage events.

## Storage

- Local store path: `.meridian/meridian.sqlite3`
- The store is ignored by git.
- Seed operational documents are initialized automatically if the database does not exist.

## Memory model

- Semantic memory stores durable facts, preferences, project details, and corrections.
- Episodic history stores time-ordered summaries of what happened, such as milestones, decisions, and recent session outcomes.
- Prompt assembly now uses a retrieval gate: it only injects semantic or episodic context when the task strongly suggests prior context is needed.

Useful tools:

- `search_memory(query)` for semantic facts
- `search_episodes(query)` for time-ordered history
- `remember_fact(name, content, category)` for durable facts
- `log_episode(summary, title, tags)` for recent work history
- `resume_context(query)` for combined semantic + episodic resume support

## Semantic recall

- Default backend: `bge-m3`
- Default local model: `BAAI/bge-m3`
- Optional backend override: `MERIDIAN_EMBEDDING_BACKEND` with `bge-m3`, `openai`, or `hash`
- Optional local-path override: `MERIDIAN_EMBEDDING_MODEL_PATH`
- Optional BGE model override: `MERIDIAN_BGE_M3_MODEL`
- Optional OpenAI model override: `MERIDIAN_OPENAI_EMBEDDING_MODEL`
- Optional batch-size override: `MERIDIAN_EMBEDDING_BATCH_SIZE`
- Optional max-length override: `MERIDIAN_EMBEDDING_MAX_LENGTH`
- Optional FP16 override: `MERIDIAN_EMBEDDING_USE_FP16`
- OpenAI-only options: `OPENAI_API_KEY`, `MERIDIAN_EMBEDDING_DIMENSIONS`

`MERIDIAN_EMBEDDING_MODEL` is still accepted as a legacy override for whichever backend you explicitly select, but new setups should prefer the backend-specific variables above.

If you keep local models outside the default Hugging Face cache, point `MERIDIAN_EMBEDDING_MODEL_PATH` at your local `bge-m3` directory.

## Validation

```powershell
python -m unittest discover -s tests -v
```
