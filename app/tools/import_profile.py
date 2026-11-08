"""Restore a saved Jarvis profile (from <repo or install dir>/profile) onto THIS machine.

usage: import_profile.py <profile_dir> [--overwrite]
Workspace files are copied into Documents\\jarvis (existing files kept unless --overwrite).
Hermes files (config, persona, memories, schedule, skills) are written into ~/.hermes in WSL,
with {{WORKSPACE}} / {{HOME}} filled in for this machine.
"""
import io, os, shutil, subprocess, sys, tarfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import jarvis_paths as P

src = sys.argv[1]
overwrite = "--overwrite" in sys.argv


def fill(text):
    return text.replace("{{WORKSPACE}}", P.WORKSPACE_WSL).replace("{{HOME}}", P.WSL_HOME)


# ---- workspace
ws = os.path.join(src, "workspace")
n_ws = 0
if os.path.isdir(ws):
    for root, _, files in os.walk(ws):
        for f in files:
            s = os.path.join(root, f)
            d = os.path.join(P.WORKSPACE_WIN, os.path.relpath(s, ws))
            if os.path.exists(d) and not overwrite:
                continue
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copy2(s, d)
            n_ws += 1
os.makedirs(os.path.join(P.WORKSPACE_WIN, "hud"), exist_ok=True)

# ---- hermes: build a tar in memory with placeholders filled, unpack inside WSL
hs = os.path.join(src, "hermes")
n_h = 0
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as t:
    if os.path.isdir(hs):
        for root, _, files in os.walk(hs):
            for f in files:
                p = os.path.join(root, f)
                rel = os.path.relpath(p, hs).replace("\\", "/")
                data = open(p, "rb").read()
                try:
                    data = fill(data.decode("utf-8")).replace("\r\n", "\n").encode("utf-8")
                except UnicodeDecodeError:
                    pass
                info = tarfile.TarInfo(rel); info.size = len(data); info.mode = 0o600
                t.addfile(info, io.BytesIO(data)); n_h += 1
r = subprocess.run(P.wsl("bash", "-c",
                         'mkdir -p "$HOME/.hermes" "$HOME/.jarvis" && tar -xzf - -C "$HOME/.hermes" && '
                         f'printf %s "{P.WORKSPACE_WSL}" > "$HOME/.jarvis/workspace"'),
                   input=buf.getvalue(), capture_output=True, timeout=120, creationflags=P.NO_WINDOW)
if r.returncode != 0:
    print("Hermes restore failed:", r.stderr.decode(errors="replace")[-500:]); sys.exit(1)
print(f"Restored {n_ws} workspace files and {n_h} Hermes files.")
