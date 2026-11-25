"""Work arriving before the configuration commit must retain its original route."""
import copy

import pytest

from tests.cases.test_session_restart import _restart_domain
from management_portal.society_service import (
    resident_domain, resident_backend, resident_execution_work, resident_fork_context,
)


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
