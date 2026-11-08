; Windows installer for AURA Desktop (M8.10). Built by .github/workflows/desktop-package.yml:
;
;   iscc /DAppVersion=<version> /DSourceDir=<Release folder> /DOutputDir=<dir> aura.iss
;
; SourceDir is Flutter's `build\windows\x64\runner\Release`, with `aura_hostd.exe` already copied
; next to `aura_client.exe` -- that is where the app looks for its bundled daemon
; (lib/core/hostd/hostd_bundled_path_io.dart).
;
; Per-user install (no administrator rights, under %LOCALAPPDATA%\Programs), so installing,
; upgrading and uninstalling never needs elevation, and the daemon the app spawns runs as the
; same unprivileged user.
;
; NEVER CHANGE AppId. Windows identifies an installed app by it: a new AppId turns the next
; upgrade into a second, side-by-side install, and the old one is never removed.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must point at the Flutter Windows Release folder
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{62D1F24E-FBE0-4CD3-AA49-031547C5B4E4}
AppName=AURA AI
AppVersion={#AppVersion}
AppPublisher=AURA AI
DefaultDirName={localappdata}\Programs\AURA AI
DefaultGroupName=AURA AI
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
OutputDir={#OutputDir}
OutputBaseFilename=aura-desktop-windows-x64-{#AppVersion}-setup
SetupIconFile=..\runner\resources\app_icon.ico
UninstallDisplayIcon={app}\aura_client.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; An upgrade replaces files the running app or daemon holds open; ask to close them first.
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\AURA AI"; Filename: "{app}\aura_client.exe"
Name: "{autodesktop}\AURA AI"; Filename: "{app}\aura_client.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Run]
Filename: "{app}\aura_client.exe"; Description: "{cm:LaunchProgram,AURA AI}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop a daemon this app left running, so its files can be removed. A daemon that is not running
; makes taskkill exit non-zero, which is fine.
Filename: "{sys}\taskkill.exe"; Parameters: "/F /IM aura_hostd.exe"; Flags: runhidden; RunOnceId: "StopHostd"
