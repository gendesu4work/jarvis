<#
  Connect Jarvis to FreeLLMAPI (the free LLM gateway app on this PC).
  - asks for your FreeLLMAPI API key in a hidden prompt (never shown, never uploaded)
  - checks it, lists FreeLLMAPI's models, tests tool use
  - makes FreeLLMAPI Jarvis's main brain, keeping Gemini as automatic backup
#>
$ErrorActionPreference = "Continue"
$App = Split-Path -Parent $PSScriptRoot
$py = Join-Path $App ".venv\Scripts\python.exe"
$env:WSL_UTF8 = "1"
function Done($m, $c = "Green") { Write-Host "`n  $m" -ForegroundColor $c; Read-Host "`n  Press Enter to close" | Out-Null; exit }

Write-Host "`n  Connect Jarvis to FreeLLMAPI" -ForegroundColor Cyan
$port = 31415
try { $port = (Get-Content "$env:APPDATA\FreeLLMAPI\config.json" -Raw | ConvertFrom-Json).port } catch {}
$base = "http://127.0.0.1:$port/v1"
try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$port/" -TimeoutSec 5 | Out-Null }
catch { Done "FreeLLMAPI is not running. Open the FreeLLMAPI app first, then run this again." "Red" }

Write-Host "`n  In FreeLLMAPI: open its dashboard -> API Keys -> create a key -> copy it." -ForegroundColor Gray
$sec = Read-Host "  Paste the FreeLLMAPI API key here (hidden)" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)).Trim()
if (-not $key) { Done "No key entered." "Red" }
$h = @{ Authorization = "Bearer $key" }
try { $models = (Invoke-RestMethod "$base/models" -Headers $h -TimeoutSec 20 -ErrorAction Stop).data | ForEach-Object { $_.id } }
catch { Done "FreeLLMAPI rejected that key ($($_.Exception.Message)). Copy it again from the dashboard." "Red" }
if (-not $models) { Done "FreeLLMAPI returned no models. Add at least one provider in its dashboard first." "Red" }

Write-Host "`n  Models available through FreeLLMAPI:" -ForegroundColor Cyan
for ($i = 0; $i -lt [Math]::Min($models.Count, 40); $i++) { Write-Host ("   {0,2}. {1}" -f ($i + 1), $models[$i]) }
$def = [Array]::IndexOf($models, ($models | Where-Object { $_ -match "^auto$|auto" } | Select-Object -First 1)); if ($def -lt 0) { $def = 0 }
$pick = Read-Host "`n  Which one should Jarvis use? Number (Enter = $($def + 1). $($models[$def]))"
if ($pick -match '^\d+$' -and [int]$pick -ge 1 -and [int]$pick -le $models.Count) { $model = $models[[int]$pick - 1] } else { $model = $models[$def] }
Write-Host "  -> $model" -ForegroundColor Green

Write-Host "`n  Testing tool use (Jarvis needs it to open apps, browse, etc.)..." -ForegroundColor Gray
$body = @{ model = $model; messages = @(@{ role = "user"; content = "What time is it in Manila? Use the tool." });
           tools = @(@{ type = "function"; function = @{ name = "get_time"; description = "Get current time in a city";
                        parameters = @{ type = "object"; properties = @{ city = @{ type = "string" } }; required = @("city") } } }) } | ConvertTo-Json -Depth 10
$tools = "unknown"
try {
    $r = Invoke-RestMethod "$base/chat/completions" -Method Post -Headers $h -ContentType "application/json" -Body $body -TimeoutSec 90 -ErrorAction Stop
    $tools = if ($r.choices[0].message.tool_calls) { "yes" } else { "no" }
} catch { $tools = "error: $($_.Exception.Message)" }
Write-Host "  Tool use: $tools" -ForegroundColor ($(if ($tools -eq "yes") { "Green" } else { "Yellow" }))
if ($tools -ne "yes") {
    $go = Read-Host "  This model may not handle Jarvis's tools well. Use it anyway? (y/N)"
    if ($go -notmatch '^[yY]') { Done "Nothing changed. Run again and pick another model." "Yellow" }
}

# Save the key inside Jarvis's sandbox only (~/.hermes/.env), via stdin - never on a command line.
$save = 'read -r k; k=$(printf %s "$k" | tr -d "\r\n "); f="$HOME/.hermes/.env"; touch "$f"; grep -v "^FREELLMAPI_KEY=" "$f" > "$f.tmp"; echo "FREELLMAPI_KEY=$k" >> "$f.tmp"; mv "$f.tmp" "$f"; chmod 600 "$f"'
$key | & wsl.exe -d Ubuntu -e bash -c $save
$cfg = @"
import os, shutil, time
from ruamel.yaml import YAML
p = os.path.expanduser("~/.hermes/config.yaml"); shutil.copy(p, p + ".before-freellmapi-" + time.strftime("%Y%m%d-%H%M%S"))
y = YAML(); y.preserve_quotes = True; c = y.load(open(p))
prev_provider, prev_model = c["model"].get("provider"), c["model"].get("default")
c.setdefault("providers", {})["freellmapi"] = {"name": "FreeLLMAPI", "api": "http://127.0.0.1:31416/v1", "key_env": "FREELLMAPI_KEY"}
c["model"]["provider"] = "freellmapi"; c["model"]["default"] = "$model"
fb = [f for f in (c.get("fallback_providers") or []) if f.get("provider") != "freellmapi"]
if prev_provider and prev_provider != "freellmapi" and not any(f.get("model") == prev_model for f in fb):
    fb.insert(0, {"provider": prev_provider, "model": prev_model})
c["fallback_providers"] = fb
y.dump(c, open(p, "w"))
print("main:", "freellmapi /", c["model"]["default"], "| backups:", [f["model"] for f in fb])
"@
$cfg | & wsl.exe -d Ubuntu -e sh -c 'PY=$(ls -td ~/.hermes/installs/*/environments/*/venv | head -1)/bin/python; exec "$PY" -'
Done "Done. Restart Jarvis (close its window and open it again) to use FreeLLMAPI. Gemini stays as automatic backup."
