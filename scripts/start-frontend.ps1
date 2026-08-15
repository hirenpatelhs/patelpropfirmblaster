param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $Root "frontend")
& npm.cmd run start -- --hostname 0.0.0.0 --port 3000
