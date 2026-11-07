"""Enable and verify kiosk screen sharing on an idle WB instrument."""
import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

CDP = "http://127.0.0.1:9222/json/list"
def kiosk_target():
    with urllib.request.urlopen(CDP, timeout=3) as response:
        targets = json.load(response)
    for target in targets:
        if target.get("type") == "page" and urlsplit(target.get("url", "")).scheme == "http" and
                urlsplit(target.get("url", "")).hostname in ("127.0.0.1", "localhost") and
                urlsplit(target.get("url", "")).port == 7777 and
                urlsplit(target.get("url", "")).path in ("", "/"):
            if target.get("webSocketDebuggerUrl"):
                return target
    raise RuntimeError("CDP reachable but kiosk page http://127.0.0.1:7777/ missing")

def check_frame(timeout=12):
    """Verify the server WebSocket emits an actual JPEG frame, not just HTTP 101."""
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(("127.0.0.1", 7777), timeout=3) as s:
        s.settimeout(timeout)
        s.sendall(("GET /screen/socket HTTP/1.1\r\nHost: 127.0.0.1:7777\r\n"
                   "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Origin: http://127.0.0.1:7777\r\n"
                   "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: " + key + "\r\n\r\n").encode())
        response = b""
        while b"\r\n\r\n" not in response:
            response += s.recv(4096)
            if len(response) > 16384:
                raise RuntimeError("WebSocket handshake too large")
        header, pending = response.split(b"\r\n\r\n", 1)
        if b" 101 " not in header.split(b"\r\n", 1)[0]:
            raise RuntimeError("Screen WebSocket refused: " + header.split(b"\r\n", 1)[0].decode(errors="replace"))
        deadline = time.monotonic() + timeout
        def read_exact(n):
            nonlocal pending
            while len(pending) < n:
                pending += s.recv(65536)
            out, pending = pending[:n], pending[n:]
            return out
        while time.monotonic() < deadline:
            first, second = read_exact(2)
            length = second & 127
            if length == 126:
                length = int.from_bytes(read_exact(2), "big")
            elif length == 127:
                length = int.from_bytes(read_exact(8), "big")
            if length > 8_000_000:
                raise RuntimeError("Oversized WebSocket message")
            mask = read_exact(4) if second & 128 else None
            payload = read_exact(length)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i,b in enumerate(payload))
            if first & 15 == 8:
                raise RuntimeError("Screen WebSocket closed before first frame")
            if first & 15 != 1:
                continue
            try:
                msg = json.loads(payload)
            except ValueError:
                continue
            if msg.get("type") == "error":
                raise RuntimeError("Screen bridge: " + str(msg.get("message")))
            if msg.get("type") == "frame" and msg.get("data"):
                raw = base64.b64decode(msg["data"], validate=True)
                if raw.startswith(b"\xff\xd8"):
                    return
        raise RuntimeError("No JPEG frame from /screen/socket within timeout")

def enable_screen(script, idle_confirmed=False):
    try:
        kiosk_target()
        print("WB screen: kiosk CDP already active; setup skipped")
    except Exception as exc:
        print("WB screen: CDP not ready: " + str(exc))
        if not idle_confirmed:
            print("WB screen: kiosk restart deferred; confirm instrument is idle and rerun with --screen-idle-confirmed")
            return False
        if not Path(script).is_file():
            raise FileNotFoundError("Missing bundled screen setup script: " + str(script))
        subprocess.run(["bash", str(script)], check=True)
    errors = []
    for attempt in range(20):
        try:
            kiosk_target()
            check_frame()
            print("WB screen: CDP kiosk and /screen/socket JPEG frame verified")
            return True
        except Exception as exc:
            errors.append(str(exc))
            time.sleep(1)
    raise RuntimeError("WB screen verification failed: " + errors[-1])
