@echo off
cd /d "%~dp0"

py -3 --version >nul 2>&1
if not errorlevel 1 (
    py -3 run.py
) else (
    python run.py
)

if errorlevel 1 (
    echo.
    echo The dashboard did not start. Install Python 3 and try again.
    pause
)
