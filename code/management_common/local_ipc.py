"""Private local byte streams, with the same protocol on every supported OS.

POSIX uses Unix sockets. Windows uses ephemeral loopback listeners discovered
through a per-user rendezvous file. Mutual HMAC authentication happens before
any application bytes; neither service names nor a guessed port grant access.
The logical path remains the endpoint identity, never a remotely supplied URL.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import sys

# The Node helper invokes this exact file with an explicit Python interpreter.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.file_permissions import atomic_write_private_bytes, read_private_bytes
from management_common.site_compatibility import compatibility_value

_SCHEMA = "portal.local-ipc.v1"
_HANDSHAKE_TIMEOUT = 5.0


def rendezvous_path(path: str | Path) -> Path:
    logical = Path(path)
    if os.name != "nt":
        return logical
    # Avoid port collisions between installations and Windows' short AF_UNIX
    # path limit. This directory's ACL is established before any secret exists.
    default = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "TFI/Portal/ipc"
    root = Path(compatibility_value('local_ipc_root', str(default))).expanduser()
    identity = os.path.normcase(str(logical.absolute())).encode("utf-8")
    return root / (hashlib.sha256(identity).hexdigest() + ".json")


def _publish(path: Path, record: dict) -> None:
    _validate(record)
    atomic_write_private_bytes(path, json.dumps(record, separators=(",", ":")).encode("utf-8"))


def _read(path: Path) -> dict:
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size > 4096:
        raise PermissionError("Invalid local IPC rendezvous")
    raw = read_private_bytes(path)
    if len(raw) > 4096:
        raise ValueError("Invalid local IPC rendezvous")
    return _validate(json.loads(raw))


def _validate(data: dict) -> dict:
    if (not isinstance(data, dict) or data.get("schema") != _SCHEMA or data.get("host") != "127.0.0.1"
            or type(data.get("port")) is not int or not 0 < data["port"] < 65536
            or not isinstance(data.get("secret"), str) or len(data["secret"]) != 64):
        raise ValueError("Invalid local IPC rendezvous")
    if len(bytes.fromhex(data["secret"])) != 32:
        raise ValueError("Invalid local IPC secret")
    return data


def _tcp_transport(transport):
    if transport not in {None, "unix", "tcp"}:
        raise ValueError("Unknown local IPC transport")
    if transport is None and os.name not in {"nt", "posix"}:
        raise NotImplementedError("Unsupported local IPC platform")
    return transport == "tcp" or (transport is None and os.name == "nt")


def _proof(secret: bytes, role: bytes, nonce: bytes) -> bytes:
    return hmac.digest(secret, role + nonce, "sha256")


def _exact(connection: socket.socket, length: int) -> bytes:
    result = bytearray()
    while len(result) < length:
        chunk = connection.recv(length - len(result))
        if not chunk:
            raise ConnectionError("Local IPC authentication was interrupted")
        result.extend(chunk)
    return bytes(result)


def connect_local(path: str | Path, *, timeout: float | None = None,
                  transport: str | None = None) -> socket.socket:
    tcp = _tcp_transport(transport)
    connection = socket.socket(socket.AF_INET if tcp else socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(min(timeout, _HANDSHAKE_TIMEOUT) if timeout is not None else _HANDSHAKE_TIMEOUT)
        if tcp:
            record = _read(rendezvous_path(path))
            secret = bytes.fromhex(record["secret"])
            connection.connect(("127.0.0.1", record["port"]))
            nonce = _exact(connection, 32)
            connection.sendall(_proof(secret, b"client", nonce))
            if not hmac.compare_digest(_exact(connection, 32), _proof(secret, b"server", nonce)):
                raise PermissionError("Local IPC server authentication failed")
        else:
            connection.connect(str(path))
        connection.settimeout(timeout)
        return connection
    except BaseException:
        connection.close()
        raise


class LocalListener:
    """Owned listener; closing an older generation never removes its successor."""

    def __init__(self, path: str | Path, *, backlog: int = 32, mode: int = 0o600,
                 replace: bool = False, transport: str | None = None):
        self.tcp = _tcp_transport(transport)
        self.path = rendezvous_path(path) if self.tcp else Path(path)
        self.secret = secrets.token_bytes(32)
        self._identity = None
        self.socket = socket.socket(socket.AF_INET if self.tcp else socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.exists() or self.path.is_symlink():
                if not replace:
                    raise FileExistsError(str(self.path))
                if not self.tcp:
                    if not stat.S_ISSOCK(self.path.lstat().st_mode):
                        raise PermissionError("Refusing to replace a non-socket IPC path")
                    self.path.unlink()
            self.socket.bind(("127.0.0.1", 0) if self.tcp else str(self.path))
            if not self.tcp:
                st = self.path.lstat()
                self._identity = (st.st_dev, st.st_ino)
            self.socket.listen(backlog)
            if self.tcp:
                _publish(self.path, {"schema": _SCHEMA, "host": "127.0.0.1",
                                    "port": self.socket.getsockname()[1], "secret": self.secret.hex()})
            else:
                self.path.chmod(mode)
            st = self.path.stat()
            self._identity = (st.st_dev, st.st_ino)
        except BaseException:
            self.close()
            raise

    def authenticate(self, connection: socket.socket) -> None:
        if not self.tcp:
            return
        connection.settimeout(_HANDSHAKE_TIMEOUT)
        nonce = secrets.token_bytes(32)
        connection.sendall(nonce)
        if not hmac.compare_digest(_exact(connection, 32), _proof(self.secret, b"client", nonce)):
            raise PermissionError("Local IPC client authentication failed")
        connection.sendall(_proof(self.secret, b"server", nonce))
        connection.settimeout(None)

    def accept(self):
        while True:
            connection, address = self.socket.accept()
            try:
                self.authenticate(connection)
                return connection, address
            except OSError:
                connection.close()

    def fileno(self):
        return self.socket.fileno()

    def settimeout(self, value):
        self.socket.settimeout(value)

    def close(self):
        self.socket.close()
        if self._identity is None:
            return
        try:
            st = self.path.lstat()
            if (st.st_dev, st.st_ino) == self._identity:
                if not self.tcp or _read(self.path)["secret"] == self.secret.hex():
                    self.path.unlink()
        except (FileNotFoundError, PermissionError, ValueError):
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class LocalAsyncServer:
    def __init__(self, server, listener):
        self.server, self.listener = server, listener

    def __getattr__(self, name):
        return getattr(self.server, name)

    def close(self):
        self.server.close()
        self.listener.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.close()
        await self.server.wait_closed()


async def start_local_server(callback, path, *, limit=65536, transport=None):
    listener = LocalListener(path, replace=True, transport=transport)

    async def handle(reader, writer):
        try:
            if listener.tcp:
                nonce = secrets.token_bytes(32)
                writer.write(nonce)
                await writer.drain()
                proof = await asyncio.wait_for(reader.readexactly(32), _HANDSHAKE_TIMEOUT)
                if not hmac.compare_digest(proof, _proof(listener.secret, b"client", nonce)):
                    raise PermissionError("Local IPC client authentication failed")
                writer.write(_proof(listener.secret, b"server", nonce))
                await writer.drain()
            await callback(reader, writer)
        except (OSError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    try:
        if listener.tcp:
            server = await asyncio.start_server(handle, sock=listener.socket, limit=limit)
        else:
            server = await asyncio.start_unix_server(handle, sock=listener.socket, limit=limit)
        return LocalAsyncServer(server, listener)
    except BaseException:
        listener.close()
        raise


def _main():
    """Node bridges reuse the Python owner for private rendezvous publication."""
    import sys
    operation, logical = sys.argv[1:3]
    path = rendezvous_path(logical)
    if operation == "read":
        json.dump(_read(path), sys.stdout)
    else:
        record = json.loads(sys.stdin.buffer.read(4097))
        if operation == "publish":
            _publish(path, record)
            _read(path)  # enforce the same contract for non-Python listeners
        elif operation == "remove":
            try:
                if _read(path)["secret"] == record["secret"]:
                    path.unlink()
            except FileNotFoundError:
                pass
        else:
            raise ValueError("Unknown IPC operation")


if __name__ == "__main__":
    _main()
