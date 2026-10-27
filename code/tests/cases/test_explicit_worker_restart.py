"""
:importance: P1
:design: decision_075f66430606
"""
from __future__ import annotations

import signal
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from management_portal.execution.worker_manager import ExecutionWorkerManager, WorkerProcess
from scripts import portal_services


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


@pytest.mark.parametrize("generation_only", [False, True])
def test_explicit_cli_and_automatic_deployment_keep_distinct_worker_intent(monkeypatch, generation_only):
    """管理命令保留显式重启与自动部署的区别

    验证显式组件重启传递强制意图，generation-only 不强制替换同版本 Worker。

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
