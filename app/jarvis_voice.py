"""Jarvis voice engine (Windows side).

Listens on the microphone for "Jarvis", transcribes what you say with
faster-whisper, sends the text to Hermes running in WSL, and speaks the reply
with Piper on the speakers. Everything except the
Gemini call runs locally.

Run directly for a console-only Jarvis, or through jarvis_hud.py for the HUD.

This lives OUTSIDE Documents\\jarvis on purpose: Jarvis can edit files in its
own folder, and must never be able to change the code that runs on Windows.
"""
import os
import queue
import re
import subprocess
import sys
import threading
import time

import numpy as np
import sounddevice as sd

# ---------------------------------------------------------------- settings --
INPUT_DEVICE = "default"                        # part of the mic's name, or "default" (settings.json: input_device)
OUTPUT_DEVICE = "default"                       # part of the speakers' name, or "default" (settings.json: output_device)
OUTPUT_GAIN_DB = -8.0      # Jarvis's voice volume: 0 = full, -6 = half, -12 = quieter
WAKE_THRESHOLD = 0.5       # 0..1, higher = fewer false wakes, may need clearer "Hey Jarvis"
WHISPER_MODEL = "small"    # tiny | base | small | medium (bigger = more accurate, slower)
SILENCE_TO_STOP = 1.2      # seconds of quiet that ends your sentence
MAX_UTTERANCE = 20.0       # hard cap on one request, seconds
FOLLOW_UP_WINDOW = 8.0     # during a conversation: how long Jarvis waits for your next sentence
FOLLOW_UP_AFTER_QUESTION = 12.0  # ...when Jarvis just asked you something
NAME_PAUSE = 0.7          # pause that ends a phrase while waiting for the name
NAME_MAX_SEGMENT = 12.0    # longest phrase checked for the name, seconds
GATE_MODEL = "base.en"   # small, fast model that only checks whether "Jarvis" was said
HERMES_TIMEOUT = 240       # seconds to wait for an answer
VOICE = "en_GB-alan-medium"
SPEECH_SPEED = 1.15        # 1.0 = normal, 1.15 = a little faster, 1.3 = fast
INTERRUPT_THRESHOLD = 2.5  # talk over Jarvis to stop it: how much louder than its echo you must be (0 = off)
# ---------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
OWW_DIR = os.path.join(HERE, "oww")
VOICE_PATH = os.path.join(HERE, "voices", VOICE + ".onnx")
# The request text travels in the JARVIS_PROMPT env var, so nothing in it is ever run as a command.
# jarvis-ask (in Ubuntu) continues the latest Jarvis conversation, or starts one.
import jarvis_paths as P  # noqa: E402
WSL_CMD = P.wsl(P.WSL_BIN + "/jarvis-ask")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# "Jarvis", "Hey Jarvis", "OK Jarvis" (plus common mishearings like "Jervis")
NAME_RE = re.compile(r"\b(?:(?:hey|hi|ok|okay|yo)[\s,]+)?j[ae]r+v[ie]s\b", re.I)

# Phrases that end a conversation (Jarvis then needs its name again)
END_RE = re.compile(r"\W*(?:(?:ok(?:ay)?|alright|no|great|cool|perfect)[\s,.!]+)?"
                    r"(?:stop|cancel|never ?mind|nothing|that'?s (?:all|it)|that is (?:all|it)|"
                    r"that(?:'ll| will) be all|thanks?(?: you)?(?: (?:so|very) much)?|thank you|"
                    r"good ?bye|bye|see you|i'?m done|we'?re done|go to sleep)"
                    r"(?:[\s,]+(?:jarvis|sir|mate|buddy))?\W*", re.I)

MIC_RATE = 48000
RATE = 16000
BLOCK = 3840               # 80 ms at 48 kHz -> 1280 samples at 16 kHz (openWakeWord frame)


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def com_init():
    """WASAPI audio needs COM set up in every thread that plays or records."""
    try:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0)
    except Exception:
        pass


def audio_thread(fn, *args):
    """Start a daemon thread that may use Windows audio."""
    def run():
        com_init()
        fn(*args)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _default_device(kind):
    """Windows's default mic/speakers (via WASAPI, falling back to PortAudio's default)."""
    key = "default_input_device" if kind == "input" else "default_output_device"
    for h in sd.query_hostapis():
        if h["name"] == "Windows WASAPI" and h[key] >= 0:
            return h[key], sd.query_devices(h[key])
    idx = sd.default.device[0 if kind == "input" else 1]
    return idx, sd.query_devices(idx)


def find_device(name_part, kind):
    """Device whose name contains name_part; "default"/"" or not found -> the Windows default."""
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    if name_part and name_part.lower() != "default":
        fallback = None
        for i, d in enumerate(sd.query_devices()):
            if d[key] > 0 and name_part.lower() in d["name"].lower():
                if sd.query_hostapis(d["hostapi"])["name"] == "Windows WASAPI":
                    return i, d
                fallback = fallback or (i, d)
        if fallback:
            return fallback
        log(f"No {kind} device matching '{name_part}' here; using the Windows default.")
    try:
        return _default_device(kind)
    except Exception:
        raise RuntimeError(f"No {kind} audio device found. Is a microphone/speaker connected?")


def clean_for_speech(text):
    text = re.sub(r"```.*?```", " (I put the code on screen.) ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)       # links -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"[*_#>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------- self-customization ----
# Jarvis may edit Documents\jarvis\hud\settings.json to change how it sounds/behaves.
# Only these keys are read, and every value is clamped to a safe range.
WORKSPACE_WIN = P.WORKSPACE_WIN
SETTINGS_PATH = os.path.join(WORKSPACE_WIN, "hud", "settings.json")
SETTING_LIMITS = {   # key: (global name, min, max)
    "voice_volume_db": ("OUTPUT_GAIN_DB", -30.0, 0.0),
    "speech_speed": ("SPEECH_SPEED", 0.7, 1.6),
    "wake_sensitivity": ("WAKE_THRESHOLD", 0.2, 0.9),
    "follow_up_seconds": ("FOLLOW_UP_WINDOW", 0.0, 30.0),
    "follow_up_after_question_seconds": ("FOLLOW_UP_AFTER_QUESTION", 0.0, 40.0),
    "pause_to_end_sentence": ("SILENCE_TO_STOP", 0.6, 3.0),
    "interrupt_threshold": ("INTERRUPT_THRESHOLD", 0.0, 8.0),
}


def load_settings():
    """Apply settings.json (if any). Returns the dict of applied values."""
    import json
    applied = {}
    try:
        data = json.load(open(SETTINGS_PATH, encoding="utf-8-sig"))
    except (OSError, ValueError):
        return applied
    for key, name in (("input_device", "INPUT_DEVICE"), ("output_device", "OUTPUT_DEVICE")):
        if isinstance(data.get(key), str) and len(data[key]) < 100:
            globals()[name] = data[key]
            applied[key] = data[key]
    for key, (name, lo, hi) in SETTING_LIMITS.items():
        if key in data:
            try:
                v = min(hi, max(lo, float(data[key])))
            except (TypeError, ValueError):
                continue
            globals()[name] = v
            applied[key] = v
    return applied


def tidy_reply(text):
    """Clean Hermes output for display/speech."""
    if "â" in text or "Ã" in text:
        try:
            text = text.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    text = re.split(r"\n[^\n]*File-mutation verifier", text)[0]
    paras, out = [p.strip() for p in text.split("\n\n")], []
    for p in paras:
        if p and (not out or out[-1] != p):
            out.append(p)
    return "\n\n".join(out).strip()


YES_RE = re.compile(r"\b(yes|yeah|yep|yup|sure|ok(ay)?|go ahead|do it|allow|approve[d]?|proceed|affirmative|please do)\b", re.I)
NO_RE = re.compile(r"\b(no|nope|nah|don'?t|do not|deny|stop|cancel|negative|abort)\b", re.I)
ALWAYS_RE = re.compile(r"\b(always|for (the|this) session|from now on|every time)\b", re.I)


def ask_hermes(prompt):
    """Returns (reply_text, ok)."""
    env = dict(os.environ, JARVIS_PROMPT=prompt, WSLENV="JARVIS_PROMPT", WSL_UTF8="1")
    try:
        out = subprocess.run(WSL_CMD, env=env, capture_output=True, timeout=HERMES_TIMEOUT,
                             stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "Sorry, that took too long. Please try again.", False
    clean = lambda b: "\n".join(l for l in b.decode("utf-8", "replace").splitlines()
                                if "fstab" not in l).strip()
    reply, err = clean(out.stdout), clean(out.stderr)
    if re.search(r"RESOURCE_EXHAUSTED|free tier|quota|rate limit|\b429\b", reply + err, re.I):
        return "I've hit Google's free usage limit. Give me about a minute, then ask again.", False
    if not reply:
        if err:
            log(f"Hermes error: {err[-400:]}")
        return "Sorry, something went wrong on my end.", False
    return reply, True


class Jarvis:
    """Voice engine. `emit(kind, data)` receives events for a UI (state, level, heard, reply...)."""

    def __init__(self, emit=None):
        self.emit = emit or (lambda kind, data: None)
        self._await_answer = threading.Event()   # an approval question wants a spoken answer
        self._answers = queue.Queue()            # spoken or clicked answers to approvals
        self._pending = None                     # current approval request (dict) for the HUD
        self.hermes = None
        self.state = "booting"
        self.voice_on = True          # speak replies
        self.mic_on = True            # listen for wake word
        self._manual_wake = threading.Event()
        self._brain_q = queue.Queue()
        self._busy = threading.Event()
        self._followup = threading.Event()
        self._play_lock = threading.Lock()
        self._stop_speech = threading.Event()
        self._barged = threading.Event()          # you talked over Jarvis
        self._speaking_audio = threading.Event()  # Jarvis's voice is actually playing right now
        self._echo_ratio = None                   # learned: how loud Jarvis's own voice is in the mic
        self._out_hist = []
        self._frames = queue.Queue()
        self.devices = {}

    # ------------------------------------------------------------ helpers --
    def set_state(self, state, detail=""):
        self.state = state
        self.emit("state", {"state": state, "detail": detail})

    def _load(self):
        self.set_state("booting", "Finding audio devices")
        deadline = time.time() + 120          # at login, USB audio can take a while to appear
        while True:
            try:
                self.in_dev, in_info = find_device(INPUT_DEVICE, "input")
                self.out_dev, out_info = find_device(OUTPUT_DEVICE, "output")
                break
            except RuntimeError as e:
                if time.time() > deadline:
                    raise
                self.set_state("booting", f"Waiting for audio devices ({e})")
                time.sleep(3)
                sd._terminate(); sd._initialize()     # re-scan devices
        self.devices = {"mic": in_info["name"], "speakers": out_info["name"],
                        "gain_db": OUTPUT_GAIN_DB}
        self.emit("devices", self.devices)
        log(f"Mic: {in_info['name']} | Speakers: {out_info['name']} ({OUTPUT_GAIN_DB:+.0f} dB)")

        self.set_state("booting", "Loading wake word")
        from openwakeword.model import Model
        self.wake = Model(wakeword_models=[os.path.join(OWW_DIR, "hey_jarvis_v0.1.onnx")],
                          melspec_model_path=os.path.join(OWW_DIR, "melspectrogram.onnx"),
                          embedding_model_path=os.path.join(OWW_DIR, "embedding_model.onnx"),
                          inference_framework="onnx")
        self.set_state("booting", "Loading speech recognition")
        from faster_whisper import WhisperModel
        self.stt = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        self.gate = WhisperModel(GATE_MODEL, device="cpu", compute_type="int8", cpu_threads=4)
        self.set_state("booting", "Loading voice")
        from piper import PiperVoice
        self.voice = PiperVoice.load(VOICE_PATH)
        self.vrate = self.voice.config.sample_rate
        self.gain = 10 ** (OUTPUT_GAIN_DB / 20)

    # -------------------------------------------------------------- audio --
    def _play(self, audio, rate, report_levels=False):
        audio = np.clip(audio * self.gain, -1.0, 1.0).astype(np.float32)
        with self._play_lock:
            self._stop_speech.clear()
            try:
                sd.play(audio, rate, device=self.out_dev,
                        extra_settings=sd.WasapiSettings(auto_convert=True))
            except Exception:
                sd.play(audio, rate, device=self.out_dev)
            if report_levels:
                hop = int(rate * 0.05)
                env = [float(np.sqrt((audio[i:i + hop] ** 2).mean()))
                       for i in range(0, len(audio), hop)] or [0.0]
                t0 = time.time()
                self._speak_t0 = t0
                self._out_hist = []                 # (time, level) of what Jarvis is playing: for barge-in
                self._speaking_audio.set()
                while True:
                    i = int((time.time() - t0) / 0.05)
                    if i >= len(env) or self._stop_speech.is_set():
                        break
                    self._out_hist.append((time.time(), env[i]))
                    if len(self._out_hist) > 40:
                        del self._out_hist[:10]
                    self.emit("level", {"v": min(1.0, env[i] * 6), "src": "out"})
                    time.sleep(0.05)
                if self._stop_speech.is_set():
                    sd.stop()
            sd.wait()
            self._speaking_audio.clear()

    def _synth(self, text):
        parts = []
        from piper import SynthesisConfig
        cfg = SynthesisConfig(length_scale=1.0 / max(0.5, SPEECH_SPEED))
        for chunk in self.voice.synthesize(text, syn_config=cfg):
            arr = getattr(chunk, "audio_float_array", None)
            if arr is None:
                arr = chunk.audio_int16_array.astype(np.float32) / 32768.0
            parts.append(arr.astype(np.float32))
        return np.concatenate(parts) if parts else np.zeros(1, np.float32)

    def say(self, text):
        text = clean_for_speech(text)
        if not text or not self.voice_on:
            return
        self.set_state("speaking")
        self._play(self._synth(text), self.vrate, report_levels=True)
        self.emit("level", {"v": 0.0, "src": "out"})

    def chime(self, up=True):
        t = np.linspace(0, 0.12, int(self.vrate * 0.12), False)
        f1, f2 = (660, 880) if up else (880, 660)
        tone = np.concatenate([np.sin(2 * np.pi * f1 * t), np.sin(2 * np.pi * f2 * t)])
        tone *= np.hanning(len(tone)) * 0.35
        self._play(tone.astype(np.float32), self.vrate)

    def stop_speaking(self):
        self._stop_speech.set()

    # ------------------------------------------------------------ control --
    def wake_now(self):
        """Manual wake (button / space key)."""
        if self.state == "speaking":
            self.stop_speaking()
        elif self.state in ("standby", "booting") or not self.mic_on:
            self._manual_wake.set()

    def submit_text(self, text):
        text = (text or "").strip()
        if text:
            self.emit("heard", {"text": text, "via": "typed"})
            self._brain_q.put((text, False))

    def set_toggle(self, key, value):
        if key == "voice":
            self.voice_on = bool(value)
            if not self.voice_on:
                self.stop_speaking()
        elif key == "mic":
            self.mic_on = bool(value)
            if self.state == "standby":
                self.set_state("standby")
        self.emit("toggles", {"voice": self.voice_on, "mic": self.mic_on})

    # -------------------------------------------------------------- brain --
    def _brain_loop(self):
        while True:
            text, spoken = self._brain_q.get()
            self._busy.set()
            if text == "__greet__":
                self.say("Jarvis online.")
                self._busy.clear(); self.set_state("standby")
                continue
            if END_RE.fullmatch(text):
                bye = "Anytime." if re.search(r"thank", text, re.I) else "Very well."
                self.emit("reply", {"text": bye, "ok": True, "latency": 0})
                self.say(bye)
                self._busy.clear(); self.set_state("standby")   # conversation over
                continue
            self.set_state("thinking", text)
            t0 = time.time()
            reply, ok = self._ask(text)
            lat = time.time() - t0
            log(f"Jarvis ({lat:.1f}s): {reply}")
            self.emit("reply", {"text": reply, "ok": ok, "latency": round(lat, 1)})
            self.say(reply)
            self._busy.clear()
            if self._barged.is_set():
                continue                    # you interrupted: the mic thread is already listening to you
            if spoken and ok and self.mic_on:
                # In a conversation: keep listening briefly for a reply (no name needed).
                self._followup_secs = FOLLOW_UP_AFTER_QUESTION if reply.rstrip().endswith("?") \
                    else FOLLOW_UP_WINDOW
                self._followup.set()
            else:
                self.set_state("standby")

    # ------------------------------------------------------- hermes link --
    def _ask(self, text):
        """Ask Hermes over the persistent ACP link; falls back to one-shot mode if it fails."""
        if self.hermes is not None:
            try:
                reply = tidy_reply(self.hermes.ask(text, timeout=HERMES_TIMEOUT))
                if re.search(r"RESOURCE_EXHAUSTED|\b429\b|free tier", reply, re.I) and len(reply) > 200:
                    return "I've hit Google's free usage limit. Give me about a minute, then ask again.", False
                if reply:
                    return reply, True
                return "Sorry, I didn't get an answer back.", False
            except Exception as e:
                log(f"ACP ask failed ({e!r}); using one-shot mode")
        reply, ok = ask_hermes(text)
        return tidy_reply(reply), ok

    def _on_permission(self, title, detail, options):
        """Hermes wants to do something that needs approval. Returns option id or None (deny)."""
        com_init()
        ids = [o["id"] for o in options]
        allow_once = next((o["id"] for o in options if o["id"] == "allow_once" or "allow_once" in o["kind"]), None)
        allow_session = next((o["id"] for o in options if o["id"] == "allow_session"), None)
        m = re.match(r"Approve edit:\s*(\S+)", title)
        if m and m.group(1).startswith(P.WORKSPACE_WSL + "/") and ".." not in m.group(1):
            self.emit("activity", {"text": "Edited " + m.group(1).split("/jarvis/", 1)[1]})
            return allow_once                    # its own folder: no need to ask
        # Everything else: ask the user (HUD buttons or voice).
        req = {"id": str(time.time()), "title": title, "detail": detail,
               "options": [o for o in options if o["id"] in ("allow_once", "allow_session", "deny")] or options}
        self._pending = req
        while not self._answers.empty():
            self._answers.get_nowait()
        self.emit("approval", req)
        log(f"Approval needed: {title} | {detail}")
        spoken = re.sub(r"[_/]", " ", title)[:120]
        prev = self.state
        if self.voice_on:
            self.say(f"I need your permission to {spoken}. Shall I go ahead?")
        self.set_state("approval", title)
        if self.mic_on:
            self._await_answer.set()
        choice = None
        try:
            ans = self._answers.get(timeout=90)
        except queue.Empty:
            ans = None
        self._await_answer.clear()
        if ans in ids:
            choice = ans
        elif isinstance(ans, str) and ans.startswith("voice:"):
            said = ans[6:]
            if YES_RE.search(said) and not NO_RE.search(said):
                choice = allow_session if (ALWAYS_RE.search(said) and allow_session) else allow_once
        if choice == "deny":
            choice = None
        self._pending = None
        self.emit("approval_done", {"id": req["id"], "allowed": bool(choice), "choice": choice})
        log(f"Approval {'granted' if choice else 'denied'}: {title}")
        self.set_state("thinking" if prev == "thinking" else prev)
        return choice

    def answer_approval(self, option_id):
        """From the HUD buttons."""
        if self._pending:
            self._answers.put(option_id)

    # ---------------------------------------------------------- listening --
    def _drain(self):
        while not self._frames.empty():
            self._frames.get_nowait()

    def _next16k(self):
        b = self._frames.get().reshape(-1, 3).mean(axis=1)   # 48k -> 16k
        lv = float(np.sqrt((b ** 2).mean()))
        self._lv_n = getattr(self, "_lv_n", 0) + 1
        if self._lv_n % 2 == 0 and self.state != "speaking":
            self.emit("level", {"v": min(1.0, lv * 12), "src": "in"})
        return b, lv

    def _record(self, wait_for_speech, initial=None):
        noise = 0.004
        got, heard, quiet, start = list(initial or []), bool(initial), 0.0, time.time()
        while True:
            if self._manual_wake.is_set() and not heard:
                self._manual_wake.clear()
            b, rms = self._next16k()
            loud = rms > max(0.012, noise * 3.0)
            if not heard:
                if not loud:
                    noise = 0.95 * noise + 0.05 * rms
                    if time.time() - start > wait_for_speech:
                        return None
                else:
                    heard = True
            if heard:
                got.append(b)
                quiet = 0.0 if loud else quiet + len(b) / RATE
                if quiet >= SILENCE_TO_STOP or time.time() - start > MAX_UTTERANCE:
                    return np.concatenate(got)

    def _barge_watch(self):
        """While Jarvis speaks: if you start talking (louder than Jarvis's own echo in the mic for
        ~0.3 s), stop speaking and listen. Returns the first frames of your speech, or None."""
        from collections import deque
        recent, hits = deque(maxlen=6), 0
        ratios, peak, peak_thr = [], 0.0, 0.0
        for _ in range(20):                         # playback may take a moment to start
            if self._speaking_audio.is_set():
                break
            time.sleep(0.05)
        while self._speaking_audio.is_set() and not self._stop_speech.is_set():
            try:
                blk = self._frames.get(timeout=0.2)
            except queue.Empty:
                continue
            b = blk.reshape(-1, 3).mean(axis=1)
            rms = float(np.sqrt((b ** 2).mean()))
            recent.append(b)
            now = time.time()
            # Jarvis's own output level over the last ~0.45 s (covers speaker->mic delay)
            out = max((v for t, v in list(self._out_hist) if now - 0.45 <= t <= now), default=0.0)
            warmup = now - getattr(self, "_speak_t0", now) < 0.6
            if out > 0.01:
                r = rms / out
                if warmup:
                    ratios.append(r)
                    if len(ratios) >= 4:
                        guess = float(np.median(ratios))
                        self._echo_ratio = guess if self._echo_ratio is None else max(self._echo_ratio * 0.5, min(self._echo_ratio * 2, guess))
                elif self._echo_ratio is not None and r < self._echo_ratio * 1.5:
                    self._echo_ratio = 0.95 * self._echo_ratio + 0.05 * r      # keep learning the echo
            if warmup or self._echo_ratio is None:
                continue
            expected = self._echo_ratio * out
            thr = max(0.014, expected * INTERRUPT_THRESHOLD + 0.006)
            if rms > peak:
                peak, peak_thr = rms, thr
            if rms > thr:
                hits += 1
            else:
                hits = max(0, hits - 1)
            if hits >= 4:
                log(f"barge-in: mic {rms:.4f} > threshold {thr:.4f} (echo ratio {self._echo_ratio:.3f})")
                return list(recent)
        log(f"barge-in check: loudest mic {peak:.4f} vs threshold {peak_thr:.4f} (no interruption)")
        return None

    def _handle_barge(self, first):
        if self._pending is not None:           # it was asking permission: just stop; the yes/no listener takes over
            self.stop_speaking()
            return
        self._barged.set()
        self.stop_speaking()
        log("Interrupted by you; listening.")
        self.emit("action", {"text": "Interrupted — listening"})
        time.sleep(0.15)
        try:
            self._listen_turn(4.0, detail="conversation", initial=first)
        finally:
            self._barged.clear()

    def _listen_turn(self, wait_for_speech, detail="", initial=None):
        """Record + transcribe + hand to brain. Returns True if a request was sent."""
        self.set_state("listening", detail)
        audio = self._record(wait_for_speech, initial=initial)
        if audio is None:
            return False
        self.set_state("transcribing")
        segs, _ = self.stt.transcribe(audio, language="en", vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segs).strip()
        self._drain()
        if len(text) < 2:
            return False
        log(f"You: {text}")
        self.emit("heard", {"text": text, "via": "voice"})
        self.chime(up=False)
        self._brain_q.put((text, True))
        return True

    def _audio_loop(self):
        if getattr(self, "stream", None) is not None:
            try:
                self.stream.close()
            except Exception:
                pass
        self.stream = sd.InputStream(device=self.in_dev, samplerate=MIC_RATE, channels=1,
                                     dtype="float32", blocksize=BLOCK,
                                     callback=lambda d, n, t, s: self._frames.put(d[:, 0].copy()))
        self.stream.start()
        # Standby: nothing is acted on unless the speech contains "Jarvis".
        # Short speech segments are checked locally (openWakeWord + a tiny Whisper name check);
        # anything without the name is discarded immediately and never logged or sent anywhere.
        preroll, seg, in_speech, quiet, noise, woke = [], [], False, 0.0, 0.004, False
        while True:
            # An approval question is waiting for a spoken yes/no.
            if self._await_answer.is_set() and self.state != "speaking":
                self._drain()
                audio = self._record(20.0)
                if audio is not None and self._await_answer.is_set():
                    segs, _ = self.stt.transcribe(audio, language="en", vad_filter=True, beam_size=1)
                    said = " ".join(s.text.strip() for s in segs).strip()
                    if said:
                        log(f"You (approval): {said}")
                        self.emit("heard", {"text": said, "via": "voice"})
                        if YES_RE.search(said) or NO_RE.search(said):
                            self._answers.put("voice:" + said)
                continue
            # Jarvis is speaking: listen for you talking over it (barge-in).
            if self._speaking_audio.is_set() and self.mic_on and INTERRUPT_THRESHOLD > 0:
                first = self._barge_watch()
                if first:
                    self._handle_barge(first)
                    self.wake.reset()
                preroll, seg, in_speech, woke = [], [], False, False
                continue
            # Jarvis is busy (thinking): ignore the mic.
            if self._busy.is_set() or not self._brain_q.empty():
                self._drain(); time.sleep(0.05)
                preroll, seg, in_speech, woke = [], [], False, False
                continue
            if self._followup.is_set():                # mid-conversation: wait for your reply
                self._followup.clear()
                self._drain()
                if not self._listen_turn(getattr(self, "_followup_secs", FOLLOW_UP_WINDOW),
                                         detail="conversation"):
                    self.set_state("standby")         # silence: conversation over, name needed again
                self.wake.reset()
                preroll, seg, in_speech, woke = [], [], False, False
                continue
            if self._manual_wake.is_set():            # button / space key
                self._manual_wake.clear()
                self._start_listening()
                preroll, seg, in_speech, woke = [], [], False, False
                continue
            b, rms = self._next16k()
            if not self.mic_on:
                preroll, seg, in_speech, woke = [], [], False, False
                continue
            if self.wake.predict((b * 32767).astype(np.int16)).get("hey_jarvis_v0.1", 0) >= WAKE_THRESHOLD:
                woke = True
            loud = rms > max(0.012, noise * 3.0)
            if not in_speech:
                if loud:
                    in_speech, seg, quiet = True, preroll + [b], 0.0
                else:
                    noise = 0.95 * noise + 0.05 * rms
                    preroll = (preroll + [b])[-4:]           # keep ~0.3 s before speech starts
                    if woke:                                  # wake word fired after speech ended
                        woke = False
                        self._start_listening()
                continue
            seg.append(b)
            quiet = 0.0 if loud else quiet + len(b) / RATE
            if quiet >= NAME_PAUSE or len(seg) * len(b) / RATE > NAME_MAX_SEGMENT:
                audio, was_woke = np.concatenate(seg), woke
                preroll, seg, in_speech, woke = [], [], False, False
                self._handle_segment(audio, was_woke)
                self.wake.reset(); self._drain()

    def _start_listening(self):
        self.chime(up=True); self._drain()
        if not self._listen_turn(5.0):
            self._drain(); self.set_state("standby")
        self.wake.reset()

    def _handle_segment(self, audio, woke):
        """A burst of speech heard in standby. Act only if it contains the name Jarvis."""
        if len(audio) < RATE * 0.3:
            return
        if not woke:
            segs, _ = self.gate.transcribe(audio, language="en", vad_filter=True, beam_size=1,
                                           condition_on_previous_text=False)
            if not NAME_RE.search(" ".join(s.text for s in segs)):
                return                              # not addressed to Jarvis: drop it
        self.set_state("transcribing")
        segs, _ = self.stt.transcribe(audio, language="en", vad_filter=True, beam_size=1)
        text = " ".join(s.text.strip() for s in segs).strip()
        m = NAME_RE.search(text)
        if not m:
            self.set_state("standby"); return       # false alarm: name not actually said
        rest = text[m.end():].strip(" ,.!?-")
        log("Name heard.")
        if len(re.sub(r"\W", "", rest)) >= 3:       # "Jarvis, what's the time?" -> ask right away
            log(f"You: {rest}")
            self.emit("heard", {"text": rest, "via": "voice"})
            self.chime(up=False)
            self._brain_q.put((rest, True))
        else:                                        # just "Jarvis" / "Hey Jarvis" -> listen
            self._start_listening()

    # ---------------------------------------------------------------- run --
    def _settings_watch(self):
        """Re-apply settings.json whenever Jarvis (or you) edits it."""
        last = None
        while True:
            try:
                m = os.path.getmtime(SETTINGS_PATH)
            except OSError:
                m = None
            if m != last:
                last = m
                applied = load_settings()
                if hasattr(self, "gain"):
                    self.gain = 10 ** (OUTPUT_GAIN_DB / 20)
                self.devices["gain_db"] = round(OUTPUT_GAIN_DB, 1)
                if applied:
                    log(f"Settings applied: {applied}")
                self.emit("settings", applied)
                self.emit("devices", self.devices)
            time.sleep(2)

    def start(self, greet=True):
        load_settings()
        try:
            import hermes_acp
            self.hermes = hermes_acp.HermesSession(
                on_permission=self._on_permission,
                on_activity=lambda t: self.emit("activity", {"text": t}), log=log)
            threading.Thread(target=self._warm, daemon=True).start()
        except Exception as e:
            log(f"ACP unavailable ({e!r}); using one-shot mode")
            self.hermes = None
        try:
            self._load()
        except Exception as e:
            log(f"Startup failed: {e}")
            self.set_state("error", str(e))
            return False
        audio_thread(self._guard, self._brain_loop, "brain")
        audio_thread(self._guard, self._audio_loop, "microphone")
        threading.Thread(target=self._settings_watch, daemon=True).start()
        self.set_state("standby")
        self.emit("toggles", {"voice": self.voice_on, "mic": self.mic_on})
        if greet:
            self._brain_q.put(("__greet__", False))
        return True

    def _warm(self):
        try:
            self.hermes.warm_up()
        except Exception as e:
            log(f"Hermes warm-up failed: {e!r}")

    def _guard(self, fn, name):
        """Keep a loop alive: on a crash, report it and restart after a pause."""
        while True:
            try:
                fn()
            except Exception as e:
                log(f"{name} loop error: {e!r}")
                self._busy.clear()
                self.set_state("error", f"{name}: {e}")
                time.sleep(3)
                self.set_state("standby")


def main():
    j = Jarvis(emit=lambda k, d: log(f"[{k}] {d}") if k in ("state", "heard") else None)
    if not j.start():
        sys.exit(1)
    log('Listening. Say "Hey Jarvis", then your request. Ctrl+C to quit.')
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("Jarvis voice stopped.")


if __name__ == "__main__":
    main()
