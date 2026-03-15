# AtlasNode Agent Template

Copy this folder into another workspace when you want that workspace to use AtlasNode.

## Recommended setup

1. Start AtlasNode with `run-atlasnode-server.bat` from the package root.
2. Copy `AGENTS.md` into the target workspace.
3. Copy `.vscode/mcp-http.json` to the target workspace as `.vscode/mcp.json`.

## Alternative setup

If you want a workspace to launch AtlasNode directly instead of using the HTTP endpoint, start from `.vscode/mcp-stdio-template.json` and replace `C:\\Path\\To\\AtlasNode-Windows-Package` with the actual package folder path.

