; Установщик LocalFlow для Windows (Inno Setup 6).
; Собирается tools/build_installer.py — он передаёт версию и папки.
;
; Ставится без прав администратора, в папку пользователя: так же, как
; автозапуск и настройки — только для этого человека.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F4A2C1E-8B3D-4E7A-9C51-2D7F0B6A4E93}
AppName=LocalFlow
AppVersion={#AppVersion}
AppVerName=LocalFlow {#AppVersion}
AppPublisher=LocalFlow
AppPublisherURL=https://github.com/1nebog/localflow-windows
AppSupportURL=https://github.com/1nebog/localflow-windows/issues
DefaultDirName={autopf}\LocalFlow
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputBaseFilename=LocalFlow-Setup-{#AppVersion}
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\LocalFlow.exe
UninstallDisplayName=LocalFlow
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ShowLanguageDialog=no
CloseApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoDescription=LocalFlow Setup

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "uk"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[CustomMessages]
en.AutoStart=Start with Windows
ru.AutoStart=Запускать вместе с Windows
uk.AutoStart=Запускати разом з Windows
de.AutoStart=Mit Windows starten
en.DeleteData=Also delete settings, history and downloaded models?
ru.DeleteData=Удалить также настройки, историю и скачанные модели?
uk.DeleteData=Видалити також налаштування, історію та завантажені моделі?
de.DeleteData=Auch Einstellungen, Verlauf und geladene Modelle löschen?

[Tasks]
Name: "autostart"; Description: "{cm:AutoStart}"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#EngineDir}\*"; DestDir: "{app}\engine"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; от прошлой версии не должно остаться старых библиотек
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\engine"

[Icons]
Name: "{autoprograms}\LocalFlow"; Filename: "{app}\LocalFlow.exe"
Name: "{autodesktop}\LocalFlow"; Filename: "{app}\LocalFlow.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "LocalFlow"; ValueData: """{app}\LocalFlow.exe"""; Tasks: autostart

[Run]
Filename: "{app}\LocalFlow.exe"; Description: "{cm:LaunchProgram,LocalFlow}"; Flags: nowait postinstall skipifsilent

[Code]
const
  RunKey = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ApprovedKey = 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run';

{ Закрыть запущенный LocalFlow: иначе его файлы не заменить и не удалить }
procedure StopLocalFlow(const Exe: String);
var
  Code: Integer;
begin
  if FileExists(Exe) then
    Exec(Exe, '--quit', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  StopLocalFlow(ExpandConstant('{app}\LocalFlow.exe'));
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  { без галочки автозапуска — убрать и прежний, если был }
  if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, RunKey, 'LocalFlow');
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('autostart') then
    RegDeleteValue(HKCU, ApprovedKey, 'LocalFlow');
end;

function InitializeUninstall(): Boolean;
begin
  StopLocalFlow(ExpandConstant('{app}\LocalFlow.exe'));
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKCU, RunKey, 'LocalFlow');
    RegDeleteValue(HKCU, ApprovedKey, 'LocalFlow');
    if not UninstallSilent then
      if MsgBox(CustomMessage('DeleteData'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
      begin
        DelTree(ExpandConstant('{userappdata}\LocalFlow'), True, True, True);
        DelTree(ExpandConstant('{localappdata}\LocalFlow'), True, True, True);
      end;
  end;
end;
