@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call setup.bat
  if errorlevel 1 exit /b 1
)
echo Starting Deep Research for phone access on the same Wi-Fi...
echo Keep this window open. The phone URL and login will be shown below.
".venv\Scripts\python.exe" -u mobile_start.py
if errorlevel 1 (
  echo.
  echo Mobile startup failed. See the message above.
  pause
  exit /b 1
)
