# AtlasNode Windows Package

This package includes:

- the AtlasNode MCP server
- the local BGE-M3 embedding model
- a bundled Python wheelhouse for offline AtlasNode dependency install
- the AtlasNode dashboard
- the dashboard icon and `AtlasNode.lnk` shortcut
- an agent template for connecting other workspaces to AtlasNode

## First run

1. Run `install-atlasnode.bat`
2. Launch the dashboard with `run-atlasnode-dashboard.bat` or the included `AtlasNode.lnk`
3. If you want agents to connect over HTTP, start `run-atlasnode-server.bat`

## Included folders

- `atlasnode_mcp/` - server, store, and dashboard source
- `models/BAAI--bge-m3/` - bundled local embedding model
- `assets/` - AtlasNode icon assets
- `agent-template/` - starter files for wiring another workspace to AtlasNode

## Notes

- This package expects Python 3.11+ on the machine.
- `install-atlasnode.bat` installs from the bundled `wheelhouse/` first, then falls back to online install only if needed.
- The bundled `wheelhouse/` is Windows-specific and tied to the Python major/minor version used to build the package. Offline install works best with that same Python version.
- The bundled model is already local, so embeddings do not require OpenAI.
- AtlasNode data is stored under `.atlasnode/` next to the package.





