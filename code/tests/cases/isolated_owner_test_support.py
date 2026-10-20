from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from tests.cases.registry_support import write_projects


@contextmanager
def local_connections_only(*, unix_paths=(), tcp_addresses=()):
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    allowed_unix = {str(Path(path)) for path in unix_paths}
    allowed_tcp = set(tcp_addresses)
    rejected = []

    def check(connection, address):
        allowed = (
            connection.family == socket.AF_UNIX and str(address) in allowed_unix
        ) or (
            connection.family in {socket.AF_INET, socket.AF_INET6}
            and tuple(address[:2]) in allowed_tcp
        )
        if not allowed:
            rejected.append(str(address))
            raise AssertionError("isolated owner attempted an unapproved connection")

    def connect(connection, address):
        check(connection, address)
        return original_connect(connection, address)

    def connect_ex(connection, address):
        check(connection, address)
        return original_connect_ex(connection, address)

    with mock.patch.object(socket.socket, "connect", connect), mock.patch.object(
        socket.socket, "connect_ex", connect_ex
    ):
        yield rejected


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("isolated owner did not reach the required state")


class CapturingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, handler):
        self.errors = []
        super().__init__(("127.0.0.1", 0), handler)

    def handle_error(self, request, client_address):
        del request, client_address
        self.errors.append(type(sys.exception()).__name__)


class PortalOwnerHTTP:
    def __init__(self, directory, monkeypatch):
        from management_portal import portal_ui_state, serve_management_portal
        from management_portal.portal_access_control import PortalAccessStore, PortalCredentialStore
        from management_portal.portal_session_auth import PortalSessionAuthority

        self.root = Path(directory)
        self.actor = "isolated-owner"
        self.trace = []
        self.control = None
        self.observations = lambda: {}
        self.store = portal_ui_state.PortalUIStateStore()
        secret = self.root / "synthetic-users.json"
        secret.write_text(json.dumps({self.actor: "unused-test-password"}))
        signing_key = self.root / "synthetic-signing.key"
        signing_key.write_bytes(os.urandom(32))
        write_projects("schema_version: 1\nprojects: []\n")
        access = PortalAccessStore()
        access.upsert_user(self.actor, actor=self.actor, status="active")
        PortalCredentialStore().set_password(self.actor, "Synthetic-test-password-908!")
        authority = PortalSessionAuthority(signing_key, access_store=access)
        monkeypatch.setattr(serve_management_portal, "_session_authority", lambda: authority)
        monkeypatch.setattr(serve_management_portal, "_PORTAL_ACCESS", access)
        monkeypatch.setattr(serve_management_portal, "_PORTAL_ALLOWED_USERS", {self.actor})
        monkeypatch.setattr(portal_ui_state, "_STORE", self.store)
        self.token = authority.issue(self.actor, max_age=3600)
        owner = self

        class Handler(serve_management_portal.Handler):
            def log_message(self, format, *args):
                del format, args

            def do_GET(self):
                if self.path == "/__isolated__/observations":
                    return serve_management_portal._serve_json(self, 200, owner.observations())
                owner.trace.append({"event": "http_start", "method": "GET", "path": self.path})
                try:
                    return super().do_GET()
                finally:
                    owner.trace.append({"event": "http_end", "method": "GET", "path": self.path})

            def do_POST(self):
                if self.path == "/__isolated__/control":
                    payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                    owner.control(payload)
                    return serve_management_portal._serve_json(self, 200, {"ok": True})
                owner.trace.append({"event": "http_start", "method": "POST", "path": self.path})
                try:
                    return super().do_POST()
                finally:
                    owner.trace.append({"event": "http_end", "method": "POST", "path": self.path})

        self.server = CapturingHTTPServer(Handler)
        self.address = self.server.server_address
        self.origin = f"http://127.0.0.1:{self.address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, path, payload=None, *, authenticated=True, origin=None):
        headers = {"Origin": origin or self.origin}
        if authenticated:
            headers["Cookie"] = "db_session=" + self.token
        body = None if payload is None else json.dumps(payload)
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(*self.address, timeout=5)
        try:
            connection.request("GET" if payload is None else "POST", path, body, headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            connection.close()


class ControlledStatusCollectors:
    def __init__(self, owner, monkeypatch):
        from management_portal import agent_status_backend as backend

        self.owner = owner
        self.backend = backend
        self.clock = 1000.0
        self.calls = []
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Event()
        monkeypatch.setattr(backend, "STATUS_CACHE_PATH", owner.root / "status-cache.json")
        monkeypatch.setattr(backend, "_REFRESHING_KEYS", set())
        monkeypatch.setattr(backend, "_now_ts", lambda: self.clock)
        scheduler = mock.Mock()
        self._sample = None
        self._sample_at = float("-inf")
        self._refused = False
        scheduler.accounts.side_effect = lambda force_refresh=False: self.collect(force=force_refresh)
        monkeypatch.setattr(backend, "ExecutionService", lambda: scheduler)
        monkeypatch.setattr(backend, "get_gpu_device_status", lambda **kwargs: {"status": "ok", "devices": [], "servers": []})
        monkeypatch.setattr(backend, "get_disk_resource_status", lambda **kwargs: {"status": "ok", "resources": []})
        owner.control = self.control
        owner.observations = self.snapshot

    def collect(self, *, force=False):
        # Like ExecutionService: a plain read returns the current sample; a forced
        # read inside the window is refused and reports the wait until the next
        # accepted one, which clears it.
        within = self._sample is not None and self.clock - self._sample_at < 300
        if force and within:
            self._refused = True
        if not force or within:
            retry = int(300 - (self.clock - self._sample_at)) if within and self._refused else 0
            return {**(self._sample or {"accounts": []}), "refresh_retry_after_seconds": retry}
        self._refused = False
        self.entered.set()
        self.owner.trace.append({"event": "collector_enter", "clock": self.clock})
        assert self.release.wait(10), "controlled collector was not released"
        self.calls.append({"method": "accounts", "clock": self.clock})
        self.owner.trace.append({"event": "collector_complete", "clock": self.clock})
        self._sample_at = self.clock
        self._sample = {"accounts": [{"account_id": "fixture", "status": "ok", "rate_limits": []}]}
        return self._sample

    def control(self, payload):
        if "clock" in payload:
            self.clock = float(payload["clock"])
        if payload.get("hold"):
            self.entered.clear()
            self.release.clear()
        if payload.get("release"):
            self.release.set()

    def snapshot(self):
        key = self.backend._agent_status_summary_cache_key(self.owner.actor)
        return {
            "clock": self.clock,
            "rpc_calls": list(self.calls),
            "refresh_in_progress": self.backend._refresh_in_progress(key),
            "collector_entered": self.entered.is_set(),
            "trace": list(self.owner.trace),
        }

    def wait(self):
        wait_until(lambda: not self.snapshot()["refresh_in_progress"])


def long_task_worker(socket_path, channel, ordinal):
    from management_portal.execution.transport import ExecutionServiceClient

    worker_id = "round6:worker-pool"
    capabilities = ["agent_session:codex"]
    with local_connections_only(unix_paths=[socket_path]):
        client = ExecutionServiceClient(socket_path, timeout=3)
        while True:
            claim = client.request("scheduler.claim.wait", {"worker_id": worker_id, "capabilities": capabilities, "timeout_seconds": 0.1})
            if claim.get("claimed"):
                break
            channel.send({"event": "blocked", "pid": os.getpid()})
            assert channel.recv() == "claim"
        execution_id = claim["execution"]["execution_id"]
        identity = {"execution_id": execution_id, "worker_id": worker_id, "claim_token": claim["claim_token"]}
        client.request("scheduler.start", identity)
        ticks = 0
        channel.send({"event": "running", "pid": os.getpid(), "execution_id": execution_id, "ordinal": ordinal})
        while True:
            ticks += 1
            client.request("scheduler.heartbeat", identity)
            if channel.poll(0.05):
                command = channel.recv()
                if command == "release":
                    break
                assert command == "probe"
                channel.send({"event": "alive", "pid": os.getpid(), "execution_id": execution_id, "ticks": ticks})
        client.request("scheduler.finish", {**identity, "result": {"execution_id": execution_id, "status": "completed", "output": {"pid": os.getpid(), "ticks": ticks}}})
        channel.send({"event": "completed", "execution_id": execution_id, "ticks": ticks})
