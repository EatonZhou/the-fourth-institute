"""Local IPC contract: byte streams, private rendezvous, ownership and auth."""
import asyncio
import json
import socket
import threading
import shutil
import subprocess
import sys
import time
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common.local_ipc import LocalListener, connect_local, start_local_server

NODE_HELPER = Path(__file__).resolve().parents[2] / 'management_common/local_ipc.cjs'


def test_tcp_directory_creation_with_windows_acl_policy(tmp_path, monkeypatch):
    """Model inherited Windows grants at the filesystem boundary, with real TCP."""
    from management_common import local_ipc as ipc, file_permissions as private
    endpoint = tmp_path / 'ipc' / 'resource.json'
    sid = 'S-1-5-21-1-2-3-1001'
    owner_only = [(0, 0, 0x1F01FF, sid)]
    inherited = owner_only + [(0, 16, 1, 'S-1-5-32-545')]
    acls = {}
    mkdir = Path.mkdir
    ensure = private.ensure_private_directory
    publish = ipc._publish

    def inheriting_mkdir(path, *args, **kwargs):
        existed = path.exists()
        result = mkdir(path, *args, **kwargs)
        if not existed:
            acls[path] = inherited
        return result

    def secure_directory(path):
        if path in acls:
            private._validate_windows_acl(sid, sid, acls[path])
        ensure(path)
        acls[path] = owner_only

    def checked_publish(path, record):
        private._validate_windows_acl(sid, sid, acls.get(path.parent, inherited))
        publish(path, record)

    monkeypatch.setattr(Path, 'mkdir', inheriting_mkdir)
    monkeypatch.setattr(ipc, 'ensure_private_directory', secure_directory, raising=False)
    monkeypatch.setattr(ipc, '_publish', checked_publish)
    with LocalListener(endpoint, transport='tcp') as listener:
        assert listener.socket.getsockname()[1] > 0
        assert json.loads(endpoint.read_text())['host'] == '127.0.0.1'
    assert not endpoint.exists()
    with LocalListener(endpoint, transport='tcp'):
        assert endpoint.exists()


def test_windows_default_uses_new_private_cache_without_touching_legacy(tmp_path, monkeypatch):
    from management_common import local_ipc as ipc
    legacy = tmp_path / 'TFI/Portal/ipc'
    legacy.mkdir(parents=True)
    marker = legacy / 'old.json'
    marker.write_text('legacy permissions and contents must remain unchanged')
    monkeypatch.setattr(ipc, 'os', SimpleNamespace(**{**vars(os), 'name': 'nt',
                                                   'environ': {'LOCALAPPDATA': str(tmp_path)}}))
    monkeypatch.setattr(ipc, 'compatibility_value', lambda key, default: default)
    logical = tmp_path / 'resource-service.sock'
    endpoint = ipc.rendezvous_path(logical)
    assert endpoint.parent == tmp_path / 'TFI/Portal/ipc-v2'
    assert ipc.rendezvous_path(logical) == endpoint
    assert ipc.rendezvous_path(tmp_path / 'execution-service.sock') != endpoint
    with LocalListener(logical, transport='tcp'):
        assert endpoint.exists()
        assert marker.read_text() == 'legacy permissions and contents must remain unchanged'
    assert list(legacy.iterdir()) == [marker]


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX generation ownership')
def test_node_retiring_listener_preserves_successor(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node required')
    result = subprocess.run(['node', '-e', '''
const net=require('net'),fs=require('fs');
const {listenLocal}=require(process.argv[1]);
const logical=process.argv[2],a=net.createServer(),b=net.createServer();
listenLocal(a,logical);a.on('listening',()=>{
  listenLocal(b,logical);b.on('listening',()=>a.close(()=>{
    if(!fs.existsSync(logical))throw new Error('successor removed');
    b.close(()=>{if(fs.existsSync(logical))throw new Error('endpoint leaked');});
  }));
});
''', str(NODE_HELPER), str(tmp_path/'ipc')], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("transport", ["unix", "tcp"])
def test_stream_round_trip_and_cleanup(tmp_path, transport):
    path = tmp_path / "endpoint"
    with LocalListener(path, transport=transport) as listener:
        def echo():
            connection, _ = listener.accept()
            with connection, connection.makefile("rb") as stream:
                connection.sendall(stream.readline())
        thread = threading.Thread(target=echo, daemon=True)
        thread.start()
        with connect_local(path, timeout=2, transport=transport) as client:
            client.sendall(b"original business protocol\n")
            assert client.makefile("rb").readline() == b"original business protocol\n"
        thread.join(3)
        assert not thread.is_alive()
    assert not path.exists()


def test_invalid_client_never_reaches_application(tmp_path):
    path = tmp_path / "endpoint"
    accepted = []
    with LocalListener(path, transport="tcp") as listener:
        def accept_one():
            connection, _ = listener.accept()
            with connection:
                accepted.append(connection.recv(4))
        thread = threading.Thread(target=accept_one, daemon=True)
        thread.start()
        record = json.loads(path.read_text())
        with socket.create_connection((record["host"], record["port"]), timeout=2) as attacker:
            assert len(attacker.recv(32)) == 32
            attacker.sendall(b"x" * 32)
            assert attacker.recv(1) == b""
        assert not accepted
        with connect_local(path, timeout=2, transport="tcp") as client:
            client.sendall(b"good")
        thread.join(3)
        assert accepted == [b"good"]


@pytest.mark.parametrize("transport", ["unix", "tcp"])
def test_retiring_listener_does_not_unlink_successor(tmp_path, transport):
    path = tmp_path / "endpoint"
    old = LocalListener(path, transport=transport)
    with LocalListener(path, transport=transport, replace=True):
        old.close()
        assert path.exists()
    assert not path.exists()


def test_descriptor_rejects_network_and_public_secret(tmp_path):
    path = tmp_path / "endpoint"
    with LocalListener(path, transport="tcp"):
        record = json.loads(path.read_text())
        record["host"] = "example.com"
        path.write_text(json.dumps(record))
        with pytest.raises(ValueError, match="rendezvous"):
            connect_local(path, transport="tcp", timeout=1)
        record["host"] = "127.0.0.1"
        path.write_text(json.dumps(record))
        path.chmod(0o644)
        with pytest.raises(PermissionError):
            connect_local(path, transport="tcp", timeout=1)
        path.chmod(0o600)


def test_async_server_uses_the_same_authenticated_contract(tmp_path):
    path = tmp_path / "async"

    async def exercise():
        async def echo(reader, writer):
            writer.write(await reader.readline())
            await writer.drain()
        server = await start_local_server(echo, path, transport="tcp")
        async with server:
            def request():
                with connect_local(path, transport="tcp", timeout=2) as connection:
                    connection.sendall(b"async\n")
                    assert connection.makefile("rb").readline() == b"async\n"
            await asyncio.to_thread(request)
        assert not path.exists()
    asyncio.run(exercise())


@pytest.mark.parametrize('tcp', [False, True, None])
def test_node_client_and_python_server_share_only_application_bytes(tmp_path, tcp):
    if not shutil.which('node'):
        pytest.skip('Node is required for cross-language IPC')
    endpoint = tmp_path / ('资料' * 30) / 'node-client' if tcp is None else tmp_path / 'node-client'
    with LocalListener(endpoint, transport=None if tcp is None else ('tcp' if tcp else 'unix')) as listener:
        def echo():
            client, _ = listener.accept()
            with client:
                client.sendall(client.recv(64))
        thread = threading.Thread(target=echo, daemon=True)
        thread.start()
        result = subprocess.run(['node', '-e', '''
const {connectLocal} = require(process.argv[1]);
const client = connectLocal(process.argv[2], socket => {
  socket.on('data', data => { process.stdout.write(data); socket.end(); });
  socket.write('business-only');
}, {python:process.argv[3], tcp:process.argv[4] === 'none' ? undefined : process.argv[4] === 'true'});
client.on('error', error => { console.error(error.message); process.exitCode=1; });
''', str(NODE_HELPER), str(endpoint), sys.executable, str(tcp).lower()],
            capture_output=True, timeout=10, check=True)
        thread.join(3)
        assert result.stdout == b'business-only'
        assert not thread.is_alive()


@pytest.mark.parametrize('automatic', [False, True])
def test_python_client_node_server_authentication_and_owned_cleanup(tmp_path, automatic):
    if not shutil.which('node'):
        pytest.skip('Node is required for cross-language IPC')
    endpoint = tmp_path / ('资料' * 30) / 'node-server' if automatic else tmp_path / 'node-server'
    process = subprocess.Popen(['node', '-e', '''
const net = require('node:net');
const {listenLocal} = require(process.argv[1]);
const server = net.createServer(socket => {
  socket.once('data', data => { socket.end(data); server.close(); });
});
server.on('error', error => { console.error(error.message); process.exitCode=1; });
listenLocal(server, process.argv[2], {tcp:process.argv[4] === 'True' ? undefined : true, python:process.argv[3]});
''', str(NODE_HELPER), str(endpoint), sys.executable, str(automatic)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not endpoint.exists():
            assert process.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        with connect_local(endpoint, transport=None if automatic else 'tcp', timeout=2) as client:
            client.sendall(b'authenticated request')
            assert client.recv(64) == b'authenticated request'
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert not endpoint.exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)
