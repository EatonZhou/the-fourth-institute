"""Explicit authentication setup for cases that own synthetic Portal sockets."""
from pathlib import Path

import pytest

from management_common.rpc_auth import RpcAuthenticator


@pytest.fixture
def socket_rpc_authenticator(monkeypatch):
    # A test socket owns its key beside the socket, never in a deployed instance.
    monkeypatch.delenv("PORTAL_RESOURCE_RPC_SIGNING_KEY_FILE", raising=False)

    def create(socket_path):
        return RpcAuthenticator(Path(socket_path))

    return create
