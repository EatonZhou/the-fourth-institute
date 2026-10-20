"""Native-service lifecycle and Codex's authenticated WebSocket wire contract."""
import json
import socket
from pathlib import Path
import sys
import threading
import time

import pytest

from management_common.file_permissions import atomic_write_private_bytes
from management_portal import codex_app_server_host as host


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    script = tmp_path / "codex.py"
    script.write_text('''
import base64, hashlib, signal, socket, sys
if "--ws-auth" not in sys.argv:
    raise SystemExit(5)
digest=sys.argv[sys.argv.index("--ws-token-sha256")+1]
listener=socket.socket()
listener.bind(("127.0.0.1",0));listener.listen()
print("  listening on: ws://127.0.0.1:%s"%listener.getsockname()[1],file=sys.stderr,flush=True)
while True:
    connection,_=listener.accept()
    with connection:
        raw=b""
        while b"\\r\\n\\r\\n" not in raw:
            chunk=connection.recv(4096)
            if not chunk:break
            raw+=chunk
        fields={}
        for line in raw.decode().split("\\r\\n")[1:]:
            if ":" in line:
                k,v=line.split(":",1);fields[k.lower()]=v.strip()
        token=fields.get("authorization","").removeprefix("Bearer ")
        if hashlib.sha256(token.encode()).hexdigest()!=digest:
            connection.sendall(b"HTTP/1.1 401 Unauthorized\\r\\n\\r\\n");continue
        key=fields["sec-websocket-key"]+"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept=base64.b64encode(hashlib.sha1(key.encode()).digest())
        connection.sendall(b"HTTP/1.1 101 Switching Protocols\\r\\nSec-WebSocket-Accept: "+accept+b"\\r\\n\\r\\n")
        connection.recv(4096)
''')
    actual = host.spawn_process
    processes = []
    def spawn(command, **kwargs):
        process = actual([sys.executable, str(script), *command[1:]], **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(host, "spawn_process", spawn)
    return script, processes


def start_host(tmp_path):
    endpoint = tmp_path / "endpoint.json"
    stop = threading.Event()
    failures = []
    def run():
        try:
            host.run_host(endpoint, Path(sys.executable), tmp_path / "account", stop,
                          platform="win32", startup_timeout=2)
        except BaseException as error:
            failures.append(error)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 4
    while not endpoint.exists() and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    return endpoint, stop, thread, failures


def test_host_readiness_authentication_and_stop_cleanup(tmp_path, fake_codex):
    endpoint, stop, thread, failures = start_host(tmp_path)
    try:
        assert not failures
        transport = host.connect_transport(endpoint, platform="win32")
        transport.close()
        record = json.loads(endpoint.read_text())
        record["token"] = "f" * 64
        atomic_write_private_bytes(endpoint, json.dumps(record).encode())
        with pytest.raises(OSError, match="401"):
            host.connect_transport(endpoint, platform="win32")
    finally:
        stop.set(); thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert all(process.poll() is not None for process in fake_codex[1])
    # An older service must leave a successor's descriptor untouched.
    assert json.loads(endpoint.read_text())["token"] == "f" * 64


def test_normal_stop_removes_owned_endpoint(tmp_path, fake_codex):
    endpoint, stop, thread, failures = start_host(tmp_path)
    assert endpoint.exists(), failures
    stop.set(); thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert not endpoint.exists()
    assert fake_codex[1][0].poll() is not None


def test_startup_timeout_reaps_process_and_never_advertises(tmp_path, fake_codex):
    fake_codex[0].write_text("import time;time.sleep(60)")
    endpoint = tmp_path / "endpoint.json"
    with pytest.raises(TimeoutError):
        host.run_host(endpoint, Path(sys.executable), tmp_path / "account", threading.Event(),
                      platform="win32", startup_timeout=0.15)
    assert fake_codex[1][0].poll() is not None
    assert not endpoint.exists()


def test_endpoint_cannot_redirect_credentials_to_network(tmp_path):
    endpoint = tmp_path / "endpoint.json"
    atomic_write_private_bytes(endpoint, json.dumps({"schema":host._SCHEMA, "host":"example.com",
                               "port":80, "token":"a"*64}).encode())
    with pytest.raises(ValueError, match="endpoint"):
        host.connect_transport(endpoint, platform="win32")


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX socket ownership')
def test_posix_startup_does_not_adopt_or_remove_old_socket(tmp_path, fake_codex, monkeypatch):
    fake_codex[0].write_text('import time;time.sleep(60)')
    endpoint = tmp_path / 'previous.sock'
    with socket.socket(socket.AF_UNIX) as old:
        old.bind(str(endpoint)); old.listen()
        def wrong_owner(*args, **kwargs):
            pytest.fail('Connected to a socket predating this child')
        monkeypatch.setattr(host, 'connect_transport', wrong_owner)
        with pytest.raises(TimeoutError):
            host.run_host(endpoint, Path(sys.executable), tmp_path / 'account', threading.Event(),
                          platform='linux', startup_timeout=.1)
        assert endpoint.exists()
    assert fake_codex[1][0].poll() is not None
