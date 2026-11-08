"""Jarvis Windows bridge: the ONLY ways Jarvis (sandboxed in WSL) can touch Windows.

1. Open apps by name: only shortcuts in the Start menu / Desktop and installed Store apps.
   No paths, no arguments, no commands - just "open the app called X".
2. Jarvis's own Chrome: a separate Chrome profile (no saved passwords, no autofill, none of
   your logins/history) that Jarvis can drive. Chrome's debug port stays on Windows localhost.

Transport: WSL can't reach Windows localhost, but Windows can reach WSL localhost. So this
bridge keeps a few idle connections open *into* WSL (relay pool on 127.0.0.1:9333). The WSL
relay hands one to each request: b"G" = pipe to Chrome's debug port, b"C" = one JSON command.
"""
import difflib
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.request

import jarvis_paths as P

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME_PORT = 9250        # Windows side. (WSL mirrors its own listeners, incl. the relay's 9222, onto Windows localhost.)
CHROME_PROFILE = os.path.join(HERE, "chrome-profile")
POOL_PORT = 9333
POOL_SIZE = 4
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RELAY_SRC = os.path.join(HERE, "wsl", "jarvis-relay")


def _log(msg):
    print(time.strftime("%H:%M:%S"), "[bridge]", msg, flush=True)


# ------------------------------------------------------------------- apps --
class AppIndex:
    SKIP = re.compile(r"uninstall|readme|help|license|release notes|website|documentation|"
                      r"^jarvis( terminal)?$", re.I)

    def __init__(self):
        self.apps = {}          # display name -> ("lnk", path) | ("appx", AppID)
        self.loaded = 0

    def refresh(self, force=False):
        if not force and time.time() - self.loaded < 600:
            return
        apps = {}
        dirs = [os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
                os.path.expandvars(r"%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs"),
                os.path.join(os.path.expanduser("~"), "Desktop"),
                os.path.expandvars(r"%PUBLIC%\Desktop")]
        for d in dirs:
            for root, _, files in os.walk(d):
                for f in files:
                    stem, ext = os.path.splitext(f)
                    if ext.lower() in (".lnk", ".url", ".appref-ms") and not self.SKIP.search(stem):
                        apps.setdefault(stem, ("lnk", os.path.join(root, f)))
        try:   # Store / UWP apps (Spotify, WhatsApp, Settings, Calculator...)
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-StartApps | ForEach-Object { $_.Name + '|' + $_.AppID }"],
                                 capture_output=True, timeout=30, creationflags=NO_WINDOW)
            for line in out.stdout.decode("utf-8", "replace").splitlines():
                if "|" in line:
                    name, appid = line.rsplit("|", 1)
                    name = name.strip()
                    if name and not self.SKIP.search(name) and "!" in appid:
                        apps.setdefault(name, ("appx", appid.strip()))
        except Exception as e:
            _log(f"Store app scan failed: {e}")
        self.apps, self.loaded = apps, time.time()
        _log(f"indexed {len(apps)} apps")

    @staticmethod
    def _norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    def find(self, query):
        self.refresh()
        q = self._norm(query)
        if not q:
            return None, []
        names = list(self.apps)
        normed = {n: self._norm(n) for n in names}
        exact = [n for n in names if normed[n] == q]
        if exact:
            return exact[0], []
        starts = sorted([n for n in names if normed[n].startswith(q)], key=len)
        contains = sorted([n for n in names if q in normed[n]], key=len)
        if starts:
            return starts[0], starts[1:4]
        if contains:
            return contains[0], contains[1:4]
        close = difflib.get_close_matches(q, [normed[n] for n in names], n=3, cutoff=0.72)
        back = {normed[n]: n for n in names}
        close = [back[c] for c in close]
        return (close[0], close[1:]) if close else (None, [])

    def launch(self, name):
        kind, target = self.apps[name]
        if kind == "lnk":
            os.startfile(target)
        else:
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"], creationflags=NO_WINDOW)


# ----------------------------------------------------------------- chrome --
def _chrome_exe():
    for p in [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
              os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")]:
        if os.path.exists(p):
            return p
    return None


def _seed_profile():
    """Turn off password saving and autofill in Jarvis's Chrome profile."""
    prefs_path = os.path.join(CHROME_PROFILE, "Default", "Preferences")
    os.makedirs(os.path.dirname(prefs_path), exist_ok=True)
    prefs = {}
    if os.path.exists(prefs_path):
        try:
            prefs = json.load(open(prefs_path, encoding="utf-8"))
        except ValueError:
            prefs = {}
    prefs["credentials_enable_service"] = False
    prefs["credentials_enable_autosignin"] = False
    prefs.setdefault("profile", {})["password_manager_enabled"] = False
    prefs.setdefault("autofill", {}).update({"enabled": False, "profile_enabled": False,
                                             "credit_card_enabled": False})
    prefs.setdefault("signin", {})["allowed"] = False
    prefs.setdefault("browser", {})["has_seen_welcome_page"] = True
    json.dump(prefs, open(prefs_path, "w", encoding="utf-8"))


def chrome_up():
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{CHROME_PORT}/json/version", timeout=1).read()
        return True
    except Exception:
        return False


_chrome_lock = threading.Lock()


def _launch_chrome(exe):
    # Launch through the Windows shell (like double-clicking). Started directly from a
    # background pythonw process, Chrome does not open its debug port.
    args = (f"--remote-debugging-port={CHROME_PORT} \"--user-data-dir={CHROME_PROFILE}\" "
            "--no-first-run --no-default-browser-check --disable-sync about:blank")
    os.startfile(exe, "open", args)


def ensure_chrome():
    with _chrome_lock:
        if chrome_up():
            return True
        exe = _chrome_exe()
        if not exe:
            return False
        _seed_profile()
        # Debug port listens on 127.0.0.1 only (Chrome's default). Separate profile = no access
        # to your normal Chrome's passwords, logins, cookies, history or autofill.
        _launch_chrome(exe)
        for attempt in range(2):
            for _ in range(40):
                if chrome_up():
                    _log("Jarvis Chrome started")
                    return True
                time.sleep(0.25)
            if attempt == 0:
                # A Jarvis-profile Chrome without the debug port is in the way: restart it.
                _log("Jarvis Chrome not reachable, restarting it")
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
                                "Where-Object { $_.CommandLine -like '*JarvisVoice\\chrome-profile*' } | "
                                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                               capture_output=True, timeout=30, creationflags=NO_WINDOW)
                time.sleep(1.5)
                _launch_chrome(exe)
        return False


# ------------------------------------------------------------------ relay --
def _pipe(a, b):
    def one(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
    threading.Thread(target=one, args=(a, b), daemon=True).start()
    threading.Thread(target=one, args=(b, a), daemon=True).start()


class Bridge:
    def __init__(self, emit=None):
        self.emit = emit or (lambda kind, data: None)
        self.apps = AppIndex()
        self.relay_proc = None

    # --- commands from Jarvis -------------------------------------------
    def handle(self, cmd):
        action = cmd.get("action")
        if action == "open_app":
            name = str(cmd.get("name", ""))[:100]
            match, others = self.apps.find(name)
            if not match:
                return {"ok": False, "error": f"No app called '{name}' in the Start menu or Desktop."}
            if match.lower() in ("chrome", "google chrome"):
                ok = ensure_chrome()
                self.emit("action", {"text": "Opened Jarvis's Chrome"})
                return {"ok": ok, "opened": "Google Chrome (Jarvis profile)"}
            self.apps.launch(match)
            _log(f"opened app: {match}")
            self.emit("action", {"text": f"Opened {match}"})
            return {"ok": True, "opened": match, "other_matches": others}
        if action == "list_apps":
            self.apps.refresh()
            f = self.apps._norm(str(cmd.get("filter", "")))
            names = sorted(n for n in self.apps.apps if f in self.apps._norm(n))
            return {"ok": True, "apps": names[:200], "total": len(names)}
        if action == "browser":
            ok = ensure_chrome()
            if ok:
                self.emit("action", {"text": "Jarvis's Chrome is open"})
            return {"ok": ok, "note": "Separate Jarvis Chrome profile, no saved passwords or personal data."}
        if action == "screenshot":
            return self.screenshot(bool(cmd.get("all")))
        if action == "win_exec":
            return self.win_exec(str(cmd.get("command", "")), cmd.get("timeout"))
        if action == "access_status":
            import jarvis_access as A
            return {"ok": True, "full_access": A.is_full(), "seconds_left": A.seconds_left()}
        if action == "spotify":
            import jarvis_spotify
            r = jarvis_spotify.handle(cmd, launch_app=lambda n: self.handle({"action": "open_app", "name": n}))
            do = cmd.get("do", "status")
            if r.get("ok") and do != "status":
                now = f" · {r['title']} — {r['artist']}" if r.get("title") else ""
                self.emit("action", {"text": f"Spotify: {do}{(' ' + str(cmd.get('value'))) if cmd.get('value') not in (None, '') else ''}{now}"})
            return r
        if action == "refresh_apps":
            self.apps.refresh(force=True)
            return {"ok": True, "total": len(self.apps.apps)}
        return {"ok": False, "error": "unknown action"}

    # --- screen capture ----------------------------------------------------
    SCREEN_DIR = os.path.join(P.WORKSPACE_WIN, "screens")
    SCREEN_WINDOW = 120          # seconds after the user last spoke/typed in which capture is allowed
    SCREEN_KEEP = 3600           # screenshots are deleted after an hour
    last_user = 0.0              # set by the HUD whenever the user speaks or types

    # --- full access: run anything on Windows (only after the user's secret code) -------------
    confirm = None      # set by the HUD: confirm(title, detail) -> bool (asks the user)

    def win_exec(self, command, timeout=None):
        import jarvis_access as A
        if not A.is_full():
            return {"ok": False, "error": "Full access is locked. Ask the user to say or type their access code "
                                          "(e.g. 'Jarvis, access code ...') to unlock it."}
        command = command.strip()[:8000]
        if not command:
            return {"ok": False, "error": "No command given."}
        if A.HARD_RISK.search(command):
            ok = self.confirm("run a high-risk Windows command", command) if self.confirm else False
            if not ok:
                return {"ok": False, "error": "The user did not approve this high-risk command."}
        t = max(5, min(600, int(timeout or 120)))
        self.emit("action", {"text": f"Windows command (full access): {command[:120]}"})
        _log(f"win_exec: {command[:300]}")
        p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=os.path.expanduser("~"),
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        self._running.add(p)                         # so the kill switch can stop it
        try:
            out, err = p.communicate(timeout=t)
        except subprocess.TimeoutExpired:
            self._kill_tree(p.pid)
            return {"ok": False, "error": f"Timed out after {t}s."}
        finally:
            self._running.discard(p)
        if self._killed_at and time.time() - self._killed_at < 5:
            return {"ok": False, "error": "Stopped by the user's kill switch."}
        out = out.decode("utf-8", "replace")
        err = err.decode("utf-8", "replace")
        return {"ok": p.returncode == 0, "exit_code": p.returncode,
                "output": out[-12000:], "errors": err[-4000:]}

    _running = set()
    _killed_at = 0.0

    @staticmethod
    def _kill_tree(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)

    def kill_all(self):
        """Kill switch: stop every Windows command Jarvis started."""
        self._killed_at = time.time()
        n = 0
        for p in list(self._running):
            if p.poll() is None:
                self._kill_tree(p.pid); n += 1
        self._running.clear()
        return n

    def screenshot(self, all_screens=False):
        import jarvis_access as A
        if time.time() - self.last_user > self.SCREEN_WINDOW and not A.is_full():
            return {"ok": False, "error": "Screen capture is only allowed right after the user asks for it. "
                                          "Ask the user to repeat the request."}
        import ctypes
        from ctypes import wintypes
        from PIL import ImageGrab
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass
        os.makedirs(self.SCREEN_DIR, exist_ok=True)
        for f in os.listdir(self.SCREEN_DIR):              # tidy old captures
            p = os.path.join(self.SCREEN_DIR, f)
            if time.time() - os.path.getmtime(p) > self.SCREEN_KEEP:
                try:
                    os.remove(p)
                except OSError:
                    pass
        mons = []
        cb_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                  ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def cb(h, dc, r, lp):
            mons.append((r.contents.left, r.contents.top, r.contents.right, r.contents.bottom)); return 1
        ctypes.windll.user32.EnumDisplayMonitors(None, None, cb_t(cb), 0)
        vx = min(m[0] for m in mons); vy = min(m[1] for m in mons)
        img = ImageGrab.grab(all_screens=True)
        which = "all screens"
        if not all_screens:
            pt = wintypes.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            mon = next((m for m in mons if m[0] <= pt.x < m[2] and m[1] <= pt.y < m[3]), mons[0])
            img = img.crop((mon[0] - vx, mon[1] - vy, mon[2] - vx, mon[3] - vy))
            which = "the screen with the mouse"
        max_w = 2400 if all_screens else 1600              # smaller image = fewer tokens
        if img.width > max_w:
            img = img.resize((max_w, int(img.height * max_w / img.width)))
        name = time.strftime("screen-%Y%m%d-%H%M%S.png")
        img.convert("RGB").save(os.path.join(self.SCREEN_DIR, name), optimize=True)
        _log(f"screenshot: {which} -> {name}")
        self.emit("action", {"text": f"Captured {which} (auto-deleted in 1 hour)"})
        return {"ok": True, "path": f"{P.WORKSPACE_WSL}/screens/{name}",
                "captured": which, "size": f"{img.width}x{img.height}"}

    # --- pool connections into WSL --------------------------------------
    def _worker(self):
        while True:
            try:
                s = socket.create_connection(("127.0.0.1", POOL_PORT), timeout=5)
                s.settimeout(None)
                kind = s.recv(1)
                if kind == b"G":                          # CDP stream to Chrome
                    if not ensure_chrome():
                        s.close(); continue
                    c = socket.create_connection(("127.0.0.1", CHROME_PORT), timeout=5)
                    c.settimeout(None)
                    _pipe(s, c)
                elif kind == b"C":                        # one JSON command
                    buf = b""
                    while not buf.endswith(b"\n") and len(buf) < 20000:
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                    try:
                        reply = self.handle(json.loads(buf or b"{}"))
                    except Exception as e:
                        reply = {"ok": False, "error": str(e)}
                    s.sendall(json.dumps(reply).encode() + b"\n")
                    s.close()
                else:
                    s.close(); time.sleep(1)
            except OSError:
                time.sleep(2)                             # relay not up yet

    def _relay_keeper(self):
        """Run the relay inside WSL for as long as Jarvis runs."""
        while True:
            try:
                self.relay_proc = subprocess.Popen(
                    P.wsl("python3", P.WSL_BIN + "/jarvis-relay"),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=NO_WINDOW)
                self.relay_proc.wait()
            except Exception as e:
                _log(f"relay error: {e}")
            time.sleep(3)

    def start(self):
        threading.Thread(target=self.apps.refresh, daemon=True).start()
        threading.Thread(target=self._relay_keeper, daemon=True).start()
        for _ in range(POOL_SIZE):
            threading.Thread(target=self._worker, daemon=True).start()

    def stop(self):
        if self.relay_proc:
            self.relay_proc.kill()
