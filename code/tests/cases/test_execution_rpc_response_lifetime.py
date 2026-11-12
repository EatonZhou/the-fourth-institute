"""Execution RPC replies do not leak their read handle or lose unknown outcomes."""
import json
import socket

import pytest

from management_portal.execution import transport


@pytest.mark.parametrize("mode", ["success", "json", "utf8", "timeout"])
def test_response_stream_is_closed_even_when_exception_is_retained(monkeypatch, tmp_path, mode):
    """成功及坏响应／超时都显式关闭流；保存异常不保留真实 socket FD。"""
    client_socket, peer = socket.socketpair()
    streams, errors = [], []
    makefile = socket.socket.makefile

    def observe_stream(connection, *args, **kwargs):
        result = makefile(connection, *args, **kwargs)
        streams.append(result)
        return result

    monkeypatch.setattr(socket.socket, "makefile", observe_stream)
    monkeypatch.setattr(transport, "connect_service", lambda *a, **k: (client_socket, tmp_path / "fixture.sock"))
    monkeypatch.setattr(transport.secrets, "token_hex", lambda n: "fixture")
    from management_common import rpc_auth
    monkeypatch.setattr(rpc_auth, "request_auth", lambda *a, **k: {})
    if mode == "success":
        peer.sendall(json.dumps({"id": "execution-service-fixture", "ok": True, "result": {"fixture": 1}}).encode() + b'\n')
    elif mode == "json":
        peer.sendall(b'broken json\n')
    elif mode == "utf8":
        peer.sendall(b'{"result":"\xe4\xb8\n')
    client = transport.ExecutionServiceClient(tmp_path / "fixture.sock", timeout=0.1, restart_wait=0)
    try:
        if mode == "success":
            assert client.request("fixture", {}) == {"fixture": 1}
        else:
            try:
                client.request("fixture", {})
            except Exception as exc:
                errors.append(exc)  # Retain the traceback just as a caller recording a failure might.
            assert len(errors) == 1 and isinstance(errors[0], transport.ExecutionResponseUnknown)
        assert len(streams) == 1 and streams[0].closed
        # The sent request is one packet, then explicit close must give EOF,
        # including when a traceback still refers to _request_at's locals.
        peer.settimeout(0.1)
        assert peer.recv(65536).endswith(b'\n')
        assert peer.recv(1) == b''
    finally:
        for stream in streams:
            stream.close()
        client_socket.close()
        peer.close()
