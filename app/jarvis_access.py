"""Full-access mode, unlocked by the user's secret code.

- The code is checked HERE on Windows, before any text reaches the AI. The AI never sees it and
  cannot unlock itself.
- Only a salted PBKDF2 hash is stored, in access.json next to the app (never synced to GitHub).
- Full access expires automatically; 5 wrong codes lock unlocking for 5 minutes.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time

APP_DIR = os.path.dirname(os.path.abspath(__file__))
FILE = os.path.join(APP_DIR, "access.json")
DEFAULT_MINUTES = 10
MAX_MINUTES = 60
_lock = threading.Lock()
_state = {"until": 0.0, "fails": 0, "locked_until": 0.0}

# Even in full access, these still need a "yes" from the user (catastrophic / unrecoverable).
HARD_RISK = re.compile(
    r"\bformat(\.com)?\s+[a-z]:|format-volume|diskpart|clear-disk|initialize-disk|remove-partition|"
    r"bcdedit|cipher\s+/w|vssadmin\s+delete|wbadmin\s+delete|"
    r"(rm|remove-item|del|rd|rmdir)\b[^|;&\n]*\b(c:\\?|c:/|/mnt/c/?|c:\\windows|c:\\users|system32)\s*($|[\"'\s])|"
    r"rm\s+-rf?\s+/\s*($|[;&|])|mkfs|dd\s+if=|"
    r"set-mppreference|disable-?(defender|windowsdefender)|reg(\.exe)?\s+delete\s+hklm|"
    r"shutdown\s+/(s|r|p)\b|stop-computer|restart-computer", re.I)


def _load():
    try:
        return json.load(open(FILE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def has_code():
    return bool(_load().get("hash"))


def _hash(code, salt):
    return hashlib.pbkdf2_hmac("sha256", normalize(code).encode(), bytes.fromhex(salt), 300_000).hex()


def normalize(code):
    """Spoken and typed codes compare equal: case, spaces and punctuation are ignored."""
    return re.sub(r"[^a-z0-9]", "", str(code).lower())


def set_code(new_code, current_code=None):
    if len(normalize(new_code)) < 4:
        return False, "The code must have at least 4 letters or digits."
    if has_code() and not verify(current_code or "", count_fail=False):
        return False, "Current code is incorrect."
    salt = secrets.token_hex(16)
    data = _load()
    data.update({"salt": salt, "hash": _hash(new_code, salt)})
    json.dump(data, open(FILE, "w", encoding="utf-8"))
    return True, "Access code saved."


def verify(code, count_fail=True):
    data = _load()
    if not data.get("hash"):
        return False
    with _lock:
        if time.time() < _state["locked_until"]:
            return False
        ok = hmac.compare_digest(_hash(code, data["salt"]), data["hash"])
        if not ok and count_fail:
            _state["fails"] += 1
            if _state["fails"] >= 5:
                _state["locked_until"] = time.time() + 300
                _state["fails"] = 0
        elif ok:
            _state["fails"] = 0
        return ok


def unlock(code, minutes=DEFAULT_MINUTES):
    if time.time() < _state["locked_until"]:
        return False, "Too many wrong codes. Unlocking is blocked for a few minutes."
    if not has_code():
        return False, "No access code is set yet. Set one first on the screen."
    if not verify(code):
        return False, "That access code is incorrect."
    minutes = max(1, min(MAX_MINUTES, float(minutes or DEFAULT_MINUTES)))
    _state["until"] = time.time() + minutes * 60
    return True, f"Full access granted for {int(minutes)} minutes."


def lock():
    _state["until"] = 0.0


def is_full():
    return time.time() < _state["until"]


def seconds_left():
    return max(0, int(_state["until"] - time.time()))


# Phrases intercepted before anything is sent to the AI
UNLOCK_RE = re.compile(r"\b(?:access|override|secret|admin|master)\s+code\b[\s:,.-]*(?:is\s+)?(?P<code>.+)$", re.I)
KILL_RE = re.compile(r"\b(kill ?switch|emergency stop|abort everything|stop everything now)\b", re.I)
LOCK_RE = re.compile(r"\b(lock (?:it|up|yourself|down|everything)|end full access|revoke (?:full )?access|"
                     r"exit full access|full access off)\b", re.I)


def mask(text):
    """Hide a spoken/typed code from logs and the screen."""
    m = UNLOCK_RE.search(text or "")
    return text if not m else text[:m.start("code")] + "••••"
