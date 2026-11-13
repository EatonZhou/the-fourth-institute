"""A newly queued, unadmitted message cannot abort an already preparing restart."""
import copy

import pytest

from tests.cases.test_session_restart import _restart_domain
from management_portal.society_service import (
    resident_domain, resident_execution_work, resident_input_queue_domain,
    resident_restart_domain,
)


@pytest.mark.parametrize("race", ["held", "submitted", "admitted", "reopened", "changed_transition"])
def test_message_arrives_during_session_preparation(monkeypatch, race):
    is_active = resident_execution_work.is_active
    record, operations, _ = _restart_domain(monkeypatch)
    monkeypatch.setattr(resident_execution_work, "is_active", is_active)
    record["intake_open"] = True

    def intake(current, state):
        record.update(intake_open=state == "open", intake_transition_id="restart-closure" if state == "closed" else "reopened")
        current.update(record)

    monkeypatch.setattr(resident_input_queue_domain, "commit_resident_intake_state", intake)
    resident_domain.restart_resident_session(
        record["resident_id"], "alice",
        {"target_provider": "claude", "model": "deepseek-v4-pro", "context_strategy": "compact"},
    )
    step = operations[0]["step"]
    assert step["intake_transition_id"] == "restart-closure"
    job = {"execution_id": "execution-queued", "user_message_id": "msg_queued", "intake_state": "closed"}
    if race == "submitted":
        job["submitted_at"] = "2026-11-13T13:33:43Z"
    elif race == "admitted":
        job["intake_admitted_at"] = "2026-11-13T13:33:43Z"
    elif race == "reopened":
        record["intake_open"] = True
    elif race == "changed_transition":
        record["intake_transition_id"] = "manual-closure"
    before = copy.deepcopy(job)
    monkeypatch.setattr(resident_execution_work, "read", lambda *_: copy.deepcopy(job))
    resident_restart_domain.resume_resident_session_restart(step)
    assert job == before
    if race == "held":
        assert record["session_restart"]["status"] == "completed"
        assert record["backend"] == "claude"
        assert record["intake_open"] is True
    else:
        assert record["session_restart"]["status"] == "failed"
        assert "已开始新的工作" in record["session_restart"]["error"]
        assert record["backend"] == "codex"
