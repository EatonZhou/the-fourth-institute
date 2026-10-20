"""
:importance: P1
:design: decision_075f66430606
"""
from __future__ import annotations

import signal
import subprocess
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from management_portal.execution.worker_manager import ExecutionWorkerManager, WorkerProcess
from scripts import portal_dev_source_reloader, portal_services


@pytest.fixture(autouse=True)
def isolated_deployment_snapshot(monkeypatch, tmp_path):
    monkeypatch.setattr(portal_dev_source_reloader, 'DEPLOYED_SOURCE_STATE', tmp_path / 'deployed.json')
    monkeypatch.setattr(portal_dev_source_reloader, 'source_state', lambda: ())


@pytest.mark.parametrize("failure", [None, "ready", "activate"])
def test_explicit_same_build_rollout_preserves_ready_activate_drain_order(
    monkeypatch, tmp_path, failure,
):
    """显式同版本重启保持就绪、接管和排空顺序

    验证显式重启替换同版本 Worker，旧 Worker 在新 Worker 就绪并接管后排空；失败保持旧实例。

    :design-point:
    """
    manager = ExecutionWorkerManager()
    old = WorkerProcess(Mock(), "old", "build-a", tmp_path / "old.json", 1)
    new = WorkerProcess(Mock(), "new", "build-a", tmp_path / "new.json", 2)
    old.process.poll.return_value = None
    manager._current = old
    events = []
    monkeypatch.setattr(manager, "_build_id", lambda: "build-a")
    monkeypatch.setattr(manager, "_spawn", lambda: events.append("spawn") or new)

    def step(name):
        def call(*args):
            events.append(name)
            if failure == name:
                raise RuntimeError(name)
        return call

    monkeypatch.setattr(manager, "_wait_ready", step("ready"))
    monkeypatch.setattr(manager, "_activate_replacement", step("activate"))
    monkeypatch.setattr(manager, "_mark_draining", step("drain"))
    monkeypatch.setattr(manager, "_cleanup_offline_registrations", lambda: None)
    monkeypatch.setattr(manager, "_discard_failed_replacement", step("discard"))
    assert manager._roll_out() is False
    assert events == []
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            manager._roll_out(force=True)
        assert manager._current is old
        assert manager._draining == []
        assert "drain" not in events
        assert events[-1] == "discard"
    else:
        assert manager._roll_out(force=True) is True
        assert events == ["spawn", "ready", "activate", "drain"]
        assert manager._current is new
        assert manager._draining == [old]


@pytest.mark.parametrize("explicit", [False, True])
def test_manager_dispatches_explicit_signal_without_forcing_reload(monkeypatch, tmp_path, explicit):
    """Worker 区分显式重启信号与普通版本加载

    验证显式信号进入强制滚动路径，普通 reload 仍保留同版本 Worker，退出后恢复原处理器。

    :design-point:
    """
    manager = ExecutionWorkerManager()
    worker = WorkerProcess(Mock(), "old", "build-a", tmp_path / "old.json", 1)
    worker.process.poll.return_value = None
    manager._current = worker
    original_handlers = {sig: signal.SIG_DFL for sig in (
        signal.SIGINT, signal.SIGTERM, signal.SIGUSR1, signal.SIGHUP,
    )}
    handlers = dict(original_handlers)

    def register_handler(sig, handler):
        previous = handlers[sig]
        handlers[sig] = handler
        return previous

    monkeypatch.setattr(signal, "signal", register_handler)
    monkeypatch.setattr(manager, "_manager_source_changed", lambda: False)
    monkeypatch.setattr(manager, "_reap", lambda: None)
    monkeypatch.setattr(manager, "_write_health_safely", lambda: True)
    monkeypatch.setattr(manager._reload, "wait", lambda timeout: None)
    calls = []

    def roll_out(*, force=False):
        assert handlers[signal.SIGUSR1] == manager.request_worker_restart
        assert handlers[signal.SIGHUP] == manager.request_reload
        calls.append(force)
        manager._current = None
        manager._stopping.set()
        return True

    monkeypatch.setattr(manager, "_roll_out", roll_out)
    if explicit:
        manager.request_worker_restart(signal.SIGUSR1, None)
    else:
        manager.request_reload(signal.SIGHUP, None)
    assert manager.run() == 0
    assert calls == [explicit]
    assert not manager._explicit_restart.is_set()
    assert handlers == original_handlers




def test_explicit_restart_waits_for_changed_worker_instead_of_old_healthy_status(monkeypatch):
    """显式重启等待新的健康 Worker 身份

    验证旧 Worker 健康不代表重启完成；等待新身份就绪，未变化则超时失败。

    :design-point:
    """
    old = {"ok": True, "runtime_ok": True, "execution_worker": {"ok": True, "runtime_ok": True, "current": {"worker_id": "old"}}}
    new = {"ok": True, "runtime_ok": True, "execution_worker": {"ok": True, "runtime_ok": True, "current": {"worker_id": "new"}}}
    snapshots = iter([old, new])
    monkeypatch.setattr(portal_services, "instance_status", lambda *args, **kwargs: next(snapshots))
    monkeypatch.setattr(portal_services, "_assert_instance", lambda *args, **kwargs: None)
    sleeps = []
    monkeypatch.setattr(portal_services.time, "sleep", lambda seconds: sleeps.append(seconds))
    assert portal_services._wait_healthy("dev", worker_id_before_restart="old") is new
    assert sleeps == [0.5]
    monkeypatch.setattr(portal_services, "instance_status", lambda *args, **kwargs: old)
    ticks = iter([0, 0, 0.1, 0.3])
    monkeypatch.setattr(portal_services.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="has not activated a new Worker"):
        portal_services._wait_healthy("dev", worker_id_before_restart="old", timeout_seconds=0.2)


@pytest.mark.parametrize("generation_only", [False, True])
def test_explicit_cli_and_automatic_deployment_keep_distinct_worker_intent(monkeypatch, generation_only):
    """管理命令保留显式重启与自动部署的区别

    验证显式组件重启传递强制意图，generation-only 和普通发布不强制替换同版本 Worker。

    :design-point:
    """
    captured = []
    monkeypatch.setattr(portal_services, "operation_lock", lambda *args: nullcontext())
    monkeypatch.setattr(portal_services, "restart", lambda *args, **kwargs: captured.append(kwargs) or {"ok": True})
    args = ["restart", "dev", "--component", "execution-worker"]
    if generation_only:
        args.append("--generation-only")
    assert portal_services.main(args) == 0
    assert captured[0]["force_worker_restart"] is (not generation_only)
    commands = []
    monkeypatch.setattr(portal_dev_source_reloader.subprocess, "run", lambda command, **kwargs: commands.append(command) or subprocess.CompletedProcess(command, 0, "", ""))
    assert portal_dev_source_reloader.restart_dev({"execution-worker"}, force_worker_restart=not generation_only) == "ok"
    assert ("--generation-only" in commands[0]) is generation_only


@pytest.mark.parametrize("requested", [["execution-worker"], []])
def test_source_coordinator_forces_only_explicit_worker_component(monkeypatch, requested, tmp_path):
    """源码协调器只强制显式指定的 Worker 重启

    验证重启队列中的 Worker 请求携带强制意图，普通部署和其他组件不携带。

    :design-point:
    """
    write_states = portal_dev_source_reloader.write_deployed_source_states
    monkeypatch.setattr(portal_dev_source_reloader, "write_deployed_source_states",
                        lambda states: write_states(states, path=tmp_path / "deployed.json"))
    baseline = portal_dev_source_reloader.component_source_states((("management_portal/execution/worker.py", 1, 1),))
    monkeypatch.setattr(portal_dev_source_reloader, "source_state", lambda: (("management_portal/execution/worker.py", 2, 1),))
    monkeypatch.setattr(portal_dev_source_reloader, "read_restart_request", lambda: {"request_id": "r", "revision": 1, "components": requested})
    calls = []
    monkeypatch.setattr(portal_dev_source_reloader, "restart_dev", lambda components, **kwargs: calls.append((components, kwargs)) or "ok")
    acks = []
    monkeypatch.setattr(portal_dev_source_reloader, "acknowledge_restart_request", lambda *args: acks.append(args))
    assert portal_dev_source_reloader.process_restart_request(baseline, "")[2] == "ok"
    assert "execution-worker" in calls[0][0]
    assert calls[0][1] == ({"force_worker_restart": True} if requested else {})
    assert acks == [("r", 1)]
