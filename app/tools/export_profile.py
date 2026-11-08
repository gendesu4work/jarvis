"""Save this machine's Jarvis settings into <repo>/profile (no secrets).

profile/workspace/  - Documents\\jarvis (notes, HUD settings/theme/layout, Jarvis's files) minus screenshots
profile/hermes/     - config.yaml, SOUL.md, memories/*.md, cron/jobs.json, skills Jarvis created
Machine paths are replaced by {{WORKSPACE}} / {{HOME}} so they can be restored anywhere.
The API key (~/.hermes/.env) and auth files are NEVER exported.
"""
import base64, io, os, re, shutil, subprocess, sys, tarfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import jarvis_paths as P

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(APP)
OUT = os.path.join(REPO, "profile")
SKIP_WS = {"screens"}
SKIP_FILES = {"wsl_interop_guide.txt"}
SECRET_KEYS = re.compile(r"^(\s*(?:api_key|token|secret|password|access_token|refresh_token|client_secret)\s*:\s*)(\S.*)$", re.M)

# ---- workspace
ws_out = os.path.join(OUT, "workspace")
shutil.rmtree(ws_out, ignore_errors=True)
for root, dirs, files in os.walk(P.WORKSPACE_WIN):
    dirs[:] = [d for d in dirs if d not in SKIP_WS and not d.startswith(".")]
    for f in files:
        if f.startswith(".") or f in SKIP_FILES:
            continue
        src = os.path.join(root, f)
        if os.path.getsize(src) > 5_000_000:
            continue
        dst = os.path.join(ws_out, os.path.relpath(src, P.WORKSPACE_WIN))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)

# ---- hermes (packed inside WSL, streamed out as base64 tar)
PACK = r'''
cd ~/.hermes || exit 1
L=""
for f in config.yaml SOUL.md cron/jobs.json; do [ -f "$f" ] && L="$L $f"; done
for f in memories/*.md; do [ -f "$f" ] && L="$L $f"; done
for d in skills/*/; do d=${d%/}; n=${d#skills/}
  [ -f "$d/SKILL.md" ] || continue                       # category folders of built-in skills
  grep -qE "^$n([[:space:]:]|$)" skills/.bundled_manifest 2>/dev/null || L="$L $d"; done
tar -czf - $L | base64 -w0
'''
r = subprocess.run(P.wsl("bash", "-c", PACK), capture_output=True, timeout=120, creationflags=P.NO_WINDOW)
h_out = os.path.join(OUT, "hermes")
shutil.rmtree(h_out, ignore_errors=True)
os.makedirs(h_out)
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(r.stdout)), mode="r:gz") as t:
    t.extractall(h_out, filter="data")

# ---- template machine paths, scrub anything secret-looking
for root, _, files in os.walk(OUT):
    for f in files:
        p = os.path.join(root, f)
        try:
            s = open(p, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        s2 = s.replace(P.WORKSPACE_WSL, "{{WORKSPACE}}").replace(P.WSL_HOME, "{{HOME}}")
        if f == "config.yaml":
            s2 = SECRET_KEYS.sub(lambda m: m.group(1) + '""', s2)
        if s2 != s:
            open(p, "w", encoding="utf-8", newline="").write(s2)

n = sum(len(fs) for _, _, fs in os.walk(OUT))
print(f"Exported {n} files to {OUT}")
