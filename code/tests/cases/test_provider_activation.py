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


def test_codex_selection_does_not_prepare_recovery_or_wait_for_running_jobs(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(updates, "_active_executions", lambda _: 1)
    monkeypatch.setattr(updates, "_prepare_execution_recovery", lambda provider: calls.append(("recovery", provider)))
    monkeypatch.setattr(updates, "_switch_runtime", lambda provider, runtime, root, **kw: calls.append(("switch", root)) or True)
    assert updates._activate_verified("codex", updates.CodexRuntime(), tmp_path)
    assert calls == [("switch", tmp_path)]


def test_recovery_preparation_failure_keeps_previous_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "_active_executions", lambda _: 1)
    monkeypatch.setattr(updates, "_prepare_execution_recovery", Mock(side_effect=RuntimeError("owner unavailable")))
    switch = Mock()
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    with pytest.raises(RuntimeError):
        updates._activate_verified("claude", updates.ClaudeRuntime(), tmp_path)
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


@pytest.mark.parametrize("replace_candidate", [False, True])
def test_watcher_queues_on_postgres_without_blocking_other_updates(monkeypatch, tmp_path, replace_candidate):
    import json
    import threading
    import time
    from scripts import update_provider_runtimes as script

    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    state_file = tmp_path / "provider-updates.json"
    state_file.write_text(json.dumps({"pending": {"kimi": str(tmp_path/"a"), "claude": str(tmp_path/"b")}}))
    runtime = Mock()
    runtime.activation_link.return_value = tmp_path / "active"
    monkeypatch.setattr(updates, "runtimes", lambda: {"kimi": runtime, "claude": runtime})
    monkeypatch.setattr(updates, "_validated_candidate", lambda name, root, baseline: root)
    other_finished = threading.Event()
    switched = []

    def switch(provider, runtime, path, **kwargs):
        switched.append((provider, path.name))
        if provider == "claude":
            other_finished.set()
        return True

    monkeypatch.setattr(updates, "_switch_if_ready", switch)
    # Bound the SQL wait even if the test fails before releasing the claim.
    monkeypatch.setattr(script, "ACTIVATION_WAIT_SECONDS", 10)
    db = portal_database(schema="execution_runtime")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor, db.connect() as holder:
            assert admitting_providers(holder, ["kimi"]) == {"kimi"}
            completion = executor.submit(script._watch_pending)
            assert other_finished.wait(5)
            deadline = time.monotonic() + 5
            while not holder.execute(
                "SELECT EXISTS(SELECT FROM pg_locks l WHERE NOT granted AND locktype='advisory' "
                "AND pg_backend_pid() = ANY(pg_blocking_pids(l.pid)))"
            ).fetchone()[0]:
                assert time.monotonic() < deadline, "provider did not join the database queue"
                time.sleep(0.01)
            with updates._update_state() as locked:
                assert locked is not None
                if replace_candidate:
                    state, save = locked
                    state["pending"]["kimi"] = str(tmp_path / "new")
                    save()
            holder.commit()
            assert completion.result(timeout=5) == 0
        assert switched == [("claude", "b"), ("kimi", "new" if replace_candidate else "a")]
        assert updates.pending_provider_updates() == {}
    finally:
        db.close()


def test_queued_lock_deadline_releases_waiter():
    import time
    with activation_window("kimi") as held:
        assert held
        with activation_window("kimi", deadline=time.monotonic() + 0.1) as acquired:
            assert not acquired
    with activation_window("kimi") as acquired:
        assert acquired


def test_all_provider_switches_publish_the_same_ready_event(monkeypatch, tmp_path):
    import json
    import psycopg
    from psycopg import sql
    from management_portal.execution.provider_activation import ACTIVATION_READY_CHANNEL

    db = portal_database(schema="execution_runtime")
    monkeypatch.setattr(updates, "_active_executions", lambda _: 0)
    try:
        with psycopg.connect(db.dsn, autocommit=True) as listener:
            listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(ACTIVATION_READY_CHANNEL)))

            def switch(provider, runtime, root, **kwargs):
                changes = list(listener.notifies(timeout=2, stop_after=1))
                assert [json.loads(change.payload) for change in changes] == [
                    {"schema": db.schema, "provider": provider, "candidate": root.name}]
                return True

            monkeypatch.setattr(updates, "_switch_runtime", switch)
            for name, runtime in updates.runtimes().items():
                assert updates._activate_verified(name, runtime, tmp_path / "candidate")
    finally:
        db.close()


def test_pending_activation_wakes_on_committed_execution_change(monkeypatch, tmp_path):
    import json
    import threading
    import time
    from management_portal import kimi_account_runtime
    from management_portal.execution.scheduler_changes import SCHEDULER_CHANGE_CHANNEL

    candidate = tmp_path / "kimi-code-2.5.3-portal-deny-all-v1"
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    (tmp_path / "provider-updates.json").write_text(json.dumps({"pending": {"kimi": str(candidate)}}))
    runtime = updates.KimiRuntime()
    monkeypatch.setattr(runtime, "activation_link", lambda: tmp_path / "current")
    monkeypatch.setattr(updates, "runtimes", lambda: {"kimi": runtime})
    monkeypatch.setattr(updates, "_validated_candidate", lambda provider, root, baseline: root)
    registered = threading.Event()
    completed = threading.Event()
    calls = []

    def active(_):
        calls.append(completed.is_set())
        if len(calls) == 2:
            registered.set()  # First read under activation lock, second after LISTEN.
        return not calls[-1]

    monkeypatch.setattr(updates, "_active_executions", active)
    monkeypatch.setattr(kimi_account_runtime, "runtime_busy", lambda: False)
    monkeypatch.setattr(kimi_account_runtime, "wait_for_runtime_idle", lambda deadline: True)
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    db = portal_database(schema="execution_runtime")
    try:
        with ThreadPoolExecutor(1) as pool:
            result = pool.submit(updates.activate_pending_provider, "kimi", str(candidate), time.monotonic() + 6)
            assert registered.wait(3)
            with db.transaction() as connection:
                assert admitting_providers(connection, ["kimi"]) == {"kimi"}
                completed.set()
                connection.execute("SELECT pg_notify(%s, %s)", (SCHEDULER_CHANGE_CHANNEL, db.schema + ":execution"))
                switch.assert_not_called()
            assert result.result(timeout=3) == {"status": "activated"}
            switch.assert_called_once()
    finally:
        db.close()


def test_contended_installation_wakes_pending_provider(monkeypatch, tmp_path):
    import json
    import threading
    import time

    candidate = str(tmp_path / "new")
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    (tmp_path / "provider-updates.json").write_text(json.dumps({"pending": {"codex": candidate}}))
    monkeypatch.setattr(updates, "_validated_candidate", lambda _, root, old: root)
    waiting = threading.Event()
    original = updates.wait_for_installation_unlock

    def wait(deadline):
        waiting.set()
        return original(deadline)

    monkeypatch.setattr(updates, "wait_for_installation_unlock", wait)
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    with ThreadPoolExecutor(1) as pool:
        with updates._update_state() as locked:
            assert locked
            result = pool.submit(updates.activate_pending_provider, "codex", candidate, time.monotonic() + 5)
            assert waiting.wait(2)
            with activation_window("codex") as available:
                assert available
            switch.assert_not_called()
        assert result.result(timeout=2) == {"status": "activated"}
        switch.assert_called_once()


def test_deferred_preparation_revalidates_after_baseline_changes(monkeypatch, tmp_path):
    import json
    import time

    root, link = tmp_path / "next", tmp_path / "current"
    old, other = tmp_path / "old", tmp_path / "other"
    link.symlink_to(old)
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    runtime = updates.CodexRuntime()
    monkeypatch.setattr(runtime, "activation_link", lambda: link)
    monkeypatch.setattr(updates, "runtimes", lambda: {"codex": runtime})
    (tmp_path / "provider-updates.json").write_text(json.dumps({"pending": {"codex": str(root)}}))
    verified = []
    def validate(_, candidate, baseline):
        verified.append(baseline)
        if len(verified) == 1:
            link.unlink()
            link.symlink_to(other)
        return candidate
    monkeypatch.setattr(updates, "_validated_candidate", validate)
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    assert updates.activate_pending_provider("codex", str(root), time.monotonic() + 3) == {"status": "activated"}
    assert verified == [old, other]
    switch.assert_called_once()
