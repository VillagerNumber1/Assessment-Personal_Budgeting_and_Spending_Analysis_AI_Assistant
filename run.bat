@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Create it first: python -m venv .venv
    pause
    exit /b 1
)

echo Starting gui.py with .venv ...
".venv\Scripts\python.exe" "%~dp0gui.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [EXIT] errorlevel=%RC%
    pause
)

exit /b %RC%
