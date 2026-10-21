@echo off
setlocal
cd /d "%~dp0"
set "MISTSAFE_NODE=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
where node >nul 2>&1
if not errorlevel 1 set "MISTSAFE_NODE=node"
if not exist "node_modules\vite\bin\vite.js" (
  echo Dependencies are missing. Install Node.js and run npm install in this folder first.
  pause
  exit /b 1
)
echo Starting MistSafe AI. Keep this window open. Press Ctrl+C to stop.
echo If port 3000 is busy, Vite will select the next available port.
"%MISTSAFE_NODE%" node_modules\vite\bin\vite.js --host 127.0.0.1 --port 3000 --open
if errorlevel 1 pause
