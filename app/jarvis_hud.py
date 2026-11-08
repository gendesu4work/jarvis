"""Jarvis HUD: runs the voice engine and shows it in an app window.

Serves hud/index.html on 127.0.0.1 only, protected by a random token, streams
engine events with Server-Sent Events, and opens Microsoft Edge in app mode.
Closing the window shuts Jarvis down.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG_PATH = os.path.join(HERE, "jarvis.log")
if sys.stdout is None:            # started with pythonw.exe (no console): log to a file
    sys.stdout = sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)

import jarvis_voice as jv  # noqa: E402

TOKEN = secrets.token_urlsafe(16)
import jarvis_paths as P  # noqa: E402
WORKSPACE = P.WORKSPACE_WIN
PANEL_REFRESH = 60

clients = []                     # list of queues, one per open event stream
clients_lock = threading.Lock()
history = deque(maxlen=60)       # conversation log for late-joining windows
snapshot = {"state": {"state": "booting", "detail": "Starting"}, "devices": {},
            "toggles": {"voice": True, "mic": True}, "panels": {}, "stats": {}}
stats = {"replies": 0, "last_latency": None, "last_ok": None, "limited_at": None,
         "started": time.time()}
seen_client = threading.Event()
last_client_gone = [time.time()]


def broadcast(kind, data):
    if kind == "state":
        snapshot["state"] = data
    elif kind == "devices":
        snapshot["devices"] = data
    elif kind == "toggles":
        snapshot["toggles"] = data
    elif kind == "approval":
        snapshot["approval"] = data
    elif kind == "approval_done":
        snapshot["approval"] = None
    if kind == "heard":            # the user just spoke/typed: screen capture allowed briefly
        try:
            bridge.last_user = time.time()
        except NameError:
            pass
    if kind in ("heard", "reply", "activity", "action"):
        item = dict(data, kind=kind, t=time.strftime("%H:%M"))
        history.append(item)
        data = item
        if kind == "reply":
            usage_now.set()
            stats["replies"] += 1
            stats["last_latency"] = data.get("latency")
            stats["last_ok"] = data.get("ok")
            if not data.get("ok") and "limit" in data.get("text", "").lower():
                stats["limited_at"] = time.time()
            snapshot["stats"] = dict(stats)
            push("stats", snapshot["stats"])
    push(kind, data)


def push(kind, data):
    msg = f"event: {kind}\ndata: {json.dumps(data)}\n\n".encode()
    with clients_lock:
        for q in list(clients):
            q.append(msg)


# ------------------------------------------------------------------ panels --
PANEL_SCRIPT = (
    "cat ~/.hermes/memories/MEMORY.md 2>/dev/null; echo '@@@'; "
    "cat ~/.hermes/memories/USER.md 2>/dev/null; echo '@@@'; "
    "~/.local/bin/hermes cron list 2>&1 | head -40; echo '@@@'; "
    "~/.local/bin/hermes config get model.default 2>/dev/null"
)


def read_panels():
    panels = {}
    try:
        out = subprocess.run(P.wsl("sh", "-c", PANEL_SCRIPT),
                             capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                             creationflags=jv.NO_WINDOW, env=dict(os.environ, WSL_UTF8="1"))
        parts = out.stdout.decode("utf-8", "replace").split("@@@")
        parts += [""] * (4 - len(parts))
        strip = lambda s: "\n".join(l for l in s.splitlines() if "fstab" not in l).strip()
        panels["memory"] = strip(parts[0])
        panels["user"] = strip(parts[1])
        panels["cron"] = strip(parts[2])
        panels["model"] = strip(parts[3]).splitlines()[-1] if strip(parts[3]) else "unknown"
        panels["brain_ok"] = out.returncode == 0
    except Exception as e:
        panels["brain_ok"] = False
        panels["error"] = str(e)
    files = []
    try:
        for name in sorted(os.listdir(WORKSPACE)):
            if name.startswith("."):
                continue
            p = os.path.join(WORKSPACE, name)
            st = os.stat(p)
            files.append({"name": name, "dir": os.path.isdir(p), "size": st.st_size,
                          "mtime": st.st_mtime})
        files.sort(key=lambda f: -f["mtime"])
    except Exception:
        pass
    panels["files"] = files[:14]
    panels["updated"] = time.strftime("%H:%M")
    return panels


def panel_loop(refresh_now):
    while True:
        snapshot["panels"] = read_panels()
        push("panels", snapshot["panels"])
        refresh_now.wait(PANEL_REFRESH)
        refresh_now.clear()


# -------------------------------------------------------- customization --
# Jarvis customizes its own HUD by editing files in Documents\jarvis\hud\ (its folder):
#   theme.css    extra CSS (colours, fonts, sizes)      -> applied live
#   layout.json  which panels go where / title / hints  -> applied live (validated)
#   settings.json voice & listening settings            -> read by the voice engine
# and keeps notes in Documents\jarvis\notes.md (shown in the Notes panel).
HUD_DIR = os.path.join(WORKSPACE, "hud")
NOTES_PATH = os.path.join(WORKSPACE, "notes.md")
PANELS = {"systems", "pc", "usage", "notes", "memory", "conversation", "schedule", "workspace"}
DEFAULT_LAYOUT = {"title": "JARVIS", "subtitle": "PERSONAL INTELLIGENCE · LOCAL", "monitor": "left",
                  "left": ["systems", "pc", "usage", "notes", "memory"], "right": ["conversation", "schedule", "workspace"],
                  "chips": ["What can you do?", "Read my notes", "Open Chrome",
                            "Remind me in 10 minutes to stretch", "What do you remember about me?"]}


def read_layout():
    lay = dict(DEFAULT_LAYOUT)
    try:
        data = json.load(open(os.path.join(HUD_DIR, "layout.json"), encoding="utf-8-sig"))
    except (OSError, ValueError):
        return lay
    for side in ("left", "right"):
        if isinstance(data.get(side), list):
            lay[side] = [p for p in data[side] if p in PANELS][:6]
    for k in ("title", "subtitle"):
        if isinstance(data.get(k), str):
            lay[k] = data[k][:40]
    if data.get("monitor") in ("left", "right", "main"):
        lay["monitor"] = data["monitor"]
    if isinstance(data.get("chips"), list):
        lay["chips"] = [str(c)[:60] for c in data["chips"]][:8]
    return lay


def read_notes():
    try:
        return open(NOTES_PATH, encoding="utf-8-sig", errors="replace").read()[:20000]
    except OSError:
        return ""


def pc_loop():
    """Live PC meters: CPU, RAM, GPU (+ how much of each Jarvis itself is using)."""
    import psutil
    specs = {"cpu": "", "cores": psutil.cpu_count(logical=False), "threads": psutil.cpu_count(),
             "ram_gb": round(psutil.virtual_memory().total / 2**30, 1), "gpu": ""}
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        specs["cpu"] = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
    except OSError:
        pass
    jarvis_procs, last_scan = [], 0.0
    me = psutil.Process()

    def scan():
        procs = [me] + me.children(recursive=True)
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (p.info["name"] or "").lower()
                cmd = " ".join(p.info["cmdline"] or [])
                if name.startswith("vmmem") or "JarvisVoice\\chrome-profile" in cmd or "JarvisVoice\\edge-profile" in cmd:
                    procs.append(p)
            except (psutil.Error, TypeError):
                pass
        seen, out = set(), []
        for p in procs:
            if p.pid not in seen:
                seen.add(p.pid); out.append(p)
                try:
                    p.cpu_percent(None)      # prime per-process CPU measurement
                except psutil.Error:
                    pass
        return out

    psutil.cpu_percent(None)
    while True:
        time.sleep(2)
        if time.time() - last_scan > 15:
            jarvis_procs, last_scan = scan(), time.time()
        vm = psutil.virtual_memory()
        j_cpu = j_mem = 0.0
        for p in jarvis_procs:
            try:
                j_cpu += p.cpu_percent(None)
                j_mem += p.memory_info().rss
            except psutil.Error:
                pass
        data = {"specs": specs, "cpu": psutil.cpu_percent(None), "ram_used_gb": round(vm.used / 2**30, 1),
                "ram_pct": vm.percent, "jarvis_cpu": round(j_cpu / (specs["threads"] or 1), 1),
                "jarvis_ram_gb": round(j_mem / 2**30, 2)}
        try:
            g = subprocess.run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                                "--format=csv,noheader,nounits"], capture_output=True, timeout=5,
                               creationflags=jv.NO_WINDOW).stdout.decode().strip().splitlines()[0].split(", ")
            specs["gpu"] = g[0]
            data.update(gpu=float(g[1]), vram_used_gb=round(float(g[2]) / 1024, 1),
                        vram_total_gb=round(float(g[3]) / 1024, 1), gpu_temp=float(g[4]))
        except Exception:
            pass
        snapshot["pc"] = data
        push("pc", data)


usage_now = threading.Event()


def usage_loop():
    """Usage meter: today's Gemini requests vs. limits (computed in WSL from Hermes's log)."""
    while True:
        try:
            out = subprocess.run(P.wsl("python3", P.WSL_BIN + "/jarvis-usage"),
                                 capture_output=True, timeout=60, stdin=subprocess.DEVNULL,
                                 creationflags=jv.NO_WINDOW)
            data = json.loads(out.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
            data["at"] = time.time()
            snapshot["usage"] = data
            push("usage", data)
        except Exception as e:
            jv.log(f"usage meter error: {e!r}")
        usage_now.wait(60)
        usage_now.clear()


def custom_watch():
    files = [os.path.join(HUD_DIR, n) for n in ("theme.css", "layout.json")] + [NOTES_PATH]
    last = None
    while True:
        try:
            sig = tuple(os.path.getmtime(f) if os.path.exists(f) else 0 for f in files)
        except OSError:
            sig = None
        if sig != last:
            last = sig
            snapshot["custom"] = {"layout": read_layout(), "css_version": int(time.time() * 1000)}
            snapshot["notes"] = read_notes()
            push("custom", snapshot["custom"])
            push("notes", {"text": snapshot["notes"]})
        time.sleep(1.5)


# ------------------------------------------------------------------ server --
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _authorized(self):
        q = parse_qs(urlparse(self.path).query)
        return secrets.compare_digest(q.get("t", [""])[0], TOKEN)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/icon.png":
            data = open(os.path.join(HERE, "hud", "icon.png"), "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers(); self.wfile.write(data)
            return
        if not self._authorized():
            self.send_error(403); return
        if path == "/":
            html = open(os.path.join(HERE, "hud", "index.html"), "rb").read()
            html = html.replace(b"__TOKEN__", TOKEN.encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(html)
        elif path == "/custom.css":
            try:
                css = open(os.path.join(HUD_DIR, "theme.css"), "rb").read()[:200000]
            except OSError:
                css = b""
            # CSS only: block anything that could load outside resources or run script.
            css = re.sub(rb"(?i)@import|url\s*\(|expression\s*\(|javascript:|</", b"/*blocked*/", css)
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers(); self.wfile.write(css)
        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            q = deque()
            init = dict(snapshot, history=list(history))
            q.append(f"event: snapshot\ndata: {json.dumps(init)}\n\n".encode())
            with clients_lock:
                clients.append(q)
            seen_client.set()
            try:
                last_ping = time.time()
                while True:
                    while q:
                        self.wfile.write(q.popleft())
                    if time.time() - last_ping > 10:
                        self.wfile.write(b": ping\n\n"); last_ping = time.time()
                    self.wfile.flush()
                    time.sleep(0.03)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass
            finally:
                with clients_lock:
                    clients.remove(q)
                    if not clients:
                        last_client_gone[0] = time.time()
        else:
            self.send_error(404)

    def do_POST(self):
        if not self._authorized():
            self.send_error(403); return
        n = int(self.headers.get("Content-Length", 0) or 0)
        try:
            cmd = json.loads(self.rfile.read(min(n, 20000)) or b"{}")
        except ValueError:
            self.send_error(400); return
        kind = cmd.get("type")
        if kind == "text":
            jarvis.submit_text(str(cmd.get("text", ""))[:4000])
        elif kind == "wake":
            jarvis.wake_now()
        elif kind == "stop":
            jarvis.stop_speaking()
        elif kind == "toggle" and cmd.get("key") in ("voice", "mic"):
            jarvis.set_toggle(cmd["key"], cmd.get("value"))
        elif kind == "refresh":
            refresh_now.set()
        elif kind == "approve":
            jarvis.answer_approval(str(cmd.get("option", "deny"))[:40])
        self.send_response(204); self.end_headers()


def find_edge():
    for p in [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]:
        if os.path.exists(p):
            return p
    return None


def monitor_origin(which):
    """Top-left (x, y) of the chosen monitor: 'left', 'right' or 'main'."""
    import ctypes
    from ctypes import wintypes

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    mons = []
    cb_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(h, dc, r, lp):
        mi = MONITORINFO(); mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(h, ctypes.byref(mi))
        mons.append((mi.rcMonitor.left, mi.rcMonitor.top, bool(mi.dwFlags & 1)))
        return 1
    ctypes.windll.user32.EnumDisplayMonitors(None, None, cb_t(cb), 0)
    main = next((m for m in mons if m[2]), mons[0] if mons else (0, 0, True))
    others = [m for m in mons if not m[2]]
    if which == "left":
        pick = min((m for m in others if m[0] < main[0]), key=lambda m: m[0], default=main)
    elif which == "right":
        pick = max((m for m in others if m[0] > main[0]), key=lambda m: m[0], default=main)
    else:
        pick = main
    return pick[0], pick[1]


def open_window(url):
    edge = find_edge()
    if not edge:
        import webbrowser
        webbrowser.open(url); return
    x, y = monitor_origin(read_layout().get("monitor", "left"))
    subprocess.Popen([edge, f"--app={url}", f"--window-position={x + 40},{y + 40}",
                      "--window-size=1280,720", "--start-fullscreen",
                      f"--user-data-dir={os.path.join(HERE, 'edge-profile')}",
                      "--no-first-run", "--disable-features=Translate"],
                     creationflags=jv.NO_WINDOW)


def already_running():
    """Single instance: if Jarvis is already up, just reopen its window."""
    import ctypes
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\JarvisHUD")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        try:
            open_window(open(os.path.join(HERE, "hud_url.txt")).read().strip())
        except OSError:
            pass
        return True
    return False


if __name__ == "__main__":
    no_window = "--no-window" in sys.argv
    if already_running():
        sys.exit(0)
    jarvis = jv.Jarvis(emit=broadcast)
    refresh_now = threading.Event()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    with open(os.path.join(HERE, "hud_url.txt"), "w") as f:
        f.write(url)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=panel_loop, args=(refresh_now,), daemon=True).start()
    threading.Thread(target=custom_watch, daemon=True).start()
    threading.Thread(target=usage_loop, daemon=True).start()
    threading.Thread(target=pc_loop, daemon=True).start()
    import jarvis_bridge
    bridge = jarvis_bridge.Bridge(emit=broadcast)
    bridge.start()
    jv.log(f"HUD at {url}")

    if not no_window:
        open_window(url)
    threading.Thread(target=jarvis.start, daemon=True).start()

    # Quit when the window has been closed for a few seconds.
    try:
        while True:
            time.sleep(1)
            if no_window:
                continue
            with clients_lock:
                n = len(clients)
            if seen_client.is_set() and n == 0 and time.time() - last_client_gone[0] > 6:
                jv.log("Window closed; shutting down.")
                break
    except KeyboardInterrupt:
        pass
    try:
        bridge.stop()
        if jarvis.hermes:
            jarvis.hermes._reset()
    except Exception:
        pass
    os._exit(0)
