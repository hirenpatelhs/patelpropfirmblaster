param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $Root "backend")
# A single Uvicorn process keeps the Windows NSSM process tree restart-safe.
# Horizontal concurrency belongs at the service/host layer; Uvicorn's Windows
# multiprocess parent can survive an NSSM wrapper restart and retain port 8000.
& .\venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
exit $LASTEXITCODE
