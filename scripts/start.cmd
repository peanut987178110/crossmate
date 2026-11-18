@echo off
REM CrossMate launcher (ASCII only; run scripts\dev.ps1 for test mode)
setlocal
cd /d "%~dp0..\backend"
if "%PORT%"=="" set PORT=8000
echo CrossMate: http://127.0.0.1:%PORT%/app/index.html
echo API docs:  http://127.0.0.1:%PORT%/docs
python -m app.main
endlocal