import json, os, re, time, urllib.request
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
url = open(os.path.join(HERE, "hud_url.txt")).read().strip(); base, tok = url.split("/?t=")
LOG = os.path.join(HERE, "jarvis.log")
def post(b): urllib.request.urlopen(urllib.request.Request(f"{base}/cmd?t={tok}", data=json.dumps(b).encode(), method="POST"), timeout=30)
def wait_reply(start, timeout=150):
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(1)
        s = open(LOG, encoding="utf-8", errors="replace").read()[start:]
        m = re.search(r"Jarvis \(([\d.]+)s\): (.*)", s)
        if m: return s
    return open(LOG, encoding="utf-8", errors="replace").read()[start:]
start = os.path.getsize(LOG)
post({"type": "text", "text": "Jarvis, access code ultron"}); time.sleep(12)
start2 = os.path.getsize(LOG)
post({"type": "text", "text": "What is the name of this computer and how much free space is on drive C? Short answer."})
s = wait_reply(start2)
print("\n".join(l for l in s.splitlines() if re.search(r"Jarvis \(|win_exec|Auto-approved", l)))
time.sleep(8); post({"type": "lock"})
