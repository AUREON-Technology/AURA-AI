# Wrapper — canonical script lives in scripts/deploy_rules.ps1
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
& (Join-Path $RepoRoot "scripts\deploy_rules.ps1") @args
