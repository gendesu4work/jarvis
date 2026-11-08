<#
  Jarvis Sync Settings: keeps notes, memory, persona, HUD settings etc. the same on all your PCs
  through your PRIVATE GitHub repo. The API key is never uploaded.
  1. saves this PC's settings into the repo   2. pulls changes from other PCs
  3. uploads the merged result                4. applies it to this PC
  Needs git + GitHub CLI signed in once:  winget install Git.Git GitHub.cli ; gh auth login
#>
$ErrorActionPreference = "Stop"
$App = Split-Path -Parent $PSScriptRoot
$Repo = Join-Path $App "repo"
$py = Join-Path $App ".venv\Scripts\python.exe"
$cfg = Join-Path $App "sync.json"
function Pause-Exit($m, $c = "Red") { Write-Host "`n  $m" -ForegroundColor $c; Read-Host "  Press Enter to close" | Out-Null; exit }

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Pause-Exit "git is not installed. Run:  winget install Git.Git" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { Pause-Exit "GitHub CLI is not installed. Run:  winget install GitHub.cli   then:  gh auth login" }
gh auth status 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { Pause-Exit "Sign in to GitHub first:  gh auth login" }

if (-not (Test-Path (Join-Path $Repo ".git"))) {
    $name = if (Test-Path $cfg) { (Get-Content $cfg | ConvertFrom-Json).repo } else { Read-Host "  Your Jarvis repo (e.g. yourname/jarvis)" }
    @{ repo = $name } | ConvertTo-Json | Set-Content $cfg
    gh repo clone $name $Repo
}
Set-Location $Repo
Write-Host "`n  1/4 Saving this PC's settings..." -ForegroundColor Cyan
& $py (Join-Path $App "tools\export_profile.py") $Repo
git add profile
git -c user.name="Jarvis Sync" -c user.email="jarvis@localhost" commit -m "Sync settings from $env:COMPUTERNAME" 2>$null | Out-Null
Write-Host "  2/4 Getting changes from your other PCs..." -ForegroundColor Cyan
git pull --no-rebase --no-edit
if ($LASTEXITCODE -ne 0) { Pause-Exit "Both PCs changed the same thing. Open $Repo in GitHub Desktop or VS Code to pick which version to keep, then sync again." }
Write-Host "  3/4 Uploading..." -ForegroundColor Cyan
git push
Write-Host "  4/4 Applying to this PC..." -ForegroundColor Cyan
& $py (Join-Path $App "tools\import_profile.py") (Join-Path $Repo "profile") --overwrite
Pause-Exit "Settings are in sync. Restart Jarvis to load them." "Green"
