from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from atlasnode_mcp.store import AtlasNodeStore


store = AtlasNodeStore()

mcp = FastMCP(
    name="atlasnode-brain",
    instructions=(
        "Use AtlasNode's SQLite hybrid vector store as the source of truth for runtime state, "
        "instruction retrieval, and durable memory."
    ),
)


def _document_text(identifier: str) -> str:
    document = store.read_document(identifier)
    return document["content"]


def _search_response(query: str, doc_types: set[str], limit: int) -> list[dict[str, str | int | float]]:
    matches = store.search_documents(query, doc_types, limit=limit)
    return [
        {
            "path": match["id"],
            "line": 1,
            "text": match["excerpt"],
            "score": match["score"],
            "title": match["title"],
            "type": match["type"],
        }
        for match in matches
    ]


@mcp.resource("atlasnode://master-spec")
def master_spec_resource() -> str:
    return _document_text("system/core")


@mcp.resource("atlasnode://readme")
def brain_readme_resource() -> str:
    return _document_text("system/overview")


@mcp.resource("atlasnode://state")
def brain_state_resource() -> str:
    return json.dumps(store.state_response(), indent=2)


@mcp.resource("atlasnode://document/{doc_id}")
def brain_document_resource(doc_id: str) -> str:
    return _document_text(doc_id)


@mcp.tool()
def list_brain_files() -> list[str]:
    """List logical AtlasNode documents available in the vector store."""
    return store.list_document_ids(include_memory=False)


@mcp.tool()
def read_brain_file(relative_path: str) -> str:
    """Read a logical AtlasNode document by ID or legacy alias."""
    return _document_text(relative_path)


@mcp.tool()
def search_brain(query: str) -> list[dict[str, str | int | float]]:
    """Search non-memory AtlasNode documents using hybrid vector and lexical retrieval."""
    return _search_response(query, {"system", "protocol", "profile", "mode"}, limit=10)


@mcp.tool()
def list_memories() -> list[str]:
    """List persisted memories from the vector store."""
    return store.list_memories()


@mcp.tool()
def list_episodes(limit: int = 50) -> list[str]:
    """List recent episodic history records from the vector store."""
    return store.list_episodes(limit=limit)


@mcp.tool()
def search_memory(query: str, limit: int = 10) -> list[dict[str, str | int | float]]:
    """Search persisted memories using hybrid vector and lexical retrieval."""
    return _search_response(query, {"memory"}, limit=limit)


@mcp.tool()
def search_episodes(query: str, limit: int = 10) -> list[dict[str, str | int | float]]:
    """Search episodic history records using hybrid vector and lexical retrieval."""
    return _search_response(query, {"episode"}, limit=limit)


@mcp.tool()
def write_memory(name: str, content: str, overwrite: bool = False) -> str:
    """Write a durable memory record into the vector store."""
    return store.write_memory(name, content, overwrite=overwrite)


@mcp.tool()
def remember_fact(name: str, content: str, category: str = "general", overwrite: bool = True) -> str:
    """Write or update a semantic fact memory with an optional category."""
    return store.remember_fact(name, content, category=category, overwrite=overwrite)


@mcp.tool()
def append_memory(name: str, content: str) -> str:
    """Append to an existing memory record or create it if missing."""
    return store.append_memory(name, content)


@mcp.tool()
def log_episode(summary: str, title: str | None = None, tags: list[str] | None = None) -> str:
    """Log a time-ordered episodic summary for recent work, decisions, or session outcomes."""
    return store.log_episode(summary, title=title, tags=tags)


@mcp.tool()
def recent_episodes(limit: int = 8, days: int = 7) -> list[dict[str, Any]]:
    """Return recent episodic history records ordered by recency."""
    return store.recent_episodes(limit=limit, days=days)


@mcp.tool()
def resume_context(
    query: str | None = None,
    memory_limit: int = 5,
    episode_limit: int = 5,
    days: int = 7,
) -> dict[str, Any]:
    """Return combined semantic and episodic context for resuming work."""
    return store.resume_context(
        query=query,
        memory_limit=memory_limit,
        episode_limit=episode_limit,
        days=days,
    )


@mcp.tool()
def get_brain_state() -> dict[str, Any]:
    """Return the shared AtlasNode runtime state."""
    return store.state_response()


@mcp.tool()
def set_mode(mode: str) -> dict[str, Any]:
    """Set the active AtlasNode mode and apply its slider preset."""
    return store.set_mode(mode)


@mcp.tool()
def set_slider(slider: str, value: int) -> dict[str, Any]:
    """Set a single slider in the shared AtlasNode runtime state."""
    return store.set_slider(slider, value)


@mcp.tool()
def reset_brain_state() -> dict[str, Any]:
    """Reset the shared AtlasNode state to defaults."""
    return store.reset_state()


@mcp.tool()
def update_focus(present: str, past: str | None = None, future: str | None = None) -> dict[str, Any]:
    """Update the shared focus fields used by the current runtime state."""
    return store.update_focus(present=present, past=past, future=future)


@mcp.tool()
def set_system_state(
    context: str | None = None,
    tools: str | None = None,
    vibe: str | None = None,
) -> dict[str, Any]:
    """Update shared system-state fields."""
    return store.set_system_state(context=context, tools=tools, vibe=vibe)


@mcp.tool()
def build_system_prompt(
    task: str | None = None,
    include_memory_summary: bool = True,
    memory_limit: int = 8,
) -> str:
    """Build the canonical AtlasNode system prompt from the vector-backed store."""
    return store.build_system_prompt(
        task=task,
        include_memory_summary=include_memory_summary,
        memory_limit=memory_limit,
    )


@mcp.prompt()
def activate_brain\(
    task: str | None = None,
    include_memory_summary: bool = True,
    memory_limit: int = 8,
) -> str:
    return build_system_prompt(
        task=task,
        include_memory_summary=include_memory_summary,
        memory_limit=memory_limit,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AtlasNode local MCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "sse", "streamable-http"),
        default="stdio",
        help="Transport to expose.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for HTTP-based transports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP-based transports.",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()


