# AURA AI Flutter Client Production Build Script
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env.
# Debug `flutter run` does not use this script; it defaults to http://localhost:8000.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClientDir = Join-Path $ScriptDir "aura_client"
$EnvFile = Join-Path $ScriptDir "deploy\.env"

. (Join-Path $ScriptDir "deploy\scripts\client_backend_url.ps1")

$BACKEND_URL = Get-AuraClientBackendUrl -EnvFile $EnvFile
Assert-AuraReleaseBackendUrl -Url $BACKEND_URL

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Building AURA AI Client for Production" -ForegroundColor Cyan
Write-Host "   Target Backend: $BACKEND_URL" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Before shipping, confirm TLS matches this hostname:" -ForegroundColor Yellow
Write-Host "   bash deploy/scripts/verify-client-hostname.sh" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan

Set-Location $ClientDir

Write-Host "Cleaning Flutter build cache..." -ForegroundColor Yellow
flutter clean

Write-Host "Resolving dependencies..." -ForegroundColor Yellow
flutter pub get

Write-Host "Building production Android APK (Obfuscated & Split)..." -ForegroundColor Yellow
flutter build apk --release `
  --obfuscate `
  --split-debug-info=build/app/outputs/symbols `
  --split-per-abi `
  --dart-define=BACKEND_URL=$BACKEND_URL

if ($IsMacOS) {
  Write-Host "Building production iOS IPA (Obfuscated)..." -ForegroundColor Yellow
  flutter build ipa --release `
    --obfuscate `
    --split-debug-info=build/ios/outputs/symbols `
    --dart-define=BACKEND_URL=$BACKEND_URL
} else {
  Write-Host "Skipping iOS build (current platform is not macOS)." -ForegroundColor Gray
}

Write-Host "Building production Web bundle..." -ForegroundColor Yellow
flutter build web --release `
  --web-renderer canvaskit `
  --no-source-maps `
  --dart-define=BACKEND_URL=$BACKEND_URL

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Production builds generated." -ForegroundColor Green
Write-Host "   Android APKs: aura_client/build/app/outputs/flutter-apk/" -ForegroundColor Green
Write-Host "   Web files:    aura_client/build/web/" -ForegroundColor Green
Write-Host "   Web CORS:     set ALLOWED_ORIGINS on the gateway to the origin that hosts this bundle (plus https://`$AURA_DOMAIN)." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
