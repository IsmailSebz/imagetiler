; Inno Setup script for Raster Image Tiler.
; Compiled by build/build.py, or by hand with:
;     "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" build\installer.iss

#define AppName        "Raster Image Tiler"
#define AppShortName   "ImageTiler"
#define AppVersion     "1.0.0"
#define AppPublisher   "Ismail"
#define AppExeName     "ImageTiler.exe"

[Setup]
; Never reuse this GUID for another product -- it is how Windows tells
; an upgrade apart from a separate application.
AppId={{7C4E1B92-4D3A-4F58-9C21-8E6B5A0D3F77}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppShortName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\exe
OutputBaseFilename={#AppShortName}-Setup-{#AppVersion}
SetupIconFile=..\icons\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; Install per-user by default so no admin prompt is needed; the user can
; still choose an all-users install from the first page.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; \
    GroupDescription: "Additional shortcuts:"

[Files]
Source: "..\exe\{#AppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; \
    Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; \
    Description: "Launch {#AppName}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Settings live in the registry under HKCU\Software\ImageTiler. Leave them
; in place on uninstall so a reinstall keeps the user's form values; the
; line below is here, commented, if you would rather clean up fully.
; Type: filesandordirs; Name: "{app}"

[Registry]
; Nothing to write at install time -- the app creates its own keys under
; HKCU\Software\ImageTiler\RasterImageTiler on first close.
