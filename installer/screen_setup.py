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
        if target.get("type") != "page":
            continue
        url = urlsplit(target.get("url", ""))
        if (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost")
                and url.port == 7777 and url.path in ("", "/")
                and target.get("webSocketDebuggerUrl")):
            return target
    raise RuntimeError("CDP reachable but kiosk page http://127.0.0.1:7777/ missing")

def _read_exact(sock, pending, size):
    while len(pending) < size:
        chunk = sock.recv(65536)
        if not chunk:
            raise RuntimeError("Screen WebSocket closed while reading a frame")
        pending += chunk
    return pending[:size], pending[size:]

def check_frame(timeout=5):
    """Complete a WebSocket handshake and require one actual JPEG screencast frame."""
    key = base64.b64encode(os.urandom(16)).decode()
    with socket.create_connection(("127.0.0.1", 7777), timeout=3) as sock:
        sock.settimeout(timeout)
        sock.sendall((
            "GET /screen/socket HTTP/1.1\r\n"
            "Host: 127.0.0.1:7777\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Origin: http://127.0.0.1:7777\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Sec-WebSocket-Key: " + key + "\r\n\r\n"
        ).encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                raise RuntimeError("Screen WebSocket closed during handshake")
            response += chunk
            if len(response) > 16384:
                raise RuntimeError("WebSocket handshake too large")
        header, pending = response.split(b"\r\n\r\n", 1)
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status:
            raise RuntimeError("Screen WebSocket refused: " + status.decode(errors="replace"))

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            first2, pending = _read_exact(sock, pending, 2)
            first, second = first2
            length = second & 127
            if length == 126:
                raw, pending = _read_exact(sock, pending, 2)
                length = int.from_bytes(raw, "big")
            elif length == 127:
                raw, pending = _read_exact(sock, pending, 8)
                length = int.from_bytes(raw, "big")
            if length > 8_000_000:
                raise RuntimeError("Oversized WebSocket message")
            if second & 128:
                mask, pending = _read_exact(sock, pending, 4)
            else:
                mask = None
            payload, pending = _read_exact(sock, pending, length)
            if mask:
                payload = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
            opcode = first & 15
            if opcode == 8:
                raise RuntimeError("Screen WebSocket closed before first frame")
            if opcode != 1:
                continue
            try:
                message = json.loads(payload)
            except (ValueError, UnicodeDecodeError):
                continue
            if message.get("type") == "error":
                raise RuntimeError("Screen bridge: " + str(message.get("message")))
            if message.get("type") == "frame" and message.get("data"):
                image = base64.b64decode(message["data"], validate=True)
                if not image.startswith(b"\xff\xd8"):
                    raise RuntimeError("Screen bridge returned data that is not JPEG")
                # Send the frame ACK as a correctly masked WebSocket client frame.
                ack = json.dumps({"type": "ack", "id": message["id"]}).encode()
                mask = os.urandom(4)
                masked = bytes(value ^ mask[i % 4] for i, value in enumerate(ack))
                size = len(ack)
                frame_header = bytes((0x81, 0x80 | size)) if size < 126 else bytes((0x81, 0xFE)) + size.to_bytes(2, "big")
                sock.sendall(frame_header + mask + masked)
                return
        raise RuntimeError("WebSocket connected and sent ready, but no JPEG frame arrived within %ss" % timeout)

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
        subprocess.run(["bash", str(script)], check=True, timeout=60)

    errors = []
    for attempt in range(3):
        try:
            kiosk_target()
            check_frame(timeout=5)
            print("WB screen: CDP kiosk and /screen/socket JPEG frame verified")
            return True
        except Exception as exc:
            errors.append(str(exc))
            if attempt < 2:
                time.sleep(1)
    raise RuntimeError("WB screen verification failed after 3 bounded attempts: " + errors[-1])
