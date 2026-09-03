# WEAID (PipeAId) 실행 스크립트
$env:PYTHONUTF8 = "1"
$uv = if (Test-Path "$HOME\.local\bin\uv.exe") { "$HOME\.local\bin\uv.exe" } else { "uv" }
Write-Host "🌟 WEAID Voice Assistant (WebRTC) 서버를 시작합니다..." -ForegroundColor Cyan
Write-Host "👉 접속 URL: http://localhost:7860" -ForegroundColor Green
& $uv run python bot_weaid.py -t webrtc --port 7860
