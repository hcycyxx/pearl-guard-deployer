#define MyAppName "Pearl 守护器"
#define MyAppVersion "2.2.0"
#define MyAppPublisher "Pearl"
#define MyAppExeName "PearlGuard.exe"
#define MyAppId "PearlGuard-2.2.0"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\PearlGuard
DefaultGroupName=Pearl 守护器
OutputDir={#GetEnv("PEARL_OUTPUT_DIR")}
OutputBaseFilename=PearlGuard-V2.2.0-Setup
SetupIconFile={#GetEnv("PEARL_PROJECT_ROOT")}\assets\pearl.ico
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
CloseApplicationsFilter=Pearl守护器_V2.2.0.exe,SRBMiner-MULTI.exe
VersionInfoVersion=2.2.0.0
VersionInfoDescription=Pearl 守护器一键部署
VersionInfoProductName=Pearl 守护器

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce
Name: "startup"; Description: "开机时自动启动守护器"; GroupDescription: "启动："; Flags: unchecked

[Files]
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\PearlGuard.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\gpu_probe.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\gpu_probe.ps1"; Flags: dontcopy
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\config.template.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\*.txt"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "{#GetEnv('PEARL_PACKAGE_ROOT')}\miner\*"; DestDir: "{app}\miner"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
Name: "{app}\miner\logs"

[Icons]
Name: "{group}\Pearl 守护器"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\Pearl 守护器"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\Pearl 守护器"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: startup

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 Pearl 守护器"; Flags: nowait postinstall skipifsilent

[Code]
var
  ProbeResultPage: TOutputMsgWizardPage;
  WorkerEdit: TNewEdit;
  WarningLabel: TNewStaticText;
  WorkerLabel: TNewStaticText;
  DetectedWorker: String;
  DetectedGpu: String;
  ExistingConfig: Boolean;

function RunGpuProbe(ProbePath, ResultPath: String): Boolean;
var
  ResultCode: Integer;
  PowerShell: String;
  CommandParameters: String;
begin
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  CommandParameters := '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ProbePath + '" -OutputFile "' + ResultPath + '"';
  Result := Exec(PowerShell, CommandParameters, ExtractFilePath(ProbePath),
    SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := Result and (ResultCode = 0);
end;

function JsonValue(Json, Key: String): String;
var
  Marker: String;
  StartPos, EndPos: Integer;
begin
  Result := '';
  Marker := '"' + Key + '":"';
  StartPos := Pos(Marker, Json);
  if StartPos = 0 then Exit;
  StartPos := StartPos + Length(Marker);
  EndPos := StartPos;
  while (EndPos <= Length(Json)) and (Json[EndPos] <> '"') do Inc(EndPos);
  if EndPos > StartPos then Result := Copy(Json, StartPos, EndPos - StartPos);
end;

procedure DetectGpu();
var
  ProbePath, ResultPath, Json, Worker: String;
  Lines: TArrayOfString;
begin
  DetectedGpu := '未识别支持的 NVIDIA / AMD 独立显卡';
  DetectedWorker := 'PearlRig';
  ExtractTemporaryFile('gpu_probe.ps1');
  ProbePath := ExpandConstant('{tmp}\gpu_probe.ps1');
  ResultPath := ExpandConstant('{tmp}\pearl_gpu_result.json');
  DeleteFile(ResultPath);
  if not RunGpuProbe(ProbePath, ResultPath) then
  begin
    if FileExists(ResultPath) then
    begin
      if LoadStringsFromFile(ResultPath, Lines) then Json := Lines[0];
    end;
    if Json = '' then
    begin
      if FileExists(ResultPath) then
      begin
        if LoadStringsFromFile(ResultPath, Lines) then Json := Lines[0];
      end;
    end;
  end
  else
  begin
    if LoadStringsFromFile(ResultPath, Lines) then Json := Lines[0];
  end;

  if Json <> '' then
  begin
    DetectedGpu := JsonValue(Json, 'gpu_name');
    Worker := JsonValue(Json, 'worker');
    if Worker <> '' then DetectedWorker := Worker;
  end;
  if DetectedGpu = '' then DetectedGpu := '未识别支持的 NVIDIA / AMD 独立显卡';
  if DetectedWorker = '' then DetectedWorker := 'PearlRig';
end;

procedure InitializeWizard();
begin
  ProbeResultPage := CreateOutputMsgPage(wpSelectDir,
    '显卡检测', '安装器会先检测本机显卡，并自动填入矿工名。',
    '已识别显卡：' + DetectedGpu + #13#10#13#10 +
    '矿工名可在安装前修改。风扇、功耗和超频参数不会写入配置。');
  WorkerEdit := TNewEdit.Create(ProbeResultPage);
  WorkerEdit.Parent := ProbeResultPage.Surface;
  WorkerEdit.Left := ScaleX(0);
  WorkerEdit.Top := ScaleY(92);
  WorkerEdit.Width := ScaleX(360);
  WorkerEdit.Height := ScaleY(26);
  WorkerEdit.Text := DetectedWorker;
  WorkerLabel := TNewStaticText.Create(ProbeResultPage);
  WorkerLabel.Parent := ProbeResultPage.Surface;
  WorkerLabel.Left := ScaleX(0);
  WorkerLabel.Top := ScaleY(74);
  WorkerLabel.Width := ScaleX(360);
  WorkerLabel.Caption := '矿工名（自动填入显卡型号，可修改）：';
  WarningLabel := TNewStaticText.Create(ProbeResultPage);
  WarningLabel.Parent := ProbeResultPage.Surface;
  WarningLabel.Left := ScaleX(0);
  WarningLabel.Top := ScaleY(126);
  WarningLabel.Width := ScaleX(430);
  WarningLabel.Caption := '';
  WarningLabel.Font.Color := clRed;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (ProbeResultPage <> nil) and (CurPageID = ProbeResultPage.ID) then
  begin
    DetectedWorker := Trim(WorkerEdit.Text);
    if DetectedWorker = '' then
    begin
      MsgBox('矿工名不能为空。', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

procedure CreateDefaultConfig();
var
  TemplatePath, TargetPath: String;
  Lines: TArrayOfString;
  I: Integer;
begin
  TargetPath := ExpandConstant('{app}\supervisor.json');
  ExistingConfig := FileExists(TargetPath);
  if ExistingConfig then Exit;
  TemplatePath := ExpandConstant('{app}\config.template.json');
  if FileExists(TemplatePath) then
  begin
    if not LoadStringsFromFile(TemplatePath, Lines) then
    begin
      Exit;
    end;
    for I := 0 to GetArrayLength(Lines) - 1 do
      StringChangeEx(Lines[I], '{WORKER}', DetectedWorker, True);
    if not SaveStringsToUTF8File(TargetPath, Lines, False) then
      MsgBox('无法写入 supervisor.json，请检查安装目录权限。', mbError, MB_OK);
  end;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = ProbeResultPage.ID then
  begin
    DetectGpu();
    WorkerEdit.Text := DetectedWorker;
    ProbeResultPage.Description :=
      '已识别显卡：' + DetectedGpu + #13#10#13#10 +
      '矿工名可在下方修改。风扇、功耗和超频参数不会写入配置。';
    if DetectedGpu = '未识别支持的 NVIDIA / AMD 独立显卡' then
      WarningLabel.Caption :=
        '未检测到受支持的 AMD / NVIDIA 显卡。请先安装显卡驱动，并核对矿工名。';
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    CreateDefaultConfig();
end;
