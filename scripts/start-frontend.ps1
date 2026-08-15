param([string]$Root = (Split-Path -Parent $PSScriptRoot))
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $Root "frontend")
$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if ($npm) {
  $npmPath = $npm.Source
  & $npmPath run start -- --hostname 0.0.0.0 --port 3000
} else {
  $projectOwnerRoot = Split-Path -Parent $Root
  $nodePath = Join-Path $projectOwnerRoot "scoop\apps\nodejs-lts\current\node.exe"
  $nextPath = Join-Path $Root "frontend\node_modules\next\dist\bin\next"
  if (-not (Test-Path -LiteralPath $nodePath)) {
    throw "npm.cmd is not on PATH and Node.js was not found in the project owner's Scoop installation."
  }
  & $nodePath $nextPath start --hostname 0.0.0.0 --port 3000
}
