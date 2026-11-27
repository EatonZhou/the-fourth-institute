"""Idle runtime replacement, claim races and automatic lock release.

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


@pytest.mark.parametrize("provider", ["antigravity", "codex", "claude", "kimi", "dsh", "opencode"])
def test_only_an_idle_provider_can_switch(monkeypatch, tmp_path, provider):
    from management_portal import kimi_account_runtime
    monkeypatch.setattr(kimi_account_runtime, "runtime_busy", lambda: False)
    active = {provider: 2, "another": 3}
    monkeypatch.setattr(updates, "_active_executions", active.get)
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    runtime = updates.runtimes()[provider]
    for remaining in (2, 1):
        active[provider] = remaining
        assert not updates._activate_verified(provider, runtime, tmp_path)
    switch.assert_not_called()
    active[provider] = 0
    assert updates._activate_verified(provider, runtime, tmp_path)
    switch.assert_called_once()


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
        return True

    def activate(provider, path, deadline):
        result = updates.activate_pending_provider(provider, path, deadline)
        if provider == "claude" and result["status"] == "activated":
            other_finished.set()
        return result

    monkeypatch.setattr(script, "activate_pending_provider", activate)
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

    from management_portal import kimi_account_runtime
    monkeypatch.setattr(kimi_account_runtime, "runtime_busy", lambda: False)
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
                connection.execute("SELECT pg_notify(%s, %s)", (SCHEDULER_CHANGE_CHANNEL, db.schema + ":execution,claim,control"))
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


def test_new_candidate_wakes_listener_while_another_provider_waits(monkeypatch, tmp_path):
    import json
    import threading
    from scripts import update_provider_runtimes as script

    state_path = tmp_path / "provider-updates.json"
    state_path.write_text(json.dumps({"pending": {"kimi": "old"}}))
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    monkeypatch.setattr(script, "ACTIVATION_WAIT_SECONDS", 120)
    sleeping, release, second = (threading.Event() for _ in range(3))
    waits = []
    real_select = script.select.select

    def wait(readers, writers, errors, timeout):
        waits.append(timeout)
        sleeping.set()
        return real_select(readers, writers, errors, timeout)

    from types import SimpleNamespace
    monkeypatch.setattr(script, "select", SimpleNamespace(select=wait))

    def validate(provider, path, baseline):
        if provider == "kimi":
            assert release.wait(5)
        else:
            second.set()
        return path

    monkeypatch.setattr(updates, "_validated_candidate", validate)
    monkeypatch.setattr(updates, "_switch_if_ready", lambda *args, **kwargs: True)
    with ThreadPoolExecutor(1) as pool:
        job = pool.submit(script._watch_pending)
        try:
            assert sleeping.wait(3)
            with updates._update_state() as (state, save):
                state["pending"]["claude"] = "added"
                save()
            assert second.wait(3), "new provider should start while Kimi is still occupied"
        finally:
            release.set()
        assert job.result(timeout=3) == 0
    assert min(waits) > 110  # Sleeps until the job deadline, even with one active candidate.


def test_activation_failure_waits_for_another_update_request(monkeypatch):
    from scripts import update_provider_runtimes as script

    monkeypatch.setattr(script, "pending_provider_updates", lambda: {"kimi": "candidate"})
    activate = Mock(return_value={"status": "failed", "phase": "service_activation"})
    monkeypatch.setattr(script, "activate_pending_provider", activate)
    assert script._watch_pending() == 1
    activate.assert_called_once()


def test_committed_release_handles_worker_loss_and_rollback():
    import psycopg
    from psycopg import sql
    from management_portal.execution.scheduler_changes import SCHEDULER_CHANGE_CHANNEL

    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)
    db = scheduler.database
    request = ExecutionRequest.new(actor="sample_admin", prompt="wait", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="persistent", binding_id="resident:release"))
    scheduler.submit(request)
    try:
        with psycopg.connect(db.dsn, autocommit=True) as listener, db.connect() as writer:
            listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(SCHEDULER_CHANGE_CHANNEL)))
            def update(status, checkpoint):
                writer.execute("UPDATE executions SET status=%s,checkpoint_json=%s WHERE execution_id=%s",
                               (status, checkpoint, request.execution_id))
            update("claimed", "{}")
            writer.commit()
            update("queued", '{"turn_id":"detached"}')
            writer.commit()
            assert updates._active_executions("codex") == 1
            assert list(listener.notifies(timeout=.05))  # Drain the claim and requeue notices.
            update("completed", "{}")
            writer.rollback()
            assert not list(listener.notifies(timeout=0.05))
            update("completed", "{}")
            writer.commit()
            assert [event.payload for event in listener.notifies(timeout=2, stop_after=1)] == [db.schema + ":execution,claim,control"]
            assert not updates._active_executions("codex")
    finally:
        db.close()


def test_missed_release_reconciles_and_rechecks_new_work_under_lock(monkeypatch, tmp_path):
    import json
    import time

    path = str(tmp_path / "new")
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    (tmp_path / "provider-updates.json").write_text(json.dumps({"pending": {"codex": path}}))
    monkeypatch.setattr(updates, "_validated_candidate", lambda name, root, baseline: root)
    monkeypatch.setattr(updates, "RECONCILE_SECONDS", .05)
    # First completion loses its notification; new work wins the admission
    # race before the update reacquires the lock. Only the second idle is safe.
    samples = iter([1, 1, 0, 1, 0, 0])
    monkeypatch.setattr(updates, "_active_executions", lambda name: next(samples))
    switch = Mock(return_value=True)
    monkeypatch.setattr(updates, "_switch_runtime", switch)
    assert updates.activate_pending_provider("codex", path, time.monotonic() + 4) == {"status": "activated"}
    switch.assert_called_once()

def test_listener_reconnect_samples_idle_without_a_second_release(monkeypatch):
    import time
    import psycopg
    from management_portal.execution.provider_activation import wait_for_execution_idle

    snapshot = Mock(side_effect=[psycopg.OperationalError("connection lost"), 0])
    assert wait_for_execution_idle("claude", snapshot, time.monotonic() + 5)
    assert snapshot.call_count == 2


def test_execution_maintenance_rearms_only_an_actionable_local_candidate(monkeypatch, tmp_path):
    import json
    from scripts import update_provider_runtimes as script
    from types import SimpleNamespace
    monkeypatch.setattr(updates, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(updates, "STATE_ROOT", tmp_path)
    start = Mock()
    monkeypatch.setattr(script, "_start_job", start)
    pending = {"codex": "/candidate"}
    for state in ({}, {"pending": pending, "activation_failures": pending}, {"pending": pending}):
        (tmp_path / "provider-updates.json").write_text(json.dumps(state))
        updates.resume_pending_activation()
    start.assert_called_once_with("--activate-pending")
