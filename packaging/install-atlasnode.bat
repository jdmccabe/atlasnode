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
call :ensure_model
if errorlevel 1 exit /b 1

echo AtlasNode install complete.
echo Dashboard launcher: run-atlasnode-dashboard.bat
echo Server launcher:    run-atlasnode-server.bat
endlocal
exit /b 0

:ensure_model
if exist "models\BAAI--bge-m3\config.json" exit /b 0
if exist "BAAI--bge-m3\config.json" exit /b 0

if exist "BAAI--bge-m3-package.zip" goto extract_model_zip
if exist "BAAI--bge-m3-package.zip.001" goto extract_model_parts

echo Local BGE-M3 model not found in this package.
echo AtlasNode will fall back to hash embeddings until a local model copy is added.
exit /b 0

:extract_model_zip
echo Extracting bundled BGE-M3 model archive...
powershell -NoProfile -Command ^
  "$ErrorActionPreference='Stop';" ^
  "New-Item -ItemType Directory -Force -Path 'models' | Out-Null;" ^
  "Expand-Archive -LiteralPath 'BAAI--bge-m3-package.zip' -DestinationPath 'models' -Force;"
if errorlevel 1 (
  echo Failed to extract BGE-M3 model archive.
  exit /b 1
)
exit /b 0

:extract_model_parts
echo Reassembling bundled BGE-M3 model archive...
powershell -NoProfile -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$parts = Get-ChildItem -LiteralPath '.' -Filter 'BAAI--bge-m3-package.zip.*' | Sort-Object Name;" ^
  "if (-not $parts) { throw 'Model archive parts were not found.' }" ^
  "$zipPath = Join-Path (Get-Location) 'BAAI--bge-m3-package.zip';" ^
  "$out = [System.IO.File]::Create($zipPath);" ^
  "try {" ^
  "  foreach ($part in $parts) {" ^
  "    $bytes = [System.IO.File]::ReadAllBytes($part.FullName);" ^
  "    $out.Write($bytes, 0, $bytes.Length);" ^
  "  }" ^
  "} finally {" ^
  "  $out.Dispose();" ^
  "}" ^
  "New-Item -ItemType Directory -Force -Path 'models' | Out-Null;" ^
  "Expand-Archive -LiteralPath $zipPath -DestinationPath 'models' -Force;"
if errorlevel 1 (
  echo Failed to reassemble or extract BGE-M3 model archive parts.
  exit /b 1
)
exit /b 0

