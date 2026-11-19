; Rhapsode — Windows installer (Inno Setup 7)
; Per-user install: no UAC prompt, lands in %LOCALAPPDATA%\Programs\Rhapsode.
; Build:  ISCC.exe packaging\Rhapsode-setup.iss   (from the repo root)

#define AppName        "Rhapsode"
#ifndef AppVersion
  #define AppVersion   "0.3.0-beta"
#endif
#define AppPublisher   "Anthony Cortez"
#define AppExe         "Rhapsode.exe"
#define SourceDir      "..\dist\Rhapsode"

[Setup]
AppId={{8D3A1C1E-6F2B-4C6D-9E4A-1F0B0A000001}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppCopyright=Copyright (C) 2026 {#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Rhapsode-{#AppVersion}-Setup
SetupIconFile=..\branding\rhapsode.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName} {#AppVersion}
LicenseFile=..\LICENSE
InfoBeforeFile=..\packaging\first-run.txt
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; One group on purpose: Inno indents the first row of each group differently
; when descriptions differ in length, which left these two checkboxes on
; different left edges. The engine row says what it is, so the second heading
; was only adding a ragged edge.
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "Additional tasks:"; Flags: unchecked
Name: "engine"; Description: "Set up the stills engine (Python environment, torch, ~3 GB of models)"; GroupDescription: "Additional tasks:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\scripts\setup_engine.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; The engine setup runs the same script the repo uses, so an AMD or NVIDIA user can
; click one thing: it creates image-venv, picks the right torch wheel, fetches the q4
; transformer and the detectors, and writes the engine profile. It is a visible
; console window on purpose - a 3 GB download should show progress.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_engine.ps1"""; WorkingDir: "{app}"; Description: "Set up the stills engine now (~3 GB download)"; Flags: postinstall nowait runascurrentuser skipifsilent; Tasks: engine
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Keep %LOCALAPPDATA%\Rhapsode (library database + 340 MB Kokoro model) on uninstall.
Type: dirifempty; Name: "{app}"
