@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Create it first: python -m venv .venv
    pause
    exit /b 1
)

echo Starting web app on http://127.0.0.1:8000 ...
echo Browser will open in 3 seconds. Press Ctrl+C here to stop.
echo.

start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8000"

".venv\Scripts\python.exe" -m uvicorn app:app --port 8000

echo.
echo [STOPPED] server exited, errorlevel=%ERRORLEVEL%
pause
