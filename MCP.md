# Meridian Local MCP Server

The local MCP server is implemented in `meridian_mcp/server.py` and backed by a SQLite hybrid vector store.

## Storage model

- Runtime state, operational documents, and durable memory live in `.meridian/meridian.sqlite3`.
- The database is initialized automatically on first run.
- Seed operational documents are chunked, embedded, and indexed at startup.
- Meridian defaults to local `BAAI/bge-m3` embeddings and selectively reindexes documents when content or embedding configuration changes.
- OpenAI embeddings remain available through an explicit backend override.

## Resources

- `meridian://master-spec`
- `meridian://readme`
- `meridian://state`
- `meridian://document/{doc_id}`

## Tools

- `list_brain_files`
- `read_brain_file`
- `search_brain`
- `get_brain_state`
- `set_mode`
- `set_slider`
- `reset_brain_state`
- `update_focus`
- `set_system_state`
- `list_memories`
- `search_memory`
- `write_memory`
- `append_memory`
- `build_system_prompt`

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

Endpoint:

```text
http://127.0.0.1:8000/mcp
```

## Notes

- `search_brain` and `search_memory` use hybrid vector and lexical retrieval.
- Default semantic backend: `MERIDIAN_EMBEDDING_BACKEND=bge-m3`.
- Optional local model path: `MERIDIAN_EMBEDDING_MODEL_PATH`.
- Optional BGE model override: `MERIDIAN_BGE_M3_MODEL` (default: `BAAI/bge-m3`).
- Optional OpenAI model override: `MERIDIAN_OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`).
- Optional batch/max-length tuning: `MERIDIAN_EMBEDDING_BATCH_SIZE`, `MERIDIAN_EMBEDDING_MAX_LENGTH`.
- Optional FP16 toggle: `MERIDIAN_EMBEDDING_USE_FP16`.
- OpenAI-only dimensionality override: `MERIDIAN_EMBEDDING_DIMENSIONS`.
- `MERIDIAN_EMBEDDING_MODEL` remains as a legacy override when a backend is explicitly selected.
- `write_memory` and `append_memory` persist durable memory into the database, not files.
- `build_system_prompt` assembles context from runtime state plus retrieved documents and memory.
- Mode names remain `base`, `research`, `creative`, `technical`, and `concise`.
