"""Worker/service process ownership without deployed services or model calls."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

from management_common import process_runtime
from management_portal.execution import service_lifecycle, worker, worker_manager


class Process:
    def __init__(self, returncode=None):
        self.pid = 12345678
        self.returncode = returncode

    def poll(self):
        return self.returncode


def managed(tmp_path, process=None):
    item = worker_manager.WorkerProcess(
        process=process or Process(), worker_id="test:worker", build_id="build",
        health_path=tmp_path / "worker.json", started_at=time.time(),
    )
    item.health_path.write_text('{"ok": true}')
    return item


@pytest.fixture
def manager(monkeypatch, tmp_path):
    monkeypatch.setenv("PORTAL_EXECUTION_WORKER_HEALTH_PATH", str(tmp_path / "manager.json"))
    instance = worker_manager.ExecutionWorkerManager()
    instance.kill_grace = .2
    instance.drain_timeout = 1
    instance.calls = []
    monkeypatch.setattr(worker_manager, "ExecutionServiceClient", lambda **kw: SimpleNamespace(
        request=lambda method, args: instance.calls.append((method, args)) or {},
    ))
    monkeypatch.setattr(instance, "_handover", lambda item: instance.calls.append("handover") or {})
    monkeypatch.setattr(instance, "_unregister", lambda identifier: instance.calls.append("unregister") or True)
    return instance


def test_retiring_worker_uses_durable_drain_without_killing_provider(manager, tmp_path):
    """停止通过既有 draining 唤醒交接，不用 Windows 硬终止替代优雅退出。"""
    item = managed(tmp_path)
    manager._mark_draining(item)
    manager._request_worker_stop(item)
    assert manager.calls == [("scheduler.worker.state", {"worker_id": item.worker_id, "state": "draining"})]
    assert item.terminate_sent_at and item.draining_started_at
    assert item.process.poll() is None
    assert not item.tree_stopped


def test_failed_drain_request_is_retried(manager, monkeypatch, tmp_path):
    """服务连接失败保留停止意图，下次重试同一 Worker 而不宣告已结束。"""
    item = managed(tmp_path)
    calls = []
    def request(method, args):
        calls.append((method, args))
        if len(calls) == 1:
            raise OSError("connection unavailable")
        return {}
    monkeypatch.setattr(worker_manager, "ExecutionServiceClient", lambda **kw: SimpleNamespace(request=request))
    manager._mark_draining(item)
    assert not item.terminate_sent_at
    manager._request_worker_stop(item)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert item.terminate_sent_at


@pytest.mark.parametrize("leader_exited", [False, True])
def test_reap_proves_tree_empty_before_handover(manager, monkeypatch, tmp_path, leader_exited):
    """超时或父进程已退出时都先确认树清空，再释放调度资格。"""
    item = managed(tmp_path, Process(0 if leader_exited else None))
    item.draining_started_at = time.time() - 5
    manager._draining = [item]
    def stop(process, **options):
        assert process is item.process and options["force"]
        manager.calls.append("tree stopped")
        process.returncode = 0
    monkeypatch.setattr(process_runtime, "terminate_process_tree", stop)
    manager._reap()
    assert manager.calls[-3:] == ["tree stopped", "handover", "unregister"]
    assert item.tree_stopped and manager._draining == []
    assert not item.health_path.exists()


def test_uncleared_tree_keeps_health_and_retries(manager, monkeypatch, tmp_path):
    """树清理失败保留健康和所有权，重入成功后才注销。"""
    item = managed(tmp_path, Process(0))
    manager._draining = [item]
    calls = []
    def stop(process, **options):
        calls.append(process)
        if len(calls) == 1:
            raise TimeoutError("Job still has descendants")
    monkeypatch.setattr(process_runtime, "terminate_process_tree", stop)
    manager._reap()
    assert manager.calls == []
    assert manager._draining == [item] and item.health_path.exists()
    assert not item.tree_stopped and "descendants" in item.cleanup_error
    manager._reap()
    assert len(calls) == 2 and not item.cleanup_error and item.tree_stopped
    assert manager.calls == ["handover", "unregister"]


def test_failed_launch_retains_uncleared_job_for_reap(manager, monkeypatch, tmp_path):
    """未就绪的子世代清理失败仍持有 Job/进程对象与健康文件。"""
    item = managed(tmp_path, Process(3))
    def stop(*args, **kwargs):
        raise TimeoutError("descendant alive")
    monkeypatch.setattr(process_runtime, "terminate_process_tree", stop)
    with pytest.raises(TimeoutError):
        manager._discard_failed_replacement(item)
    assert item in manager._draining
    assert item.health_path.exists() and item.cleanup_error


def test_shutdown_includes_exited_current_and_reports_cleanup_failure(manager, monkeypatch, tmp_path):
    """manager 退出也回收已退出父进程的子孙，失败不报成功。"""
    item = managed(tmp_path, Process(0))
    manager._current = item
    manager._stopping.set()
    manager.drain_timeout = 0
    manager.kill_grace = 0
    monkeypatch.setattr(manager, "_write_health_safely", lambda: True)
    def stop(*args, **kwargs):
        raise TimeoutError("descendant alive")
    monkeypatch.setattr(process_runtime, "terminate_process_tree", stop)
    with pytest.raises(RuntimeError, match="trees did not stop"):
        manager.run()
    assert manager._draining == [item] and item.health_path.exists()
    assert "handover" not in manager.calls


def test_idle_manager_exits_when_execution_service_is_stopped(tmp_path):
    """An exited worker needs no live service to finish stopping its manager."""
    program = textwrap.dedent('''
        import json, os, sys, tempfile
        from pathlib import Path
        from types import SimpleNamespace
        from management_portal.execution import worker_manager as wm
        from management_portal.execution.transport import ExecutionServiceClient

        root = Path(sys.argv[1])
        os.environ['PORTAL_EXECUTION_WORKER_HEALTH_PATH'] = str(root / 'manager.json')
        with tempfile.TemporaryDirectory(prefix='worker-stop-') as sockets:
            wm.ExecutionServiceClient = lambda **kw: ExecutionServiceClient(
                socket_path=Path(sockets) / 'missing.sock', **kw)
            # No synthetic PID can reach the OS process-control boundary.
            wm.process_runtime.terminate_process_tree = lambda process, **kw: None
            manager = wm.ExecutionWorkerManager()
            health = root / 'retired-worker.json'
            health.write_text('{"active_executions": []}')
            manager._current = wm.WorkerProcess(
                process=SimpleNamespace(pid=12345678, poll=lambda: 0),
                worker_id='test:retired', build_id='test', health_path=health,
                started_at=1.0)
            manager._stopping.set()
            assert manager.run() == 0
            payload = json.loads((root / 'manager.json').read_text())
            assert payload['shutdown_pending_recovery'] == ['test:retired']
            assert health.exists()  # Unacknowledged Scheduler cleanup remains durable.
            assert payload['draining'][0]['tree_stopped'] is True
    ''')
    subprocess.run([sys.executable, '-c', program, str(tmp_path)], check=True,
                   capture_output=True, text=True, timeout=4)


def test_manager_shutdown_rpc_cannot_start_after_its_deadline(manager, monkeypatch):
    manager._shutdown_deadline = time.monotonic() - 1
    def unexpected_client(**kw):
        raise AssertionError('No RPC may start after the shared deadline')
    monkeypatch.setattr(worker_manager, 'ExecutionServiceClient', unexpected_client)
    with pytest.raises(TimeoutError, match='shutdown deadline'):
        manager._retirement_request('scheduler.worker.handover', {})


def test_manager_shutdown_rpc_response_uses_remaining_budget(manager, monkeypatch):
    from management_common import rpc_auth
    from management_portal.execution.transport import ExecutionServiceClient, ExecutionResponseUnknown
    if not hasattr(socket, 'AF_UNIX'):
        pytest.skip('Unix-domain socket response deadline')
    finished = threading.Event()
    def no_response(listener):
        with listener.accept()[0] as connection:
            connection.recv(65536)
            finished.wait(3)
    with tempfile.TemporaryDirectory(prefix='stop-rpc-') as directory:
        path = os.path.join(directory, 'service.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)
            thread = threading.Thread(target=no_response, args=(listener,), daemon=True)
            thread.start()
            monkeypatch.setattr(rpc_auth, 'request_auth', lambda *args, **kw: {})
            monkeypatch.setattr(worker_manager, 'ExecutionServiceClient',
                                lambda **kw: ExecutionServiceClient(socket_path=path, **kw))
            manager._shutdown_deadline = time.monotonic() + .2
            started = time.monotonic()
            try:
                with pytest.raises(ExecutionResponseUnknown):
                    manager._retirement_request('scheduler.worker.handover', {})
                assert time.monotonic() - started < 1.5
            finally:
                finished.set()
                thread.join(2)
            assert not thread.is_alive()


@pytest.mark.parametrize("owner", ["worker", "manager", "service"])
def test_lifecycle_restores_handlers_even_on_startup_failure(monkeypatch, owner):
    """启动失败仍恢复进程处理器，重载/重启仅交能力层安装。"""
    constructors = {
        "worker": worker.ExecutionWorkerDaemon,
        "manager": worker_manager.ExecutionWorkerManager,
        "service": service_lifecycle.ServiceLifecycle,
    }
    item = constructors[owner].__new__(constructors[owner])
    calls = []
    previous = object()
    def install(callback, **options):
        calls.append((callback, options))
        return previous
    monkeypatch.setattr(process_runtime, "install_shutdown_handlers", install)
    monkeypatch.setattr(process_runtime, "restore_shutdown_handlers", lambda value: calls.append(value))
    def fail():
        raise RuntimeError("startup failed")
    item._run = fail
    with pytest.raises(RuntimeError, match="startup failed"):
        item.run()
    assert calls[-1] is previous
    assert set(calls[0][1]) == ({"reload", "restart"} if owner == "manager" else {"reload"} if owner == "service" else set())


def test_worker_spawn_is_managed_and_starts_standby(manager, monkeypatch):
    """新 Worker 经共同 owner 创建，仍先待命并沿用既有身份环境。"""
    calls = []
    process = Process()
    monkeypatch.setattr(process_runtime, "spawn_process", lambda *args, **kwargs: calls.append((args, kwargs)) or process)
    item = manager._spawn()
    options = calls[0][1]
    assert item.process is process
    assert options["env"]["PORTAL_EXECUTION_WORKER_INITIAL_STATE"] == "standby"
    assert options["env"]["PORTAL_EXECUTION_WORKER_ID"] == item.worker_id
    assert options.get("kill_on_owner_exit", True)


def test_crashed_generation_is_reaped_while_replacement_backs_off(manager, monkeypatch, tmp_path):
    """替换启动退避期间仍立即清理崩溃世代，不等新世代启动成功。"""
    item = managed(tmp_path, Process(1))
    manager._current = item
    manager._next_retry_monotonic = time.monotonic() + 60
    monkeypatch.setattr(manager, "_write_health_safely", lambda: True)
    monkeypatch.setattr(manager._reload, "wait", lambda seconds: manager._stopping.set())
    def stop(process, **options):
        assert process is item.process
        manager.calls.append("tree stopped")
    monkeypatch.setattr(process_runtime, "terminate_process_tree", stop)
    assert manager.run() == 0
    assert manager.calls[-3:] == ["tree stopped", "handover", "unregister"]
    assert manager._current is None and manager._draining == []


@pytest.mark.parametrize("profile", ["windows", "macos"])
@pytest.mark.parametrize("owner", ["worker", "manager", "service"])
def test_platform_signal_contract_preserves_programmatic_controls(monkeypatch, profile, owner):
    """Windows 无 HUP/USR1 仍安装停止能力；macOS 保留已有重载/重启。"""
    registered = {}
    fake_signal = SimpleNamespace(SIGINT=2, SIGTERM=15)
    if profile == "windows":
        fake_signal.SIGBREAK = 21
    else:
        fake_signal.SIGHUP = 1
        fake_signal.SIGUSR1 = 30
    def set_handler(signum, callback):
        previous = registered.get(signum, "original")
        registered[signum] = callback
        return previous
    fake_signal.signal = set_handler
    monkeypatch.setattr(process_runtime, "signal", fake_signal)
    monkeypatch.setattr(process_runtime, "_backend", lambda: None)
    if owner == "worker":
        item = worker.ExecutionWorkerDaemon.__new__(worker.ExecutionWorkerDaemon)
        item._stopping = threading.Event()
        item._draining_started_at = 0
        item._active_changed = threading.Condition()
    elif owner == "manager":
        item = worker_manager.ExecutionWorkerManager.__new__(worker_manager.ExecutionWorkerManager)
        item._stopping = threading.Event()
        item._reload = threading.Event()
        item._explicit_restart = threading.Event()
    else:
        item = service_lifecycle.ServiceLifecycle(SimpleNamespace())
    observed = []
    def run():
        observed.extend(registered)
        registered[21 if profile == "windows" else 15](0, None)
        assert (item.stop if owner == "service" else item._stopping).is_set()
        if owner == "manager":
            item.request_worker_restart(0, None)
            assert item._explicit_restart.is_set() and item._reload.is_set()
        return 0
    item._run = run
    assert item.run() == 0
    expected = {2, 15, 21} if profile == "windows" else {2, 15}
    if profile == "macos" and owner != "worker":
        expected.add(1)
        if owner == "manager":
            expected.add(30)
    assert set(observed) == expected
    assert set(registered.values()) == {"original"}


class Channel:
    def __init__(self, ready):
        self.ready = ready
        self.closed = False
    def fileno(self):
        return 42
    def close(self):
        self.closed = True
    def settimeout(self, value):
        pass
    def recv(self, size):
        return self.ready


@pytest.mark.parametrize("ready", [b"R", b""])
def test_service_successor_ownership_and_failed_tree_cleanup(monkeypatch, ready):
    """成功继任者独立于旧世代 Job；失败继任者即使父已退仍清理树。"""
    channels = [Channel(ready), Channel(ready)]
    monkeypatch.setattr(service_lifecycle.socket, "socketpair", lambda: channels)
    monkeypatch.setattr(service_lifecycle, "notify", lambda message: None)
    monkeypatch.setattr(service_lifecycle, "TimingRelay", lambda channel: SimpleNamespace())
    server = SimpleNamespace(socket=channels[0], timing_proxy=None, preserve_socket=False)
    lifecycle = service_lifecycle.ServiceLifecycle(server, command=["fixture"])
    calls = []
    process = Process(0)
    monkeypatch.setattr(process_runtime, "spawn_process", lambda *args, **kwargs: calls.append(("spawn", kwargs)) or process)
    monkeypatch.setattr(process_runtime, "terminate_process_tree", lambda p, **kwargs: calls.append(("stop", p)))
    if ready:
        lifecycle.replace()
        assert server.preserve_socket
        assert len(calls) == 1
    else:
        with pytest.raises(RuntimeError, match="before readiness"):
            lifecycle.replace()
        assert calls[-1] == ("stop", process)
        assert all(channel.closed for channel in channels)
        assert not server.preserve_socket
    assert calls[0][1]["kill_on_owner_exit"] is False


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native process-tree validation")
def test_real_exited_worker_descendant_is_cleaned_before_unregister(manager, tmp_path):
    """真实父进程先退而子进程忽略 TERM，manager 仍完成树清理。"""
    pid_file = tmp_path / "descendant.pid"
    program = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c',"
        "\"import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)\"])"
    )
    process = process_runtime.spawn_process([sys.executable, "-c", program])
    try:
        process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        descendant = int(pid_file.read_text())
        assert process_runtime.process_alive(descendant)
        item = managed(tmp_path, process)
        manager._draining = [item]
        manager._reap()
        assert manager._draining == [] and item.tree_stopped
        assert not process_runtime.process_alive(descendant)
        assert manager.calls == ["handover", "unregister"]
    finally:
        process_runtime.terminate_process_tree(process, force=True)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native signal validation")
def test_native_worker_shutdown_handler_wakes_graceful_drain():
    """真实进程信号只设置 worker 停止意图并唤醒等待线程。"""
    daemon = worker.ExecutionWorkerDaemon.__new__(worker.ExecutionWorkerDaemon)
    daemon._stopping = threading.Event()
    daemon._draining_started_at = 0
    daemon._active_changed = threading.Condition()
    previous = process_runtime.install_shutdown_handlers(daemon.request_stop)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        assert daemon._stopping.is_set() and daemon._draining_started_at
    finally:
        process_runtime.restore_shutdown_handlers(previous)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native descriptor handover")
def test_real_service_successor_survives_previous_generation_exit(monkeypatch, tmp_path):
    """真实服务连续换代后旧进程退出，新会话/进程组仍继续接单。"""
    socket_path = str(tmp_path / "service.sock")
    monkeypatch.setenv("PROBE_SOCKET", socket_path)
    monkeypatch.setenv("NO_TIMING_PROXY", "1")
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    command = [sys.executable, "-c", "from tests.cases.test_worker_process_runtime import run_service_probe; run_service_probe()"]
    log_path = tmp_path / "service.log"
    with log_path.open("w") as log:
        root = process_runtime.spawn_process(command, stdout=log, stderr=log)
    successors = []
    def serving_pid(previous=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if not successors and previous is None and root.poll() is not None:
                raise AssertionError(log_path.read_text())
            try:
                with socket.socket(socket.AF_UNIX) as stream:
                    stream.settimeout(.2)
                    stream.connect(socket_path)
                    pid = int(stream.recv(100))
                if pid != previous:
                    return pid
            except (OSError, ValueError):
                pass
            time.sleep(.02)
        raise AssertionError("No new service generation became ready: " + log_path.read_text())
    try:
        first = serving_pid()
        assert first == root.pid
        os.kill(first, signal.SIGHUP)
        second = serving_pid(first)
        successors.append(second)
        root.wait(timeout=8)
        assert serving_pid() == second
        assert os.getpgid(second) == second
        os.kill(second, signal.SIGHUP)
        third = serving_pid(second)
        successors.append(third)
        deadline = time.monotonic() + 8
        while process_runtime.process_alive(second) and time.monotonic() < deadline:
            time.sleep(.02)
        assert not process_runtime.process_alive(second)
        assert process_runtime.process_alive(third) and os.getpgid(third) == third
    finally:
        for pid in reversed(successors):
            # IDs were obtained only from this private probe socket; every
            # successor is a new session, unlike the historical test fixture.
            process_runtime.terminate_process_tree(pid, force=True)
        process_runtime.terminate_process_tree(root, force=True)


def run_service_probe():
    """A private stdlib listener; all generation behavior is production code."""
    import socketserver
    inherited = service_lifecycle.inherited_descriptors()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(f"{os.getpid()}\n".encode())
    class Server(socketserver.ThreadingUnixStreamServer):
        def __init__(self):
            self.started = threading.Event()
            self.timing_proxy = None
            self.preserve_socket = bool(inherited)
            super().__init__(os.environ["PROBE_SOCKET"], Handler, bind_and_activate=not inherited)
            if inherited:
                self.socket.close()
                self.socket = socket.socket(fileno=inherited["rpc"])
        def serve_forever(self):
            self.started.set()
            super().serve_forever(poll_interval=.02)
    command = [sys.executable, "-c", "from tests.cases.test_worker_process_runtime import run_service_probe; run_service_probe()"]
    service_lifecycle.ServiceLifecycle(Server(), inherited, command=command).run()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux native failed successor cleanup")
def test_real_failed_service_successor_cleans_descendant(tmp_path):
    """继任启动失败且父已退出时，真实后代也在保留旧 listener 前清理。"""
    pid_file = tmp_path / "child.pid"
    code = ('import subprocess,sys,time; from pathlib import Path; '
            'subprocess.Popen([sys.executable,"-c",'
            '"import os,time; from pathlib import Path; '
            f'Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(60)"]); '
            f'p=Path({str(pid_file)!r}); '
            '\nwhile not p.exists(): time.sleep(.01)\n'
            'sys.exit(3)')
    with socket.socket() as listener:
        server = SimpleNamespace(socket=listener, timing_proxy=None, preserve_socket=False)
        lifecycle = service_lifecycle.ServiceLifecycle(server, command=[sys.executable, '-c', code])
        with pytest.raises(RuntimeError, match="before readiness"):
            lifecycle.replace()
        descendant = int(pid_file.read_text())
        assert not process_runtime.process_alive(descendant)
        assert not server.preserve_socket and listener.fileno() >= 0
