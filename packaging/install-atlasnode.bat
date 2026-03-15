@echo off
setlocal

cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto install

where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11+ is required but was not found on PATH.
  echo Install Python, then run this file again.
  pause
  exit /b 1
)

echo Creating AtlasNode virtual environment...
python -m venv ".venv"
if errorlevel 1 exit /b 1

:install
echo Installing AtlasNode package and dependencies...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1

if exist "wheelhouse" (
  echo Installing from bundled wheelhouse...
  call ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheelhouse atlasnode-mcp
  if not errorlevel 1 goto installed
  echo Bundled wheelhouse install failed. Falling back to online install...
)

call ".venv\Scripts\python.exe" -m pip install .
if errorlevel 1 exit /b 1

:installed

echo AtlasNode install complete.
echo Dashboard launcher: run-atlasnode-dashboard.bat
echo Server launcher:    run-atlasnode-server.bat
endlocal

