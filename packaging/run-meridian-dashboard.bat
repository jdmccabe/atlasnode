@echo off
setlocal

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Meridian is not installed yet.
  echo Run install-meridian.bat first.
  pause
  exit /b 1
)

set "MERIDIAN_EMBEDDING_BACKEND=bge-m3"
set "MERIDIAN_EMBEDDING_MODEL_PATH=%~dp0models\BAAI--bge-m3"

start "" /min ".venv\Scripts\python.exe" -m meridian_mcp.dashboard
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765

endlocal
