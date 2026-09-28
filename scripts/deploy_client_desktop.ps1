# AURA AI Flutter Client Desktop Build Script (Windows)
# Run from anywhere. Resolves BACKEND_URL from AURA_DOMAIN in deploy/.env, same as
# deploy_client.ps1. Separate script (not a mode of deploy_client.ps1) because the desktop
# target needs the Windows-specific Visual Studio toolchain the mobile/web build doesn't, and
# because `aura_hostd` -- the daemon the desktop app supervises (see
# docs/architecture/011-desktop-engineer-agent.md) -- is not bundled: a person still configures
# its executable path themselves, so this script does not attempt to build or embed it.
#
# Desktop login (ADR-011 Unit 7) needs no credential injected here: it is a loopback OAuth flow
# against a public "Desktop app" client and authenticates with PKCE alone (RFC 8252 section 8.5),
# so the only build-time input is BACKEND_URL. This script used to resolve a
# GOOGLE_DESKTOP_CLIENT_SECRET from deploy/.env and pass it as --dart-define; that existed only
# because the registered OAuth client was of the confidential "Web application" type and its
# token endpoint refused a secret-less exchange. Fixing the client registration removed the need,
# so nothing reads the key any more -- see aura_client/lib/core/config/config.dart's
# googleDesktopClientSecret, which survives as a --dart-define-only escape hatch for a
# deployment that deliberately points at a confidential client.
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

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Building AURA AI Client for Desktop (Windows)" -ForegroundColor Cyan
Write-Host "   Target Backend: $BACKEND_URL" -ForegroundColor Cyan
Write-Host "   Desktop login: PKCE only, no client secret required" -ForegroundColor Cyan
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

Write-Host "Building production Windows desktop bundle..." -ForegroundColor Yellow
Invoke-Flutter -FlutterArgs (@("build", "windows", "--release") + $DartDefines)

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Desktop build generated." -ForegroundColor Green
Write-Host "   Windows bundle: aura_client/build/windows/x64/runner/Release/" -ForegroundColor Green
Write-Host "   aura_hostd is NOT bundled -- build/install it separately (cd aura_hostd &&" -ForegroundColor Green
Write-Host "   dart pub get && dart compile exe bin/aura_hostd.dart -o aura_hostd.exe) and point" -ForegroundColor Green
Write-Host "   the client's daemon executable-path setting at the resulting binary." -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
