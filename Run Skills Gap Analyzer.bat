@echo off
setlocal
cd /d "%~dp0"

set "PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not defined PYTHON if exist "%~dp0..\..\.venv\Scripts\python.exe" set "PYTHON=%~dp0..\..\.venv\Scripts\python.exe"

if not defined PYTHON (
    where py >nul 2>&1
    if errorlevel 1 (
        where python >nul 2>&1
        if errorlevel 1 goto no_python
        python -m venv "%~dp0.venv"
    ) else (
        py -3 -m venv "%~dp0.venv"
    )
    if errorlevel 1 goto setup_failed
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
)

"%PYTHON%" -c "import requests, dotenv, pdfplumber, docx, spacy, sklearn, statsmodels, streamlit, pandas, yaml" >nul 2>&1
if errorlevel 1 (
    echo Installing or repairing the analyzer's Python packages...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 goto setup_failed
)

if not exist "%~dp0.env" (
    copy "%~dp0.env.example" "%~dp0.env" >nul
    echo Created .env from the example. Add your Adzuna keys there to enable live job fetching.
)

echo Starting the Skills Gap Analyzer dashboard...
"%PYTHON%" -m streamlit run "src\dashboard\app.py"
if errorlevel 1 goto app_failed
exit /b 0

:no_python
echo Python 3.10 or newer was not found. Install Python, then run this file again.
pause
exit /b 1

:setup_failed
echo Python environment setup failed. Check your internet connection and Python installation.
pause
exit /b 1

:app_failed
echo The dashboard stopped with an error. Review the message above.
pause
exit /b 1
