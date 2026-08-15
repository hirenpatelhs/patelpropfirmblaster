param([string]$InstallRoot = "C:\PatelPropfirmBlaster")
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
& "$InstallRoot\scripts\backup-database.ps1" -InstallRoot $InstallRoot
Stop-Service PatelPropfirmFrontend, PatelPropfirmWorker, PatelPropfirmBackend -ErrorAction SilentlyContinue
Set-Location "$InstallRoot\backend"
& .\venv\Scripts\python.exe -m pip install -r requirements-windows.txt
& .\venv\Scripts\alembic.exe upgrade head
Set-Location "$InstallRoot\frontend"
& npm.cmd ci
& npm.cmd run build
Start-Service PatelPropfirmBackend, PatelPropfirmWorker, PatelPropfirmFrontend
Write-Host "Update $stamp complete." -ForegroundColor Green
