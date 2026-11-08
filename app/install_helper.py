"""Copy Jarvis's helper scripts into Ubuntu's ~/.local/bin (clean LF, no BOM)."""
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import jarvis_paths as P
HERE = os.path.dirname(os.path.abspath(__file__))
FILES = {"jarvis-ask.sh": "jarvis-ask", os.path.join("wsl", "jarvis-relay"): "jarvis-relay",
         os.path.join("wsl", "open-app"): "open-app", os.path.join("wsl", "jarvis-usage"): "jarvis-usage",
         os.path.join("wsl", "spotify"): "spotify", os.path.join("wsl", "winrun"): "winrun"}
for src, name in FILES.items():
    data = open(os.path.join(HERE, src), "rb").read().lstrip(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    dst = f"{P.WSL_BIN}/{name}"
    r = subprocess.run(P.wsl("sh", "-c", f"mkdir -p {P.WSL_BIN} && cat > {dst} && chmod 755 {dst}"),
                       input=data, capture_output=True)
    print(name, "ok" if r.returncode == 0 else r.stderr.decode()[-300:])
