"""Native service host and transport for a single Codex account.

POSIX retains Codex's Unix WebSocket. Windows uses its authenticated loopback
WebSocket; the OS chooses the port and only a private descriptor reveals it.
No Portal JSON-RPC or model protocol is translated here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import stat
import subprocess
import sys
import threading
import time

from management_common.file_permissions import atomic_write_private_bytes, ensure_private_directory, read_private_bytes
from management_common.local_ipc import rendezvous_path
from management_common.process_runtime import install_shutdown_handlers, restore_shutdown_handlers, spawn_process, terminate_process_tree
from .provider_websocket import WebSocketTransport

_SCHEMA = "portal.codex-websocket.v1"
_ADDRESS = re.compile(r"listening on: ws://127\.0\.0\.1:(\d+)")


def _platform(platform):
    selected = platform or sys.platform
    if selected not in {"linux", "darwin", "win32"}:
        raise NotImplementedError(f"Unsupported Codex service platform: {selected}")
    return selected


def _read_endpoint(path):
    raw = read_private_bytes(rendezvous_path(path))
    if len(raw) > 4096:
        raise ValueError("Invalid Codex endpoint")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema") != _SCHEMA or data.get("host") != "127.0.0.1"
            or type(data.get("port")) is not int or not 0 < data["port"] < 65536
            or not isinstance(data.get("token"), str) or not re.fullmatch(r"[a-f0-9]{64}", data["token"])):
        raise ValueError("Invalid Codex endpoint")
    return data


def connect_transport(path, *, timeout=5.0, platform=None):
    """Return an authenticated WebSocket, closing the socket on every failure."""
    windows = _platform(platform) == "win32"
    record = _read_endpoint(path) if windows else None
    connection = socket.socket(socket.AF_INET if windows else socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(timeout)
        connection.connect(("127.0.0.1", record["port"]) if record else str(path))
        transport = WebSocketTransport(connection, bearer_token=record["token"] if record else None)
        connection.settimeout(None)
        return transport
    except BaseException:
        connection.close()
        raise


def run_host(path, binary, account_home, stop_event, *, platform=None, startup_timeout=30.0):
    windows = _platform(platform) == "win32"
    path, binary, account_home = Path(path), Path(binary), Path(account_home)
    if not binary.is_absolute() or not binary.is_file():
        raise ValueError("Codex executable must be an existing absolute path")
    ensure_private_directory(account_home)
    if not windows:
        ensure_private_directory(path.parent)
    previous_identity = None
    if not windows:
        try:
            previous = path.lstat()
            previous_identity = (previous.st_dev, previous.st_ino)
        except FileNotFoundError:
            pass
    token = secrets.token_hex(32) if windows else None
    command = [str(binary), "app-server", "--listen", "ws://127.0.0.1:0" if windows else f"unix://{path}"]
    if windows:
        command += ["--ws-auth", "capability-token", "--ws-token-sha256", hashlib.sha256(token.encode()).hexdigest()]
    env = dict(os.environ, CODEX_HOME=str(account_home))
    process = spawn_process(command, cwd=str(account_home), env=env, stdin=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    addresses = queue.Queue(maxsize=1)
    published = False
    unix_identity = None

    def drain():
        for line in iter(lambda: process.stderr.readline(8192), ""):
            match = _ADDRESS.search(line)
            if match:
                try:
                    addresses.put_nowait(int(match[1]))
                except queue.Full:
                    pass
            # Preserve native diagnostics; the secret is never in the command.
            sys.stderr.write(line)

    reader = threading.Thread(target=drain, name="codex-service-stderr", daemon=True)
    reader.start()
    try:
        deadline = time.monotonic() + startup_timeout
        while not stop_event.is_set():
            if process.poll() is not None:
                raise RuntimeError(f"Codex app-server exited during startup ({process.returncode})")
            if windows:
                try:
                    port = addresses.get_nowait()
                except queue.Empty:
                    port = None
                if port is not None:
                    record = {"schema": _SCHEMA, "host": "127.0.0.1", "port": port, "token": token}
                    # Verify authentication before advertising readiness. An old
                    # CLI lacking capability-token support fails closed.
                    with socket.create_connection(("127.0.0.1", port), timeout=5) as probe:
                        WebSocketTransport(probe, bearer_token=token).close()
                    atomic_write_private_bytes(rendezvous_path(path), json.dumps(record).encode())
                    published = True
                    break
            else:
                try:
                    st = path.lstat()
                    identity = (st.st_dev, st.st_ino)
                    if stat.S_ISSOCK(st.st_mode) and identity != previous_identity:
                        # Never mistake an earlier service's socket for this
                        # child's readiness, or remove it on startup failure.
                        unix_identity = identity
                        transport = connect_transport(path, platform=platform)
                        transport.close()
                        break
                except (FileNotFoundError, ConnectionRefusedError):
                    pass
            if time.monotonic() >= deadline:
                raise TimeoutError("Codex app-server did not become ready")
            stop_event.wait(0.05)
        while not stop_event.wait(0.1):
            result = process.poll()
            if result is not None:
                return result
        return 0
    finally:
        try:
            terminate_process_tree(process, timeout=10)
        finally:
            reader.join(2)
            process.stderr.close()
            if windows and published:
                try:
                    if _read_endpoint(path)["token"] == token:
                        rendezvous_path(path).unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
            elif unix_identity is not None:
                try:
                    st = path.lstat()
                    if (st.st_dev, st.st_ino) == unix_identity:
                        path.unlink()
                except FileNotFoundError:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--account-home", required=True)
    args = parser.parse_args()
    stop = threading.Event()
    previous = install_shutdown_handlers(lambda *_: stop.set())
    try:
        return run_host(args.socket, args.binary, args.account_home, stop)
    finally:
        restore_shutdown_handlers(previous)


if __name__ == "__main__":
    raise SystemExit(main())
