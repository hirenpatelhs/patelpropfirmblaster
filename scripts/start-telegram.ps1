param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $Root "backend")
& .\venv\Scripts\python.exe -m app.telegram.main
if ($LASTEXITCODE -ne 0) { throw "Telegram listener exited with code $LASTEXITCODE" }
