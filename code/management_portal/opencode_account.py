"""Keep an OpenCode HTTP server alive across session bridges in one account and shell scope."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management_common.file_lock import lock_stream
from management_common.local_ipc import LocalListener, connect_local
from management_common.process_runtime import spawn_process, terminate_process_tree


def lease(config):
    path = Path(config["account_socket"])
    directory = Path(config["account_state"])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "launch.lock").open("a+") as lock, lock_stream(lock):
        try:
            return attach(path)
        except (FileNotFoundError, ConnectionRefusedError, ConnectionResetError):
            pass
        with (directory / "runtime.log").open("ab") as log:
            process = spawn_process([sys.executable, "-I", __file__, str(Path(config["state_dir"]) / "launch.json")],
                                    cwd=directory, env=dict(os.environ), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=log, kill_on_owner_exit=False)
        ready = threading.Event()
        result = []

        def read_ready():
            with process.stdout as stream:
                result.append(stream.readline(32))
            ready.set()

        threading.Thread(target=read_ready, daemon=True).start()
        try:
            if not ready.wait(90):
                raise TimeoutError("OpenCode account host startup timed out")
            if result != [b"ready\n"]:
                raise RuntimeError("OpenCode account host exited during startup")
            return attach(path)
        except BaseException:
            terminate_process_tree(process, force=True)
            raise


def attach(path):
    connection = connect_local(path)
    try:
        connection.settimeout(15)
        with connection.makefile("rb") as stream:
            frame = stream.readline(2048)
        if not frame:
            raise ConnectionResetError("OpenCode account host closed during admission")
        address = json.loads(frame)
        connection.settimeout(None)
        return connection, address
    except BaseException:
        connection.close()
        raise


def serve(config):
    from management_portal.opencode_bridge import OpenCodeServer

    root = Path(config["account_state"])
    server = OpenCodeServer({**config, "state_dir": str(root), "cwd": str(root)})
    stopped = threading.Event()
    listener = LocalListener(config["account_socket"], replace=True)
    lock = threading.Lock()
    clients = set()
    timer = None

    def stop(*_):
        stopped.set()
        listener.close()
        server.stop()

    def idle():
        with lock:
            if not clients:
                stop()

    def watch(conn):
        nonlocal timer
        try:
            with conn:
                conn.sendall(json.dumps({"host": server.host, "port": server.port, "auth": server.auth}).encode() + b"\n")
                conn.recv(1)
        except OSError:
            pass
        finally:
            with lock:
                clients.discard(conn)
                if not clients and not stopped.is_set():
                    timer = threading.Timer(300, idle)
                    timer.daemon = True
                    timer.start()

    def native_exit():
        server.process.wait()
        os._exit(0)
    threading.Thread(target=native_exit, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print("ready", flush=True)
    while not stopped.is_set():
        conn, _ = listener.accept()
        with lock:
            if timer:
                timer.cancel()
            clients.add(conn)
        threading.Thread(target=watch, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    serve(json.loads(Path(sys.argv[1]).read_text()))
