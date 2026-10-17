#define AppName "RadioStudio Python"
#define AppVersion "1.0.0"
#define AppLabel "Sirius Beta"
#define AppPublisher "Raemi Labs"
#define BuildDir AddBackslash(SourcePath) + "..\build\dist\RadioStudio Python.dist"

[Setup]
AppId={{65E1C19A-206A-4744-9AC3-5B9C4C7871B3}
AppName={#AppName}
AppVersion={#AppVersion} {#AppLabel}
AppVerName={#AppName} {#AppVersion} {#AppLabel}
AppPublisher={#AppPublisher}
AppPublisherURL=https://raemi.xyz
AppSupportURL=https://raemi.xyz
AppContact=raphael@raemi.xyz
AppCopyright=Copyright (C) 2026 Raemi Labs
VersionInfoVersion=1.0.0.0
VersionInfoTextVersion=1.0.0 Sirius Beta
VersionInfoCompany=Raemi Labs
VersionInfoDescription=RadioStudio Python 1.0.0 Sirius Beta Setup
VersionInfoProductName=RadioStudio Python 1.0.0 Sirius Beta
DefaultDirName={autopf}\RadioStudio Python
DefaultGroupName=RadioStudio Python
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\RadioStudio Python.exe
OutputDir={#SourcePath}..\release
OutputBaseFilename=RadioStudio-Python (1.0.0 Sirius Beta) - Setup
SetupIconFile={#SourcePath}..\radiostudio\assets\icon.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
Uninstallable=yes
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "startmenu"; Description: "Criar atalho no menu Iniciar"; GroupDescription: "Atalhos:"; Flags: checkedonce
Name: "desktopicon"; Description: "Criar atalho na Área de Trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\RadioStudio Python"; Filename: "{app}\RadioStudio Python.exe"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{autodesktop}\RadioStudio Python"; Filename: "{app}\RadioStudio Python.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\RadioStudio Python.exe"; Description: "Iniciar o RadioStudio Python"; Flags: postinstall nowait skipifsilent
