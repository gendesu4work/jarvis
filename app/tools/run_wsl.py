"""Run a local script inside Ubuntu without wsl.exe re-parsing it.
usage: run_wsl.py script.sh|script.py"""
import subprocess, sys
src = sys.argv[1]
data = open(src, "rb").read().lstrip(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
if src.endswith(".py"):
    cmd = 'PY=$(ls -td ~/.hermes/installs/*/environments/*/venv | head -1)/bin/python; exec "$PY" -'
else:
    cmd = "exec bash -s"
r = subprocess.run(["wsl.exe", "-d", "Ubuntu", "-e", "sh", "-c", cmd], input=data, capture_output=True)
out = (r.stdout + r.stderr).decode("utf-8", "replace")
print("\n".join(l for l in out.splitlines() if "fstab" not in l))
sys.exit(r.returncode)
