"""Immediate runtime replacement, claim races and automatic lock release.

:module: execution.service
"""
from unittest.mock import Mock
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_common.portal_database import portal_database
from management_portal.execution import provider_updates as updates
from management_portal.execution.provider_activation import activation_window, admitting_providers
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler


def _peer(call):
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(call).result(timeout=10)


def test_running_execution_is_preserved_then_runtime_is_replaced_without_wait(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updates, "_active_executions", lambda _: 1)
    monkeypatch.setattr(updates, "_prepare_execution_recovery", lambda provider: calls.append(("recovery", provider)))
    monkeypatch.setattr(updates, "_switch_runtime", lambda provider, runtime, root, **kw: calls.append(("switch", root)) or True)
    assert updates._activate("codex", updates.CodexRuntime(), tmp_path)
    assert calls == [("recovery", "codex"), ("switch", tmp_path)]


def test_recovery_preparation_failure_keeps_previous_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "_active_executions", lambda _: 1)
    monkeypatch.setattr(updates, "_prepare_execution_recovery", Mock(side_effect=RuntimeError("owner unavailable")))
    switch = Mock()
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    with pytest.raises(RuntimeError):
        updates._activate("codex", updates.CodexRuntime(), tmp_path)
    switch.assert_not_called()
    # The failed updater cannot leave dispatch paused.
    db = portal_database(schema="execution_runtime")
    try:
        with db.transaction() as connection:
            assert admitting_providers(connection, ["codex"]) == {"codex"}
    finally:
        db.close()


def test_claim_and_update_windows_are_exclusive_and_release_after_failure():
    db = portal_database(schema="execution_runtime")
    try:
        with db.connect() as connection:
            assert admitting_providers(connection, ["codex"]) == {"codex"}
            def acquire():
                with activation_window("codex") as reserved:
                    return reserved
            assert not _peer(acquire)
            connection.commit()
        with pytest.raises(RuntimeError):
            with activation_window("codex") as reserved:
                assert reserved
                def admit():
                    with db.transaction() as connection:
                        return admitting_providers(connection, ["codex", "claude"])
                assert _peer(admit) == {"claude"}
                raise RuntimeError("updater failed")
        with db.transaction() as connection:
            assert admitting_providers(connection, ["codex", "claude"]) == {"codex", "claude"}
    finally:
        db.close()


def test_recovery_capture_handles_empty_legacy_checkpoints_and_retained_terminal_rows(monkeypatch):
    database = portal_database(schema="legacy_runtime_activation")
    with database.transaction() as connection:
        connection.execute("CREATE TABLE executions(status TEXT,request_json TEXT,checkpoint_json TEXT)")
        connection.execute("INSERT INTO executions VALUES ('queued',%s,''),('running',%s,'{}'),('completed','','')",
                           ('{"provider":"codex"}', '{"provider":"codex"}'))
    monkeypatch.setattr(updates, "portal_database", lambda **kw: database)
    assert updates._active_executions("codex") == 1


def test_queued_work_waits_only_during_switch_and_does_not_prevent_activation():
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    scheduler.register_worker(worker_id="worker", build_id="test", capabilities=["agent_session:codex"])
    request = ExecutionRequest.new(actor="sample_admin", prompt="continue the task", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="persistent", binding_id="resident:queued-update", backend_route_id="zero"))
    scheduler.submit(request)
    try:
        assert updates._active_executions("codex") == 0
        with activation_window("codex") as reserved:
            assert reserved
            assert _peer(lambda: scheduler.claim(worker_id="worker", capabilities=["agent_session:codex"])) == {
                "claimed": False, "reason": "runtime_update"}
            assert _peer(lambda: scheduler.snapshot(request.execution_id))["scheduler"]["queue_reason"] == "runtime_update"
        claim = scheduler.claim(worker_id="worker", capabilities=["agent_session:codex"])
        assert claim["claimed"] and claim["execution"]["execution_id"] == request.execution_id
    finally:
        scheduler.database.close()


@pytest.mark.parametrize("pending", [False, True])
def test_runtime_status_reports_real_pending_work_without_permanent_blockers(monkeypatch, tmp_path, pending):
    monkeypatch.setattr(updates.CodexRuntime, "installed", lambda _: ("0.167.2",))
    monkeypatch.setattr(updates, "runtimes", lambda: {"codex": updates.CodexRuntime()})
    monkeypatch.setattr(updates, "_read_state", lambda: {
        "pending": {"codex": str(tmp_path)} if pending else {},
        "observations": {"codex": {"available_version": "0.169.0"}}})
    result = updates.provider_runtime_status()["codex"]
    assert result["pending_switch"] is pending
    assert result["installed_version"] == "0.167.2"
    assert "activation_blocker" not in result
