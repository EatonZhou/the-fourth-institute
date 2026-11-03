"""Observation shutdown reaps IO owners without changing account facts.

:module: execution.service
:design: decision_d40f59f5508f
"""
import json
import sys
import threading
import time
import pytest
from types import SimpleNamespace
from unittest.mock import Mock

from management_portal.execution.account_observation import ObservationLifetime, cancel_resource, login_status, urlopen
from management_portal.execution.account_monitor import AccountMonitor


def test_monitor_retries_catalog_pool_timeout_without_losing_samples(monkeypatch, caplog):
    """目录暂不可读时监控保留事实，下轮恢复；异常内容不进入日志。"""
    from psycopg_pool import PoolTimeout
    from management_portal.execution import account_monitor
    monkeypatch.setattr(account_monitor, "POLL_SECONDS", .01)
    account = SimpleNamespace(account_id="native", provider="test", kind="service")
    accounts = SimpleNamespace(accounts=lambda: [account], adapters={"test": SimpleNamespace()})
    monitor = AccountMonitor(accounts, Mock(), usage_store=Mock(), login_state_store=Mock())
    monitor._samples["native"] = {"checked_at": 123}
    reads = 0
    recovered = threading.Event()
    def catalog():
        nonlocal reads
        reads += 1
        if reads <= 2:
            raise PoolTimeout("private database connection detail")
        assert monitor._samples["native"] == {"checked_at": 123}
        return [account]
    accounts.accounts = catalog
    monkeypatch.setattr(monitor, "refresh_account", lambda *a, **k: recovered.set())
    monitor.start()
    try:
        assert recovered.wait(2)
        assert monitor.thread.is_alive()
        assert "PoolTimeout" in caplog.text
        assert "private database" not in caplog.text
    finally:
        monitor.close()
    assert not monitor._refreshing and not monitor._inflight


def test_monitor_isolates_slow_and_failed_accounts_and_discovers_new_accounts(monkeypatch, caplog):
    """慢账号不阻止其他账号再次刷新；错误处理再次失败也不终止监控。"""
    from psycopg_pool import PoolTimeout
    from management_portal.execution import account_monitor
    monkeypatch.setattr(account_monitor, "POLL_SECONDS", .01)
    def account(name):
        return SimpleNamespace(account_id=name, provider="test", kind="service", billing_account_id="",
                               quota_policy="reserve", quota_enabled=True)
    catalog = [account("slow"), account("broken"), account("healthy")]
    entered, released, refreshed, discovered = (threading.Event() for _ in range(4))
    counts = dict.fromkeys(["slow", "broken", "healthy", "new"], 0)
    def observe(a, **kwargs):
        counts[a.account_id] += 1
        if a.account_id == "slow":
            with cancel_resource(released.set):
                entered.set()
                assert released.wait(3)
        return {"status": "ok", "quota": {"status": "available"}}
    def update(name, sample):
        if name == "broken":
            raise PoolTimeout("private quota write detail")
        if name == "healthy" and counts[name] >= 2:
            refreshed.set()
        if name == "new":
            discovered.set()
    accounts = SimpleNamespace(accounts=lambda: list(catalog), adapters={"test": SimpleNamespace()}, observe=observe)
    scheduler = SimpleNamespace(quota_reserve_enabled=True, update_account_quota=update)
    monitor = AccountMonitor(accounts, scheduler, usage_store=Mock(), login_state_store=Mock())
    monitor.start()
    try:
        assert entered.wait(2)
        catalog.append(account("new"))
        assert refreshed.wait(2) and discovered.wait(2)
        assert counts["slow"] == 1 and not released.is_set()
        assert monitor.thread.is_alive() and counts["broken"] >= 2
        assert "Account observation failed for broken (PoolTimeout)" in caplog.text
        assert "private quota" not in caplog.text
    finally:
        monitor.close()
    assert released.is_set() and not monitor.thread.is_alive()


def test_monitor_close_cancels_and_joins_pool_without_overwriting_facts():
    entered, released = threading.Event(), threading.Event()
    account = SimpleNamespace(account_id="native", provider="test", billing_account_id="", quota_policy="none")
    def observe(*a, **k):
        with cancel_resource(released.set):
            entered.set()
            assert released.wait(5)
            raise OSError("cancelled IO")
    accounts = SimpleNamespace(accounts=lambda: [account], adapters={"test": SimpleNamespace()}, observe=observe)
    scheduler = Mock()
    login = Mock()
    monitor = AccountMonitor(accounts, scheduler, usage_store=Mock(), login_state_store=login)
    previous = {"status": "ok", "rate_limits": [{"remaining_percent": 42}], "checked_at": 123}
    monitor._samples["native"] = previous.copy()
    monitor.start()
    assert entered.wait(2)
    started = time.monotonic()
    monitor.close()
    assert time.monotonic() - started < 1
    assert not monitor.thread.is_alive()
    assert monitor._samples["native"] == previous
    scheduler.update_account_quota.assert_not_called()
    login.observe.assert_not_called()


def test_native_status_process_is_reaped_before_observer_finishes(tmp_path):
    pid_file = tmp_path / "pid"
    owner = ObservationLifetime()
    errors = []
    def work():
        try:
            with owner.scope():
                login_status([sys.executable, "-c", "import os,time,pathlib;pathlib.Path(" + repr(str(pid_file)) + ").write_text(str(os.getpid()));time.sleep(60)"], env=None)
        except InterruptedError:
            errors.append("cancelled")
    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 3
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    assert pid_file.exists()
    started = time.monotonic()
    owner.close()
    thread.join(2)
    from management_common.process_runtime import process_alive
    assert not thread.is_alive() and not process_alive(int(pid_file.read_text()))
    assert errors == ["cancelled"]
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("body", [False, True])
def test_blocked_http_headers_cancel_owned_socket(body):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if body:
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.flush()
            entered.set()
            release.wait(5)
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    owner = ObservationLifetime()
    def work():
        try:
            with owner.scope(), urlopen("http://127.0.0.1:" + str(server.server_port), timeout=30) as response:
                response.read()
        except OSError:
            pass
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(2)
        owner.close()
        thread.join(1)
        assert not thread.is_alive()
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        serving.join(2)


def test_antigravity_initialize_cancel_reaps_native_child(tmp_path, monkeypatch):
    from management_portal import antigravity_accounts as accounts, antigravity_client
    entered = threading.Event()
    processes = []
    real_spawn = antigravity_client.spawn_process
    def spawn(*args, **kwargs):
        process = real_spawn(*args, **kwargs)
        processes.append(process)
        entered.set()
        return process
    monkeypatch.setattr(antigravity_client, "spawn_process", spawn)
    monkeypatch.setattr(accounts, "command", lambda: [sys.executable, "-c", "import time;time.sleep(60)"])
    monkeypatch.setattr(accounts, "environment", lambda *_: None)
    owner = ObservationLifetime()
    def work():
        try:
            with owner.scope(): accounts.observe_account("test")
        except (OSError, ValueError, RuntimeError): pass
    thread = threading.Thread(target=work)
    thread.start()
    assert entered.wait(2)
    owner.close()
    thread.join(2)
    assert not thread.is_alive()
    assert processes[0].poll() is not None


def test_cancel_before_connection_does_not_start_observation(monkeypatch):
    from management_common import process_runtime
    spawn = Mock(side_effect=AssertionError("cancelled observation must not spawn"))
    monkeypatch.setattr(process_runtime, "spawn_process", spawn)
    owner = ObservationLifetime()
    owner.close()
    with pytest.raises(InterruptedError), owner.scope(), urlopen("http://example.invalid", timeout=30):
        pass
    spawn.assert_not_called()


@pytest.mark.parametrize("stage", ["dns", "tcp"])
def test_blocked_connection_setup_is_cancelled_and_process_reaped(tmp_path, monkeypatch, stage):
    from management_common import process_runtime
    marker = tmp_path / "dns-pid"
    processes = []
    spawn = process_runtime.spawn_process
    def block_dns(argv, **kwargs):
        prefix = ("import socket,time,pathlib,os\ndef resolve(*a,**k):\n pathlib.Path(" + repr(str(marker))
                  + ").write_text(str(os.getpid()))\n time.sleep(60)\nsocket."
                  + ("getaddrinfo" if stage == "dns" else "create_connection") + "=resolve\n")
        argv = [*argv[:2], prefix + argv[2]]
        process = spawn(argv, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(process_runtime, "spawn_process", block_dns)
    owner = ObservationLifetime()
    errors = []
    def work():
        try:
            with owner.scope(), urlopen("http://127.0.0.1:1", timeout=30): pass
        except InterruptedError: errors.append("cancelled")
    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline: time.sleep(.01)
    assert marker.exists()
    started = time.monotonic()
    owner.close()
    thread.join(2)
    assert not thread.is_alive() and processes[0].poll() is not None
    assert errors == ["cancelled"] and time.monotonic() - started < 2


@pytest.mark.parametrize("status", [200, 429])
def test_http_observation_preserves_result_and_http_failure(status):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.error import HTTPError
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'{"remaining":42}')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=server.serve_forever)
    serving.start()
    owner = ObservationLifetime()
    try:
        with owner.scope():
            if status == 200:
                with urlopen("http://127.0.0.1:" + str(server.server_port), timeout=3) as response:
                    assert json.load(response) == {"remaining": 42}
            else:
                with pytest.raises(HTTPError) as failure:
                    with urlopen("http://127.0.0.1:" + str(server.server_port), timeout=3): pass
                assert failure.value.code == 429
                assert json.load(failure.value) == {"remaining": 42}
    finally:
        owner.close()
        server.shutdown()
        server.server_close()
        serving.join(2)


def test_tls_handshake_cancellation_reaps_http_observer():
    import socket
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(3)
    owner = ObservationLifetime()
    errors = []
    def work():
        try:
            with owner.scope(), urlopen("https://127.0.0.1:" + str(listener.getsockname()[1]), timeout=30): pass
        except InterruptedError: errors.append("cancelled")
    thread = threading.Thread(target=work)
    thread.start()
    peer = None
    try:
        peer, _ = listener.accept()
        peer.settimeout(2)
        assert peer.recv(1)  # Real ClientHello, peer deliberately never replies.
        owner.close()
        thread.join(2)
        assert not thread.is_alive() and errors == ["cancelled"]
    finally:
        owner.close()
        if peer: peer.close()
        listener.close()
        thread.join(2)
