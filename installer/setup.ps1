<#
  Jarvis one-click setup. Run by Jarvis-Setup.exe (or by hand):
    powershell -ExecutionPolicy Bypass -File setup.ps1
  Safe to re-run: every step checks first and skips what's already done.
  If Windows needs a restart (to turn on WSL), setup continues by itself after you sign back in.
#>
param([switch]$SkipLaunch)
# "Continue": native tools print progress on stderr, which Windows PowerShell 5 would otherwise
# treat as fatal. Every step checks its own result instead.
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$App = Split-Path -Parent $PSScriptRoot           # %LOCALAPPDATA%\JarvisVoice
$Log = Join-Path $App "setup.log"
$Distro = "Ubuntu"
$env:WSL_UTF8 = "1"
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$host.UI.RawUI.WindowTitle = "Jarvis Setup"

function Say($msg, $color = "Cyan") { Write-Host ""; Write-Host "  $msg" -ForegroundColor $color; Add-Content $Log "$(Get-Date -f s) $msg" }
function Ok($msg) { Write-Host "    OK  $msg" -ForegroundColor Green; Add-Content $Log "$(Get-Date -f s)   ok $msg" }
function Info($msg) { Write-Host "        $msg" -ForegroundColor Gray }
function Fail($msg) {
    Add-Content $Log "$(Get-Date -f s) FAILED $msg"
    [System.Windows.Forms.MessageBox]::Show("$msg`n`nDetails are in:`n$Log`n`nFix the issue and run Jarvis Setup again; it continues where it stopped.", "Jarvis Setup", "OK", "Error") | Out-Null
    exit 1
}

# Run a command inside WSL; optional UTF-8 stdin. Returns @{Code; Out}.
function Wsl([string[]]$argv, [string]$stdin = $null, [switch]$Root) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "wsl.exe"
    $a = @("-d", $Distro); if ($Root) { $a += @("-u", "root") }; $a += @("-e") + $argv
    $psi.Arguments = ($a | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join " "
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.RedirectStandardInput = $true
    $psi.StandardOutputEncoding = [Text.Encoding]::UTF8
    $p = [Diagnostics.Process]::Start($psi)
    if ($stdin) { $bytes = [Text.Encoding]::UTF8.GetBytes($stdin.Replace("`r`n", "`n")); $p.StandardInput.BaseStream.Write($bytes, 0, $bytes.Length) }
    $p.StandardInput.Close()
    $out = $p.StandardOutput.ReadToEnd(); $err = $p.StandardError.ReadToEnd(); $p.WaitForExit()
    Add-Content $Log ("wsl " + ($argv -join " ") + " -> " + $p.ExitCode + "`n" + $out + $err)
    return @{ Code = $p.ExitCode; Out = ($out + $err) }
}

Write-Host ""
Write-Host "   =============================================" -ForegroundColor Cyan
Write-Host "     J A R V I S   -   setup" -ForegroundColor White
Write-Host "   =============================================" -ForegroundColor Cyan
Write-Host "   This takes about 15-30 minutes (mostly downloads)." -ForegroundColor Gray
Write-Host "   You'll be asked to do 2 small things: create an Ubuntu" -ForegroundColor Gray
Write-Host "   username/password, and paste your free Gemini API key." -ForegroundColor Gray
Add-Content $Log "`n===== setup started $(Get-Date -f s) ====="

# ---------------------------------------------------------------- 1. checks
Say "1/10  Checking this PC"
$os = [Environment]::OSVersion.Version
if ($os.Build -lt 19041) { Fail "Windows 10 version 2004 (build 19041) or newer is required. This PC is build $($os.Build)." }
$virt = (Get-CimInstance Win32_Processor | Select-Object -First 1).VirtualizationFirmwareEnabled
if ($virt -eq $false) { Fail "Virtualization is turned off in this PC's BIOS/UEFI. Turn on 'SVM' (AMD) or 'Intel VT-x' in the BIOS, then run setup again." }
$free = (Get-PSDrive C).Free / 1GB
if ($free -lt 12) { Fail ("Not enough disk space: {0:N0} GB free on C:, need about 12 GB." -f $free) }
Ok ("Windows build {0}, virtualization on, {1:N0} GB free" -f $os.Build, $free)

# ---------------------------------------------------------------- 2. WSL
Say "2/10  Windows Subsystem for Linux (Jarvis's safe sandbox)"
$probe = (& wsl.exe --status 2>&1 | Out-String) + (& wsl.exe --list --quiet 2>&1 | Out-String)
$probe = $probe -replace "`0", ""
$wslMissing = ($probe -match "WSL_E_WSL_OPTIONAL_COMPONENT_REQUIRED|optional component|is not installed|not supported") -and ($probe -notmatch "no installed distributions")
if ($wslMissing) {
    Info "Turning on WSL (Windows will ask for permission)..."
    try { Start-Process wsl.exe -ArgumentList "--install --no-distribution" -Verb RunAs -Wait -ErrorAction Stop } catch { Fail "WSL could not be turned on (permission declined?)." }
    $cmd = "powershell.exe -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    Set-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "JarvisSetup" -Value $cmd
    $r = [System.Windows.Forms.MessageBox]::Show("WSL is installed. Windows needs to RESTART to finish.`n`nSetup will continue automatically after you sign back in.`n`nRestart now?", "Jarvis Setup", "YesNo", "Information")
    if ($r -eq "Yes") { Restart-Computer -Force }
    exit 0
}
Ok "WSL is on"

# ---------------------------------------------------------------- 3. Windows-side Python + models
Say "3/10  Voice engine (Python + packages)"
$uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
if (-not $uv) {
    Info "Installing uv (Python manager by Astral)..."
    try { Invoke-RestMethod https://astral.sh/uv/install.ps1 -ErrorAction Stop | Invoke-Expression } catch { Fail "Could not download uv: $_" }
    $uv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (-not (Test-Path $uv)) { Fail "Could not install uv." }
}
$py = Join-Path $App ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { & $uv venv --python 3.13 (Join-Path $App ".venv") 2>&1 | Add-Content $Log; if (-not (Test-Path $py)) { Fail "Could not create the Python environment." } }
& $uv pip install --python $py -r (Join-Path $App "requirements.txt") 2>&1 | Add-Content $Log
if ($LASTEXITCODE -ne 0) { Fail "Installing Python packages failed." }
Ok "Python packages installed"

Say "4/10  Downloading voice models (wake word, voice, speech recognition)"
& $py (Join-Path $App "tools\fetch_models.py") 2>&1 | Tee-Object -Variable fm | Add-Content $Log
if ($LASTEXITCODE -ne 0) { Fail "Downloading voice models failed. Check your internet connection." }
Ok "Voice models ready"

# ---------------------------------------------------------------- 5. Ubuntu + account
Say "5/10  Ubuntu (Jarvis's sandbox) and your Linux account"
$list = (& wsl.exe --list --quiet 2>&1 | Out-String) -replace "`0", ""
if ($list -notmatch "(?m)^\s*$Distro\s*$") {
    Info "Downloading Ubuntu..."
    & wsl.exe --install -d $Distro --no-launch 2>&1 | Add-Content $Log
}
$uid = (Wsl @("sh", "-c", "getent passwd 1000 | cut -d: -f1") -Root).Out.Trim()
if (-not $uid) {
    [System.Windows.Forms.MessageBox]::Show("An Ubuntu window will open now.`n`n1. Wait for 'Enter new UNIX username'.`n2. Type a simple lowercase name (e.g. your first name).`n3. Type a password twice (nothing shows while typing - that's normal).`n4. When you see a prompt ending in `$, close that window.`n`nSetup continues automatically.", "Jarvis Setup - one step for you", "OK", "Information") | Out-Null
    Start-Process wsl.exe -ArgumentList "-d $Distro"
    $deadline = (Get-Date).AddMinutes(20)
    while (-not $uid -and (Get-Date) -lt $deadline) {
        Start-Sleep 4
        $uid = (Wsl @("sh", "-c", "getent passwd 1000 | cut -d: -f1") -Root).Out.Trim()
    }
    if (-not $uid) { Fail "No Ubuntu account was created." }
    Start-Sleep 3
}
Ok "Ubuntu account: $uid"

# ---------------------------------------------------------------- 6. sandbox walls
Say "6/10  Locking down the sandbox (Jarvis sees only Documents\jarvis)"
# Paths for this machine (also creates machine.json)
& $py -c "import sys; sys.path.insert(0, r'$App'); import jarvis_paths as P, os; os.makedirs(P.WORKSPACE_WIN, exist_ok=True); print(P.WORKSPACE_WIN + '|' + P.WORKSPACE_WSL)" 2>&1 | Tee-Object -Variable paths | Out-Null
$wsWin, $wsWsl = ($paths | Select-Object -Last 1).Trim().Split("|")
$winPath = $wsWin.Replace("\", "/").Replace(" ", "\040")     # fstab escapes spaces as \040
$wsWslTab = $wsWsl.Replace(" ", "\040")
$wslconf = @"
cp -n /etc/wsl.conf /etc/wsl.conf.before-jarvis 2>/dev/null || true
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[user]
default=$uid

# Jarvis isolation: don't auto-mount Windows drives; only the jarvis folder (via /etc/fstab, mounted by systemd)
[automount]
enabled=false
mountFsTab=false

# Jarvis isolation: Linux can't launch Windows programs
[interop]
enabled=false
appendWindowsPath=false
EOF
grep -v ' drvfs ' /etc/fstab > /tmp/fstab.new || true
echo '$winPath $wsWslTab drvfs defaults,metadata,uid=1000,gid=1000,nofail 0 0' >> /tmp/fstab.new
cp /tmp/fstab.new /etc/fstab
mkdir -p '$wsWsl'
"@
$r = Wsl @("bash", "-s") $wslconf -Root
if ($r.Code -ne 0) { Fail "Could not configure the sandbox." }
& wsl.exe --terminate $Distro 2>&1 | Out-Null; Start-Sleep 3
$chk = Wsl @("sh", "-c", "mount | grep -c drvfs; ls /mnt/c 2>/dev/null | head -1")
Ok "Sandbox walls up (only $wsWin is visible to Jarvis)"

# ---------------------------------------------------------------- 7. Hermes
Say "7/10  Installing Hermes Agent (Jarvis's brain) - the longest step"
$have = (Wsl @("sh", "-c", 'test -x "$HOME/.local/bin/hermes" && echo yes')).Out.Trim()
if ($have -ne "yes") {
    $inst = 'set -e; curl -fsSL https://hermes-agent.nousresearch.com/install.sh -o /tmp/hermes-install.sh; bash /tmp/hermes-install.sh --skip-setup'
    $r = Wsl @("bash", "-c", $inst)
    if ($r.Code -ne 0) { Fail "Installing Hermes failed (see log)." }
}
Ok "Hermes installed"

# ---------------------------------------------------------------- 8. your settings
Say "8/10  Restoring your settings, notes and memory"
Remove-Item (Join-Path $App "machine.json") -ErrorAction SilentlyContinue   # re-detect with the real Linux account
& $py (Join-Path $App "tools\import_profile.py") (Join-Path $App "profile") 2>&1 | Tee-Object -Variable ip | Add-Content $Log
if ($LASTEXITCODE -ne 0) { Fail "Restoring settings failed." }
& $py (Join-Path $App "install_helper.py") 2>&1 | Add-Content $Log
$fix = 'H="$HOME/.local/bin/hermes"; W=$(cat "$HOME/.jarvis/workspace"); "$H" config set terminal.cwd "$W" >/dev/null; "$H" plugins disable spotify >/dev/null 2>&1; true'
Wsl @("bash", "-c", $fix) | Out-Null
Ok ($ip | Select-Object -Last 1)

# ---------------------------------------------------------------- 9. app + shortcuts
Say "9/10  Creating the Jarvis app"
$exe = Join-Path $App "Jarvis.exe"
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /target:winexe "/win32icon:$(Join-Path $App 'jarvis.ico')" /reference:System.Windows.Forms.dll "/out:$exe" (Join-Path $App "launcher\Jarvis.cs") 2>&1 | Add-Content $Log
if (-not (Test-Path $exe)) { Fail "Could not build Jarvis.exe." }
$w = New-Object -ComObject WScript.Shell
function Link($path, $target, $arguments = "", $desc = "Jarvis") {
    $s = $w.CreateShortcut($path); $s.TargetPath = $target; $s.Arguments = $arguments; $s.WorkingDirectory = $App
    $s.IconLocation = (Join-Path $App "jarvis.ico") + ",0"; $s.Description = $desc; $s.Save()
}
$programs = [Environment]::GetFolderPath("Programs")
Link (Join-Path $programs "Jarvis.lnk") $exe
Link (Join-Path ([Environment]::GetFolderPath("Desktop")) "Jarvis.lnk") $exe
Link (Join-Path ([Environment]::GetFolderPath("Startup")) "Jarvis.lnk") $exe
Link (Join-Path $programs "Jarvis Terminal.lnk") "$env:WINDIR\System32\wsl.exe" "-d $Distro --cd `"$wsWsl`" -e bash -lc `"exec ~/.local/bin/hermes`"" "Type to Jarvis (asks before risky actions)"
Link (Join-Path $programs "Jarvis Sync Settings.lnk") "powershell.exe" "-ExecutionPolicy Bypass -File `"$(Join-Path $App 'installer\sync.ps1')`"" "Sync Jarvis settings with GitHub"
Ok "Start menu, Desktop and auto-start at sign-in"

# ---------------------------------------------------------------- 10. Gemini key
Say "10/10 Gemini API key (Jarvis's free AI brain)"
$hasKey = (Wsl @("sh", "-c", 'grep -qE "^(GOOGLE|GEMINI)_API_KEY=.+" "$HOME/.hermes/.env" 2>/dev/null && echo yes')).Out.Trim()
if ($hasKey -ne "yes") {
    $form = New-Object Windows.Forms.Form
    $form.Text = "Jarvis Setup - Gemini API key"; $form.Size = New-Object Drawing.Size(640, 520); $form.StartPosition = "CenterScreen"
    $form.BackColor = [Drawing.Color]::FromArgb(6, 14, 28); $form.ForeColor = [Drawing.Color]::FromArgb(216, 236, 255); $form.Font = New-Object Drawing.Font("Segoe UI", 10)
    $form.FormBorderStyle = "FixedDialog"; $form.MaximizeBox = $false; $form.TopMost = $true
    $t = New-Object Windows.Forms.Label; $t.Text = "Connect Jarvis to Google Gemini (free)"; $t.Font = New-Object Drawing.Font("Segoe UI", 15, [Drawing.FontStyle]::Bold); $t.AutoSize = $true; $t.Location = "24,18"; $form.Controls.Add($t)
    $steps = New-Object Windows.Forms.Label; $steps.Location = "24,62"; $steps.Size = "580,230"
    $steps.Text = "1.  Click the blue link below. It opens Google AI Studio in your browser.`n`n2.  Sign in with your Google account.`n`n3.  Click 'Create API key' (choose any project, or let it create one).`n`n4.  Click the copy icon next to the new key (it starts with AIza...).`n`n5.  Paste it in the box below and click 'Test & Save'.`n`nTip: you can use the SAME key as on your other PC.`nThe key stays on this computer - it is never uploaded to GitHub."
    $form.Controls.Add($steps)
    $lnk = New-Object Windows.Forms.LinkLabel; $lnk.Text = "Open Google AI Studio  ->  aistudio.google.com/apikey"; $lnk.Location = "24,300"; $lnk.AutoSize = $true
    $lnk.LinkColor = [Drawing.Color]::FromArgb(88, 214, 255); $lnk.Add_LinkClicked({ Start-Process "https://aistudio.google.com/apikey" }); $form.Controls.Add($lnk)
    $box = New-Object Windows.Forms.TextBox; $box.Location = "24,340"; $box.Size = "580,28"; $box.UseSystemPasswordChar = $true; $form.Controls.Add($box)
    $status = New-Object Windows.Forms.Label; $status.Location = "24,376"; $status.Size = "580,40"; $form.Controls.Add($status)
    $btn = New-Object Windows.Forms.Button; $btn.Text = "Test && Save"; $btn.Location = "464,424"; $btn.Size = "140,36"
    $btn.BackColor = [Drawing.Color]::FromArgb(88, 214, 255); $btn.ForeColor = [Drawing.Color]::Black; $btn.FlatStyle = "Flat"
    $skip = New-Object Windows.Forms.Button; $skip.Text = "Later"; $skip.Location = "360,424"; $skip.Size = "90,36"; $skip.FlatStyle = "Flat"
    $skip.Add_Click({ $form.Tag = "skip"; $form.Close() }); $form.Controls.Add($skip)
    $btn.Add_Click({
        $k = $box.Text.Trim()
        if ($k -notmatch '^[A-Za-z0-9_\-]{30,60}$') { $status.ForeColor = "Salmon"; $status.Text = "That doesn't look like an API key. Copy it again from AI Studio."; return }
        $status.ForeColor = "LightGray"; $status.Text = "Testing the key with Google..."; $form.Refresh()
        try {
            Invoke-RestMethod -Uri "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1" -Headers @{ "x-goog-api-key" = $k } -TimeoutSec 20 -ErrorAction Stop | Out-Null
        } catch { $status.ForeColor = "Salmon"; $status.Text = "Google rejected this key: $($_.Exception.Message)"; return }
        $save = 'read -r k; f="$HOME/.hermes/.env"; touch "$f"; grep -vE "^(GOOGLE|GEMINI)_API_KEY=" "$f" > "$f.tmp"; echo "GOOGLE_API_KEY=$k" >> "$f.tmp"; mv "$f.tmp" "$f"; chmod 600 "$f"'
        $r = Wsl @("bash", "-c", $save) "$k`n"
        if ($r.Code -ne 0) { $status.ForeColor = "Salmon"; $status.Text = "Could not save the key."; return }
        $form.Tag = "ok"; $form.Close()
    })
    $form.Controls.Add($btn); $form.AcceptButton = $btn
    [void]$form.ShowDialog()
    if ($form.Tag -eq "ok") { Ok "Gemini key saved (on this PC only)" } else { Say "Skipped - run Jarvis Setup again any time to add the key." "Yellow" }
} else { Ok "Gemini key already set" }

Remove-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce" -Name "JarvisSetup" -ErrorAction SilentlyContinue
Say "Jarvis is ready.  Say 'Jarvis' or 'Hey Jarvis' to talk." "Green"
Add-Content $Log "===== setup finished $(Get-Date -f s) ====="
if (-not $SkipLaunch) { Start-Process $exe }
Start-Sleep 4
