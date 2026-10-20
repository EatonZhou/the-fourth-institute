"""WebSocket framing shared by native Provider event transports."""
from __future__ import annotations

import base64
import hashlib
import os
import socket
import struct

_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

class WebSocketUnavailable(OSError):
    pass


class WebSocketTransport:
    def __init__(self, sock: socket.socket, *, host: str = "localhost", path: str = "/",
                 bearer_token: str | None = None) -> None:
        self.sock = sock
        self.host = host
        self.path = path
        self._buffer = bytearray()
        self._handshake(bearer_token)

    def _handshake(self, bearer_token: str | None) -> None:
        if bearer_token is not None and (not bearer_token or not bearer_token.isascii()
                                         or any(c.isspace() for c in bearer_token)):
            raise ValueError("Invalid WebSocket capability token")
        authorization = f"Authorization: Bearer {bearer_token}\r\n" if bearer_token else ""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"{authorization}\r\n"
        ).encode("ascii")
        self.sock.sendall(request)
        response = self._read_until(b"\r\n\r\n", limit=64 * 1024)
        header_text = response.decode("iso-8859-1", errors="replace")
        lines = header_text.split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            raise WebSocketUnavailable(f"WebSocket handshake rejected: {lines[0] if lines else ''}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1((key + _WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketUnavailable("WebSocket handshake returned an invalid accept key")

    def _read_until(self, marker: bytes, *, limit: int) -> bytes:
        while marker not in self._buffer:
            chunk = self.sock.recv(8192)
            if not chunk:
                raise WebSocketUnavailable("WebSocket connection closed during handshake")
            self._buffer.extend(chunk)
            if len(self._buffer) > limit:
                raise WebSocketUnavailable("WebSocket handshake response is too large")
        end = self._buffer.index(marker) + len(marker)
        value = bytes(self._buffer[:end])
        del self._buffer[:end]
        return value

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self.sock.recv(max(8192, size - len(self._buffer)))
            if not chunk:
                raise EOFError("WebSocket connection closed")
            self._buffer.extend(chunk)
        value = bytes(self._buffer[:size])
        del self._buffer[:size]
        return value

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        length = len(payload)
        if length < 126:
            header = bytes((0x80 | opcode, 0x80 | length))
        elif length <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", length)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", length)
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv_text(self) -> str:
        fragments = bytearray()
        text_started = False
        while True:
            first, second = self._read_exact(2)
            final = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length)
            if masked:
                payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
            if opcode == 0x8:
                raise EOFError("WebSocket peer closed the connection")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode == 0x1:
                text_started = True
                fragments.extend(payload)
            elif opcode == 0x0 and text_started:
                fragments.extend(payload)
            else:
                continue
            if final:
                return fragments.decode("utf-8")

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()

