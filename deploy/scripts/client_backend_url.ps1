# Resolve and validate the Flutter client's compile-time BACKEND_URL.
# Canonical host is AURA_DOMAIN in deploy/.env (or the environment).
# Dot-source from deploy_client.ps1:
#   . "$PSScriptRoot\deploy\scripts\client_backend_url.ps1"

function Read-AuraEnvKey {
    param(
        [Parameter(Mandatory = $true)][string]$Key,
        [Parameter(Mandatory = $true)][string]$EnvFile
    )
    if (-not (Test-Path $EnvFile)) {
        return $null
    }
    $line = Get-Content $EnvFile | Where-Object { $_ -match "^\s*$Key=" } | Select-Object -Last 1
    if (-not $line) {
        return $null
    }
    return (($line -split "=", 2)[1].Trim().Trim('"').Trim("'"))
}

function Get-AuraClientBackendUrl {
    param([string]$EnvFile)

    $url = $env:BACKEND_URL
    $domain = $env:AURA_DOMAIN

    if ($EnvFile) {
        if (-not $url) {
            $url = Read-AuraEnvKey -Key "BACKEND_URL" -EnvFile $EnvFile
        }
        if (-not $domain) {
            $domain = Read-AuraEnvKey -Key "AURA_DOMAIN" -EnvFile $EnvFile
        }
    }

    if (-not $url -and $domain) {
        $url = "https://$domain"
    }

    return $url
}

function Assert-AuraReleaseBackendUrl {
    param([string]$Url)

    if ([string]::IsNullOrWhiteSpace($Url)) {
        throw "BACKEND_URL is empty. Set AURA_DOMAIN (or BACKEND_URL) in deploy/.env."
    }

    $uri = $null
    try {
        $uri = [Uri]$Url
    } catch {
        throw "BACKEND_URL is not a valid URI: $Url"
    }

    if ($uri.Scheme -ne "https") {
        throw "Release BACKEND_URL must be https://... (got: $Url). Android and iOS block cleartext HTTP."
    }

    $hostName = $uri.Host
    if ($hostName -in @("localhost", "127.0.0.1", "::1")) {
        throw "Release BACKEND_URL must not target localhost (got: $Url)."
    }

    if ($hostName -in @("aura.example.com", "example.com", "your-domain", "your-vps-domain")) {
        throw "AURA_DOMAIN is still the placeholder '$hostName'. Set a real DNS name in deploy/.env."
    }

    if ($hostName -match '^\d+\.\d+\.\d+\.\d+$') {
        throw "Release BACKEND_URL must be a hostname, not a raw IP ($hostName). Set AURA_DOMAIN to a real DNS name (e.g. https://aureon-ai.tech) in deploy/.env."
    }
}
