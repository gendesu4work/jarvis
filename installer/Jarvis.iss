; Jarvis installer (Inno Setup 6). Built automatically by GitHub Actions -> Releases -> Jarvis-Setup.exe
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{6B1C7E2A-4F0D-4E8B-9C3A-5A1E2D3C4B10}
AppName=Jarvis
AppVersion={#AppVersion}
AppPublisher=Jarvis
DefaultDirName={localappdata}\JarvisVoice
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Jarvis-Setup
SetupIconFile=..\app\jarvis.ico
UninstallDisplayIcon={app}\jarvis.ico
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Files]
Source: "..\app\*"; DestDir: "{app}"; Excludes: "__pycache__,*.pyc,tools\_*"; Flags: recursesubdirs ignoreversion
Source: "..\profile\*"; DestDir: "{app}\profile"; Flags: recursesubdirs ignoreversion skipifsourcedoesntexist
Source: "setup.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion
Source: "sync.ps1"; DestDir: "{app}\installer"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\setup.ps1"""; Description: "Set up Jarvis (WSL, Hermes, voice, your settings)"; Flags: postinstall

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\oww"
Type: filesandordirs; Name: "{app}\voices"
Type: filesandordirs; Name: "{app}\edge-profile"
Type: filesandordirs; Name: "{app}\chrome-profile"
Type: files; Name: "{app}\Jarvis.exe"
Type: files; Name: "{app}\machine.json"
Type: files; Name: "{app}\*.log"

[Icons]
Name: "{userprograms}\Jarvis Setup (repair)"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\installer\setup.ps1"""; IconFilename: "{app}\jarvis.ico"
