"""Jarvis audio player: a separate process that only plays sound, so nothing else Jarvis is doing
(speech recognition, the HUD, Hermes) can ever make the voice stutter.

Protocol on stdin: one JSON header line, then (for "play") exactly n bytes of float32 mono samples.
  {"cmd": "play", "id": 7, "n": 384000}
  {"cmd": "stop"}
Reports on stdout (one JSON per line): {"id": 7, "pos": <frames played>} ... {"id": 7, "done": true}
usage: jarvis_player.py <device_index> <samplerate>
"""
import json
import queue
import sys
import threading

import numpy as np
import sounddevice as sd

try:
    import ctypes
    ctypes.windll.ole32.CoInitializeEx(None, 0)
except Exception:
    pass

DEV, RATE = int(sys.argv[1]), int(sys.argv[2])
BLOCK = 1024
jobs = queue.Queue()
stop = threading.Event()
out = sys.stdout


def say(obj):
    out.write(json.dumps(obj) + "\n"); out.flush()


# Progress is reported by a side thread (~10x/s, latest value only). If Jarvis is busy and not
# reading, only this thread waits - the audio loop never blocks on the pipe.
progress = {"id": None, "pos": 0, "sent": -1}
events = queue.Queue()


def reporter():
    import time
    while True:
        try:
            ev = events.get(timeout=0.1)
            say(ev)
            continue
        except queue.Empty:
            pass
        jid, pos = progress["id"], progress["pos"]
        if jid is not None and pos != progress["sent"]:
            progress["sent"] = pos
            say({"id": jid, "pos": pos})


threading.Thread(target=reporter, daemon=True).start()


def reader():
    inp = sys.stdin.buffer
    while True:
        line = inp.readline()
        if not line:
            stop.set(); jobs.put(None); return
        try:
            cmd = json.loads(line)
        except ValueError:
            continue
        if cmd.get("cmd") == "stop":
            stop.set()
        elif cmd.get("cmd") == "play":
            data = inp.read(int(cmd["n"]))
            jobs.put((cmd.get("id"), np.frombuffer(data, dtype=np.float32)))


def open_stream():
    kw = dict(device=DEV, samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK, latency="high")
    try:
        s = sd.OutputStream(extra_settings=sd.WasapiSettings(auto_convert=True), **kw)
    except Exception:
        s = sd.OutputStream(**kw)
    s.start()
    return s


threading.Thread(target=reader, daemon=True).start()
say({"ready": True})
stream = None
while True:
    job = jobs.get()
    if job is None:
        break
    jid, audio = job
    stop.clear()
    if stream is None:
        stream = open_stream()
    pos, under = 0, 0
    progress["id"], progress["pos"], progress["sent"] = jid, 0, -1
    while pos < len(audio) and not stop.is_set():
        chunk = audio[pos:pos + BLOCK]
        if len(chunk) < BLOCK:
            chunk = np.concatenate([chunk, np.zeros(BLOCK - len(chunk), np.float32)])
        if stream.write(chunk.reshape(-1, 1)):
            under += 1
        pos += BLOCK
        progress["pos"] = min(pos, len(audio))
    if stop.is_set():
        stream.abort(); stream.close(); stream = None     # cut off immediately
    progress["id"] = None
    events.put({"id": jid, "done": True, "underflows": under})
