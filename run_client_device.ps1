# Wrapper — canonical script lives in scripts/run_client_device.ps1
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RepoRoot "scripts\run_client_device.ps1") @args
