# Build and run the full desktop Engineer stack on THIS machine (Windows) -- aura_hostd (the
# local daemon that gives the Engineer agent real file/terminal access on this PC, ADR-011) plus
# the aura_client desktop app, wired to talk to the production backend. Run this ON THE MACHINE
# you want the Engineer agent to act on, not on a server -- aura_hostd's workspace roots and
# containment are local to wherever it runs.
#
# What this script does NOT do: it does not pair the daemon or grant it a workspace root. Those
# are one-time, in-app steps (see the printed instructions at the end) -- ADR-011 deliberately
# has no bundled/auto-discovered aura_hostd binary or auto-pairing, the same disclosed-until-
# configured shape the backend's own OAuth client id used before it was registered.
#
# Usage:
#   .\scripts\run_desktop_stack.ps1                # build aura_hostd + aura_client, then run both
#   .\scripts\run_desktop_stack.ps1 -SkipBuild      # reuse whatever was built last time
#   .\scripts\run_desktop_stack.ps1 -HostdOnly      # build/run only aura_hostd

param(
  [switch]$SkipBuild,
  [switch]$HostdOnly
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
$ClientDir = Join-Path $RepoRoot "aura_client"
$HostdDir = Join-Path $RepoRoot "aura_hostd"

if (-not (Get-Command flutter -ErrorAction SilentlyContinue)) {
  throw "flutter not found on PATH. Install the Flutter SDK first: https://docs.flutter.dev/get-started/install"
}
if (-not (Get-Command dart -ErrorAction SilentlyContinue)) {
  throw "dart not found on PATH. It ships inside the Flutter SDK -- make sure Flutter's bin\ directory (which provides both flutter and dart) is on PATH."
}

$HostdBin = Join-Path $HostdDir "build\aura_hostd.exe"
$HostdLog = Join-Path $HostdDir "build\aura_hostd.log"

if (-not $SkipBuild) {
  Write-Host "==========================================================" -ForegroundColor Cyan
  Write-Host "Building aura_hostd..." -ForegroundColor Cyan
  Write-Host "==========================================================" -ForegroundColor Cyan
  New-Item -ItemType Directory -Force -Path (Join-Path $HostdDir "build") | Out-Null
  Push-Location $HostdDir
  try {
    & dart pub get
    if ($LASTEXITCODE -ne 0) { throw "dart pub get failed with exit code $LASTEXITCODE" }
    & dart compile exe bin/aura_hostd.dart -o $HostdBin
    if ($LASTEXITCODE -ne 0) { throw "dart compile exe failed with exit code $LASTEXITCODE" }
  } finally {
    Pop-Location
  }
}
if (-not (Test-Path $HostdBin)) {
  throw "aura_hostd binary not found at $HostdBin -- run without -SkipBuild first."
}

if ((-not $HostdOnly) -and (-not $SkipBuild)) {
  Write-Host "==========================================================" -ForegroundColor Cyan
  Write-Host "Building aura_client (desktop, production backend)..." -ForegroundColor Cyan
  Write-Host "==========================================================" -ForegroundColor Cyan
  & (Join-Path $ScriptDir "deploy_client_desktop.ps1")
}

$ClientBin = Join-Path $ClientDir "build\windows\x64\runner\Release\aura_client.exe"
if ((-not $HostdOnly) -and (-not (Test-Path $ClientBin))) {
  throw "aura_client desktop binary not found at $ClientBin -- run without -SkipBuild first."
}

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Starting aura_hostd..." -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
$hostdProcess = Start-Process -FilePath $HostdBin -ArgumentList "run" -RedirectStandardOutput $HostdLog `
  -RedirectStandardError "$HostdLog.err" -PassThru -WindowStyle Hidden

Start-Sleep -Seconds 2
if ($hostdProcess.HasExited) {
  Write-Host "aura_hostd exited immediately. Log:" -ForegroundColor Red
  Get-Content $HostdLog -ErrorAction SilentlyContinue
  Get-Content "$HostdLog.err" -ErrorAction SilentlyContinue
  exit 1
}
Write-Host "aura_hostd running (pid $($hostdProcess.Id)). Log: $HostdLog" -ForegroundColor Green

try {
  Write-Host "==========================================================" -ForegroundColor Cyan
  Write-Host "One-time setup inside the app (skip if already paired):" -ForegroundColor Yellow
  Write-Host "  1. Sign in with Google." -ForegroundColor Yellow
  Write-Host "  2. Open the Host Daemon screen (toolbar icon on Engineer history) -- the status" -ForegroundColor Yellow
  Write-Host "     card should already show it running (this script started it), so do NOT tap" -ForegroundColor Yellow
  Write-Host "     Start there too, or you'll spawn a second aura_hostd process. Set the" -ForegroundColor Yellow
  Write-Host "     executable path field to:" -ForegroundColor Yellow
  Write-Host "       $HostdBin" -ForegroundColor Yellow
  Write-Host "     anyway, so the app remembers it for a future launch where it does the starting." -ForegroundColor Yellow
  Write-Host "  3. Tap Pair, and approve the code from this same signed-in session." -ForegroundColor Yellow
  Write-Host "  4. Add a workspace root (a folder on this machine) and, optionally, Trust it" -ForegroundColor Yellow
  Write-Host "     to skip per-command confirmation inside that folder." -ForegroundColor Yellow
  Write-Host "==========================================================" -ForegroundColor Cyan

  if ($HostdOnly) {
    Write-Host "aura_hostd is running -- launch aura_client separately, or Ctrl-C to stop it." -ForegroundColor Green
    Wait-Process -Id $hostdProcess.Id
  } else {
    Write-Host "Launching aura_client..." -ForegroundColor Cyan
    Start-Process -FilePath $ClientBin -Wait
  }
} finally {
  if (-not $hostdProcess.HasExited) {
    Write-Host "Stopping aura_hostd (pid $($hostdProcess.Id))..." -ForegroundColor Yellow
    Stop-Process -Id $hostdProcess.Id -Force -ErrorAction SilentlyContinue
  }
}
