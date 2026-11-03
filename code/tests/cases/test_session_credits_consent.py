"""Human consent stays with exactly the explicitly created target Session."""
import time
from types import SimpleNamespace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.execution.account_monitor import AccountMonitor
from management_portal.execution.account_session_credits import admit_session
from management_portal.society_service import resident_backend, resident_domain, resident_restart_domain
from management_portal.society_service import resident_session_credits
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import _restart_domain, _bind
from tests.cases.test_codex_quota_reserve import scheduler, submit, claim, sample


def paid_quota(**credit_changes):
    return {**sample(100), "credits": {"authenticated": True, "observed_at": time.time(),
            "has_credits": True, "balance": 12, "stale": False, **credit_changes}}


@pytest.mark.parametrize("changes", [{"balance": 0}, {"balance": -1}, {"balance": float('nan')},
    {"has_credits": False}, {"authenticated": False}, {"stale": True},
    {"observed_at": 1}, {"observed_at": time.time() + 1000}])
def test_credit_consent_cannot_replace_real_usable_account_reading(changes):
    with pytest.raises(ValueError, match="可用 credits"):
        admit_session({"enabled": True}, paid_quota(**changes), use_credits=True)


def test_credit_consent_neither_enables_account_nor_changes_default_policy():
    with pytest.raises(ValueError, match="停用"):
        admit_session({"enabled": False}, paid_quota(), use_credits=True)
    with pytest.raises(ValueError, match="额度已用完"):
        admit_session({"enabled": True}, paid_quota())
    assert admit_session({"enabled": True}, paid_quota(), use_credits=True)["used_up"]


@pytest.mark.parametrize("actor", ["resident_dbcd3920b7cc", "service-user"])
def test_nonhuman_cannot_grant_or_replay_human_consent(monkeypatch, actor):
    monkeypatch.setattr(resident_session_credits, "get_portal_access_store",
                        lambda: SimpleNamespace(is_service_actor=lambda value: value == "service-user"))
    with pytest.raises(PermissionError, match="人类"):
        resident_domain.restart_resident_session("resident_aaaaaaaaaaaa", actor,
                                                {"credits_consent": {"confirmed": True}})


@pytest.mark.parametrize("bad", [None, {}, {"account_id": "two", "confirmed": True},
    {"account_id": "one", "confirmed": "true"}, {"account_id": "one", "confirmed": True, "always": True}])
def test_confirmation_requires_explicit_matching_scope(monkeypatch, bad):
    monkeypatch.setattr(resident_session_credits, "human_actor", lambda _: True)
    target = SimpleNamespace(provider="codex", account={"account_id": "one", "quota_protection_enabled": True})
    body = {"target_account": "one", "idempotency_key": "operation", "credits_consent": bad}
    if bad is None:
        assert resident_session_credits.validate_consent(body, "alice", target) is False
    else:
        with pytest.raises(ValueError): resident_session_credits.validate_consent(body, "alice", target)
    body["credits_consent"] = {"account_id": "one", "confirmed": True}
    body.pop("idempotency_key")
    with pytest.raises(ValueError): resident_session_credits.validate_consent(body, "alice", target)


@pytest.mark.parametrize("strategy", ["preserve", "fresh"])
def test_paid_migration_and_new_session_persist_consent_promote_and_replay(monkeypatch, isolated_portal_case, strategy):
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": paid_quota()}}}})
        body = {"target_provider": "codex", "target_account": "one", "context_strategy": strategy,
                "idempotency_key": "paid-create", "credits_consent": {"account_id": "one", "confirmed": True}}
        result = resident_domain.restart_resident_session(record["resident_id"], "alice", body)
        assert result["status"] == "accepted" and not result.get("configuration_only")
        assert result["credits_authorization"]["confirmed_by"] == "alice"
        step = operations[0]["step"]
        scope = step["quota_override"]["credits_scope"]
        assert scope == {"account_id": "one", "operation_id": result["restart_id"], "execution_id": result["execution_id"]}
        assert resident_domain.restart_resident_session(record["resident_id"], "alice", body) == result
        assert len(operations) == 1
        resident_restart_domain.resume_resident_session_restart(step)
        assert record["session_restart"]["status"] == "completed"
        assert record["quota_reserve_override"]["credits_scope"] == {
            "account_id": "one", "operation_id": result["restart_id"], "provider_session_ref": "provider_new"}
        assert record["session_restart"]["resume_failed_input"] is False
        with pytest.raises(ValueError, match="different request"):
            resident_domain.restart_resident_session(record["resident_id"], "alice", {**body, "credits_consent": None})


def test_cancelled_paid_preparation_never_grants_resident_session(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": paid_quota()}}}})
        result = resident_domain.restart_resident_session(record["resident_id"], "alice", {
            "target_provider": "codex", "target_account": "one", "context_strategy": "fresh",
            "idempotency_key": "cancel-paid", "credits_consent": {"account_id": "one", "confirmed": True}})
        record["session_restart"]["status"] = "cancelling"
        resident_restart_domain.resume_resident_session_restart(operations[0]["step"])
        assert record["session_restart"]["status"] == "cancelled"
        assert not record.get("quota_reserve_override")


def test_scheduler_preparation_consent_is_exact_account_and_execution(scheduler):
    account = scheduler.accounts.get("one").account_id
    scheduler.update_account_quota(account, paid_quota())
    scheduler.update_account_quota("two", paid_quota())
    binding = "session-restart:paid-scope"
    wrong = submit(scheduler, account="two", binding_id=binding)
    other = submit(scheduler, binding_id=binding)
    intended = submit(scheduler, binding_id=binding, operation="create")
    assert claim(scheduler)["reason"] == "quota_reserve"
    scheduler.configure_binding_quota_overrides([{"binding_id": binding, "enabled": True, "revision": 1,
        "credits_scope": {"account_id": account, "operation_id": "paid-create", "execution_id": intended.execution_id}}])
    assert scheduler.snapshot(wrong.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"
    assert scheduler.snapshot(other.execution_id)["scheduler"]["queue_reason"] == "quota_reserve"
    assert claim(scheduler)["execution"]["execution_id"] == intended.execution_id


def test_scheduler_session_consent_is_not_inherited_on_rebind_reset_or_fork(scheduler):
    account = scheduler.accounts.get("one").account_id
    scheduler.update_account_quota(account, paid_quota())
    binding = "resident:paid-session"
    store = AgentDataStore()
    _bind(store, binding, "codex", "paid-thread", backend_route_id="one")
    ref = store.provider_binding(binding)["provider_session_ref"]
    scheduler.configure_binding_quota_overrides([{"binding_id": binding, "enabled": True, "revision": 1,
        "credits_scope": {"account_id": account, "operation_id": "paid-create", "provider_session_ref": ref}}])
    states = scheduler.binding_quota_states({binding: account, "resident:fork-child": account})
    assert states[binding]["state"] == "open"
    assert states["resident:fork-child"]["state"] == "closed"
    assert scheduler.binding_quota_states({binding: scheduler.accounts.get("two").account_id})[binding]["override_enabled"] is False
    _bind(store, "session-restart:replacement", "codex", "next-thread", backend_route_id="one")
    store.replace_provider_binding(binding, "session-restart:replacement")
    assert scheduler.binding_quota_states({binding: account})[binding]["state"] == "closed"
    store.delete_provider_binding(binding)
    assert scheduler.binding_quota_states({binding: account})[binding]["state"] == "closed"


def test_credit_exhaustion_and_failed_refresh_close_scoped_override(scheduler):
    account = scheduler.accounts.get("one").account_id
    scheduler.update_account_quota(account, paid_quota())
    binding = "resident:credit-reading"
    store = AgentDataStore(); _bind(store, binding, "codex", "credit-thread", backend_route_id="one")
    scheduler.configure_binding_quota_overrides([{"binding_id": binding, "enabled": True, "revision": 1,
        "credits_scope": {"account_id": account, "operation_id": "paid-create",
                          "provider_session_ref": store.provider_binding(binding)["provider_session_ref"]}}])
    assert scheduler.binding_quota_states({binding: account})[binding]["state"] == "open"
    scheduler.update_account_quota(account, paid_quota(balance=0))
    assert scheduler.binding_quota_states({binding: account})[binding]["state"] == "closed"
    scheduler.update_account_quota(account, paid_quota())
    scheduler.update_account_quota(account, {"status": "unknown", "credits": {}})
    assert scheduler.binding_quota_states({binding: account})[binding]["state"] == "closed"


def test_monitor_publishes_authenticated_credit_readings_and_revokes_on_failure(monkeypatch, scheduler):
    account = scheduler.accounts.get("one")
    monitor = AccountMonitor(scheduler.accounts, scheduler)
    monkeypatch.setattr(monitor, "refresh_usage", lambda *_: None)
    reading = {"status": "ok", "quota": sample(100), "credits": {"has_credits": True, "balance": 10}}
    monkeypatch.setattr(scheduler.accounts, "observe", lambda *_, **__: reading)
    monitor.refresh_account(account)
    quota = scheduler.health()["capacity_partitions"][account.capacity_key]["quota_reserve"]
    assert admit_session({"enabled": True}, quota, use_credits=True)
    monkeypatch.setattr(scheduler.accounts, "observe", lambda *_, **__: (_ for _ in ()).throw(TimeoutError()))
    monitor.refresh_account(account)
    quota = scheduler.health()["capacity_partitions"][account.capacity_key]["quota_reserve"]
    with pytest.raises(ValueError, match="可用 credits"):
        admit_session({"enabled": True}, quota, use_credits=True)


def test_paid_new_session_without_source_uses_candidate_not_lazy_configuration(monkeypatch, isolated_portal_case):
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        monkeypatch.setattr(resident_backend, "session_record", lambda *_args, **_kwargs: {})
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": paid_quota()}}}})
        result = resident_domain.restart_resident_session(record["resident_id"], "alice", {
            "target_provider": "codex", "target_account": "one", "context_strategy": "fresh",
            "idempotency_key": "new-paid", "credits_consent": {"account_id": "one", "confirmed": True}})
        assert result["status"] == "accepted"
        assert result["previous_session_retained"] is False
        assert operations[0]["operation"] == "create" and operations[0]["prompt"] == ""
        assert operations[0]["step"]["quota_override"]["credits_scope"]["execution_id"] == result["execution_id"]


def test_paid_preserving_session_establishes_imported_residents_main_agent(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_record_domain

    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        record['resident_id'] = 'resident_bbbbbbbbbbbb'
        monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target',
                            lambda *_args, **_kwargs: SimpleNamespace(resident_id=record['resident_id'], record=record))
        monkeypatch.setattr(resident_backend, 'session_record', lambda *_args, **_kwargs: {})
        monkeypatch.setattr(execution_port, 'health', lambda: {'scheduler': {'capacity_partitions': {
            'codex:one': {'quota_reserve': paid_quota()}}}})
        store = AgentDataStore()
        assert not store.agent(record['resident_id'])
        body = {'target_provider': 'codex', 'target_account': 'one', 'context_strategy': 'preserve',
                'idempotency_key': 'imported-paid', 'credits_consent': {'account_id': 'one', 'confirmed': True}}
        result = resident_domain.restart_resident_session(record['resident_id'], 'alice', body)
        assert result['status'] == 'accepted'
        agent = store.agent(record['resident_id'])
        assert agent['sequence_kind'] == 'agent_trace' and agent['parent_agent_id'] is None
        seed = operations[0]['step']['request']['metadata']['fork_context_input']
        assert seed['empty_context'] is True and seed['segments'] == []
        assert operations[0]['operation'] == 'create' and operations[0]['prompt'] == ''
        assert resident_domain.restart_resident_session(record['resident_id'], 'alice', body) == result
        assert len(operations) == 1 and store.agent(record['resident_id']) == agent


def test_family_cannot_copy_session_consent_and_supervision_uses_scheduler(monkeypatch):
    from management_portal.society_service import family_quota, resident_record_domain
    facade = {"resident_id": "resident_aaaaaaaaaaaa", "quota_reserve_override": {
        "binding_id": "resident:resident_aaaaaaaaaaaa", "enabled": True, "revision": 1,
        "credits_scope": {"account_id": "codex_one", "operation_id": "one",
                          "provider_session_ref": "specific_session"}}}
    role = {"resident_id": "resident_bbbbbbbbbbbb"}
    monkeypatch.setattr(resident_record_domain, "state_lock", lambda *_: pytest.fail("must not propagate consent"))
    assert family_quota._follow_facade_quota_release(role, facade) == role
    for allowed in (False, True):
        monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(binding_quota_states=lambda values: {
            binding: {"override_enabled": allowed} for binding in values}))
        assert resident_session_credits.override_enabled(facade) is allowed


def test_paid_execution_still_stops_when_credits_run_out(scheduler):
    account = scheduler.accounts.get("one").account_id
    scheduler.update_account_quota(account, paid_quota())
    request = submit(scheduler, binding_id="session-restart:paid-running", operation="create")
    scheduler.configure_binding_quota_overrides([{"binding_id": request.session.binding_id, "enabled": True, "revision": 1,
        "credits_scope": {"account_id": account, "operation_id": "paid-create", "execution_id": request.execution_id}}])
    assert claim(scheduler)["execution"]["execution_id"] == request.execution_id
    assert not scheduler.snapshot(request.execution_id)["scheduler"]["cancel_requested"]
    scheduler.update_account_quota(account, paid_quota(balance=0))
    assert scheduler.snapshot(request.execution_id)["scheduler"]["cancel_requested"]


def test_control_sync_retry_does_not_create_another_paid_session(monkeypatch, isolated_portal_case):
    from management_portal.society_service import resident_input_queue_domain
    from management_portal.society_service.resident_lifecycle import LifecycleStepPending
    with isolated_portal_case():
        record, operations, _ = _restart_domain(monkeypatch)
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": paid_quota()}}}})
        resident_domain.restart_resident_session(record["resident_id"], "alice", {
            "target_provider": "codex", "target_account": "one", "context_strategy": "fresh",
            "idempotency_key": "sync-paid", "credits_consent": {"account_id": "one", "confirmed": True}})
        step = operations[0]["step"]
        monkeypatch.setattr(resident_input_queue_domain, "sync_resident_quota_overrides", lambda _: False)
        with pytest.raises(LifecycleStepPending): resident_restart_domain.resume_resident_session_restart(step)
        assert record["session_restart"]["status"] == "completed"
        monkeypatch.setattr(resident_input_queue_domain, "sync_resident_quota_overrides", lambda _: True)
        monkeypatch.setattr(execution_port, "service", lambda: pytest.fail("committed session must not be submitted again"))
        resident_restart_domain.resume_resident_session_restart(step)
        assert len(operations) == 1


@pytest.mark.parametrize("strategy", ["fresh", "preserve"])
def test_paid_empty_session_keeps_queued_input_for_atomic_handoff(monkeypatch, isolated_portal_case, strategy):
    """A credits-funded candidate must not reroute an unbound queued turn first."""
    from copy import deepcopy
    from management_portal.society_service import resident_execution_work
    from tests.cases.test_session_restart import ExecutionStub

    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        job = {"execution_id": "execution-original", "user_message_id": "msg_original",
               "actor": "resident_bbbbbbbbbbbb", "status": "queued",
               "submitted_at": "2026-11-03T00:00:00Z"}
        original = deepcopy(job)
        rerouted = []
        monkeypatch.setattr(resident_backend, "session_record", lambda *a, **kw: {})
        monkeypatch.setattr(resident_execution_work, "read", lambda *a: job)
        monkeypatch.setattr(resident_execution_work, "is_active", lambda value: bool(value))
        monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
            reassign_queued_route=lambda *a, **kw: rerouted.append((a, kw))))
        monkeypatch.setattr(execution_port, "health", lambda: {"scheduler": {"capacity_partitions": {
            "codex:one": {"quota_reserve": paid_quota()}}}})
        body = {"target_provider": "codex", "target_account": "one", "context_strategy": strategy,
                "idempotency_key": "paid-queued", "credits_consent": {"account_id": "one", "confirmed": True}}
        result = resident_domain.restart_resident_session(record["resident_id"], "alice", body)
        assert result["status"] == "accepted" and result["phase"] == "waiting_current_execution"
        assert result["deferred"]["payload"]["credits_consent"] == body["credits_consent"]
        assert result["deferred"]["actor"] == "alice"
        assert job == original and not rerouted and not operations
        assert saved[-1]["session_restart"]["restart_id"] == result["restart_id"]
        assert resident_domain.restart_resident_session(record["resident_id"], "alice", body) == result
