@echo off
set PYTHONUTF8=1
echo [WEAID] Starting WEAID Voice Assistant at http://localhost:7860...
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    "%USERPROFILE%\.local\bin\uv.exe" run python bot_weaid.py -t webrtc --port 7860
) else (
    uv run python bot_weaid.py -t webrtc --port 7860
)
