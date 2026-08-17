; Inno Setup script for Real Estate App New.
; Produces RealEstateAppNew-Setup-<version>.exe from the PyInstaller onedir
; output (dist/RealEstateAppNew/).
;
; Build (on Windows, after PyInstaller):
;   "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" desktop\installer.iss
; GitHub Actions runs this automatically (.github/workflows/build-desktop.yml).

#define MyAppName        "Real Estate App New"
#define MyAppVersion     "1.0.0"
#define MyAppPublisher   "Suhail"
#define MyAppExeName     "RealEstateAppNew.exe"

[Setup]
AppId={{7B2E4C9D-1A5F-4E80-9C31-REALESTATE001}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; The data snapshot is hundreds of MB: keep default compression but allow it
; to breathe. lzma2/ultra64 on a mostly-SQLite file gains little (SQLite is
; already compact) and makes the installer build very slow.
Compression=lzma2/normal
SolidCompression=no
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=..\dist\installer
OutputBaseFilename=RealEstateAppNew-Setup-{#MyAppVersion}
SetupIconFile=app.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"

[Files]
; The whole PyInstaller onedir tree, INCLUDING data\dxb.db (copied in by CI).
Source: "..\dist\RealEstateAppNew\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; User settings live in %APPDATA%\RealEstateAppNew (settings.json) and are
; deliberately NOT removed — only program files go.
Type: filesandordirs; Name: "{app}"
