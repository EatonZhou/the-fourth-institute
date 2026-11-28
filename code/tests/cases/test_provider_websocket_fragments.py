"""Validate the client wire format, including UTF-8 split between frames."""
import pytest

from management_portal import provider_websocket as ws


@pytest.mark.parametrize(("text", "headers"), [
    ("", [0x81]), ("abcd", [0x81]), ("ab你cde", [0x01, 0x80]),
    ("abcdefghi", [0x01, 0x00, 0x80]),
])
def test_text_fragmentation(monkeypatch, text, headers):
    monkeypatch.setattr(ws, "_TEXT_FRAGMENT_BYTES", 4)
    monkeypatch.setattr(ws.WebSocketTransport, "_handshake", lambda *_: None)
    frames = []

    class Socket:
        sendall = staticmethod(frames.append)

    ws.WebSocketTransport(Socket()).send_text(text)
    assert [frame[0] for frame in frames] == headers
    payload = bytearray()
    for frame in frames:
        assert frame[1] == 0x80 | (len(frame) - 6)
        assert len(frame) - 6 <= 4
        payload.extend(byte ^ frame[2 + i % 4] for i, byte in enumerate(frame[6:]))
    assert payload.decode("utf-8") == text
