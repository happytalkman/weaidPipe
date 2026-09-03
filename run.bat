@echo off
setlocal
cd /d "%~dp0"
set JARVIS_CLI=1
set JARVIS_SKIP_CLAP_GATE=1
set PYTHONIOENCODING=utf-8
echo [WEAID] AI Voice Assistant Starting...
.\.venv\Scripts\python.exe main.py
pause
