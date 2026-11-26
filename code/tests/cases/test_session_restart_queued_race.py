"""Work arriving before the configuration commit must retain its original route."""
import copy
from types import SimpleNamespace

import pytest

from tests.cases.test_session_restart import _restart_domain
from management_portal.society_service import (
    resident_domain, resident_backend, resident_execution_work, resident_fork_context,
    resident_restart_domain, resident_deferred_restart,
)
from management_portal.society_service.domain_ports import execution_port


@pytest.mark.parametrize("race", ["queued", "submitted", "admitted"])
def test_message_arrives_before_session_configuration_commit(monkeypatch, race):
    is_active = resident_execution_work.is_active
    record, operations, _ = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_execution_work, "is_active", is_active)
    before_binding = copy.deepcopy(resident_backend.session_record(record))
    job = {"execution_id": "execution-queued", "user_message_id": "msg_queued"}
    if race == "submitted":
        job["submitted_at"] = "2026-11-24T01:00:00Z"
    elif race == "admitted":
        job["intake_admitted_at"] = "2026-11-24T01:00:00Z"
    before = copy.deepcopy(job)
    material = resident_fork_context.require_snapshot
    def arrives(*args, **kwargs):
        monkeypatch.setattr(resident_execution_work, "read", lambda *_: copy.deepcopy(job))
        return material(*args, **kwargs)
    monkeypatch.setattr(resident_fork_context, "require_snapshot", arrives)
    with pytest.raises(ValueError, match="已开始新的工作"):
        resident_domain.restart_resident_session(
            record["resident_id"], "alice",
            {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "preserve"},
        )
    assert job == before
    assert record["backend"] == "codex"
    assert resident_backend.session_record(record) == before_binding
    assert not operations


@pytest.mark.parametrize("outcome", ["running", "queued", "permission", "unknown"])
def test_wait_uses_atomic_account_reassignment_result(monkeypatch, outcome):
    """A claim wins over a wait-mode route change without masking other failures."""
    job = {"execution_id": "execution-claimed", "user_message_id": "msg_queued",
           "actor": "alice", "submitted_at": "2026-11-26T00:00:00Z", "status": "queued"}
    monkeypatch.setattr(resident_execution_work, "read", lambda _: job)
    calls = []

    def reassign(execution_id, **options):
        calls.append(("reassign", execution_id))
        if outcome == "permission":
            raise PermissionError("denied")
        if outcome == "unknown":
            return {}
        if outcome == "running":
            return {"execution_id": execution_id, "reassigned": False, "reason": "not_unstarted"}
        return {"execution_id": execution_id, "status": "queued"}

    monkeypatch.setattr(execution_port, "service", lambda: SimpleNamespace(reassign_queued_route=reassign))
    monkeypatch.setattr(resident_deferred_restart, "schedule",
                        lambda *args, **kwargs: calls.append(("wait", None)) or {"status": "accepted"})

    def settle():
        return resident_restart_domain._settle_active_work(
            {}, "resident_aaaaaaaaaaaa", "alice", {}, {"has_session": False},
            SimpleNamespace(source_provider="codex", provider="codex", route="one"),
            {"context_strategy": "preserve"},
            active_execution_policy="wait", portal_reason="", prepare_session=False,
        )

    if outcome in {"permission", "unknown"}:
        with pytest.raises(PermissionError if outcome == "permission" else RuntimeError):
            settle()
    elif outcome == "running":
        assert settle().scheduled == {"status": "accepted"}
        assert calls[-1] == ("wait", None)
    else:
        assert settle().reassigned_execution_id == job["execution_id"]
    assert [kind for kind, _ in calls] == (["reassign", "wait"] if outcome == "running" else ["reassign"])
