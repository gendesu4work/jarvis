"""Where things live on THIS machine. Everything machine-specific is detected here (or read from
machine.json, which the installer writes), so the same code runs on any PC/laptop."""
import json
import os
import subprocess

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MACHINE_FILE = os.path.join(APP_DIR, "machine.json")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _documents():
    """The real Documents folder (handles OneDrive / moved Documents)."""
    try:
        import ctypes
        from ctypes import wintypes
        import uuid
        fid = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}")   # FOLDERID_Documents
        guid = (ctypes.c_byte * 16).from_buffer_copy(fid.bytes_le)
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p)) == 0:
            out = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            return out
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def win_to_wsl(path):
    """C:\\Users\\me\\Documents\\jarvis -> /mnt/c/Users/me/Documents/jarvis"""
    drive, rest = os.path.splitdrive(os.path.abspath(path))
    return f"/mnt/{drive[0].lower()}" + rest.replace("\\", "/")


def _load():
    try:
        return json.load(open(MACHINE_FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(cfg):
    try:
        json.dump(cfg, open(MACHINE_FILE, "w", encoding="utf-8"), indent=2)
    except OSError:
        pass


_cfg = _load()
changed = False
if not _cfg.get("distro"):
    _cfg["distro"] = "Ubuntu"; changed = True
if not _cfg.get("workspace_win"):
    _cfg["workspace_win"] = os.path.join(_documents(), "jarvis"); changed = True
if not _cfg.get("workspace_wsl"):
    _cfg["workspace_wsl"] = win_to_wsl(_cfg["workspace_win"]); changed = True
if not _cfg.get("wsl_home"):
    try:
        r = subprocess.run(["wsl.exe", "-d", _cfg["distro"], "-e", "sh", "-c", 'printf %s "$HOME"'],
                           capture_output=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
        home = r.stdout.decode("utf-8", "replace").strip()
        if home.startswith("/"):
            _cfg["wsl_home"] = home; changed = True
    except Exception:
        pass
if changed and _cfg.get("wsl_home"):
    _save(_cfg)

DISTRO = _cfg["distro"]
WORKSPACE_WIN = _cfg["workspace_win"]
WORKSPACE_WSL = _cfg["workspace_wsl"]
WSL_HOME = _cfg.get("wsl_home", "/root")
WSL_BIN = WSL_HOME + "/.local/bin"


def wsl(*args, cwd=None):
    """argv for running a command inside Jarvis's Linux sandbox (no shell parsing)."""
    base = ["wsl.exe", "-d", DISTRO]
    if cwd:
        base += ["--cd", cwd]
    return base + ["-e", *args]
