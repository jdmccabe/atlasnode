@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo AtlasNode is not installed yet.
  echo Run install-atlasnode.bat first.
  pause
  exit /b 1
)

set "ATLASNODE_EMBEDDING_BACKEND=bge-m3"
set "ATLASNODE_EMBEDDING_MODEL_PATH=%~dp0models\BAAI--bge-m3"

".venv\Scripts\python.exe" -m atlasnode_mcp.server --transport streamable-http --host 127.0.0.1 --port 8000

endlocal

