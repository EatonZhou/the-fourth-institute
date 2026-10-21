"""Account changes preserve the queued input's principal and audit the operator."""
from copy import deepcopy

import pytest

from management_portal.execution.accounts import ExecutionAccount, ExecutionAccounts
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.society_service import resident_backend, resident_domain, resident_execution_work
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import ExecutionStub, _restart_domain


class Accounts:
    provider = "codex"

    def accounts(self):
        return tuple(
            ExecutionAccount(f"codex_{route}", self.provider, route, f"codex:{route}", 1, "reserve")
            for route in ("zero", "one")
        )

    def resolve(self, route_id, options, *, bound=False):
        return next(account for account in self.accounts() if account.route_id == route_id)


@pytest.mark.parametrize("input_actor", ["alice", "resident_bbbbbbbbbbbb"])
def test_unbound_restart_reroutes_original_principal_execution(monkeypatch, input_actor):
    """管理员切换账号保留其他成员投递的输入身份，并记录实际操作人。"""
    record, operations, saved = _restart_domain(monkeypatch)
    scheduler = Scheduler(accounts=ExecutionAccounts((Accounts(),)))
    request = ExecutionRequest.new(
        actor=input_actor, agent_id=record["resident_id"], prompt="pending input",
        service_type="agent_session", provider="codex", operation="turn",
        session=SessionOptions(mode="persistent", binding_id=f"resident:{record['resident_id']}",
                               backend_route_id="zero"),
    )
    scheduler.submit(request)
    job = {"execution_id": request.execution_id, "actor": input_actor,
           "user_message_id": "msg_original", "submitted_at": "2026-10-19T00:00:00Z"}
    original_job = deepcopy(job)
    monkeypatch.setattr(resident_backend, "session_record", lambda *args, **kwargs: {})
    monkeypatch.setattr(resident_execution_work, "read", lambda *args: job)
    monkeypatch.setattr(resident_execution_work, "is_active", lambda value: bool(value))
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
        reassign_queued_route=scheduler.reassign_queued_route,
    ))

    result = resident_domain.restart_resident_session(record["resident_id"], "alice", {
        "target_account": "one", "active_execution_policy": "wait",
    })

    assert result["status"] == "completed"
    assert result["configuration_only"] is True
    assert result["active_execution_reassigned"] is True
    assert result["execution_id"] == request.execution_id
    assert result["configured_by"] == "alice"
    assert job == original_job
    snapshot = scheduler.snapshot(request.execution_id)
    assert snapshot["status"] == "queued"
    assert snapshot["request"]["actor"] == input_actor
    assert snapshot["request"]["agent_id"] == record["resident_id"]
    assert snapshot["request"]["session"]["backend_route_id"] == "one"
    route_event = next(event for event in snapshot["events"] if event["type"] == "route_changed")
    assert route_event["details"]["actor"] == "alice"
    assert route_event["details"]["request_actor"] == input_actor
    assert operations == [] and saved[-1]["backend_route_id"] == "one"


def test_unbound_restart_rejects_missing_input_principal(monkeypatch):
    """缺失原输入身份时不得借用当前操作人改派执行。"""
    record, operations, saved = _restart_domain(monkeypatch)
    job = {"execution_id": "execution_missing_principal", "submitted_at": "2026-10-19T00:00:00Z"}
    monkeypatch.setattr(resident_backend, "session_record", lambda *args, **kwargs: {})
    monkeypatch.setattr(resident_execution_work, "read", lambda *args: job)
    monkeypatch.setattr(resident_execution_work, "is_active", lambda value: bool(value))
    monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
        reassign_queued_route=lambda *args, **kwargs: pytest.fail("No verified input principal"),
    ))

    with pytest.raises(PermissionError, match="no verified input principal"):
        resident_domain.restart_resident_session(record["resident_id"], "alice", {"target_account": "one"})
    assert operations == [] and saved == []
