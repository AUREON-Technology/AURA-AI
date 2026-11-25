@echo off
rem ---------------------------------------------------------------------------------------------
rem AURA Desktop IDE -- setup.  One file: double-click it, or run it from a terminal.
rem
rem Every run installs the NEWEST build of the AURA Desktop IDE that was published from the `main`
rem branch.  Nothing here is a snapshot: the build is looked up and downloaded when this file runs
rem (from https://web.aureon-ai.tech/desktop-ide/download/), so a copy of this file that is a year
rem old still installs today's app.  Running it again later updates an existing install.  No GitHub
rem account or repository access is needed.
rem
rem The build is made and published by .github/workflows/desktop-main-channel.yml.
rem
rem Options (all optional):  -Silent  -Force  -DownloadOnly  -BaseUrl <https://host/path>
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
    [string]$BaseUrl = 'https://web.aureon-ai.tech/desktop-ide/download',
    [switch]$Silent,
    [switch]$Force,
    [switch]$DownloadOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
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

# Windows PowerShell 5.1 may not offer TLS 1.2 by default.
try { [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }
Add-Type -AssemblyName System.Net.Http

# Downloads a URL to a file, with a coarse progress line. Returns $true, or $false for HTTP 404 when
# -NotFoundOk is given; any other failure throws.
function Save-Url {
    param([string]$Url, [string]$OutFile, [string]$Label = 'file', [switch]$NotFoundOk)
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(30)
    try {
        $req = New-Object System.Net.Http.HttpRequestMessage([System.Net.Http.HttpMethod]::Get, $Url)
        [void]$req.Headers.TryAddWithoutValidation('User-Agent', 'AURA-Desktop-Setup')
        [void]$req.Headers.TryAddWithoutValidation('Cache-Control', 'no-cache')
        $resp = $client.SendAsync($req, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        try {
            $code = [int]$resp.StatusCode
            if ($code -eq 404 -and $NotFoundOk) { return $false }
            if (-not $resp.IsSuccessStatusCode) { throw "HTTP $code while downloading the $Label" }
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
            }
            return $true
        } finally {
            $resp.Dispose()
        }
    } finally {
        $client.Dispose()
    }
}

# ------------------------------------------------------------------------------------------------
Write-Host ''
Write-Host 'AURA Desktop IDE setup' -ForegroundColor Green
Write-Host '----------------------'

if (-not [Environment]::Is64BitOperatingSystem) { Stop-Setup 'AURA Desktop needs 64-bit Windows.' }
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { Stop-Setup 'AURA Desktop setup runs on Windows only.' }

# https only, so a build cannot be swapped on the wire. Plain http is accepted for this machine
# alone (testing a local copy of the site).
try { $base = [Uri]$BaseUrl } catch { Stop-Setup "-BaseUrl is not a valid URL: $BaseUrl" }
$localHost = @('localhost', '127.0.0.1') -contains $base.Host
if (-not ($base.Scheme -eq 'https' -or ($base.Scheme -eq 'http' -and $localHost))) {
    Stop-Setup '-BaseUrl must be an https:// address.'
}
$baseText = $BaseUrl.TrimEnd('/')

$work = Join-Path ([IO.Path]::GetTempPath()) ('aura-setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $work | Out-Null

try {
    # 1. Find the newest build -----------------------------------------------------------------
    Write-Step 'Looking up the latest AURA Desktop build'
    $manifestPath = Join-Path $work 'latest.json'
    $found = $false
    try { $found = Save-Url -Url "$baseText/latest.json" -OutFile $manifestPath -Label 'build manifest' -NotFoundOk }
    catch { Stop-Setup "Could not reach $baseText. Check the internet connection and try again." }
    if (-not $found) { Stop-Setup 'No AURA Desktop build has been published yet. Please try again later.' }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.installer -or -not $manifest.installer.name -or -not $manifest.installer.sha256 -or -not $manifest.version) {
        Stop-Setup 'The build manifest is missing fields; refusing to continue.'
    }
    $installerName = [string]$manifest.installer.name
    if ($installerName -ne [IO.Path]::GetFileName($installerName) -or $installerName -notlike '*.exe') {
        Stop-Setup 'The build manifest names an unexpected installer file; refusing to continue.'
    }
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
                [void](Save-Url -Url 'https://aka.ms/vs/17/release/vc_redist.x64.exe' -OutFile $vcPath -Label 'Visual C++ runtime')
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
    $ok = $false
    try { $ok = Save-Url -Url ("$baseText/" + [Uri]::EscapeDataString($installerName)) -OutFile $installerPath -Label 'installer' -NotFoundOk }
    catch { Stop-Setup $_.Exception.Message }
    if (-not $ok) { Stop-Setup 'The installer is not on the server (a new build may be going up right now). Please run setup again in a minute.' }
    $actual = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne ([string]$manifest.installer.sha256).ToLowerInvariant()) {
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        Stop-Setup 'The downloaded installer does not match its published SHA-256 checksum. It was deleted and NOT run. If this keeps happening, a new build may be going up: try again in a minute.'
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
