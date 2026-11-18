@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where python >nul 2>&1
if errorlevel 1 (
  echo Python was not found. Install Python 3.12 and enable Add Python to PATH.
  pause
  exit /b 1
)
python -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,13)" >nul 2>&1
if errorlevel 1 (
  echo Install Python 3.12, then try again. Supported versions: Python 3.11-3.13.
  pause
  exit /b 1
)
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 goto failed
echo Setup complete. Double-click start.bat to launch.
exit /b 0
:failed
echo Setup failed. Check your network and the error above.
pause
exit /b 1
