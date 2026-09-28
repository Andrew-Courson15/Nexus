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
Name: "ollama"; Description: "Also download and install Ollama, the free AI that runs on this PC (about 1 GB)"; Check: not OllamaInstalled

[Files]
Source: "..\dist\NEXUS\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{userprograms}\NEXUS"; Filename: "{app}\NEXUS.exe"; Comment: "Your creative archive"
Name: "{userdesktop}\NEXUS"; Filename: "{app}\NEXUS.exe"; Tasks: desktopicon
Name: "{userstartup}\NEXUS"; Filename: "{app}\NEXUS.exe"; Parameters: "--no-browser"; Tasks: startup

[Run]
Filename: "{tmp}\OllamaSetup.exe"; Parameters: "/SILENT /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "Installing Ollama (the AI)..."; Flags: waituntilterminated skipifdoesntexist; Tasks: ollama
Filename: "{app}\NEXUS.exe"; Description: "Open NEXUS now"; Flags: nowait postinstall skipifsilent
; An update started from inside NEXUS (Settings → Updates) installs silently with /RELAUNCH=1: open NEXUS again.
Filename: "{app}\NEXUS.exe"; Parameters: "--no-browser"; Flags: nowait; Check: Relaunch

[UninstallRun]
; Stop NEXUS before its files are removed (your ideas in %LOCALAPPDATA%\NEXUS are kept).
Filename: "{sys}\taskkill.exe"; Parameters: "/IM NEXUS.exe /F"; Flags: runhidden; RunOnceId: "StopNexus"

[UninstallDelete]
; The "Start with Windows" shortcut the tray menu may have made.
Type: files; Name: "{userstartup}\NEXUS.lnk"

[Code]
var
  DownloadPage: TDownloadWizardPage;

function Relaunch: Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

function OllamaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{localappdata}\Programs\Ollama\ollama.exe'))
    or FileExists(ExpandConstant('{commonpf}\Ollama\ollama.exe'));
end;

function OnDownloadProgress(const Url, FileName: String; const Progress, ProgressMax: Int64): Boolean;
begin
  Result := True;
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Downloading Ollama', 'NEXUS uses Ollama to run its AI on this PC.', @OnDownloadProgress);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpReady) and WizardIsTaskSelected('ollama') then
  begin
    DownloadPage.Clear;
    DownloadPage.Add('https://ollama.com/download/OllamaSetup.exe', 'OllamaSetup.exe', '');
    DownloadPage.Show;
    try
      try
        DownloadPage.Download;
      except
        SuppressibleMsgBox('Ollama couldn''t be downloaded (' + GetExceptionMessage + '). NEXUS works without it; ' +
          'you can install it later from ollama.com, and NEXUS''s setup check will remind you.', mbInformation, MB_OK, IDOK);
      end;
    finally
      DownloadPage.Hide;
    end;
  end;
end;
