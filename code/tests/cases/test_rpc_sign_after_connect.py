"""Restart waits must not consume the lifetime of an unsent RPC signature."""
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common import rpc_auth
from management_portal.hatg import socket_rpc
from management_portal.resources import transport


@pytest.mark.parametrize('kind', ['hatg', 'resource'])
def test_rpc_is_signed_after_waiting_for_restarting_owner(monkeypatch, kind):
    clock = [1000]
    key = b'local-test-key-with-thirty-two-bytes'
    monkeypatch.setattr(rpc_auth.time, 'time', lambda: clock[0])
    monkeypatch.setattr(rpc_auth, 'load_rpc_signing_key', lambda **kwargs: key)
    monkeypatch.setattr(transport, 'load_rpc_signing_key', lambda **kwargs: key)
    received = []

    class Connection:
        def sendall(self, raw):
            payload = json.loads(raw)
            # Verify exactly as the receiving service does. A pre-connect
            # signature would now be 90 seconds old and must be rejected.
            actor, _, timestamp = rpc_auth.verify_rpc_auth(
                key, payload['method'], payload['params'], payload['auth'], now=clock[0])
            assert actor == 'alice' and timestamp == clock[0]
            received.append(payload)

        def makefile(self, mode):
            return io.BytesIO(json.dumps({'id': received[-1]['id'], 'ok': True, 'result': {}}).encode() + b'\n')

        def close(self):
            pass

    def connect(*args, **kwargs):
        clock[0] += 90
        return Connection(), Path('test.sock')

    if kind == 'hatg':
        monkeypatch.setattr(socket_rpc, 'connect_service', connect)
        monkeypatch.setattr(socket_rpc, 'owner_socket', lambda owner: Path('test.sock'))
        payload = {'id': 'request-1', 'method': socket_rpc.METHOD, 'params': {'example': 'preserved'}}
        connection, stream = socket_rpc._open('society', payload, 120, SimpleNamespace(caller='alice'))
        stream.close()
        connection.close()
        assert received[0]['params'] == {'example': 'preserved'}
    else:
        monkeypatch.setattr(transport, 'connect_service', connect)
        client = transport.ResourceServiceClient('test.sock')
        client._exchange('resource.query', {'actor': 'alice', 'example': 'preserved'})
        assert received[0]['params'] == {'example': 'preserved'}
    assert len(received) == 1, 'waiting must not replay an instruction'


def test_hatg_auth_failure_after_connect_closes_unsent_socket(monkeypatch):
    connection = SimpleNamespace(close=lambda: closed.append(True))
    closed = []
    monkeypatch.setattr(socket_rpc, 'owner_socket', lambda owner: Path('test.sock'))
    monkeypatch.setattr(socket_rpc, 'connect_service', lambda *args, **kwargs: (connection, Path('test.sock')))
    def refused(*args):
        raise PermissionError('test signing failure')
    monkeypatch.setattr(socket_rpc, '_signed', refused)
    with pytest.raises(PermissionError, match='test signing failure'):
        socket_rpc._open('society', {'params': {}}, 120, SimpleNamespace(caller='alice'))
    assert closed == [True]
