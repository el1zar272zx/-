@echo off
rem Starts both bots and restarts them automatically if they crash.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Python was not found. Install Python 3.11+ from python.org and try again.
        pause
        exit /b 1
    )
)

"venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    exit /b 1
)

:loop
"venv\Scripts\python.exe" run.py
echo.
echo Bots stopped. Restarting in 10 seconds. Close this window to stop.
timeout /t 10 /nobreak >nul
goto loop
