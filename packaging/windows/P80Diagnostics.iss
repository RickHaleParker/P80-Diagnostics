; SPDX-License-Identifier: GPL-3.0-or-later
; Copyright (C) 2026 Rick Hale Parker
;
; Inno Setup 6 script for P80 Diagnostics. Built by build_windows.bat,
; which passes the version as /DMyAppVersion=x.y.z.
; Paths are relative to this file.

#ifndef MyAppVersion
  #define MyAppVersion "1.1.0"
#endif
#define MyAppName "P80 Diagnostics"
#define MyAppExe "P80Diagnostics.exe"

[Setup]
AppId={{1A212793-F4C5-4DF3-AF65-AE5048C5379E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Rick Hale Parker
AppCopyright=Copyright (C) 2026 Rick Hale Parker. GPL v3 or later.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequiredOverridesAllowed=dialog
LicenseFile=..\..\LICENSE
SetupIconFile=..\..\p80diag\icon.ico
UninstallDisplayIcon={app}\{#MyAppExe}
OutputDir=..\..\dist
OutputBaseFilename=P80Diagnostics-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\{#MyAppExe}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\DATABASE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExe}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
