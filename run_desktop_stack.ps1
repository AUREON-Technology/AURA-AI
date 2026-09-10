# Wrapper — canonical script lives in scripts/run_desktop_stack.ps1
param(
  [switch]$SkipBuild,
  [switch]$HostdOnly
)
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RepoRoot "scripts\run_desktop_stack.ps1") -SkipBuild:$SkipBuild -HostdOnly:$HostdOnly
