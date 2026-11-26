; Rhapsode — Windows installer (Inno Setup 7)
; Per-user install: no UAC prompt, lands in %LOCALAPPDATA%\Programs\Rhapsode.
; Build:  ISCC.exe packaging\Rhapsode-setup.iss   (from the repo root)

#define AppName        "Rhapsode"
#ifndef AppVersion
  #define AppVersion   "0.3.6-beta"
#endif
#define AppPublisher   "endynamoo"
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
; The bundle contains GPL-3.0 components (eSpeak NG via phonemizer), so the
; page the user agrees to is the notice that says so, not the MIT text alone.
LicenseFile=..\licenses\NOTICE.txt
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
Name: "engine"; Description: "Set up the stills engine (Python environment, torch, FLUX.2 and the text encoder - about 16 GB)"; GroupDescription: "Additional tasks:"; Flags: checkedonce

[Files]
Source: "{#SourceDir}\{#AppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion
Source: "..\scripts\setup_engine.ps1"; DestDir: "{app}\scripts"; Flags: ignoreversion
Source: "..\packaging\uv.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
; The voice model is not shipped in the installer. The app prepares it on first start,
; with a progress bar and a cancel - which is where that belongs, since a frozen Windows
; build has no console to print to during install. Starting it here means a stranger's
; first sight of Rhapsode is "getting your voice ready", not an empty library.
Filename: "{app}\{#AppExe}"; Description: "Start Rhapsode now (it downloads its voice on first start, 340 MB)"; Flags: postinstall nowait runascurrentuser skipifsilent
; The engine setup runs the same script the repo uses, so an AMD or NVIDIA user can
; click one thing: it creates image-venv, picks the right torch wheel, fetches the q4
; transformer and the detectors, and writes the engine profile. It is a visible
; console window on purpose - a 3 GB download should show progress.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\scripts\setup_engine.ps1"""; WorkingDir: "{app}"; Description: "Set up the stills engine now (~3 GB download)"; Flags: postinstall nowait runascurrentuser skipifsilent; Tasks: engine

[UninstallDelete]
; Keep %LOCALAPPDATA%\Rhapsode (library database + 340 MB Kokoro model) on uninstall.
Type: dirifempty; Name: "{app}"
