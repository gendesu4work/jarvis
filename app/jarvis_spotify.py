"""Control the Spotify desktop app on this PC (works with Spotify Free; no Spotify login).

Uses Windows's media controls (the same ones the media keys / volume flyout use), targeted at
Spotify's session only, plus per-app volume for Spotify. Searching/opening uses spotify: links.
"""
import asyncio
import os
import re
import sys
import time
from urllib.parse import quote

sys.coinit_flags = 0   # comtypes (used by pycaw): use the same COM mode as Jarvis's audio threads


def _com_init():
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


async def _session():
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
    mgr = await M.request_async()
    for s in mgr.get_sessions():
        if "spotify" in (s.source_app_user_model_id or "").lower():
            return s
    return None


async def _status(s):
    p = await s.try_get_media_properties_async()
    status = int(s.get_playback_info().playback_status)
    return {"playing": status == 4, "state": {4: "playing", 5: "paused", 3: "stopped"}.get(status, "idle"),
            "title": p.title or "", "artist": p.artist or "", "album": p.album_title or ""}


def _spotify_running():
    import psutil
    return any((p.info["name"] or "").lower() == "spotify.exe" for p in psutil.process_iter(["name"]))


def _set_volume(pct):
    _com_init()
    from pycaw.pycaw import AudioUtilities
    found = False
    for sess in AudioUtilities.GetAllSessions():
        if sess.Process and sess.Process.name().lower() == "spotify.exe":
            sess.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, pct / 100.0)), None)
            found = True
    return found


def _get_volume():
    _com_init()
    from pycaw.pycaw import AudioUtilities
    for sess in AudioUtilities.GetAllSessions():
        if sess.Process and sess.Process.name().lower() == "spotify.exe":
            return round(sess.SimpleAudioVolume.GetMasterVolume() * 100)
    return None


URI_RE = re.compile(r"^(?:https://open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|playlist|artist|show|episode)/([A-Za-z0-9]{10,40})"
                    r"|spotify:(track|album|playlist|artist|show|episode):([A-Za-z0-9]{10,40}))")


def handle(cmd, launch_app=None):
    """cmd: {"do": status|play|pause|toggle|next|previous|volume|search|open, "value": ...}"""
    _com_init()
    do = str(cmd.get("do", "status"))
    val = cmd.get("value")
    if do in ("search", "open") or not _spotify_running():
        if not _spotify_running():
            if launch_app:
                launch_app("Spotify")
            for _ in range(40):
                if _spotify_running():
                    break
                time.sleep(0.25)
            time.sleep(2)
    if do == "search":
        q = str(val or "").strip()[:200]
        if not q:
            return {"ok": False, "error": "Nothing to search for."}
        os.startfile("spotify:search:" + quote(q))
        return {"ok": True, "did": f"Opened Spotify search for '{q}'. Press play on a result (Spotify's free app can't auto-play search results)."}
    if do == "open":
        m = URI_RE.match(str(val or "").strip())
        if not m:
            return {"ok": False, "error": "Give a Spotify link like https://open.spotify.com/playlist/<id> or spotify:album:<id>."}
        kind, sid = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        os.startfile(f"spotify:{kind}:{sid}")
        return {"ok": True, "did": f"Opened the {kind} in Spotify."}
    if do == "volume":
        try:
            pct = float(val)
        except (TypeError, ValueError):
            cur = _get_volume()
            return {"ok": cur is not None, "volume": cur}
        ok = _set_volume(pct)
        return {"ok": ok, "did": f"Spotify volume set to {round(pct)}%." if ok else "Spotify isn't playing audio yet; start playback first."}

    async def run():
        s = await _session()
        if s is None:
            return {"ok": False, "error": "Spotify is open but has no active media session. Press play in Spotify once."}
        if do == "play":
            await s.try_play_async()
        elif do == "pause":
            await s.try_pause_async()
        elif do == "toggle":
            await s.try_toggle_play_pause_async()
        elif do == "next":
            await s.try_skip_next_async()
        elif do == "previous":
            await s.try_skip_previous_async()
        elif do != "status":
            return {"ok": False, "error": f"Unknown Spotify command '{do}'."}
        if do != "status":
            await asyncio.sleep(1.2)
        st = await _status(s)
        st["volume"] = _get_volume()
        st["ok"] = True
        return st
    return asyncio.run(run())
