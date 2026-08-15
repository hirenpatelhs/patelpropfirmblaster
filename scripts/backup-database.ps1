param([string]$InstallRoot = "C:\PatelPropfirmBlaster", [string]$DatabaseUrl = $env:PPB_DATABASE_URL)
$ErrorActionPreference = "Stop"
if (-not $DatabaseUrl) { throw "Set PPB_DATABASE_URL to a libpq-compatible PostgreSQL URL before backup." }
$backupDir = Join-Path $InstallRoot "backups"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
$target = Join-Path $backupDir ("ppb-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".dump")
& pg_dump --format=custom --file=$target $DatabaseUrl
if ($LASTEXITCODE -ne 0) { throw "Database backup failed." }
Get-ChildItem $backupDir -Filter "ppb-*.dump" | Where-Object LastWriteTime -lt (Get-Date).AddDays(-30) | Remove-Item -Force
Write-Host "Backup created: $target" -ForegroundColor Green
