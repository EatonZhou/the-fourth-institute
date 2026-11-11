"""Explicit quota-switch intent shares Scheduler eligibility and restart handoff.

:module: resident.session
:importance: P1
:aspects: functional, behavior
"""
from types import SimpleNamespace

import pytest

from management_portal.execution import account_registrations
from management_portal.society_service import (
    family_quota as quota, resident_account_policy as policy, resident_backend,
    resident_execution_work as work, resident_input_queue_domain as intake,
    resident_record_domain as records, resident_restart_domain as restart,
    resident_conversation_stream as conversation,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_family_quota import _account


@pytest.fixture
def switching(team, monkeypatch):
    record = dict(team.source)
    record.update(backend="codex", backend_route_id="one", family={},
                  execution_settings={"account_selection": "auto", "auto_switch_on_quota": True})
    intake.commit_resident_intake_state(record, "open")
    monkeypatch.setattr(records, "find_resident", lambda _: record)
    monkeypatch.setattr(resident_backend, "session_record", lambda _: {"backend_route_id": "one", "backend_session_id": "original"})
    accounts = [_account("one", 0), _account("two", 80)]
    accounts[0]["quota_reserve"].update(used_up=True, automatic_paused=True)
    gate = {"state": "closed", "reason": "quota_used_up", "account_id": "codex_one"}
    order = ["codex:subscription_login"]
    calls = []
    monkeypatch.setattr(account_registrations, "resolve_role", lambda name: ({"account_selection": "auto", "backend_priority": order}, False))
    service = SimpleNamespace(accounts=lambda: {"accounts": accounts},
        binding_quota_states=lambda bindings: {"resident:" + record["resident_id"]: dict(gate)})
    monkeypatch.setattr(execution_port, "service", lambda: service)
    monkeypatch.setattr(restart, "restart_resident_session", lambda *args, **kwargs: calls.append(args[2]))
    return SimpleNamespace(record=record, accounts=accounts, gate=gate, order=order, calls=calls, service=service)


def test_conversation_quota_hint_follows_automatic_policy_and_current_receipt(switching):
    from management_portal.society_service import resident_protocol
    f = switching
    target = SimpleNamespace(record=f.record)
    state = {'intake_state': 'open', 'execution_gate': f.gate}
    controls = resident_protocol._conversation_controls(target, 'CASE-A', state, allowed=True)
    assert '自动切换' in controls['send_hint']
    assert controls['can_send']
    assert quota.ensure_role_reserve(f.record, f.accounts)
    receipt = quota.quota_switch_state(f.record, execution_gate=f.gate)
    # The hint carries the actual operation receipt, not an unrelated quota slogan.
    assert resident_protocol._conversation_controls(target, 'CASE-A', state, allowed=True)['send_hint'] == receipt['message']
    f.record['execution_settings']['auto_switch_on_quota'] = False
    assert '人工放行' in resident_protocol._conversation_controls(target, 'CASE-A', state, allowed=True)['send_hint']
    state['intake_state'] = 'closed'
    assert '关闭接单' in resident_protocol._conversation_controls(target, 'CASE-A', state, allowed=True)['send_hint']


def test_explicit_toggle_overrides_fixed_and_named_role_but_missing_keeps_legacy(switching, monkeypatch):
    f = switching
    f.record["execution_settings"]["backend_role"] = "special"
    names = []
    monkeypatch.setattr(account_registrations, "resolve_role", lambda name: (names.append(name) or {"backend_priority": [name], "account_selection": "auto"}, False))
    assert quota.auto_migration_enabled(f.record)
    assert quota.auto_migration_priority(f.record) == ["default"]
    assert names == ["default"]
    f.record["execution_settings"]["auto_switch_on_quota"] = False
    assert not quota.auto_migration_enabled(f.record)
    assert not quota.ensure_role_reserve(f.record)
    del f.record["execution_settings"]["auto_switch_on_quota"]
    f.record["execution_settings"]["account_selection"] = "fixed"
    assert not quota.auto_migration_enabled(f.record)  # old fixed policy
    f.record["execution_settings"]["account_selection"] = "auto"
    assert quota.auto_migration_enabled(f.record)
    assert quota.auto_migration_priority(f.record) == ["special"]


@pytest.mark.parametrize("gate", [{}, {"state": "unknown", "reason": "quota_unknown"},
    {"state": "closed", "reason": "manual"}, {"state": "closed", "reason": "account_disabled"},
    {"state": "closed", "reason": "quota_recovery_unconfirmed"}, {"state": "open", "reason": ""}])
def test_projection_never_requests_manual_choice_without_confirmed_quota(switching, monkeypatch, gate):
    f = switching
    f.record["execution_settings"]["auto_switch_on_quota"] = False
    monkeypatch.setattr(execution_port, "service", lambda: pytest.fail("read projection must not call Execution"))
    projected = quota.quota_switch_state(f.record, execution_gate=gate)
    assert projected["auto_switch_on_quota"] is False
    assert projected["reason"] != "auto_switch_disabled"
    assert projected["state"] != "manual_required"


def test_disabled_quota_requests_manual_choice_without_touching_work(switching, monkeypatch):
    f = switching
    f.record["execution_settings"]["auto_switch_on_quota"] = False
    monkeypatch.setattr(execution_port, "service", lambda: pytest.fail("disabled must not probe or migrate"))
    assert not quota.ensure_role_reserve(f.record)
    assert quota.quota_switch_state(f.record, execution_gate=f.gate)["reason"] == "auto_switch_disabled"
    assert f.calls == []


@pytest.mark.parametrize("reason", ["quota_unknown", "quota_recovery_unconfirmed", "account_disabled", "execution_unavailable"])
def test_explicit_switch_does_not_treat_unknown_or_account_fault_as_quota(switching, reason):
    f = switching
    f.gate["reason"] = reason
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert f.calls == []
    assert quota.role_migration(f.record) == {}


def test_default_order_is_strict_and_same_kind_does_not_jump_ahead(switching):
    f = switching
    other = _account("three", 50, provider="claude")
    accounts = [*f.accounts, other]
    assert policy.fallback_account(accounts, "codex", "one", [], priority=["claude:subscription_login", "codex:subscription_login"], priority_only=True) == other
    assert policy.fallback_account(accounts, "codex", "one", [], priority=["claude:subscription_login"], priority_only=True) == other
    assert policy.fallback_account(accounts, "codex", "one", [], priority=[], priority_only=True) == {}
    assert policy.fallback_account(accounts, "codex", "one", [], priority=[]) == f.accounts[1]  # legacy


def test_pinned_quota_restart_preserves_model_and_reuses_operation(switching):
    f = switching
    f.record["execution_settings"].update(account_selection="fixed", model="default", quota_switch_strategy="same_provider_model")
    f.accounts[1]["enabled"] = True
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert f.calls[0]["target_provider"] == "codex"
    assert f.calls[0]["target_account"] == "two"
    assert f.calls[0]["context_strategy"] == "preserve"
    assert f.calls[0]["resume_failed_input"] is False
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert len(f.calls) == 1


def test_empty_default_order_has_exact_manual_hint_and_changed_state_event(switching):
    f = switching
    f.order.clear()
    repository = conversation.get_repository()
    before = repository.events_after(f.record["resident_id"], 0)
    assert quota.ensure_role_reserve(f.record, f.accounts)
    result = quota.quota_switch_state(f.record, execution_gate=f.gate)
    assert result["state"] == "manual_required"
    assert result["reason"] == "default_backends_unavailable"
    assert result["message"] == "当前没有默认可切换的ai后端, 请手动选择"
    assert f.calls == []
    after = repository.events_after(f.record["resident_id"], 0)
    assert after["cursor"] > before["cursor"]
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert repository.events_after(f.record["resident_id"], 0) == after  # no poll-driven duplicate event


def test_switch_uses_preserve_and_wait_without_replaying_failed_input(switching):
    f = switching
    f.order.extend([f"unused:api_profile:{i}" for i in range(30)])
    assert quota.ensure_role_reserve(f.record, f.accounts)
    payload, = f.calls
    assert payload["target_account"] == "two"
    assert payload["context_strategy"] == "preserve"
    assert payload["active_execution_policy"] == "wait"
    assert payload["resume_failed_input"] is False
    assert len(payload["idempotency_key"]) <= 240
    assert f.record["backend_route_id"] == "one"
    assert quota.quota_switch_state(f.record, execution_gate=f.gate)["state"] == "switching"
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert len(f.calls) == 1


def test_confirmed_gate_can_switch_without_numeric_quota_percentage(switching):
    f = switching
    f.accounts[0]["quota_reserve"] = {"used_up": True, "automatic_paused": True}
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert len(f.calls) == 1


def test_transient_retry_keeps_operation_and_definitive_failure_keeps_input(switching, monkeypatch):
    f = switching
    now = [1000.0]
    monkeypatch.setattr(quota.time, "time", lambda: now[0])
    def unavailable(*args, **kwargs):
        f.calls.append(args[2])
        raise RuntimeError("Execution RPC temporarily unavailable")
    monkeypatch.setattr(restart, "restart_resident_session", unavailable)
    with pytest.raises(RuntimeError):
        quota.ensure_role_reserve(f.record, f.accounts)
    pending = quota.role_migration(f.record)["pending_restart"]
    now[0] += 61
    with pytest.raises(RuntimeError):
        quota.ensure_role_reserve(f.record, f.accounts)
    assert f.calls == [pending, pending]
    assert quota.role_migration(f.record)["attempts"] == 1
    def rejected(*args, **kwargs):
        raise ValueError("context cannot be preserved")
    monkeypatch.setattr(restart, "restart_resident_session", rejected)
    now[0] += 61
    assert quota.ensure_role_reserve(f.record, f.accounts)
    result = quota.quota_switch_state(f.record, execution_gate=f.gate)
    assert result["state"] == "manual_required" and result["reason"] == "migration_failed"
    assert f.record["backend_route_id"] == "one"
    now[0] += 61
    monkeypatch.setattr(restart, "restart_resident_session", lambda *a, **k: pytest.fail("definitive failure must not repeat"))
    assert quota.ensure_role_reserve(f.record, f.accounts)


def test_failed_or_cancelled_handoff_projects_manual_without_replaying(switching):
    f = switching
    assert quota.ensure_role_reserve(f.record, f.accounts)
    f.record["session_restart"] = {"request_id": f.calls[0]["idempotency_key"], "status": "failed"}
    assert quota.quota_switch_state(f.record, execution_gate=f.gate)["reason"] == "migration_failed"
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert len(f.calls) == 1


def test_old_two_switch_limit_does_not_skip_remaining_default_candidate(switching, monkeypatch):
    f = switching
    assert quota.ensure_role_reserve(f.record, f.accounts)
    with quota.get_lineage_data_store().family_quota_migrations.transaction(f.record["lineage_id"], f.record["resident_id"]) as state:
        state.update(attempts=2, tried_accounts=["codex_two", "codex_three"], checked_at=0)
        state.pop("request_id", None)
    f.accounts.extend([_account("three", 90), _account("four", 40)])
    assert quota.ensure_role_reserve(f.record, f.accounts)
    assert f.calls[-1]["target_account"] == "four"


def test_submitted_quota_wait_is_offered_to_formal_handoff_but_running_or_stop_is_not(switching, monkeypatch):
    f = switching
    job = {"execution_id": "execution-" + "a" * 24, "user_message_id": "msg_" + "a" * 32,
           "submitted_at": "2026-11-05T01:00:00Z", "status": "queued"}
    monkeypatch.setattr(work, "read", lambda _: job)
    monkeypatch.setattr(work, "is_active", lambda _: True)
    monkeypatch.setattr(work, "can_preserve_for_session_restart", lambda _: False)
    monkeypatch.setattr(work, "execution_snapshot", lambda _: {"status": "queued"})
    assert quota.reconcile_role_reserves([f.record]) == 1
    assert f.calls[0]["active_execution_policy"] == "wait"
    assert job["execution_id"] == "execution-" + "a" * 24
    job["status"] = "running"
    assert quota.reconcile_role_reserves([f.record]) == 0
    job.update(status="queued", stop_requested_at="now")
    assert quota.reconcile_role_reserves([f.record]) == 0
    assert len(f.calls) == 1
