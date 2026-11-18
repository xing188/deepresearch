@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup is required.
  call setup.bat
  if errorlevel 1 exit /b 1
)
".venv\Scripts\python.exe" -c "import fastapi, langgraph, langchain_openai, langchain_mcp_adapters, bleach" >nul 2>&1
if errorlevel 1 (
  echo Dependencies are missing. Running setup...
  call setup.bat
  if errorlevel 1 exit /b 1
)
echo Starting LangChain Open Deep Research...
echo The browser will open automatically. The server prints its URL below.
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -u server.py --open
if errorlevel 1 (
  echo.
  echo Startup failed. The error is shown above.
  pause
  exit /b 1
)
