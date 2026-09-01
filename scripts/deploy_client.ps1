# AURA AI Flutter Client Production Build Script
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env.
# Debug `flutter run` does not use this script; it defaults to http://localhost:8000.

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ClientDir = Join-Path $RepoRoot "aura_client"
$EnvFile = Join-Path $RepoRoot "deploy\.env"

. (Join-Path $RepoRoot "deploy\scripts\client_backend_url.ps1")

function Invoke-Flutter {
  param([Parameter(Mandatory = $true)][string[]]$FlutterArgs)

  & flutter @FlutterArgs
  if ($LASTEXITCODE -ne 0) {
    throw "flutter $($FlutterArgs -join ' ') failed with exit code $LASTEXITCODE"
  }
}

function Stop-AuraGradleDaemons {
  $androidDir = Join-Path $ClientDir "android"
  $gradlew = Join-Path $androidDir "gradlew.bat"
  if (-not (Test-Path $gradlew)) {
    return
  }

  Write-Host "Stopping leftover Gradle daemons..." -ForegroundColor Yellow
  Push-Location $androidDir
  try {
    & $gradlew --stop
  } finally {
    Pop-Location
  }
}

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
Invoke-Flutter -FlutterArgs @("clean")

Write-Host "Resolving dependencies..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @("pub", "get")

Stop-AuraGradleDaemons

Write-Host "Building production Android APK (Obfuscated & Split)..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @(
  "build", "apk", "--release",
  "--obfuscate",
  "--split-debug-info=build/app/outputs/symbols",
  "--split-per-abi",
  "--dart-define=BACKEND_URL=$BACKEND_URL"
)

if ($IsMacOS) {
  Write-Host "Building production iOS IPA (Obfuscated)..." -ForegroundColor Yellow
  Invoke-Flutter -FlutterArgs @(
    "build", "ipa", "--release",
    "--obfuscate",
    "--split-debug-info=build/ios/outputs/symbols",
    "--dart-define=BACKEND_URL=$BACKEND_URL"
  )
} else {
  Write-Host "Skipping iOS build (current platform is not macOS)." -ForegroundColor Gray
}

Write-Host "Building production Web bundle..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @(
  "build", "web", "--release",
  "--no-source-maps",
  "--dart-define=BACKEND_URL=$BACKEND_URL"
)

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Production builds generated." -ForegroundColor Green
Write-Host "   Android APKs: aura_client/build/app/outputs/flutter-apk/" -ForegroundColor Green
Write-Host "   Web files:    aura_client/build/web/" -ForegroundColor Green
Write-Host "   Web CORS:     set ALLOWED_ORIGINS on the gateway to the origin that hosts this bundle (plus https://`$AURA_DOMAIN)." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
