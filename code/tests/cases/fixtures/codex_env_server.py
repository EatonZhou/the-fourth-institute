"""Offline Codex stand-in: a Unix WebSocket listener, no auth or model calls."""
import base64
import hashlib
import socket
import sys
from pathlib import Path

endpoint = sys.argv[sys.argv.index("--listen") + 1].removeprefix("unix://")
with socket.socket(socket.AF_UNIX) as listener:
    listener.bind(endpoint)
    listener.listen()
    accepted = 0
    while True:
        connection, _ = listener.accept()
        with connection:
            raw = b""
            while b"\r\n\r\n" not in raw:
                chunk = connection.recv(4096)
                if not chunk:
                    break
                raw += chunk
            fields = dict(line.split(": ", 1) for line in raw.decode().split("\r\n") if ": " in line)
            key = fields["Sec-WebSocket-Key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(hashlib.sha1(key.encode()).digest())
            connection.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                               b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            connection.recv(4096)
        accepted += 1
        if accepted >= 2:
            Path(endpoint).with_suffix(".ready").touch()
