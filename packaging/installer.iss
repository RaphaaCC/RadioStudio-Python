#define AppName "RadioStudio Python Sirius"
#define AppVersion "v1.0.2 Beta"
#define AppPublisher "Raemi Labs"
#define BuildDir AddBackslash(SourcePath) + "..\build\dist\RadioStudio Python.dist"

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
english.AppLanguageTitle=Application language
english.AppLanguageDescription=Choose the initial language used by RadioStudio. You can change it later in Settings.
english.AutoLanguage=Automatic (system language)
english.CreateStartMenuShortcut=Create a Start Menu shortcut
english.CreateDesktopShortcut=Create a desktop shortcut
english.ShortcutsGroup=Shortcuts:
english.LaunchApp=Launch RadioStudio Python Sirius
brazilianportuguese.AppLanguageTitle=Idioma do aplicativo
brazilianportuguese.AppLanguageDescription=Escolha o idioma inicial do RadioStudio. Você poderá alterá-lo depois nas Configurações.
brazilianportuguese.AutoLanguage=Automático (idioma do sistema)
brazilianportuguese.CreateStartMenuShortcut=Criar atalho no menu Iniciar
brazilianportuguese.CreateDesktopShortcut=Criar atalho na Área de Trabalho
brazilianportuguese.ShortcutsGroup=Atalhos:
brazilianportuguese.LaunchApp=Iniciar o RadioStudio Python Sirius
spanish.AppLanguageTitle=Idioma de la aplicación
spanish.AppLanguageDescription=Elige el idioma inicial de RadioStudio. Podrás cambiarlo después en Configuración.
spanish.AutoLanguage=Automático (idioma del sistema)
spanish.CreateStartMenuShortcut=Crear un acceso directo en el menú Inicio
spanish.CreateDesktopShortcut=Crear un acceso directo en el escritorio
spanish.ShortcutsGroup=Accesos directos:
spanish.LaunchApp=Iniciar RadioStudio Python Sirius
korean.AppLanguageTitle=앱 언어
korean.AppLanguageDescription=RadioStudio에서 처음 사용할 언어를 선택하세요. 나중에 설정에서 변경할 수 있습니다.
korean.AutoLanguage=자동(시스템 언어)
korean.CreateStartMenuShortcut=시작 메뉴 바로 가기 만들기
korean.CreateDesktopShortcut=바탕 화면 바로 가기 만들기
korean.ShortcutsGroup=바로 가기:
korean.LaunchApp=RadioStudio Python Sirius 실행
japanese.AppLanguageTitle=アプリの言語
japanese.AppLanguageDescription=RadioStudio の初回使用言語を選択してください。後で設定から変更できます。
japanese.AutoLanguage=自動（システム言語）
japanese.CreateStartMenuShortcut=スタートメニューにショートカットを作成
japanese.CreateDesktopShortcut=デスクトップにショートカットを作成
japanese.ShortcutsGroup=ショートカット:
japanese.LaunchApp=RadioStudio Python Sirius を起動

[Setup]
AppId={{65E1C19A-206A-4744-9AC3-5B9C4C7871B3}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} — {#AppVersion}
UninstallDisplayName={#AppName}
AppPublisher={#AppPublisher}
AppPublisherURL=https://raemi.xyz
AppSupportURL=https://raemi.xyz
AppContact=raphael@raemi.xyz
AppCopyright=Copyright (C) 2026 Raemi Labs
VersionInfoVersion=1.0.2.0
VersionInfoTextVersion=v1.0.2 Beta
VersionInfoCompany=Raemi Labs
VersionInfoDescription=RadioStudio Python Sirius
VersionInfoProductName=RadioStudio Python Sirius
DefaultDirName={autopf}\RadioStudio Python
DefaultGroupName=RadioStudio Python Sirius
DisableProgramGroupPage=yes
ShowLanguageDialog=no
UninstallDisplayIcon={app}\RadioStudio Python.exe
OutputDir={#SourcePath}..\release
OutputBaseFilename=RadioStudio Python Sirius — v1.0.2 Beta — Setup
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
Name: "startmenu"; Description: "{cm:CreateStartMenuShortcut}"; GroupDescription: "{cm:ShortcutsGroup}"; Flags: checkedonce
Name: "desktopicon"; Description: "{cm:CreateDesktopShortcut}"; GroupDescription: "{cm:ShortcutsGroup}"; Flags: unchecked

[Files]
Source: "{#BuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\RadioStudio Python Sirius"; Filename: "{app}\RadioStudio Python.exe"; IconFilename: "{app}\radiostudio\assets\icon.ico"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{autodesktop}\RadioStudio Python Sirius"; Filename: "{app}\RadioStudio Python.exe"; IconFilename: "{app}\radiostudio\assets\icon.ico"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\RadioStudio Python.exe"; Description: "{cm:LaunchApp}"; Flags: postinstall nowait skipifsilent

[Code]
var
  AppLanguagePage: TInputOptionWizardPage;

function AppLanguageName(Index: Integer): String;
begin
  case Index of
    0: Result := 'system';
    1: Result := 'pt_BR';
    2: Result := 'en_US';
    3: Result := 'es_MX';
    4: Result := 'ko_KR';
    5: Result := 'ja_JP';
    6: Result := 'zh_TW';
    7: Result := 'zh_CN';
  end;
end;

procedure InitializeWizard;
var
  SavedLanguage: String;
  Index: Integer;
begin
  AppLanguagePage := CreateInputOptionPage(wpSelectDir,
    ExpandConstant('{cm:AppLanguageTitle}'),
    ExpandConstant('{cm:AppLanguageTitle}'),
    ExpandConstant('{cm:AppLanguageDescription}'), True, False);
  AppLanguagePage.Add(ExpandConstant('{cm:AutoLanguage}'));
  AppLanguagePage.Add('Português (Brasil)');
  AppLanguagePage.Add('English (United States)');
  AppLanguagePage.Add('Español (México)');
  AppLanguagePage.Add('한국어');
  AppLanguagePage.Add('日本語');
  AppLanguagePage.Add('繁體中文');
  AppLanguagePage.Add('简体中文');
  AppLanguagePage.SelectedValueIndex := 0;
  if FileExists(ExpandConstant('{app}\language.cfg')) then begin
    if LoadStringFromFile(ExpandConstant('{app}\language.cfg'), SavedLanguage) then begin
      for Index := 0 to 7 do
        if Trim(SavedLanguage) = AppLanguageName(Index) then
          AppLanguagePage.SelectedValueIndex := Index;
    end;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  LanguagePath: String;
begin
  if CurStep = ssPostInstall then begin
    LanguagePath := ExpandConstant('{app}\language.cfg');
    if not FileExists(LanguagePath) then
      SaveStringToFile(LanguagePath,
        AppLanguageName(AppLanguagePage.SelectedValueIndex) + #13#10, False);
  end;
end;
