# Wrapper — canonical script lives in scripts/deploy_client.ps1
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RepoRoot "scripts\deploy_client.ps1") @args
