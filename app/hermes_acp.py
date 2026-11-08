"""Persistent connection to Hermes (in WSL) over the Agent Client Protocol.

Why: one long-lived Hermes process (faster replies, real conversation context), and Hermes's
"dangerous command" checks come back to us as permission requests that the user answers in
the HUD or by voice - instead of being silently refused.
"""
import asyncio
import threading
import time

import acp
from acp.schema import (AllowedOutcome, ClientCapabilities, DeniedOutcome, FileSystemCapabilities,
                        RequestPermissionResponse)

import subprocess as _sp

# Start wsl.exe hidden: the ACP library has no option for it, so add CREATE_NO_WINDOW to
# every subprocess this process starts through asyncio (Jarvis only starts wsl.exe this way).
_orig_exec = asyncio.create_subprocess_exec


async def _hidden_exec(*a, **kw):
    kw.setdefault("creationflags", getattr(_sp, "CREATE_NO_WINDOW", 0))
    return await _orig_exec(*a, **kw)


asyncio.create_subprocess_exec = _hidden_exec

import jarvis_paths as P

WSL = P.wsl(P.WSL_BIN + "/hermes", "acp", cwd=P.WORKSPACE_WSL)
CWD = P.WORKSPACE_WSL
NEW_SESSION_AFTER_IDLE = 15 * 60      # a fresh conversation after 15 quiet minutes (saves quota)


def _text_of(content):
    if content is None:
        return ""
    t = getattr(content, "text", None)
    if t is not None:
        return t
    if isinstance(content, list):
        return "".join(_text_of(c) for c in content)
    inner = getattr(content, "content", None)
    return _text_of(inner) if inner is not None else ""


class _Client:
    def __init__(self, owner):
        self.owner = owner

    async def session_update(self, session_id, update, **kw):
        kind = getattr(update, "session_update", None) or type(update).__name__
        if kind in ("agent_message_chunk", "AgentMessageChunk"):
            self.owner._chunks.append(_text_of(update.content))
        elif kind in ("tool_call", "ToolCallStart"):
            title = getattr(update, "title", "") or ""
            if title:
                self.owner.on_activity(title)

    async def request_permission(self, options, session_id, tool_call, **kw):
        title = getattr(tool_call, "title", None) or "run an action"
        raw = getattr(tool_call, "raw_input", None)
        detail = ""
        if isinstance(raw, dict):
            detail = str(raw.get("command") or raw.get("code") or raw.get("path") or "")[:400]
        opts = [{"id": o.option_id, "name": o.name, "kind": str(getattr(o.kind, "value", o.kind))}
                for o in options]
        loop = asyncio.get_running_loop()
        choice = await loop.run_in_executor(None, self.owner.on_permission, title, detail, opts)
        if choice:
            return RequestPermissionResponse(outcome=AllowedOutcome(option_id=choice, outcome="selected"))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # Hermes runs its own tools; we don't offer file/terminal access through the client.
    async def read_text_file(self, *a, **k):
        raise RuntimeError("not supported")

    async def write_text_file(self, *a, **k):
        raise RuntimeError("not supported")

    async def create_terminal(self, *a, **k):
        raise RuntimeError("not supported")

    async def terminal_output(self, *a, **k):
        raise RuntimeError("not supported")

    async def release_terminal(self, *a, **k):
        raise RuntimeError("not supported")

    async def wait_for_terminal_exit(self, *a, **k):
        raise RuntimeError("not supported")

    async def kill_terminal(self, *a, **k):
        raise RuntimeError("not supported")

    async def ext_method(self, method, params):
        return {}

    async def ext_notification(self, method, params):
        return None

    def on_connect(self, conn):
        pass


class HermesSession:
    """Thread-safe facade: ask(text) -> reply text. Callbacks:
       on_permission(title, detail, options) -> option_id | None   (blocking, runs in a worker thread)
       on_activity(title)                                           (tool started)"""

    def __init__(self, on_permission, on_activity=lambda t: None, log=print):
        self.on_permission = on_permission
        self.on_activity = on_activity
        self.log = log
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._conn = None
        self._proc = None
        self._session = None
        self._last = 0.0
        self._chunks = []
        self._lock = threading.Lock()
        threading.Thread(target=self._run_loop, daemon=True).start()

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _connect(self):
        if self._conn is not None and self._proc is not None and self._proc.returncode is None:
            return
        # stderr -> DEVNULL: Hermes logs there; an unread pipe would eventually fill and stall it.
        self._cm = acp.spawn_agent_process(lambda agent: _Client(self), *WSL,
                                           transport_kwargs={"stderr": _sp.DEVNULL})
        self._conn, self._proc = await self._cm.__aenter__()
        await self._conn.initialize(
            protocol_version=acp.PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(
                fs=FileSystemCapabilities(read_text_file=False, write_text_file=False), terminal=False))
        self._session = None
        self.log("Hermes ACP connected")

    async def _ask(self, text):
        await self._connect()
        if self._session is None or time.time() - self._last > NEW_SESSION_AFTER_IDLE:
            res = await self._conn.new_session(cwd=CWD, mcp_servers=[])
            self._session = res.session_id
        self._chunks = []
        await self._conn.prompt(session_id=self._session, prompt=[acp.text_block(text)])
        self._last = time.time()
        return "".join(self._chunks).strip()

    def ask(self, text, timeout=300):
        with self._lock:
            fut = asyncio.run_coroutine_threadsafe(self._ask(text), self._loop)
            try:
                return fut.result(timeout=timeout)
            except Exception:
                fut.cancel()
                self._reset()
                raise

    def new_conversation(self):
        self._session = None

    def _reset(self):
        async def kill():
            try:
                if self._proc and self._proc.returncode is None:
                    self._proc.kill()
            except Exception:
                pass
            self._conn = self._proc = self._session = None
        asyncio.run_coroutine_threadsafe(kill(), self._loop).result(timeout=10)

    def warm_up(self):
        asyncio.run_coroutine_threadsafe(self._connect(), self._loop).result(timeout=120)
