"""Exercise Windows' authenticated TCP transport, including on POSIX CI.

These are real local connections, not native Windows process/ACL validation.
No Provider requests or live service restarts are involved.

:design: decision_96a887d83e8a
"""
import json
import socket
import threading
import time

import pytest

from management_common import service_socket
from management_common.local_ipc import LocalListener, rendezvous_path
from management_common.service_endpoint import service_endpoint
from management_portal.execution.transport import (
    ExecutionResponseUnknown, ExecutionServiceClient, ExecutionServiceUnavailable,
)
from tests.cases.rpc_test_support import socket_rpc_authenticator  # noqa: F401

pytestmark = pytest.mark.service_restart_wait


@pytest.fixture
def windows_transport(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-data"))
    monkeypatch.setattr(service_socket, "service_endpoint",
                        lambda path: service_endpoint(path, platform="win32"))


@pytest.mark.parametrize("stale", [False, True], ids=["missing-descriptor", "stale-port"])
def test_windows_execution_waits_for_restart_and_sends_once(
        tmp_path, windows_transport, socket_rpc_authenticator, stale):
    path = tmp_path / "实例目录" / "execution-service.sock"
    receiver = socket_rpc_authenticator(path)
    old = LocalListener(path, transport="tcp") if stale else None
    if old is not None:
        old.socket.close()  # Simulate a dead process leaving a descriptor.
    client = ExecutionServiceClient(path, timeout=2, restart_wait=3)
    before = time.monotonic()
    with pytest.raises(ExecutionServiceUnavailable):
        client.health()
    assert time.monotonic() - before < 1

    received, errors = [], []
    def restart():
        time.sleep(.3)
        try:
            with LocalListener(path, transport="tcp", replace=True) as listener:
                if old is not None:
                    old.close()  # Retiring generation cannot delete the new descriptor.
                assert rendezvous_path(path).is_file()
                listener.settimeout(3)
                connection, _ = listener.accept()
                with connection, connection.makefile("rb") as stream:
                    payload = json.loads(stream.readline())
                    receiver.authenticate(payload["method"], payload["params"], payload["auth"])
                    received.append(payload)
                    reply = {"id": payload["id"], "ok": True, "result": {"answered": True}}
                    connection.sendall(json.dumps(reply).encode() + b"\n")
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=restart, daemon=True)
    thread.start()
    try:
        assert client.request("accounts.usage", {}) == {"answered": True}
    finally:
        thread.join(5)
        if old is not None:
            old.close()
    assert not thread.is_alive()
    assert not errors
    assert [p["method"] for p in received] == ["accounts.usage"]
    assert not rendezvous_path(path).exists()


def test_windows_lost_reply_is_unknown_and_not_replayed_on_peer(
        tmp_path, windows_transport, socket_rpc_authenticator):
    path = tmp_path / "primary" / "execution-service.sock"
    peer_path = tmp_path / "peer" / "execution-service.sock"
    receiver = socket_rpc_authenticator(path)
    received, errors = [], []
    with LocalListener(path, transport="tcp") as listener, \
            LocalListener(peer_path, transport="tcp") as peer:
        listener.settimeout(3)
        def lose_reply():
            try:
                connection, _ = listener.accept()
                with connection, connection.makefile("rb") as stream:
                    payload = json.loads(stream.readline())
                    receiver.authenticate(payload["method"], payload["params"], payload["auth"])
                    received.append(payload)
                    # The service accepted the operation; its reply is lost.
            except Exception as exc:
                errors.append(exc)
        thread = threading.Thread(target=lose_reply, daemon=True)
        thread.start()
        client = ExecutionServiceClient(path, timeout=1, restart_wait=0)
        client.socket_paths = (path, peer_path)
        try:
            with pytest.raises(ExecutionResponseUnknown):
                client.request("scheduler.claim.wait", {})
        finally:
            thread.join(4)
        assert not thread.is_alive()
        assert not errors
        assert [p["method"] for p in received] == ["scheduler.claim.wait"]
        # Inspect raw accept: even a replay attempt waiting for authentication
        # would appear here, before any application request reached the peer.
        peer.socket.settimeout(.05)
        with pytest.raises(socket.timeout):
            peer.socket.accept()
