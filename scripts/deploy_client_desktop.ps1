# AURA AI Flutter Client Desktop Build Script (Windows)
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env, same as
# deploy_client.ps1. Separate script (not a mode of deploy_client.ps1) because the desktop
# target needs the Windows-specific Visual Studio toolchain the mobile/web build doesn't, and
# because `aura_hostd` -- the daemon the desktop app supervises (see
# docs/architecture/011-desktop-engineer-agent.md) -- is not bundled: a person still configures
# its executable path themselves, so this script does not attempt to build or embed it.
#
# Also resolves GOOGLE_DESKTOP_CLIENT_SECRET from deploy/.env (or the environment) and injects
# it via --dart-define, the one piece of desktop login (ADR-011 Unit 7) that must NEVER be
# hardcoded in source -- deploy/.env itself is untracked (same as AURA_DOMAIN/BACKEND_URL), so
# this is the only place it should ever live on disk. Missing it is not a hard build failure:
# the build still succeeds, desktop login just refuses with a clear error until it's set.
#
# Linux/macOS desktop are built by deploy_client_desktop.sh instead -- Flutter does not
# cross-compile the Windows desktop build from a non-Windows host.

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

$BACKEND_URL = Get-AuraClientBackendUrl -EnvFile $EnvFile
Assert-AuraReleaseBackendUrl -Url $BACKEND_URL

$GoogleDesktopClientSecret = $env:GOOGLE_DESKTOP_CLIENT_SECRET
if (-not $GoogleDesktopClientSecret) {
  $GoogleDesktopClientSecret = Read-AuraEnvKey -Key "GOOGLE_DESKTOP_CLIENT_SECRET" -EnvFile $EnvFile
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Building AURA AI Client for Desktop (Windows)" -ForegroundColor Cyan
Write-Host "   Target Backend: $BACKEND_URL" -ForegroundColor Cyan
if (-not $GoogleDesktopClientSecret) {
  Write-Host "   WARNING: GOOGLE_DESKTOP_CLIENT_SECRET is not set (deploy/.env or environment) --" -ForegroundColor Yellow
  Write-Host "            this build's desktop login will refuse with a clear error until it is." -ForegroundColor Yellow
} else {
  Write-Host "   Desktop OAuth client secret: configured" -ForegroundColor Cyan
}
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Before shipping, confirm TLS matches this hostname:" -ForegroundColor Yellow
Write-Host "   bash deploy/scripts/verify-client-hostname.sh" -ForegroundColor Yellow
Write-Host "==========================================================" -ForegroundColor Cyan

Set-Location $ClientDir

Write-Host "Cleaning Flutter build cache..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @("clean")

Write-Host "Resolving dependencies..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @("pub", "get")

Write-Host "Enabling Windows desktop support (no-op if already enabled)..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs @("config", "--enable-windows-desktop")

$DartDefines = @("--dart-define=BACKEND_URL=$BACKEND_URL")
if ($GoogleDesktopClientSecret) {
  $DartDefines += "--dart-define=GOOGLE_DESKTOP_CLIENT_SECRET=$GoogleDesktopClientSecret"
}

Write-Host "Building production Windows desktop bundle..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs (@("build", "windows", "--release") + $DartDefines)

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Desktop build generated." -ForegroundColor Green
Write-Host "   Windows bundle: aura_client/build/windows/x64/runner/Release/" -ForegroundColor Green
Write-Host "   aura_hostd is NOT bundled -- build/install it separately (cd aura_hostd &&" -ForegroundColor Green
Write-Host "   dart pub get && dart compile exe bin/aura_hostd.dart -o aura_hostd.exe) and point" -ForegroundColor Green
Write-Host "   the client's daemon executable-path setting at the resulting binary." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
