@echo off
rem ---------------------------------------------------------------------------------------------
rem AURA Desktop IDE -- setup.  One file: double-click it, or run it from a terminal.
rem
rem Every run installs the NEWEST build of the AURA Desktop IDE that was published from the `main`
rem branch on GitHub (.github/workflows/desktop-main-channel.yml).  Nothing here is a snapshot:
rem the build is looked up and downloaded when this file runs, so a copy of this file that is a
rem year old still installs today's app.  Running it again later updates an existing install.
rem
rem Options (all optional):  -Silent  -Force  -DownloadOnly  -Token <github token>  -Repo owner/name
rem Docs: docs/guides/desktop-releases.md ("Always-latest setup")
rem
rem How this file is built: the batch lines below start Windows PowerShell on this very file and
rem hand it everything after the marker line.  The marker is assembled from two pieces on the
rem command line so that only the real marker line (the last one) matches.
rem ---------------------------------------------------------------------------------------------
setlocal
set "AURA_SETUP_FILE=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$t = [IO.File]::ReadAllText($env:AURA_SETUP_FILE); $m = '#' + '#POWERSHELL##'; $i = $t.LastIndexOf($m); & ([scriptblock]::Create($t.Substring($i + $m.Length))) %*"
set "AURA_RC=%ERRORLEVEL%"
rem Double-clicked (Explorer starts "cmd /c <this file>"): keep the window open to read the result.
if /i "%AURA_NO_PAUSE%"=="1" exit /b %AURA_RC%
echo "%cmdcmdline%" | find /i "%~nx0" >nul && pause
exit /b %AURA_RC%
##POWERSHELL##
param(
    [string]$Repo = 'anasibrahimgnedy-collab/AURA-AI',
    [switch]$Silent,
    [switch]$Force,
    [switch]$DownloadOnly,
    [string]$Token,
    [string]$ApiBase = 'https://api.github.com'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ChannelTag = 'desktop-main'
$ManifestName = 'aura-desktop-main.json'
# Inno Setup's registry key for the installer's AppId (aura_client/windows/installer/aura.iss).
$UninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{62D1F24E-FBE0-4CD3-AA49-031547C5B4E4}_is1'

function Write-Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Write-Note($text) { Write-Host "    $text" }
function Write-Warn($text) { Write-Host "    WARNING: $text" -ForegroundColor Yellow }

function Stop-Setup($message) {
    Write-Host ''
    Write-Host "Setup failed: $message" -ForegroundColor Red
    exit 1
}

# Windows PowerShell 5.1 may not offer TLS 1.2 by default; GitHub requires it.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
Add-Type -AssemblyName System.Net.Http

function Get-GitHubToken {
    if ($Token) { return $Token }
    if ($env:AURA_GITHUB_TOKEN) { return $env:AURA_GITHUB_TOKEN }
    if ($env:GITHUB_TOKEN) { return $env:GITHUB_TOKEN }
    # A signed-in GitHub CLI is the easy way to reach a private repository.
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if ($gh) {
        try {
            $t = (& $gh.Source auth token 2>$null | Select-Object -First 1)
            if ($LASTEXITCODE -eq 0 -and $t) { return "$t".Trim() }
        } catch { }
    }
    return $null
}

# Downloads a URL to a file. Redirects are followed by hand so the GitHub token is sent only to
# the first host (api.github.com) and never on to the storage host a release asset redirects to.
function Save-Url {
    param([string]$Url, [string]$OutFile, [hashtable]$Headers = @{}, [string]$Label = 'file')
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.AllowAutoRedirect = $false
    $client = New-Object System.Net.Http.HttpClient($handler)
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    $current = $Url
    $sendHeaders = $Headers
    try {
        for ($hop = 0; $hop -lt 6; $hop++) {
            $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $current)
            [void]$req.Headers.TryAddWithoutValidation('User-Agent', 'AURA-Desktop-Setup')
            foreach ($k in $sendHeaders.Keys) { [void]$req.Headers.TryAddWithoutValidation($k, [string]$sendHeaders[$k]) }
            $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
            $code = [int]$resp.StatusCode
            if ($code -ge 300 -and $code -lt 400 -and $resp.Headers.Location) {
                $next = $resp.Headers.Location
                if (-not $next.IsAbsoluteUri) { $next = New-Object Uri((New-Object Uri($current)), $next) }
                $current = $next.AbsoluteUri
                $sendHeaders = @{}
                $resp.Dispose()
                continue
            }
            if (-not $resp.IsSuccessStatusCode) {
                $resp.Dispose()
                throw "HTTP $code while downloading the $Label"
            }
            $total = $resp.Content.Headers.ContentLength
            $in = $resp.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
            $out = [IO.File]::Create($OutFile)
            try {
                $buffer = New-Object byte[] (1MB)
                $done = 0L
                $lastPct = -10
                while (($n = $in.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $out.Write($buffer, 0, $n)
                    $done += $n
                    if ($total -and $total -gt 1MB) {
                        $pct = [int](100 * $done / $total)
                        if ($pct -ge $lastPct + 10) {
                            Write-Note ("{0}%  ({1:N1} of {2:N1} MB)" -f $pct, ($done / 1MB), ($total / 1MB))
                            $lastPct = $pct
                        }
                    }
                }
            } finally {
                $out.Dispose()
                $in.Dispose()
                $resp.Dispose()
            }
            return
        }
        throw "too many redirects while downloading the $Label"
    } finally {
        $client.Dispose()
    }
}

function Get-StatusCode($err) {
    $r = $err.Exception.Response
    if ($r) { try { return [int]$r.StatusCode } catch { } }
    return $null
}

# ------------------------------------------------------------------------------------------------
Write-Host ''
Write-Host 'AURA Desktop IDE setup' -ForegroundColor Green
Write-Host '----------------------'

if (-not [Environment]::Is64BitOperatingSystem) { Stop-Setup 'AURA Desktop needs 64-bit Windows.' }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { Stop-Setup 'AURA Desktop setup runs on Windows only.' }

$work = Join-Path ([IO.Path]::GetTempPath()) ('aura-setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

try {
    # 1. Find the newest main-branch build -----------------------------------------------------
    Write-Step 'Looking up the latest AURA Desktop build on GitHub'
    $tokenValue = Get-GitHubToken
    $apiHeaders = @{ 'Accept' = 'application/vnd.github+json'; 'User-Agent' = 'AURA-Desktop-Setup'; 'X-GitHub-Api-Version' = '2022-11-28' }
    if ($tokenValue) { $apiHeaders['Authorization'] = "Bearer $tokenValue" }

    $release = $null
    try {
        $release = Invoke-RestMethod -Uri "$ApiBase/repos/$Repo/releases/tags/$ChannelTag" -Headers $apiHeaders -Method Get
    } catch {
        $status = Get-StatusCode $_
        if ($status -eq 404) {
            if ($tokenValue) {
                Stop-Setup "No '$ChannelTag' build is published on $Repo yet (or this GitHub account cannot see the repository). Ask the maintainer to run the 'Desktop Main Channel' workflow once."
            }
            Stop-Setup "Could not find the '$ChannelTag' build on $Repo. Either none is published yet, or the repository is private: sign in with 'gh auth login' (or set AURA_GITHUB_TOKEN) with an account that can read it, then run this again."
        }
        if ($status -eq 403 -or $status -eq 429) {
            Stop-Setup 'GitHub refused the request (rate limit?). Wait a few minutes, or set AURA_GITHUB_TOKEN, then run this again.'
        }
        if ($status -eq 401) { Stop-Setup 'GitHub rejected the token. Check AURA_GITHUB_TOKEN / the -Token value / gh auth status.' }
        Stop-Setup "Could not reach GitHub ($($_.Exception.Message)). Check the internet connection and try again."
    }

    function Get-ReleaseAsset($name) {
        foreach ($a in $release.assets) { if ($a.name -eq $name) { return $a } }
        return $null
    }
    # With a token, assets come from the API (works for a private repo); without one, from the
    # public download URL (not rate limited).
    function Save-Asset($asset, $outFile, $label) {
        if ($tokenValue) {
            Save-Url -Url $asset.url -OutFile $outFile -Headers @{ 'Accept' = 'application/octet-stream'; 'Authorization' = "Bearer $tokenValue" } -Label $label
        } else {
            Save-Url -Url $asset.browser_download_url -OutFile $outFile -Label $label
        }
    }

    $manifestAsset = Get-ReleaseAsset $ManifestName
    if (-not $manifestAsset) { Stop-Setup "The '$ChannelTag' release has no $ManifestName; the build looks incomplete. Try again after the next successful build." }
    $manifestPath = Join-Path $work $ManifestName
    Save-Asset $manifestAsset $manifestPath 'build manifest'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.installer -or -not $manifest.installer.name -or -not $manifest.installer.sha256 -or -not $manifest.version) {
        Stop-Setup "$ManifestName is missing fields; refusing to continue."
    }
    $installerName = [string]$manifest.installer.name
    if ($installerName -ne [IO.Path]::GetFileName($installerName) -or $installerName -notlike '*.exe') {
        Stop-Setup "$ManifestName names an unexpected installer file; refusing to continue."
    }
    $installerAsset = Get-ReleaseAsset $installerName
    if (-not $installerAsset) { Stop-Setup "The release has no $installerName." }

    $shortCommit = ([string]$manifest.commit).Substring(0, [Math]::Min(7, ([string]$manifest.commit).Length))
    Write-Note "Latest build : $($manifest.version)  (main @ $shortCommit, built $($manifest.builtAt))"

    # 2. Already current? ----------------------------------------------------------------------
    $installed = $null
    try { $installed = (Get-ItemProperty -LiteralPath $UninstallKey -ErrorAction Stop).DisplayVersion } catch { }
    if ($installed) { Write-Note "Installed    : $installed" } else { Write-Note 'Installed    : not installed' }
    if ($installed -and $installed -eq [string]$manifest.version -and -not $Force -and -not $DownloadOnly) {
        Write-Host ''
        Write-Host 'AURA Desktop is already up to date. (Use -Force to reinstall.)' -ForegroundColor Green
        exit 0
    }

    # 3. Runtime the app needs -----------------------------------------------------------------
    if (-not $DownloadOnly) {
        $vc = $null
        try { $vc = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction Stop).Installed } catch { }
        if ($vc -ne 1) {
            Write-Step 'Installing the Microsoft Visual C++ runtime (AURA Desktop needs it)'
            $vcPath = Join-Path $work 'vc_redist.x64.exe'
            try {
                Save-Url -Url 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcPath -Label 'Visual C++ runtime'
                $vcSig = Get-AuthenticodeSignature -LiteralPath $vcPath
                if ($vcSig.Status -ne 'Valid' -or $vcSig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
                    throw 'the download is not signed by Microsoft'
                }
                Write-Note 'Windows will ask for permission to install it.'
                $p = Start-Process -FilePath $vcPath -ArgumentList '/install', '/quiet', '/norestart' -Verb RunAs -Wait -PassThru
                if (@(0, 1638, 3010) -notcontains $p.ExitCode) { throw "installer exited with code $($p.ExitCode)" }
                if ($p.ExitCode -eq 3010) { Write-Warn 'A restart is needed before AURA Desktop starts.' }
            } catch {
                Write-Warn "Could not install the Visual C++ runtime ($($_.Exception.Message)). If AURA does not start, install it from https://aka.ms/vs/17/release/vc_redist.x64.exe"
            }
        }
    }

    # 4. Download and verify the installer -----------------------------------------------------
    Write-Step "Downloading $installerName"
    $installerPath = Join-Path $work $installerName
    Save-Asset $installerAsset $installerPath 'installer'
    $actual = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne ([string]$manifest.installer.sha256).ToLowerInvariant()) {
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        Stop-Setup 'The downloaded installer does not match its published SHA-256 checksum. It was deleted and NOT run.'
    }
    Write-Note 'Checksum OK.'
    $sig = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($sig.Status -eq 'Valid') { Write-Note "Signed by $($sig.SignerCertificate.Subject)." }
    else { Write-Note 'The installer is not code-signed yet; Windows may show an "unknown publisher" prompt.' }

    if ($DownloadOnly) {
        $dest = Join-Path (Get-Location).Path $installerName
        Move-Item -LiteralPath $installerPath -Destination $dest -Force
        Write-Host ''
        Write-Host "Saved the installer to $dest (not installed)." -ForegroundColor Green
        exit 0
    }

    # 5. Install -------------------------------------------------------------------------------
    Write-Step 'Installing AURA Desktop'
    if ($Silent) {
        $proc = Start-Process -FilePath $installerPath -ArgumentList @('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS') -Wait -PassThru
    } else {
        $proc = Start-Process -FilePath $installerPath -Wait -PassThru
    }
    if ($proc.ExitCode -ne 0) { Stop-Setup "The installer exited with code $($proc.ExitCode)." }

    Write-Host ''
    Write-Host "AURA Desktop $($manifest.version) is installed." -ForegroundColor Green
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Note 'Tip: the Engineer agent works on Git repositories. Git is not installed here; get it with: winget install Git.Git'
    }
    exit 0
} catch {
    Stop-Setup $_.Exception.Message
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
