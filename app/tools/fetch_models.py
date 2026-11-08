"""Download Jarvis's local voice models (wake word, Piper voice, Whisper)."""
import os, sys, urllib.request
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import jarvis_voice as jv

os.makedirs(jv.OWW_DIR, exist_ok=True)
if not os.path.exists(os.path.join(jv.OWW_DIR, "hey_jarvis_v0.1.onnx")):
    import openwakeword.utils as u
    u.download_models(model_names=["hey_jarvis"], target_directory=jv.OWW_DIR)
print("wake word ok")

vd = os.path.dirname(jv.VOICE_PATH); os.makedirs(vd, exist_ok=True)
base = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx"
for ext in ("", ".json"):
    p = jv.VOICE_PATH + ext
    if not os.path.exists(p):
        urllib.request.urlretrieve(base + ext, p)
print("voice ok")

from faster_whisper import WhisperModel
for m in (jv.WHISPER_MODEL, jv.GATE_MODEL):
    WhisperModel(m, device="cpu", compute_type="int8")
print("speech recognition ok")
