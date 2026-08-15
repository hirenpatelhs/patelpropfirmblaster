param(
  [Parameter(Mandatory=$true)][string]$DatabaseUrl,
  [Parameter(Mandatory=$true)][string]$RedisUrl,
  [string]$Root = (Split-Path -Parent $PSScriptRoot),
  [string]$PostgresTestServiceName = "",
  [string]$RedisTestServiceName = ""
)
$ErrorActionPreference = "Stop"
if ($DatabaseUrl -notmatch '(?i)test') { throw "DatabaseUrl must name a dedicated TEST database." }
if ($RedisUrl -match '/0(?:\?|$)' -or $RedisUrl -notmatch '/[1-9][0-9]*(?:\?|$)') { throw "RedisUrl must use a dedicated nonzero Redis database." }
$env:DATABASE_URL = $DatabaseUrl
$env:REDIS_URL = $RedisUrl
$env:PPB_RUN_REAL_INTEGRATION = "1"
$env:ENVIRONMENT = "integration-test"
$env:APP_SECRET = "integration-test-only-not-for-production"
$env:WORKER_CLAIM_IDLE_MS = "1000"
if ($PostgresTestServiceName) {
  $env:PPB_TEST_POSTGRES_STOP_JSON = ConvertTo-Json @("powershell.exe", "-NoProfile", "-Command", "Stop-Service -Name '$PostgresTestServiceName' -Force") -Compress
  $env:PPB_TEST_POSTGRES_START_JSON = ConvertTo-Json @("powershell.exe", "-NoProfile", "-Command", "Start-Service -Name '$PostgresTestServiceName'") -Compress
}
if ($RedisTestServiceName) {
  $env:PPB_TEST_REDIS_STOP_JSON = ConvertTo-Json @("powershell.exe", "-NoProfile", "-Command", "Stop-Service -Name '$RedisTestServiceName' -Force") -Compress
  $env:PPB_TEST_REDIS_START_JSON = ConvertTo-Json @("powershell.exe", "-NoProfile", "-Command", "Start-Service -Name '$RedisTestServiceName'") -Compress
}
Set-Location (Join-Path $Root "backend")
& .\venv\Scripts\python.exe -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Test database migration failed." }
& .\venv\Scripts\python.exe -m pytest tests\integration -q
if ($LASTEXITCODE -ne 0) { throw "Real-infrastructure integration tests failed." }
