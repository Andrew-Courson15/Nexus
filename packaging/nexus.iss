; Inno Setup recipe for NEXUS-Setup-<version>.exe (https://jrsoftware.org/isinfo.php, free).
; Built by scripts\build_app_windows.ps1 after PyInstaller, when Inno Setup 6 is installed:
;   winget install JRSoftware.InnoSetup
; Installs for the current user only (no admin prompt). Uninstalling removes the app, never your
; ideas: they stay in %LOCALAPPDATA%\NEXUS.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6E5A8C1D-2F43-4B7A-9E0C-4E58A1B9C0DE}
AppName=NEXUS
AppVersion={#AppVersion}
AppVerName=NEXUS {#AppVersion}
AppPublisher=NEXUS
DefaultDirName={localappdata}\Programs\NEXUS
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=NEXUS-Setup-{#AppVersion}
SetupIconFile=..\static\icons\nexus.ico
UninstallDisplayIcon={app}\NEXUS.exe
UninstallDisplayName=NEXUS
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Put a NEXUS icon on the desktop"
Name: "startup"; Description: "Start NEXUS when I sign in (so my phone can reach it)"; Flags: unchecked

[Files]
Source: "..\dist\NEXUS\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\NEXUS"; Filename: "{app}\NEXUS.exe"; Comment: "Your creative archive"
Name: "{userdesktop}\NEXUS"; Filename: "{app}\NEXUS.exe"; Tasks: desktopicon
Name: "{userstartup}\NEXUS"; Filename: "{app}\NEXUS.exe"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{app}\NEXUS.exe"; Description: "Open NEXUS now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Stop NEXUS before its files are removed (your ideas in %LOCALAPPDATA%\NEXUS are kept).
Filename: "{sys}\taskkill.exe"; Parameters: "/IM NEXUS.exe /F"; Flags: runhidden; RunOnceId: "StopNexus"

[UninstallDelete]
; The "Start with Windows" shortcut the tray menu may have made.
Type: files; Name: "{userstartup}\NEXUS.lnk"
