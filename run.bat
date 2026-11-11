@echo off
cd /d "%~dp0"

set "PYTHON_LAUNCHER="
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
if not defined PYTHON_LAUNCHER (
    python -c "import sys; raise SystemExit(sys.version_info < (3, 10))" >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
)

if not defined PYTHON_LAUNCHER (
    echo No Python 3.10 or later interpreter was found.
    goto startup_failed
)

%PYTHON_LAUNCHER% run.py
if errorlevel 1 goto startup_failed
goto :eof

:startup_failed
echo.
echo Dashboard startup failed. Review the error messages above.
pause
exit /b 1
