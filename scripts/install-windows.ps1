param([string]$InstallRoot = "C:\PatelPropfirmBlaster")
$ErrorActionPreference = "Stop"

function Require-Command([string]$Name, [string]$Help) {
  if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name is required. $Help" }
}

Write-Host "Installing Patel Propfirm Blaster into $InstallRoot" -ForegroundColor Cyan
Require-Command "python" "Install Python 3.12+ and enable Add Python to PATH."
Require-Command "node" "Install the current Node.js LTS release."
Require-Command "npm" "npm is installed with Node.js."
Require-Command "psql" "Install PostgreSQL and add its bin directory to PATH."
Require-Command "nssm" "Download NSSM and add nssm.exe to PATH before registering services."

$pythonVersion = (& python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
if ([version]$pythonVersion -lt [version]"3.12") { throw "Python 3.12+ is required; found $pythonVersion" }
$nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
if ($nodeMajor -lt 20) { throw "Node.js LTS 20+ is required." }

New-Item -ItemType Directory -Force -Path $InstallRoot, "$InstallRoot\logs", "$InstallRoot\data\telegram", "$InstallRoot\backups" | Out-Null
Copy-Item -Path "$PSScriptRoot\..\*" -Destination $InstallRoot -Recurse -Force -Exclude @("venv", "node_modules", ".next", ".git")

Set-Location "$InstallRoot\backend"
if (-not (Test-Path "venv")) { & python -m venv venv }
& .\venv\Scripts\python.exe -m pip install --upgrade pip
& .\venv\Scripts\python.exe -m pip install -r requirements-windows.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Warning "Edit backend\.env before starting services." }

Set-Location "$InstallRoot\frontend"
& npm.cmd ci
if (-not (Test-Path ".env.local")) { Copy-Item ".env.example" ".env.local" }
& npm.cmd run build

Write-Host "Files installed. Configure PostgreSQL, Redis, Telegram and encryption values in backend\.env." -ForegroundColor Yellow
Write-Host "Then run: cd $InstallRoot\backend; .\venv\Scripts\alembic.exe upgrade head" -ForegroundColor Yellow
Write-Host "Finally run scripts\register-services.ps1 -InstallRoot '$InstallRoot'" -ForegroundColor Green
