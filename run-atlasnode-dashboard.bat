@echo off
setlocal

cd /d "%~dp0"
if not exist ".atlasnode" mkdir ".atlasnode"
set "ATLASNODE_DASHBOARD_URL=http://127.0.0.1:8765/?ts=%RANDOM%%RANDOM%"

set "ATLASNODE_EMBEDDING_BACKEND=bge-m3"
set "ATLASNODE_EMBEDDING_MODEL_PATH="
if exist "%~dp0models\BAAI--bge-m3\config.json" (
  set "ATLASNODE_EMBEDDING_MODEL_PATH=%~dp0models\BAAI--bge-m3"
) else if exist "%~dp0BAAI--bge-m3\config.json" (
  set "ATLASNODE_EMBEDDING_MODEL_PATH=%~dp0BAAI--bge-m3"
) else if exist "%~dp0packaging\models\BAAI--bge-m3\config.json" (
  set "ATLASNODE_EMBEDDING_MODEL_PATH=%~dp0packaging\models\BAAI--bge-m3"
) else if exist "%~dp0packaging\BAAI--bge-m3\config.json" (
  set "ATLASNODE_EMBEDDING_MODEL_PATH=%~dp0packaging\BAAI--bge-m3"
) else (
  set "ATLASNODE_EMBEDDING_BACKEND=hash"
  echo Bundled BGE-M3 model not found. Falling back to hash embeddings.>> ".atlasnode\dashboard-launch.log"
)

echo Starting AtlasNode dashboard...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":8765 .*LISTENING"') do (
  taskkill /PID %%P /F >nul 2>nul
)
timeout /t 1 /nobreak >nul

start "" /min "%ComSpec%" /c "cd /d ""%~dp0"" && python -m atlasnode_mcp.dashboard >> "".atlasnode\dashboard-launch.log"" 2>&1"
for /l %%I in (1,1,30) do (
  powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/ -TimeoutSec 2; exit 0 } catch { exit 1 }" >nul 2>nul
  if not errorlevel 1 goto open_browser
  timeout /t 1 /nobreak >nul
)

echo Dashboard did not start within 30 seconds.
echo Recent log output:
powershell -NoProfile -Command "Get-Content -Path '.atlasnode\\dashboard-launch.log' -Tail 40" 2>nul
pause
exit /b 1

:open_browser
echo Opening AtlasNode dashboard in your browser...
powershell -NoProfile -Command "Start-Process '%ATLASNODE_DASHBOARD_URL%'" >nul 2>nul

endlocal

