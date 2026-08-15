param([string]$InstallRoot = "C:\PatelPropfirmBlaster")
$ErrorActionPreference = "Stop"
if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) { throw "nssm.exe must be available on PATH." }

$services = @(
  @{ Name="PatelPropfirmBackend"; Script="start-backend.ps1" },
  @{ Name="PatelPropfirmWorker"; Script="start-worker.ps1" },
  @{ Name="PatelPropfirmTelegram"; Script="start-telegram.ps1" },
  @{ Name="PatelPropfirmFrontend"; Script="start-frontend.ps1" }
)
foreach ($service in $services) {
  $name = $service.Name
  if (Get-Service -Name $name -ErrorAction SilentlyContinue) {
    throw "Service '$name' is already registered. Refusing to create a duplicate service definition."
  }
  $script = Join-Path $InstallRoot "scripts\$($service.Script)"
  & nssm install $name "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Root `"$InstallRoot`""
  & nssm set $name AppDirectory $InstallRoot
  & nssm set $name AppStdout "$InstallRoot\logs\$name.out.log"
  & nssm set $name AppStderr "$InstallRoot\logs\$name.err.log"
  & nssm set $name AppRotateFiles 1
  & nssm set $name AppRotateBytes 10485760
  & nssm set $name AppExit Default Restart
  & nssm set $name AppRestartDelay 5000
  & nssm set $name AppThrottle 10000
  & nssm set $name Start SERVICE_DELAYED_AUTO_START
}
& nssm set PatelPropfirmFrontend DependOnService PatelPropfirmBackend
& nssm set PatelPropfirmTelegram DependOnService PatelPropfirmBackend
Write-Host "Services registered. Start them only after the database migration and health check pass." -ForegroundColor Green
