; Inno Setup script for Saidcue.
; Packages the PyInstaller onedir build at dist\Saidcue into a single
; SaidcueSetup.exe.
;
; Build order:
;   1. pyinstaller installer\windows\saidcue.spec --noconfirm
;   2. "C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer\windows\saidcue.iss
;
; Output lands in installer\windows\output\SaidcueSetup.exe.

#define MyAppName "Saidcue"
#define MyAppVersion "0.1.0"
#define MyAppPublisher "Saidcue"
#define MyAppURL "https://saidcue.com"
#define MyAppExeName "Saidcue.exe"
#define SourceDir "..\..\dist\Saidcue"

[Setup]
AppId={{B6C9B6E0-6B0B-4C3A-9B6B-5F2E9B9B6A2A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=output
OutputBaseFilename=SaidcueSetup
SetupIconFile=..\..\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "startupicon"; Description: "Start Saidcue automatically when Windows starts"; GroupDescription: "Additional icons:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; The app's own writable config (%APPDATA%\Saidcue) is intentionally left
; in place on uninstall, same as most Windows apps -- a reinstall should
; not lose the user's shortcuts/settings.
