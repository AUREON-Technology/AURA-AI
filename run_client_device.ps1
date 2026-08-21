# AURA AI Flutter Client — physical Android debug run
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env
# (falls back to deploy/.env.example), syncs aura_client/config/device.json,
# then `flutter run` with that production HTTPS URL.
# Desktop/web local debug still uses config/dev.json (localhost).

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClientDir = Join-Path $ScriptDir "aura_client"
$EnvFile = Join-Path $ScriptDir "deploy\.env"
if (-not (Test-Path $EnvFile)) {
    $EnvFile = Join-Path $ScriptDir "deploy\.env.example"
}

. (Join-Path $ScriptDir "deploy\scripts\client_backend_url.ps1")

$BACKEND_URL = Get-AuraClientBackendUrl -EnvFile $EnvFile
Assert-AuraReleaseBackendUrl -Url $BACKEND_URL

$ConfigDir = Join-Path $ClientDir "config"
if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir | Out-Null
}
$DeviceJsonPath = Join-Path $ConfigDir "device.json"
$DeviceJson = "{`n  `"BACKEND_URL`": `"$BACKEND_URL`"`n}`n"
[System.IO.File]::WriteAllText($DeviceJsonPath, $DeviceJson)

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Running AURA AI Client on device (production HTTPS)" -ForegroundColor Cyan
Write-Host "   Target Backend: $BACKEND_URL" -ForegroundColor Cyan
Write-Host "   Synced:         aura_client/config/device.json" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Do not use a plain ``flutter run`` on a physical phone —" -ForegroundColor Yellow
Write-Host "localhost is the device itself, not the PC or VPS." -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan

Set-Location $ClientDir

flutter run --dart-define=BACKEND_URL=$BACKEND_URL
