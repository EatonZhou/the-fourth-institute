import json
import socket

from management_portal import ai_socket_relay as relay
from tests.cases.test_wall08_round4_auxiliary_gaps import frame, relay_fixture


def test_upstream_recursion_error_remains_502_and_connection_reusable(relay_fixture, monkeypatch, caplog):
    """上游转发抛出 RecursionError 时中继返回 502 且不泄露凭据，同一连接仍可继续使用"""
    original_forward = relay._forward_chat_completion
    calls = []
    sentinel = 'synthetic-recursion-credential'

    def forward(provider, payload):
        calls.append(provider)
        if len(calls) == 1:
            raise RecursionError(sentinel)
        return original_forward(provider, payload)

    monkeypatch.setattr(relay, '_forward_chat_completion', forward)
    with relay_fixture.start() as (server, semaphore):
        with socket.create_connection(server.server_address, timeout=2) as connection:
            relay._write_frame(connection, frame())
            response = relay._read_frame(connection, 10000)
            assert response['status'] == 502
            assert response['error'] == 'upstream request failed'
            assert sentinel not in json.dumps(response)
            relay._write_frame(connection, frame(2))
            recovered = relay._read_frame(connection, 10000)
            assert recovered['status'] == 200
            assert recovered['request_id'] == 'synthetic-2'
    assert len(calls) == 2 and len(relay_fixture.calls) == 1
    assert sentinel not in caplog.text
