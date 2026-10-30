from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from management_common.service_shutdown import stop_workers
from management_common.service_socket import connect_service
from management_portal.society_service.server import LineageReadProcessPool


@pytest.mark.parametrize("finishes", [True, False])
def test_shutdown_drains_accepted_reply_but_bounds_unfinished_work(finishes):
    """停止时保留已接纳请求的回执通道；空闲读立即唤醒，超时请求按界限断开。"""
    import socketserver
    from concurrent.futures import ThreadPoolExecutor
    from management_common.service_shutdown import DrainingStreamServer

    entered = threading.Event()
    accepted = threading.Event()
    release = threading.Event()
    idle_finished = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            entered.set()
            if not self.rfile.readline():
                idle_finished.set()
                return
            accepted.set()
            release.wait(2)
            if finishes:
                self.wfile.write(b"committed\n")
                self.wfile.flush()

    class Owner(DrainingStreamServer):
        address_family = socket.AF_INET

    server = Owner(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=1) as client:
            client.sendall(b"mutation\n")
            assert accepted.wait(1)
            entered.clear()
            with socket.create_connection(server.server_address, timeout=1) as idle, ThreadPoolExecutor(1) as pool:
                assert entered.wait(1)
                started = time.monotonic()
                stopped = pool.submit(server.begin_shutdown, drain_seconds=.2)
                assert server.stopping.wait(1)
                assert idle.recv(100) == b"" and idle_finished.wait(1)
                if finishes:
                    release.set()
                    assert client.recv(100) == b"committed\n"
                else:
                    assert client.recv(100) == b""
                stopped.result(timeout=1)
                assert time.monotonic() - started < 1
                before = time.monotonic()
                server.begin_shutdown()
                assert time.monotonic() - before < .1
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def _line(process, timeout=20):
    assert select.select([process.stdout], [], [], timeout)[0], "child did not become ready"
    return process.stdout.readline().strip()


@pytest.mark.parametrize("owner", ["lineage", "resource"])
def test_owner_sigterm_closes_subscription_and_listener(tmp_path, owner):
    """服务主进程收到 SIGTERM 后关闭订阅流与监听器

    Exercise each real main(), with a lightweight owner and a waiting stream."""
    socket_path = tmp_path / "owner.sock"
    script = r'''
import socketserver, sys, threading
from types import SimpleNamespace
from management_common.service_shutdown import DrainingStreamServer, wait_for_shutdown
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
from management_portal.society_service import server as lineage
from management_portal.resources import server as resource
reported = threading.Event()
class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"watching\n"); self.wfile.flush()
        assert wait_for_shutdown(120)
        print("stream_cancelled", flush=True)
        reported.set()
class Owner(LocalServiceSocketMixin, DrainingStreamServer):
    def __init__(self, path, **kwargs):
        self.endpoint = service_endpoint(path)
        self.address_family = self.endpoint.family
        super().__init__(self.endpoint.address, Handler)
    def serve_forever(self):
        print("ready", flush=True)
        super().serve_forever(poll_interval=.01)
    def server_close(self):
        # The woken stream reports before the process exits and ends its daemon thread.
        reported.wait(2)
        super().server_close()
module = lineage if sys.argv[1] == "lineage" else resource
module.parser = lambda: SimpleNamespace(parse_args=lambda: SimpleNamespace(socket=sys.argv[2], housekeeping_seconds=300, read_workers=0))
module.SocietyServiceServer = module.ResourceServiceServer = Owner
module.get_lineage_data_store = module.get_resident_conversation_stream = lambda: None
module.main()
'''
    process = subprocess.Popen([sys.executable, "-u", "-c", script, owner, str(socket_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _line(process) == "ready"
        connection, _ = connect_service(socket_path, timeout=3, restart_wait=0)
        with connection as client:
            assert client.recv(100) == b"watching\n"
            started = time.monotonic()
            process.send_signal(signal.SIGTERM)
            assert client.recv(100) == b""
            assert process.wait(timeout=4) == 0
            assert time.monotonic() - started < 4
            assert "stream_cancelled" in process.stdout.read()
        with pytest.raises(OSError):
            connect_service(socket_path, timeout=1, restart_wait=0)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def test_shutdown_deadline_includes_executor_exit_and_preserves_durable_work(tmp_path):
    """服务停止期限包含执行器退出且持久任务可恢复"""
    import psycopg

    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"], autocommit=True) as connection:
        connection.execute("CREATE TABLE shutdown_recovery_probe (id int PRIMARY KEY, state text)")
        connection.execute("INSERT INTO shutdown_recovery_probe VALUES (1, 'pending')")
    script = r'''
import os, socketserver, sys, threading
import psycopg
from management_common.service_shutdown import DrainingStreamServer, serve_until_stopped
from management_common.service_endpoint import LocalServiceSocketMixin, service_endpoint
from management_portal.society_service.coordinator import LineageCoordinator
gate = threading.Event()
def work():
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"]) as connection:
        connection.execute("UPDATE shutdown_recovery_probe SET state='uncommitted' WHERE id=1")
        print("working", flush=True)
        gate.wait(120)
    return {}
coordinator = LineageCoordinator(resume_residents=work, resume_resident=lambda _: {}, reconcile_all=lambda: {}, reconcile_lineage=lambda _: {})
class Owner(LocalServiceSocketMixin, DrainingStreamServer):
    def __init__(self, path, handler):
        self.endpoint = service_endpoint(path)
        self.address_family = self.endpoint.family
        super().__init__(self.endpoint.address, handler)
    def serve_forever(self):
        coordinator.start()
        super().serve_forever(poll_interval=.01)
    def server_close(self):
        coordinator.stop(timeout=.05)
        assert not coordinator._submit(("resident", "new"), lambda: None)
        print("cleanup_returned", flush=True)
        super().server_close()
serve_until_stopped(Owner(sys.argv[1], socketserver.StreamRequestHandler))
'''
    process = subprocess.Popen([sys.executable, "-u", "-c", script, str(tmp_path / "deadline.sock")],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert _line(process) == "working"
        started = time.monotonic()
        process.terminate()
        assert _line(process, 2) == "cleanup_returned"
        assert process.wait(timeout=39) == 0
        assert 29 <= time.monotonic() - started < 39
        stderr = process.stderr.read()
        # A slow stop shows its threads early, then again at the deadline.
        assert stderr.index("Service shutdown still waiting") < stderr.index("Service shutdown deadline reached")
        assert "gate.wait" in stderr or "in work" in stderr
        with psycopg.connect(os.environ["PORTAL_DATABASE_URL"], autocommit=True) as connection:
            assert connection.execute("SELECT state FROM shutdown_recovery_probe").fetchone()[0] == "pending"
            # The replacement process can claim the durable item; the old
            # transaction and its row lock are gone.
            connection.execute("SET lock_timeout = '1s'")
            connection.execute("UPDATE shutdown_recovery_probe SET state='recovered' WHERE id=1")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def _blocking_read(started_path):
    Path(started_path).touch()
    time.sleep(120)


def test_read_pool_cancels_running_read_and_cannot_restart_after_close(tmp_path):
    """读取进程池关闭时取消运行中读取且不允许重启"""
    pool = LineageReadProcessPool(1)
    executor = pool._executor
    processes = list(executor._processes.values())
    marker = tmp_path / "read-started"
    executor.submit(_blocking_read, str(marker))
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "child must be executing before cancellation"
        started = time.monotonic()
        pool.close()
        assert time.monotonic() - started < 3
        assert not any(process.is_alive() for process in processes)
        pool._replace(executor)
        assert pool._executor is None
        with pytest.raises(RuntimeError, match="stopping"):
            pool.call({})
    finally:
        pool.close()


def _never_ready():
    time.sleep(120)


def _hanging_restarts(pool):
    """Make the pool's restarts spawn workers that never finish starting; return their processes."""
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing

    processes = []

    class Recording(ProcessPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            processes.extend(p for p in (self._processes or {}).values() if p not in processes)
            return future

    pool._new_executor = lambda: Recording(
        max_workers=pool.workers, mp_context=multiprocessing.get_context("spawn"), initializer=_never_ready)
    return processes


def _wait_for(processes):
    deadline = time.monotonic() + 10
    while not processes and time.monotonic() < deadline:
        time.sleep(.01)
    assert processes, "the broken pool must be restarting"
    return processes


def test_read_pool_stopping_during_restart_ends_the_new_workers(tmp_path):
    """服务停止杀掉读取进程后，正在重建的进程池放弃等待新进程，关闭立即完成并结束新进程"""
    pool = LineageReadProcessPool(1)
    original = list(pool._executor._processes.values())
    restarted = _hanging_restarts(pool)
    marker = tmp_path / "read-started"
    errors = []

    def read():
        try:
            pool._submit(_blocking_read, str(marker))
        except Exception as exc:
            errors.append(exc)

    caller = threading.Thread(target=read, daemon=True)
    caller.start()
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert marker.exists(), "child must be executing before the stop"
        # systemd's stop signal reaches the read workers along with the service.
        for process in original:
            process.kill()
        _wait_for(restarted)
        closer = threading.Thread(target=pool.close, daemon=True)
        closer.start()
        closer.join(5)
        assert not closer.is_alive(), "close waited for the restart"
        caller.join(5)
        assert not caller.is_alive() and errors, "the interrupted read must fail"
        assert not any(process.is_alive() for process in restarted)
        assert pool._executor is None
    finally:
        for process in restarted:
            process.kill()
        pool.close()


def test_read_pool_does_not_restart_once_stopping():
    """关闭开始后，等锁的读取遇到坏掉的进程池也不再启动新进程"""
    pool = LineageReadProcessPool(1)
    failed = pool._executor
    restarted = _hanging_restarts(pool)
    try:
        for process in list(failed._processes.values()):
            process.kill()
        # close() has signalled the stop and is waiting for the lock.
        pool._stopping.set()
        pool._replace(failed)
        assert not restarted
    finally:
        for process in restarted:
            process.kill()
        pool.close()


def test_read_pool_restart_gives_up_when_new_workers_do_not_start(monkeypatch):
    """新读取进程在启动期限内没有就绪时，重建失败并结束这些进程，下一次读取再重建"""
    from management_portal.society_service import server

    pool = LineageReadProcessPool(1)
    # Shorten the deadline only for the restart: a spawned worker's first
    # start (imports included) can itself take longer than half a second.
    monkeypatch.setattr(server, "READ_POOL_START_SECONDS", .5)
    failed = pool._executor
    restarted = _hanging_restarts(pool)
    try:
        for process in list(failed._processes.values()):
            process.kill()
        with pytest.raises(RuntimeError, match="did not start"):
            pool._replace(failed)
        assert restarted and not any(process.is_alive() for process in restarted)
        assert pool._executor is failed
    finally:
        for process in restarted:
            process.kill()
        pool.close()


def test_worker_stops_share_a_single_grace_period():
    """多个工作线程停止时共享同一个宽限期"""
    release = threading.Event()
    started = [threading.Event(), threading.Event()]

    def stop(index):
        started[index].set()
        release.wait(5)

    try:
        before = time.monotonic()
        stop_workers({str(i): lambda i=i: stop(i) for i in range(2)}, timeout=.1)
        assert all(event.is_set() for event in started)
        assert time.monotonic() - before < 1
    finally:
        release.set()


def test_running_workers_keep_their_lease_until_they_exit(monkeypatch):
    """仍在运行的工作线程直到退出前继续持有租约"""
    from management_portal.society_service.resident_context.worker import ResidentContextWorker
    from management_portal.society_service.script_runs import worker as scripts

    def unexpected(*args, **kwargs):
        raise AssertionError("running worker released its lease")

    thread = SimpleNamespace(join=lambda **kwargs: None, is_alive=lambda: True)
    context = SimpleNamespace(store=SimpleNamespace(release_worker_lease=unexpected))
    resident_worker = ResidentContextWorker(context)
    resident_worker._thread = thread
    resident_worker.stop(timeout=0)
    assert resident_worker._stop.is_set()
    script_worker = scripts.Worker()
    script_worker.thread = thread
    monkeypatch.setattr(scripts.store, "release_lease", unexpected)
    script_worker.stop()
    assert script_worker.stop_event.is_set()


@pytest.mark.parametrize("name", ["conversation", "definition", "runs"])
def test_watch_instructions_wake_when_the_owner_stops(monkeypatch, name):
    """所属服务停止时监听指令立即唤醒结束"""
    from management_common import service_shutdown
    from management_portal.society_service.isa import conversation, definition, runs
    from management_portal.society_service import state_store

    module = {"conversation": conversation, "definition": definition, "runs": runs}[name]
    queried = threading.Event()

    def observed(value):
        queried.set()
        return value

    monkeypatch.setattr(conversation, "protocol", lambda: SimpleNamespace(
        get_resident_events=lambda *args, **kwargs: observed({"events": [], "cursor": 0})))
    monkeypatch.setattr(definition, "lineage_state", lambda: SimpleNamespace(
        lineage_access_projection=lambda *args: {}, _ensure_visible=lambda *args: None))
    monkeypatch.setattr(definition, "lineage_revision", lambda *args: observed(0))
    monkeypatch.setattr(runs, "_observe", lambda *args, **kwargs: observed({"path": {"status": "active", "cursor": 0}}))
    monkeypatch.setattr(module, "STREAM_POLL_SECONDS", 120)
    invocation = SimpleNamespace(argument=lambda key: 0 if key == "after_cursor" else None, caller="alice")
    stopping = threading.Event()
    finished = threading.Event()

    def read():
        token = service_shutdown._request_stop.set(stopping)
        try:
            handler = {"conversation": conversation.watch_conversation_events,
                       "definition": definition.watch_lineage, "runs": runs.watch_trigger_path}[name]
            list(handler(invocation))
            finished.set()
        finally:
            service_shutdown._request_stop.reset(token)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        assert queried.wait(2)
        stopping.set()
        assert finished.wait(1)
    finally:
        stopping.set()
        thread.join(2)


def test_coordinator_stop_does_not_wait_for_work_it_cancelled_before_it_ran():
    """SocietyService 停止时不等待已取消的排队工作

    协调器停止时取消尚未开始的排队工作，并立即从待完成列表移除；停止在 1 秒内返回，之后可以重新启动，不会因为被取消的 future 永远不标记为已通知而等满超时。
    """
    from concurrent.futures import ThreadPoolExecutor
    from management_common.service_shutdown import wait_for_shutdown
    from management_portal.society_service.coordinator import LineageCoordinator

    coordinator = LineageCoordinator(
        resume_residents=dict, resume_resident=lambda _id: {},
        reconcile_all=dict, reconcile_lineage=lambda _id: {},
    )
    # One worker: the second item stays queued behind the first.
    coordinator._executor = ThreadPoolExecutor(max_workers=1)
    running = threading.Event()

    def until_stopped():
        running.set()
        wait_for_shutdown(5)

    assert coordinator._submit(("maintenance", "running"), until_stopped)
    assert coordinator._submit(("maintenance", "queued"), lambda: None)
    assert running.wait(2)
    started = time.monotonic()
    coordinator.stop(timeout=5)
    assert time.monotonic() - started < 1
    assert coordinator._work == {}
    coordinator.start()  # Nothing is left draining.
    coordinator.stop(timeout=1)


def test_full_reconcile_stops_between_lineages_when_the_coordinator_stops(monkeypatch):
    """SocietyService 停止时全量对账在 Lineage 之间收手

    LineageCoordinator 停止时，正在进行的全量对账在检查完当前 Lineage 后立即结束，停止在 1 秒内返回而不是等完整轮；没有停止时同一轮仍检查全部 Lineage。
    """
    from management_portal.society_service import lineage_program_polling as polling
    from management_portal.society_service.coordinator import LineageCoordinator

    monkeypatch.setattr(polling.business_fact_views, "canonical_view", lambda value, **_: value)
    monkeypatch.setattr(polling, "_compiled_program", lambda _lineage: {})
    from management_common.principal import OperationIdentity
    event = {"id": "shutdown-recovery", "type": "manual.triggered", "occurred_at": "2026-10-12T00:00:00Z",
             "identity": OperationIdentity.for_actor("resident_111111111111").to_dict()}
    monkeypatch.setattr(polling, "lineage_program_runtime", SimpleNamespace(read_runtime=lambda _id: {}))
    monkeypatch.setattr(polling, "lineage_program_resident_inputs", SimpleNamespace(
        pending_resident_handoffs=lambda _id: [{"event": event}]))
    lineages = [{"lineage_id": f"lineage_{index:012x}"} for index in range(40)]
    monkeypatch.setattr(polling, "recovery_lineage_ids", lambda **_: [x["lineage_id"] for x in lineages])
    delay = [.25]
    seen = []
    reconciling = threading.Event()

    def reconcile_one(lineage_id, actor, **_kwargs):
        seen.append(lineage_id)
        reconciling.set()
        time.sleep(delay[0])
        return {}

    ports = polling.ProgramPollingPorts(
        lineage_catalog_store=lambda: SimpleNamespace(get=lambda identifier: next(x for x in lineages if x["lineage_id"] == identifier)),
        lineage_runtime_actor=lambda _lineage: "alice",
        read_lineage_state=lambda lineage_id: {"lineage_id": lineage_id},
        read_resident_activity_snapshot=lambda _resident_ids: {},
        ensure_manager=lambda *_args: None,
        reconcile_lineage_system_events=reconcile_one,
        reconcile_lineage_runtime=reconcile_one,
    )
    passes = []

    def reconcile_all():
        passes.append(polling.poll_all_system_events(ports=ports))
        return passes[-1]

    coordinator = LineageCoordinator(
        resume_residents=dict, resume_resident=lambda _id: {},
        reconcile_all=reconcile_all, reconcile_lineage=lambda _id: {},
    )
    coordinator.start()
    try:
        assert reconciling.wait(5)
        started = time.monotonic()
        coordinator.stop(timeout=10)
        assert time.monotonic() - started < 1
    finally:
        coordinator.stop(timeout=1)
    assert passes and passes[0]["stopped"] is True
    assert 0 < len(seen) < len(lineages)

    # Outside a stopping owner the same pass checks every Lineage.
    delay[0] = 0
    seen.clear()
    result = polling.poll_all_system_events(ports=ports)
    assert result["stopped"] is False
    assert result["checked"] == len(seen) == len(lineages)


def test_lineage_repair_pass_stops_between_invocations_and_resumes_on_the_next_pass(monkeypatch):
    """Lineage 修复轮在停止时于两次调用之间收手并在下一轮续上

    单个 Lineage 的运行修复轮在所属服务开始停止后，处理完当前 Resident 调用即结束并标记 stopped，跳过收尾清理；下一轮不在停止中时补完剩下的调用。
    """
    from contextlib import nullcontext
    from unittest.mock import Mock
    from management_common.service_shutdown import stopping_with
    from management_portal.society_service import lineage_program_resident_inputs
    from management_portal.society_service import lineage_program_terminal_reconciliation as terminal
    from management_portal.society_service import resident_conversation_stream
    from management_portal.society_service.lineage_catalog import LineageCatalog
    from management_portal.society_service.state_store import LineageDataStore
    from tests.cases.test_lineage_catalogs import _lineage_definition, LINEAGE_ID

    graph = _lineage_definition()
    LineageCatalog(LineageDataStore()).upsert(graph)
    residents = ("resident_111111111111", "resident_222222222222")
    from management_portal.society_service import trigger_paths
    for index, resident_id in enumerate(residents):
        from management_common.principal import OperationIdentity
        event = {"id": f"event-stop-{index}", "type": "manual.triggered", "occurred_at": "2026-10-12T00:00:00Z", "payload": {},
                 "identity": OperationIdentity.for_actor(resident_id).to_dict()}
        path = trigger_paths.begin_event(LINEAGE_ID, "event-start", event, cause_kind="external", cause_id=event["id"])
        lineage_program_resident_inputs.register_resident_handoff(LINEAGE_ID, message_id=f"msg_stop_{index}", resident_id=resident_id,
                                             trigger_path_id=path, event=event, source_wire_id=f"wire_stop_{index}")
    stopping = threading.Event()

    def input_state(_resident_id, _message_id):
        stopping.set()  # The owner begins to stop while the first invocation settles.
        return {"state": "cancelled", "updated_at": "2026-10-12T00:00:00Z"}

    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(input_state=input_state))
    monkeypatch.setattr(terminal, "_compiled_program", lambda _: {})
    absorption = Mock(return_value=0)
    monkeypatch.setattr(terminal, "reconcile_terminal_absorption_edges", absorption)
    state = Mock(lineage_scope=lambda _: nullcontext(), read_lineage=lambda *a, **kw: graph,
                 ensure_manager=lambda *_: None)
    ports = terminal.ProgramTerminalPorts(state=state, resident=Mock(),
                                          directory=Mock(lineage_execution_authority=lambda _: nullcontext()),
                                          flow=Mock(drain_program_event_wire_backlog=lambda *_: {}))
    with stopping_with(stopping):
        stopped = terminal.reconcile_runtime_with_authority(LINEAGE_ID, "portal", ports=ports)
    assert stopped["stopped"] is True
    assert stopped["recovered_handoff_count"] == 1
    assert len(lineage_program_resident_inputs.pending_resident_handoffs(LINEAGE_ID)) == 1
    absorption.assert_not_called()

    # The next pass, outside a stopping owner, settles what the stop left.
    resumed = terminal.reconcile_runtime_with_authority(LINEAGE_ID, "portal", ports=ports)
    assert resumed["stopped"] is False
    assert resumed["recovered_handoff_count"] == 1
    assert lineage_program_resident_inputs.pending_resident_handoffs(LINEAGE_ID) == []
    assert absorption.called
