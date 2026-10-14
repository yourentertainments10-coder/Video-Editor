; Inno Setup 6 script for the Offline Video Editor.
; Built by packaging\build_windows.ps1 (it passes /DAppVersion=<version from pyproject.toml>).
;
; Per-user install, no admin rights: %LOCALAPPDATA%\Programs\OfflineVideoEditor
; User projects, caches and logs live in %LOCALAPPDATA%\OfflineVideoEditor and are
; NOT removed on uninstall or upgrade.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "Offline Video Editor"
#define AppExe "OfflineVideoEditor.exe"

[Setup]
AppId={{8F3C7A4E-2B1D-4E6A-9C55-0E7D3A1B9F21}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Offline Video Editor
DefaultDirName={localappdata}\Programs\OfflineVideoEditor
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\build\installer
OutputBaseFilename=OfflineVideoEditor-Setup-{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\build\dist\OfflineVideoEditor\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove files left by an older version before copying the new one.
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
