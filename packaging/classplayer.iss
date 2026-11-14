; ClassPlayer 윈도우 설치 프로그램 (Inno Setup 6)
; 관리자 권한 없이 사용자 폴더(%LOCALAPPDATA%\Programs\ClassPlayer)에 설치한다.
; → 앱 안의 '업데이트 확인'이 설치 폴더를 직접 덮어쓸 수 있다.
; 빌드: iscc /DAppVersion=0.4.2 packaging\classplayer.iss   (dist\ClassPlayer 폴더가 있어야 함)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6C1F3B0E-7A2D-4F0B-9C4E-2B7C1A5D9E31}
AppName=ClassPlayer
AppVersion={#AppVersion}
AppVerName=ClassPlayer {#AppVersion}
AppPublisher=ClassPlayer
AppPublisherURL=https://github.com/janghwansangai/classplayer
DefaultDirName={localappdata}\Programs\ClassPlayer
DefaultGroupName=ClassPlayer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=ClassPlayer-Setup-windows-x64
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\ClassPlayer.exe
UninstallDisplayName=ClassPlayer
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

#ifndef NOKOREAN
[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "바로가기:"

[Files]
Source: "..\dist\ClassPlayer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ClassPlayer"; Filename: "{app}\ClassPlayer.exe"
Name: "{autodesktop}\ClassPlayer"; Filename: "{app}\ClassPlayer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ClassPlayer.exe"; Description: "지금 ClassPlayer 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
