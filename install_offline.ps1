param(
  [string]$VenvDir = ".venv",
  [string]$PythonBin = "python",
  [string]$Wheelhouse = ""
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not $Wheelhouse) {
  $Wheelhouse = Join-Path $RootDir "offline\wheelhouse"
}

if (-not [System.IO.Path]::IsPathRooted($VenvDir)) {
  $VenvDir = Join-Path $RootDir $VenvDir
}

if (-not (Test-Path -Path $Wheelhouse -PathType Container)) {
  throw "Offline wheelhouse is missing: $Wheelhouse"
}

$Wheel = Get-ChildItem -Path $Wheelhouse -Filter "*.whl" -File | Select-Object -First 1
if (-not $Wheel) {
  throw "Offline wheelhouse is empty: $Wheelhouse. Build or download a tarball that includes offline/wheelhouse/*.whl."
}

Write-Host "Installing from offline wheelhouse: $Wheelhouse"
& $PythonBin -m venv $VenvDir

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$ConsoleUpgrade = Join-Path $VenvDir "Scripts\console-upgrade.exe"
$DashboardConfig = Join-Path $VenvDir "Scripts\dashboard-config.exe"

& $VenvPython -m pip install --no-index --find-links $Wheelhouse "setuptools>=68" "wheel>=0.41"
& $VenvPython -m pip install --no-index --find-links $Wheelhouse --no-build-isolation $RootDir

& $ConsoleUpgrade --help | Out-Null
& $DashboardConfig --help | Out-Null

Write-Host "Installed cisco-device-onboard into $VenvDir"
Write-Host "Run: $VenvDir\Scripts\Activate.ps1"
Write-Host "Commands: console-upgrade, dashboard-config"
