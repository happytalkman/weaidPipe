$env:JARVIS_CLI = '1'
$env:JARVIS_SKIP_CLAP_GATE = '1'
$env:PYTHONIOENCODING = 'utf-8'
Write-Host '[WEAID] AI Voice Assistant Starting...' -ForegroundColor Cyan
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\main.py"
