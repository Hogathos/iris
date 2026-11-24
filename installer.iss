; installer.iss — Inno Setup script for Iris (consumer app).
;
; Per-user install, no admin needed. Installs the PyInstaller
; --onedir bundle to %LOCALAPPDATA%\Programs\Iris. User data
; (journal, lock files) lives separate at
; %LOCALAPPDATA%\Iris Guardian\, so an update overwrites the
; bundle without touching user data. Uninstall leaves the data
; folder behind on purpose.
;
; Build (needs Inno Setup 6, `iscc` on PATH), from the repo root:
;   pyinstaller iris.spec
;   iscc installer.iss
; The single setup exe lands in installer-out\.

#define MyAppName "Iris"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Iris"
#define MyAppId "{D22919A6-0D79-4DB5-8313-7943EE09D607}"

[Setup]
AppId={{#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Iris
DisableDirPage=no
PrivilegesRequired=lowest
OutputDir=installer-out
OutputBaseFilename=Iris-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=Iris
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Dirs]
; The data folder survives updates AND uninstall: user data is
; never deleted by setup. The app creates it too on first run.
Name: "{localappdata}\Iris Guardian\"; Flags: uninsneveruninstall

[Files]
Source: "dist\Iris\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Iris"; Filename: "{app}\Iris.exe"
Name: "{autodesktop}\Iris"; Filename: "{app}\Iris.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Run]
Filename: "{app}\Iris.exe"; Description: "Launch Iris"; Flags: nowait postinstall skipifsilent
