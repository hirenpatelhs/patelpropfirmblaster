param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"
$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, "Global\PatelPropfirmBlasterWorker", [ref]$createdNew)
if (-not $createdNew) {
  $mutex.Dispose()
  throw "Another Patel Propfirm Blaster worker process is already running on this host."
}
Set-Location (Join-Path $Root "backend")
try {
  & .\venv\Scripts\python.exe -m app.workers.main
  if ($LASTEXITCODE -ne 0) { throw "Worker exited with code $LASTEXITCODE" }
} finally {
  $mutex.ReleaseMutex()
  $mutex.Dispose()
}
