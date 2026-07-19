"""Automated Hermes repair-loop harness: real turn + window capture, zero focus theft.

Added 2026-07-19 (CRITICAL #25/#26 aftermath). The user asked for a fully
automated verify loop: drive a REAL prompt through the desktop backend's own
WS RPC (the exact path the desktop app uses), read the streamed reply, and
SEE the actual UI — all without interrupting whatever is fullscreen (the
capture uses Win32 ``PrintWindow``, which renders a window's own surface even
when it is covered by a game; no focus change, no clicks).

Wire protocol (learned live 2026-07-19, jsonrpc 2.0 over WS):
  - endpoint: ``ws://127.0.0.1:<port>/api/ws?token=<HERMES_DASHBOARD_SESSION_TOKEN>``
    * port: last ``HERMES_BACKEND_READY port=N`` line in logs/desktop.log
    * token: env ``HERMES_DASHBOARD_SESSION_TOKEN`` (set in ~/.hermes/.env;
      canonical copy in D:/secrets/hermes-serve-token.txt) — web_server.py
      line ~280 uses it verbatim instead of minting a per-boot random token.
  - requests:  {"id": N, "method": "...", "params": {...}}
  - replies:   {"jsonrpc":"2.0","id":N,"result":{...}} / {"error":{...}}
  - events:    {"jsonrpc":"2.0","method":"event","params":{"type": T,
                "session_id": S, "payload": {...}}}
    types seen: gateway.ready, session.info, message.start, thinking.delta,
    message.delta, status.update (payload.kind=lifecycle carries error text),
    message.complete (payload.text = final assistant text).

Usage (run with the hermes venv python; PYTHONIOENCODING=utf-8):
  python scripts/e2e_smoke.py --model claude-sonnet-4-6 --provider anthropic \
      --prompt "Reply with exactly: PLANLANE-OK" --expect PLANLANE-OK \
      --screenshot C:/path/hermes-window.png

Exit codes: 0 = turn PASS (+capture ok if requested); 4 = provider error
surfaced in-turn (infrastructure fine, provider/quota failed — the error
text is printed); other non-zero = harness/infrastructure failure.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import aiohttp

HERMES_HOME = Path(os.environ.get("HERMES_HOME", r"C:\Users\Burgboy\AppData\Local\hermes"))
DESKTOP_LOG = HERMES_HOME / "logs" / "desktop.log"
TOKEN_FILE = Path(r"D:\secrets\hermes-serve-token.txt")


def discover_port() -> int:
    """Last HERMES_BACKEND_READY port in desktop.log = the live serve port."""
    text = DESKTOP_LOG.read_text(encoding="utf-8", errors="replace")
    ports = re.findall(r"HERMES_BACKEND_READY port=(\d+)", text)
    if not ports:
        raise SystemExit("no HERMES_BACKEND_READY line in desktop.log — is the app running?")
    return int(ports[-1])


def _read_process_env_token(pid: int) -> str:
    """Read HERMES_DASHBOARD_SESSION_TOKEN from a live process's environment.

    WHY (2026-07-19 incident): a static token in ~/.hermes/.env OVERRIDES the
    per-boot token Electron generates and hands its renderer (the backend's
    .env loader wins), which breaks the desktop's own WS auth — the renderer
    gets stuck on "Hermes couldn't start / Could not connect to Hermes
    gateway". So the .env token approach is FORBIDDEN. Instead we read the
    token the backend ACTUALLY holds: Electron passes it via the spawn env,
    and a same-user process may read another's environment block
    (NtQueryInformationProcess -> PEB -> ProcessParameters -> Environment).
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.windll.kernel32
    ntdll = ctypes.windll.ntdll
    PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
    h = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        return ""
    try:
        class PBI(ctypes.Structure):
            _fields_ = [("ExitStatus", ctypes.c_void_p), ("PebBaseAddress", ctypes.c_void_p),
                        ("AffinityMask", ctypes.c_void_p), ("BasePriority", ctypes.c_void_p),
                        ("UniqueProcessId", ctypes.c_void_p), ("InheritedFromUniqueProcessId", ctypes.c_void_p)]

        pbi = PBI()
        if ntdll.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return ""

        def read_ptr(addr):
            buf = ctypes.c_void_p()
            if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), ctypes.byref(buf), 8, None):
                return None
            return buf.value or 0

        # x64 offsets: PEB+0x20 = ProcessParameters; params+0x80 = Environment
        # pointer; params+0x3F0 = EnvironmentSize (Vista+).
        params = read_ptr(int(pbi.PebBaseAddress) + 0x20)
        if not params:
            return ""
        env_ptr = read_ptr(params + 0x80)
        env_size_raw = read_ptr(params + 0x3F0) or 0x8000
        env_size = min(int(env_size_raw) or 0x8000, 0x40000)
        if not env_ptr:
            return ""
        raw = ctypes.create_string_buffer(env_size)
        if not k32.ReadProcessMemory(h, ctypes.c_void_p(env_ptr), raw, env_size, None):
            return ""
        block = raw.raw.decode("utf-16-le", errors="ignore")
        for entry in block.split("\x00"):
            if entry.startswith("HERMES_DASHBOARD_SESSION_TOKEN="):
                return entry.split("=", 1)[1].strip()
    finally:
        k32.CloseHandle(h)
    return ""


def _find_backend_pid() -> int:
    """Newest `hermes_cli.main serve` python process (the desktop backend)."""
    import subprocess

    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and "
         "$_.CommandLine -match 'hermes_cli.main serve' } | Sort-Object CreationDate | "
         "Select-Object -Last 1 -ExpandProperty ProcessId"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    return int(out) if out.isdigit() else 0


def discover_token() -> str:
    """Token priority: live backend's process env (always correct) ->
    explicit env var -> D:/secrets file (only valid for self-spawned serves)."""
    pid = _find_backend_pid()
    if pid:
        tok = _read_process_env_token(pid)
        if tok:
            print(f"[e2e] token read from live backend pid={pid}")
            return tok
    tok = os.environ.get("HERMES_DASHBOARD_SESSION_TOKEN", "").strip()
    if not tok and TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not tok:
        raise SystemExit("no token: no live backend to read from, no env, no secrets file")
    return tok


def capture_hermes_window(out_path: str) -> str:
    """Capture the Hermes window bitmap via PrintWindow — works while COVERED.

    PW_RENDERFULLCONTENT (=2) asks the window to render its full DWM surface,
    which captures hardware-accelerated (Electron) windows correctly and does
    not require the window to be visible/frontmost. It does NOT work for a
    minimized window — we restore-detect only (no activation) and report that
    case instead of stealing focus.
    """
    import ctypes
    from ctypes import wintypes

    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32
    hwnd_found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _l):
        if u32.IsWindowVisible(hwnd):
            n = u32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u32.GetWindowTextW(hwnd, buf, n + 1)
                if buf.value == "Hermes":
                    hwnd_found.append(hwnd)
        return True

    u32.EnumWindows(_enum, 0)
    if not hwnd_found:
        return "NO-WINDOW: no visible top-level window titled 'Hermes'"
    hwnd = hwnd_found[0]
    if u32.IsIconic(hwnd):
        return "MINIMIZED: PrintWindow cannot render a minimized window (not restoring it — that would steal focus)"

    r = wintypes.RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc_win = u32.GetWindowDC(hwnd)
    hdc_mem = g32.CreateCompatibleDC(hdc_win)
    hbmp = g32.CreateCompatibleBitmap(hdc_win, w, h)
    g32.SelectObject(hdc_mem, hbmp)
    ok = u32.PrintWindow(hwnd, hdc_mem, 2)  # 2 = PW_RENDERFULLCONTENT

    # Pull the bits (top-down 32bpp BGRA) and write a PNG (Pillow) or BMP.
    class BMIH(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                    ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPPM", ctypes.c_long),
                    ("biYPPM", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                    ("biClrImp", wintypes.DWORD)]

    bmi = BMIH(ctypes.sizeof(BMIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    buf = (ctypes.c_char * (w * h * 4))()
    g32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    g32.DeleteObject(hbmp); g32.DeleteDC(hdc_mem); u32.ReleaseDC(hwnd, hdc_win)

    try:
        from PIL import Image

        img = Image.frombuffer("RGBA", (w, h), bytes(buf), "raw", "BGRA", 0, 1)
        img.convert("RGB").save(out_path)
    except ImportError:
        out_path = os.path.splitext(out_path)[0] + ".raw"
        Path(out_path).write_bytes(bytes(buf))
        return f"CAPTURED-RAW (no Pillow): {out_path} {w}x{h} BGRA"
    return f"CAPTURED: {out_path} ({w}x{h}, PrintWindow ok={bool(ok)})"


async def run_turn(port: int, token: str, model: str, provider: str,
                   prompt: str, timeout_s: float) -> tuple[int, str]:
    url = f"ws://127.0.0.1:{port}/api/ws?token={token}"
    rid = 0
    final_text: list[str] = []
    error_text: list[str] = []
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(url, max_msg_size=64 * 1024 * 1024) as ws:
            async def call(method: str, params: dict) -> int:
                nonlocal rid
                rid += 1
                await ws.send_str(json.dumps({"id": rid, "method": method, "params": params}))
                return rid

            create_id = await call("session.create", {
                "model": model, "provider": provider,
                "title": f"e2e-smoke-{int(time.time())}", "source": "desktop", "cwd": "D:/",
            })
            sid = None
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=max(0.1, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                o = json.loads(msg.data)
                if o.get("id") == create_id and "result" in o:
                    sid = o["result"]["session_id"]
                    await call("prompt.submit", {"session_id": sid, "text": prompt})
                    continue
                if o.get("id") == create_id and "error" in o:
                    return 2, f"session.create error: {json.dumps(o['error'])[:300]}"
                if o.get("method") != "event":
                    continue
                p = o.get("params") or {}
                if p.get("session_id") not in (None, sid):
                    continue
                etype, payload = p.get("type"), p.get("payload") or {}
                if etype == "status.update" and payload.get("kind") == "lifecycle":
                    txt = str(payload.get("text") or "")
                    if "error" in txt.lower() or "HTTP 4" in txt or "HTTP 5" in txt:
                        error_text.append(txt)
                if etype == "message.complete":
                    final_text.append(str(payload.get("text") or ""))
                    break
    text = "".join(final_text)
    # message.complete may itself carry the provider error text (observed:
    # "HTTP 400: You're out of extra usage...") — classify either signal as
    # an in-turn provider error, distinct from harness failure.
    if text.startswith("HTTP 4") or text.startswith("HTTP 5") or error_text:
        return 4, f"provider error surfaced in-turn: {text or '; '.join(error_text)}"
    if not text:
        return 3, "no message.complete received (timeout?)"
    return 0, text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--provider", default="anthropic")
    ap.add_argument("--prompt", default="Reply with exactly: PLANLANE-OK")
    ap.add_argument("--expect", default=None, help="substring the reply must contain for PASS")
    ap.add_argument("--screenshot", default=None, help="capture the Hermes window to this PNG path")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--no-turn", action="store_true", help="capture only, skip the LLM turn")
    a = ap.parse_args()

    port, token = discover_port(), discover_token()
    print(f"[e2e] backend port={port}")

    rc, text = 0, ""
    if not a.no_turn:
        rc, text = asyncio.run(run_turn(port, token, a.model, a.provider, a.prompt, a.timeout))
        tail = text[-400:].replace("\n", " ")
        print(f"[e2e] turn rc={rc} model={a.model}/{a.provider}")
        print(f"[e2e] reply tail: {tail}")
        if rc == 0 and a.expect and a.expect not in text:
            rc = 5
            print(f"[e2e] FAIL: expected substring {a.expect!r} not in reply")
        elif rc == 0:
            print("[e2e] turn PASS")

    if a.screenshot:
        print("[e2e] " + capture_hermes_window(a.screenshot))
    return rc


if __name__ == "__main__":
    sys.exit(main())
