"""Portal internal RPC keeps owner identity while changing local transport."""
from __future__ import annotations

import socket
import socketserver
import threading

import pytest

from management_common.service_endpoint import LocalServiceSocketMixin, ServiceEndpoint, service_endpoint
from management_common.service_socket import connect_service
from management_common.service_shutdown import DrainingStreamServer
from management_common.rpc_auth import load_rpc_signing_key, rpc_signing_key_file


def test_windows_endpoints_are_loopback_and_separate_by_instance_and_owner(tmp_path):
    endpoints = [
        service_endpoint(tmp_path / instance / f"{owner}-service.sock", platform="win32")
        for instance in ("dev", "release", "custom installation/runtime/s")
        for owner in ("resource", "execution", "society")
    ]
    assert len({endpoint.logical_path for endpoint in endpoints}) == len(endpoints)
    assert all(endpoint.private_local for endpoint in endpoints)
    assert all(endpoint.family == socket.AF_INET for endpoint in endpoints)
    assert all(endpoint.address[0] == "127.0.0.1" for endpoint in endpoints)
    with pytest.raises(ValueError, match="No Windows RPC endpoint"):
        service_endpoint(tmp_path / "dev" / "unknown.sock", platform="win32")


def test_connect_service_accepts_tcp_endpoint_without_changing_logical_path(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        logical = tmp_path / "dev" / "resource-service.sock"
        endpoint = ServiceEndpoint(logical, socket.AF_INET, listener.getsockname())
        connection, chosen = connect_service(endpoint, timeout=1, restart_wait=0)
        peer, _ = listener.accept()
        try:
            assert chosen == logical
            connection.sendall(b"ok")
            assert peer.recv(2) == b"ok"
        finally:
            peer.close()
            connection.close()


def test_draining_server_binds_tcp_when_owner_selects_windows_endpoint(tmp_path):
    class TcpOwner(LocalServiceSocketMixin, DrainingStreamServer):
        def __init__(self, path, handler):
            self.endpoint = service_endpoint(path, platform='win32')
            self.address_family = self.endpoint.family
            super().__init__(self.endpoint.address, handler)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(self.rfile.readline())

    path = tmp_path / 'custom instance' / 'resource-service.sock'
    server = TcpOwner(path, Handler)
    other = TcpOwner(tmp_path / 'another' / 'resource-service.sock', Handler)
    assert server.server_address != other.server_address
    other.server_close()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client, chosen = connect_service(server.endpoint, timeout=1, restart_wait=0)
        assert chosen == path
        with client:
            client.sendall(b"hello\n")
            assert client.recv(6) == b"hello\n"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert not path.exists()


def test_windows_rpc_key_is_private_to_installation_in_local_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    first = rpc_signing_key_file(socket_path=tmp_path / "dev" / "resource-service.sock", platform="win32")
    second = rpc_signing_key_file(socket_path=tmp_path / "release" / "resource-service.sock", platform="win32")
    assert first != second
    assert first.is_relative_to(tmp_path / "local-data")
    key = load_rpc_signing_key(required=True, create=True, path=first, platform="win32")
    assert len(key) == 32
    assert load_rpc_signing_key(required=True, path=first, platform="win32") == key

