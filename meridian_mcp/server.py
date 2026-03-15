from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from meridian_mcp.store import MeridianStore


store = MeridianStore()

mcp = FastMCP(
    name="meridian-brain",
    instructions=(
        "Use Meridian's SQLite hybrid vector store as the source of truth for runtime state, "
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


@mcp.resource("meridian://master-spec")
def master_spec_resource() -> str:
    return _document_text("system/core")


@mcp.resource("meridian://readme")
def brain_readme_resource() -> str:
    return _document_text("system/overview")


@mcp.resource("meridian://state")
def brain_state_resource() -> str:
    return json.dumps(store.state_response(), indent=2)


@mcp.resource("meridian://document/{doc_id}")
def brain_document_resource(doc_id: str) -> str:
    return _document_text(doc_id)


@mcp.tool()
def list_brain_files() -> list[str]:
    """List logical Meridian documents available in the vector store."""
    return store.list_document_ids(include_memory=False)


@mcp.tool()
def read_brain_file(relative_path: str) -> str:
    """Read a logical Meridian document by ID or legacy alias."""
    return _document_text(relative_path)


@mcp.tool()
def search_brain(query: str) -> list[dict[str, str | int | float]]:
    """Search non-memory Meridian documents using hybrid vector and lexical retrieval."""
    return _search_response(query, {"system", "protocol", "profile", "mode"}, limit=10)


@mcp.tool()
def list_memories() -> list[str]:
    """List persisted memories from the vector store."""
    return store.list_memories()


@mcp.tool()
def search_memory(query: str, limit: int = 10) -> list[dict[str, str | int | float]]:
    """Search persisted memories using hybrid vector and lexical retrieval."""
    return _search_response(query, {"memory"}, limit=limit)


@mcp.tool()
def write_memory(name: str, content: str, overwrite: bool = False) -> str:
    """Write a durable memory record into the vector store."""
    return store.write_memory(name, content, overwrite=overwrite)


@mcp.tool()
def append_memory(name: str, content: str) -> str:
    """Append to an existing memory record or create it if missing."""
    return store.append_memory(name, content)


@mcp.tool()
def get_brain_state() -> dict[str, Any]:
    """Return the shared Meridian runtime state."""
    return store.state_response()


@mcp.tool()
def set_mode(mode: str) -> dict[str, Any]:
    """Set the active Meridian mode and apply its slider preset."""
    return store.set_mode(mode)


@mcp.tool()
def set_slider(slider: str, value: int) -> dict[str, Any]:
    """Set a single slider in the shared Meridian runtime state."""
    return store.set_slider(slider, value)


@mcp.tool()
def reset_brain_state() -> dict[str, Any]:
    """Reset the shared Meridian state to defaults."""
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
    """Build the canonical Meridian system prompt from the vector-backed store."""
    return store.build_system_prompt(
        task=task,
        include_memory_summary=include_memory_summary,
        memory_limit=memory_limit,
    )


@mcp.prompt()
def activate_meridian(
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
    parser = argparse.ArgumentParser(description="Run the Meridian local MCP server.")
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
