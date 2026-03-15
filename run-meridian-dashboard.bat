@echo off
setlocal

cd /d "%~dp0"
if not exist ".meridian" mkdir ".meridian"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  taskkill /PID %%P /F >nul 2>nul
)
timeout /t 1 /nobreak >nul

start "" /min "%ComSpec%" /c "cd /d ""%~dp0"" && python -m meridian_mcp.dashboard >> "".meridian\dashboard-launch.log"" 2>&1"
for /l %%I in (1,1,30) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/ -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto open_browser
  timeout /t 1 /nobreak >nul
)

:open_browser
start "" "http://127.0.0.1:8765/?ts=%RANDOM%%RANDOM%"

endlocal
