@echo off
rem Live2D preview launcher. Keep this file ASCII-only: cmd reads .bat in GBK
rem and non-ASCII characters will swallow the following bytes.
setlocal
cd /d "%~dp0"

set "PORT=8137"
set "PY=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

echo.
echo   Serving:  %~dp0
echo   Demo:     http://127.0.0.1:%PORT%/demo.html
echo   Stop:     close the minimized "live2d-preview" window
echo.

rem "%~dp0." instead of "%~dp0" on purpose: a trailing backslash before the
rem closing quote would escape it and break the --directory argument.
start "live2d-preview" /min "%PY%" -m http.server %PORT% --bind 127.0.0.1 --directory "%~dp0."
timeout /t 2 >nul
start "" "http://127.0.0.1:%PORT%/demo.html"

pause
