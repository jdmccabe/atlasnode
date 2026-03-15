@echo off
setlocal

cd /d "%~dp0"

start "" /min cmd /c "cd /d "%~dp0" && python -m meridian_mcp.dashboard"
timeout /t 2 /nobreak >nul
start "" http://127.0.0.1:8765

endlocal
