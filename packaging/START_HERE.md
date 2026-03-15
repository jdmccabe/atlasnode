# Meridian Windows Package

This package includes:

- the Meridian MCP server
- the local BGE-M3 embedding model
- a bundled Python wheelhouse for offline Meridian dependency install
- the Meridian dashboard
- the dashboard icon and `Meridian-Brain` shortcut
- an agent template for connecting other workspaces to Meridian

## First run

1. Run `install-meridian.bat`
2. Launch the dashboard with `run-meridian-dashboard.bat` or the included `Meridian-Brain.lnk`
3. If you want agents to connect over HTTP, start `run-meridian-server.bat`

## Included folders

- `meridian_mcp/` - server, store, and dashboard source
- `models/BAAI--bge-m3/` - bundled local embedding model
- `assets/` - Meridian icon assets
- `agent-template/` - starter files for wiring another workspace to Meridian

## Notes

- This package expects Python 3.11+ on the machine.
- `install-meridian.bat` installs from the bundled `wheelhouse/` first, then falls back to online install only if needed.
- The bundled `wheelhouse/` is Windows-specific and tied to the Python major/minor version used to build the package. Offline install works best with that same Python version.
- The bundled model is already local, so embeddings do not require OpenAI.
- Meridian data is stored under `.meridian/` next to the package.
