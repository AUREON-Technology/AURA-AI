# Collect the last N days of Contabo VPS logs and save them under docs/reports/.
# Runs deploy/scripts/collect-vps-logs.sh on the VPS via SSH, then downloads the file.
#
# Usage (PowerShell):
#   .\scripts\collect-vps-logs.ps1
#   .\scripts\collect-vps-logs.ps1 -Days 3
#   .\scripts\collect-vps-logs.ps1 -Days 7 -OutFile "docs\reports\my-logs.log"

param(
    [int]$Days = 7,
    [string]$VpsHost = "169.58.188.43",
    [string]$VpsUser = "root",
    [string]$SshKey = "$env:USERPROFILE\.ssh\contabo_vps_ed25519",
    [string]$OutFile = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$RemoteScript = "/tmp/collect-vps-logs.sh"
$RemoteLog = "/tmp/aura-vps-logs-${Days}d.log"
$LocalScript = Join-Path $RepoRoot "deploy\scripts\collect-vps-logs.sh"

if (-not (Test-Path $LocalScript)) {
    throw "Missing script: $LocalScript"
}
if (-not (Test-Path $SshKey)) {
    throw "SSH key not found: $SshKey"
}

if ([string]::IsNullOrWhiteSpace($OutFile)) {
    $date = Get-Date -Format "yyyy-MM-dd"
    $OutFile = Join-Path $RepoRoot "docs\reports\vps-logs-last-${Days}d-$date.log"
}

$OutDir = Split-Path -Parent $OutFile
if (-not (Test-Path $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}

$sshArgs = @(
    "-i", $SshKey,
    "-o", "StrictHostKeyChecking=no",
    "-o", "IdentitiesOnly=yes"
)

Write-Host "Uploading collector to ${VpsUser}@${VpsHost}..." -ForegroundColor Cyan
& scp @sshArgs $LocalScript "${VpsUser}@${VpsHost}:${RemoteScript}"

Write-Host "Collecting last $Days day(s) of logs on VPS (this may take a few minutes)..." -ForegroundColor Cyan
& ssh @sshArgs "${VpsUser}@${VpsHost}" "chmod +x $RemoteScript && AURA_LOG_DAYS=$Days $RemoteScript $RemoteLog"

Write-Host "Downloading to $OutFile ..." -ForegroundColor Cyan
& scp @sshArgs "${VpsUser}@${VpsHost}:${RemoteLog}" $OutFile

& ssh @sshArgs "${VpsUser}@${VpsHost}" "rm -f $RemoteScript $RemoteLog" | Out-Null

$info = Get-Item $OutFile
Write-Host "Saved: $($info.FullName)" -ForegroundColor Green
Write-Host "Size:  $([math]::Round($info.Length / 1MB, 2)) MB" -ForegroundColor Green
