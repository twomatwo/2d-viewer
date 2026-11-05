; The AppId and user-data location must remain stable across releases.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef BundleDir
  #error BundleDir is required
#endif
#ifndef SpinnakerInstaller
  #error SpinnakerInstaller is required
#endif
#ifndef ReleaseDir
  #error ReleaseDir is required
#endif

[Setup]
AppId={{349B7837-9981-480E-A5DA-A12753686DC7}
AppName=2D Viewer
AppVersion={#AppVersion}
AppVerName=2D Viewer {#AppVersion}
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\2D Viewer
DefaultGroupName=2D Viewer
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#ReleaseDir}
OutputBaseFilename=2D-Viewer-Setup-{#AppVersion}-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\2D Viewer.exe
CloseApplications=yes
CloseApplicationsFilter=LabView.exe,2D Viewer.exe
RestartApplications=no
SetupLogging=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(SourcePath) + "ChineseSimplified.isl")
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Extract only when camera prerequisites need installation, never on routine upgrades.
Source: "{#SpinnakerInstaller}"; DestName: "SpinnakerSDK_FULL_4.4.0.246_x64.exe"; Flags: dontcopy nocompression

[Icons]
Name: "{group}\2D Viewer"; Filename: "{app}\2D Viewer.exe"; WorkingDir: "{app}"
Name: "{group}\安装与更新说明"; Filename: "{app}\实验室安装与更新说明.html"
Name: "{group}\数据与标定目录"; Filename: "{localappdata}\LabView"
Name: "{autodesktop}\2D Viewer"; Filename: "{app}\2D Viewer.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[InstallDelete]
; Same AppId and previous install directory: remove only our obsolete executable.
Type: files; Name: "{app}\LabView.exe"

[Run]
Filename: "{app}\2D Viewer.exe"; Description: "启动 2D Viewer"; Flags: nowait postinstall skipifsilent

[Code]
const
  SDKVersion = '4.4.0.246';
  DriverProduct = '{239F3E9A-5E47-40A7-AE8C-26CE29ADC716}';
  BinariesProduct = '{DD109EFE-FB37-4DA1-9B6C-90D3185C5C2A}';
  GenTLProduct = '{8ED824A1-5DCD-47AD-8284-B0074CE87386}';
  GenICamProduct = '{B51DC8A9-A946-4E46-90A8-210995094CC4}';

var
  SDKInstalledThisRun: Boolean;
  SDKRebootRequired: Boolean;

procedure RemoveOwnedLegacyShortcut(const LinkPath, ExpectedTarget: String);
var
  Shell, Link: Variant;
  Target: String;
begin
  if not FileExists(LinkPath) then exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(LinkPath);
    Target := Link.TargetPath;
    if Lowercase(Target) = Lowercase(ExpectedTarget) then
    begin
      if DeleteFile(LinkPath) then Log('Removed renamed application shortcut: ' + LinkPath);
    end;
  except
    Log('Could not inspect legacy shortcut: ' + LinkPath);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RemoveOwnedLegacyShortcut(ExpandConstant('{autodesktop}\LabView.lnk'),
      ExpandConstant('{app}\LabView.exe'));
    RemoveOwnedLegacyShortcut(ExpandConstant('{userprograms}\LabView\LabView.lnk'),
      ExpandConstant('{app}\LabView.exe'));
    RemoveOwnedLegacyShortcut(ExpandConstant('{userprograms}\LabView\安装与更新说明.lnk'),
      ExpandConstant('{app}\实验室安装与更新说明.html'));
    RemoveOwnedLegacyShortcut(ExpandConstant('{userprograms}\LabView\数据与标定目录.lnk'),
      ExpandConstant('{localappdata}\LabView'));
    { RemoveDir only removes an empty old group; never delete unrelated shortcuts. }
    RemoveDir(ExpandConstant('{userprograms}\LabView'));
  end;
end;

function MsiQueryFeatureState(ProductCode, Feature: String): Integer;
  external 'MsiQueryFeatureStateW@msi.dll stdcall';

function ComponentInstalled(const Product: String): Boolean;
var
  InstalledVersion: String;
begin
  Result := RegQueryStringValue(HKLM64,
    'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\' + Product,
    'DisplayVersion', InstalledVersion) and (InstalledVersion = SDKVersion);
end;

function SDKReady: Boolean;
begin
  Result := ComponentInstalled(BinariesProduct) and
    ComponentInstalled(GenTLProduct) and ComponentInstalled(GenICamProduct) and
    ComponentInstalled(DriverProduct) and
    (MsiQueryFeatureState(DriverProduct, 'USBDrivers') = 3);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  InstallerPath: String;
begin
  Result := '';
  if SDKInstalledThisRun or SDKReady then
  begin
    Log('Matching Spinnaker runtime and USB3 driver are already installed.');
    exit;
  end;
  WizardForm.StatusLabel.Caption := '正在安装相机运行库和 USB3 驱动，请允许管理员权限请求…';
  ExtractTemporaryFile('SpinnakerSDK_FULL_4.4.0.246_x64.exe');
  InstallerPath := ExpandConstant('{tmp}\SpinnakerSDK_FULL_4.4.0.246_x64.exe');
  if not ShellExec('runas', InstallerPath,
    '/install /silent /norestart Features=Runtimes_v140,USBDrivers',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    Result := '相机驱动安装未完成。请允许官方安装程序的管理员权限请求，再运行 2D Viewer 安装包。错误：' +
      IntToStr(ResultCode);
    exit;
  end;
  Log('Spinnaker installer exit code: ' + IntToStr(ResultCode));
  if (ResultCode <> 0) and (ResultCode <> 3010) and (ResultCode <> 1641) then
  begin
    Result := '官方相机运行库安装失败，错误码：' + IntToStr(ResultCode) +
      '。请查看安装说明中的“安装失败”，处理后重新运行安装包。';
    exit;
  end;
  SDKRebootRequired := (ResultCode = 3010) or (ResultCode = 1641);
  if not SDKReady then
  begin
    Result := '尚未检测到 Spinnaker 4.4.0.246 运行库及 USB3 驱动。请重启电脑后重新运行安装包；' +
      '若仍提示此信息，请按安装说明修复官方相机运行环境。';
    exit;
  end;
  SDKInstalledThisRun := True;
end;

function NeedRestart: Boolean;
begin
  Result := SDKRebootRequired;
end;
