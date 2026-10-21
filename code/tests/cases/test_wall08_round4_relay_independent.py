import json
import socket
import struct

import pytest

from management_portal import ai_socket_relay as relay
from tests.cases.test_wall08_round4_auxiliary_gaps import relay_fixture


def test_relay_json_decoder_depth_failure_is_400_and_closes(relay_fixture):
    """中继收到嵌套过深的 JSON 帧时返回 400 并关闭连接，不转发"""
    depth = 16384
    body = b'{"nested":' + b'[' * depth + b'0' + b']' * depth + b'}'
    with pytest.raises(RecursionError):
        json.loads(body)
    with relay_fixture.start() as (server, semaphore):
        server.max_bytes = len(body) + 16
        with socket.create_connection(server.server_address, timeout=2) as connection:
            connection.sendall(struct.pack('>I', len(body)) + body)
            response = relay._read_frame(connection, 10000)
            assert response['status'] == 400
            assert connection.recv(1) == b''
    assert relay_fixture.calls == []
