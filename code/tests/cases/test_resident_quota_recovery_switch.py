"""The explicit quota policy also governs bounded failed-input recovery.

:module: resident.session
:importance: P1
:aspects: functional, behavior
"""
from types import SimpleNamespace

import pytest

from management_portal.execution import account_registrations
from management_portal.society_service import (
    family_quota, resident_record_domain as records, resident_restart_domain as restart,
    resident_work_recovery as recovery,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401
from tests.cases.test_resident_mailbox_requeue import _record
from tests.cases.test_resident_work_recovery import _failed, _account


@pytest.fixture
def exhausted(team, monkeypatch):
    rid, mid = _failed(team, monkeypatch, "You've hit your weekly limit")
    record = _record(rid)
    record.update(backend_route_id="one", execution_settings={
        **record["execution_settings"], "account_selection": "auto", "auto_switch_on_quota": True})
    records.save_resident_record(record, commit_configuration=True)
    accounts = [_account("codex", "one", 0, used_up=True, automatic_paused=True),
                _account("codex", "two", 80)]
    order = ["codex:subscription_login"]
    monkeypatch.setattr(account_registrations, "resolve_role", lambda _: ({"backend_priority": order}, False))
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=lambda: {"accounts": accounts}))
    calls = []
    monkeypatch.setattr(restart, "restart_resident_session", lambda *a, **kw: calls.append(a[2]) or {"status": "accepted"})
    return SimpleNamespace(rid=rid, mid=mid, accounts=accounts, order=order, calls=calls)


def test_explicit_disabled_never_migrates_even_when_legacy_selection_is_auto(exhausted):
    f = exhausted
    record = _record(f.rid)
    record["execution_settings"].update(account_selection="auto", auto_switch_on_quota=False)
    records.save_resident_record(record, commit_configuration=True)
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 0
    assert f.calls == []


def test_failed_work_obeys_empty_default_order_and_projects_manual(exhausted):
    f = exhausted
    f.order.clear()
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 0
    projected = family_quota.quota_switch_state(_record(f.rid), execution_gate={"state": "closed", "reason": "quota_used_up"})
    assert projected["reason"] == "default_backends_unavailable"
    assert projected["message"] == family_quota.NO_DEFAULT_BACKEND
    assert f.calls == []


def test_failed_work_does_not_skip_later_default_candidates_after_two_switches(exhausted):
    f = exhausted
    f.accounts.extend([_account("codex", "three", 90), _account("codex", "four", 60)])
    record = _record(f.rid)
    recovery.set_recovery_entry(record, f.mid, switch_attempts=2, tried_accounts=["codex_two", "codex_three"])
    records.save_resident_record(record)
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 1
    payload, = f.calls
    assert payload["target_account"] == "four"
    assert payload["resume_failed_input"] is True  # only this already-failed original input
    assert payload["idempotency_key"] == f"work-recovery:{f.mid}:3"
    assert recovery.recovery_entry(_record(f.rid), f.mid).get("attempts", 0) == 0  # no business replay here


def test_context_rejection_stops_retry_and_exposes_manual_reason(exhausted, monkeypatch):
    f = exhausted
    def reject(*args, **kwargs):
        f.calls.append(args[2])
        raise ValueError("context preservation unavailable")
    monkeypatch.setattr(restart, "restart_resident_session", reject)
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 0
    record = _record(f.rid)
    assert recovery.recovery_entry(record, f.mid)["switch_reason"] == "migration_failed"
    projected = family_quota.quota_switch_state(record, execution_gate={"state": "closed", "reason": "quota_used_up"})
    assert projected["state"] == "manual_required" and projected["reason"] == "migration_failed"
    recovery.set_recovery_entry(record, f.mid, checked_at=0)
    records.save_resident_record(record)
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 0
    assert len(f.calls) == 1
    assert _record(f.rid)["backend_route_id"] == "one"


def test_switch_off_during_selection_prevents_migration(exhausted, monkeypatch):
    f = exhausted
    def accounts():
        record = _record(f.rid)
        record["execution_settings"]["auto_switch_on_quota"] = False
        records.save_resident_record(record, commit_configuration=True)
        return {"accounts": f.accounts}
    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(accounts=accounts))
    assert recovery.reconcile_failed_work([_record(f.rid)]) == 0
    assert f.calls == []


def test_previous_failed_input_does_not_override_new_message_switch_state(exhausted, monkeypatch):
    f = exhausted
    record = _record(f.rid)
    recovery.set_recovery_entry(record, f.mid, switch_reason="migration_failed")
    records.save_resident_record(record)
    from management_portal.society_service import resident_execution_work
    monkeypatch.setattr(resident_execution_work, "read", lambda _: {"user_message_id": "msg_new_input"})
    monkeypatch.setattr(resident_execution_work, "is_active", lambda _: True)
    projection = family_quota.quota_switch_state(record, execution_gate={"state": "closed", "reason": "quota_used_up"})
    assert projection["state"] == "ready" and projection["reason"] == ""


def test_bounded_recovery_exhaustion_is_manual_not_an_infinite_retry(exhausted):
    f = exhausted
    record = _record(f.rid)
    recovery.set_recovery_entry(record, f.mid, attempts=2)
    records.save_resident_record(record)
    projection = family_quota.quota_switch_state(record, execution_gate={"state": "closed", "reason": "quota_used_up"})
    assert projection["state"] == "manual_required" and projection["reason"] == "migration_failed"
    assert recovery.reconcile_failed_work([record]) == 0
    assert not f.calls
