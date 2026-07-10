$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist) { Remove-Item -Recurse -Force dist }
if (-not (Test-Path release)) { New-Item -ItemType Directory release | Out-Null }

py -m PyInstaller `
  --noconfirm `
  --clean `
  --windowed `
  --onefile `
  --name "BO2GscLiveInjector" `
  --add-data "templates;templates" `
  --add-data "tools;tools" `
  --add-data "xbox-gsc-dump-mp.json;." `
  --add-data "xbox-gsc-dump-zm.json;." `
  app_qt.py

$Exe = Join-Path $Root "dist\BO2GscLiveInjector.exe"
if (-not (Test-Path $Exe)) {
  throw "Build failed; exe not found: $Exe"
}

$Version = "v0.4.16"
$Zip = Join-Path $Root "release\BO2GscLiveInjector-$Version.zip"
if (Test-Path $Zip) { Remove-Item -Force $Zip }
Compress-Archive -Path $Exe, (Join-Path $Root "README.md") -DestinationPath $Zip
Copy-Item $Exe (Join-Path $Root "release\BO2GscLiveInjector.exe") -Force

Write-Host "Release zip: $Zip"
Write-Host "Exe copy:    $(Join-Path $Root 'release\BO2GscLiveInjector.exe')"
