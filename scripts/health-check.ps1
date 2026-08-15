param([string]$BackendUrl = "http://127.0.0.1:8000", [string]$FrontendUrl = "http://127.0.0.1:3000")
$ErrorActionPreference = "Stop"
$backend = Invoke-RestMethod "$BackendUrl/health" -TimeoutSec 10
if ($backend.status -ne "ok") { throw "Backend health check failed." }
$system = Invoke-RestMethod "$BackendUrl/api/v1/system/health" -TimeoutSec 10
Write-Host "Backend: $($system.checks.backend); Database: $($system.checks.database); Redis: $($system.checks.redis)"
$frontend = Invoke-WebRequest $FrontendUrl -UseBasicParsing -TimeoutSec 10
if ($frontend.StatusCode -ne 200) { throw "Frontend health check failed." }
Write-Host "Patel Propfirm Blaster health check completed." -ForegroundColor Green
