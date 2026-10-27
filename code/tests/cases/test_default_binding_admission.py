"""First API binding preserves immutable admission and pending business input."""
from copy import deepcopy
from dataclasses import replace

import pytest

from management_portal.execution import account_registrations, execution_lifecycle
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution.accounts import ExecutionAccounts
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.society_service import (
    principal_hierarchy_domain, resident_catalog, resident_default_backend,
    resident_domain, resident_execution_work, resident_intake_domain,
)
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401

pytestmark = pytest.mark.empty_execution_registrations


@pytest.fixture
def pending(future_creation_store, monkeypatch):
    monkeypatch.setattr(resident_intake_domain, "start_resident_continuation", lambda *a: None)
    ama = principal_hierarchy_domain.ensure_personal_ama("api_user", "api_user")
    resident_domain.deliver_resident_message_content(ama["resident_id"], "api_user", "original input")
    record = resident_catalog.get_catalog().get(ama["resident_id"])
    account_registrations.create_registration({"account_id": "deepseek_only", "provider": "provider_api",
        "kind": "api_profile", "config": {"api_id": "deepseek_test"}}, "api_user")
    account_registrations.update_role("default", "deepseek_only", "", "", 0, "api_user")
    return record


@pytest.mark.parametrize("local_ack", [False, True])
def test_scheduler_admission_prevents_cross_provider_binding(pending, local_ack):
    """真实调度提交无论本地确认是否丢失，都不能改绑或取消原执行。"""
    record = pending
    job = resident_execution_work.read(record)
    request = replace(ExecutionRequest.new(actor=resident_execution_work.request_actor(job),
        agent_id=record["resident_id"], prompt="original input", service_type="agent_session",
        provider="codex", operation="turn", session=SessionOptions(mode="stateless")),
        execution_id=job["execution_id"])
    # Queue admission is real, but no credential or worker is needed for this
    # provenance boundary. Do not resolve the site's actual Codex accounts.
    scheduler = Scheduler(dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0,
                          accounts=ExecutionAccounts(()))
    scheduler.submit(request)
    if local_ack:
        job["submitted_at"] = "2026-10-22T12:00:00Z"
        resident_execution_work.write(record, job)
    before_record, before_job = deepcopy(record), resident_execution_work.read(record)
    with pytest.raises(ValueError, match="admitted Execution"):
        resident_default_backend.bind_before_execution(record, "api_user")
    assert record == before_record
    saved = resident_catalog.get_catalog().get(record["resident_id"])
    assert (saved["backend"], saved["backend_route_id"]) == ("codex", "")
    assert resident_execution_work.read(record) == before_job
    assert get_agent_data_store().execution(job["execution_id"])["status"] == "queued"
    assert scheduler.snapshot(job["execution_id"])["status"] == "queued"


def test_binding_transaction_rolls_back_new_preparation(pending, monkeypatch):
    """新预约后写入失败时旧预约、配置和交接回执一起回滚。"""
    record = pending
    original = resident_execution_work.read(record)
    store = get_agent_data_store()
    replacements = []
    def fail_write(record, job):
        replacements.append(job["execution_id"])
        raise RuntimeError("injected write failure")
    monkeypatch.setattr(resident_execution_work, "write", fail_write)
    with pytest.raises(RuntimeError, match="injected write failure"):
        resident_default_backend.bind_before_execution(record, "api_user")
    assert (record["backend"], record["backend_route_id"]) == ("codex", "")
    assert resident_catalog.get_catalog().get(record["resident_id"])["backend"] == "codex"
    assert resident_execution_work.read(record) == original
    assert store.execution(original["execution_id"])["status"] == "preparing"
    assert store.execution(replacements[0]) is None
    with store.database.read() as connection:
        assert execution_lifecycle.unstarted_handoff(connection, original["execution_id"], actor="api_user") == {}


def test_legacy_bound_api_reprepares_old_codex_input(pending):
    """升级前已改绑但未提交的旧准备可恢复；不能沿用旧 Provider 提示词。"""
    from management_portal.society_service.resident_workspace_domain import (
        save_resident_record as _save_resident_record,
    )
    record = pending
    original = resident_execution_work.read(record)
    original.update(actual_sent_prompt="old provider prompt", prompt_built_at="2026-10-22T12:00:00Z",
                    prompt_builder_version="old", runtime_context={"old_provider": True},
                    stop_requested_at="2026-10-22T12:01:00Z", stop_requested_by="api_user")
    resident_execution_work.write(record, original)
    record.update(backend="provider_api", backend_route_id="deepseek_test")
    _save_resident_record(record, commit_configuration=True)
    resident_default_backend.bind_before_execution(record, "api_user")
    current = resident_execution_work.read(record)
    assert current["execution_id"] != original["execution_id"]
    for key in ("user_message_id", "actor", "raw_message", "continuation_id", "queue", "intake_state",
                "stop_requested_at", "stop_requested_by"):
        assert current.get(key) == original.get(key)
    for key in ("actual_sent_prompt", "prompt_built_at", "prompt_builder_version", "runtime_context"):
        assert not current.get(key)
    resident_execution_work.write(record, current)
    resident_default_backend.bind_before_execution(record, "api_user")
    assert resident_execution_work.read(record)["execution_id"] == current["execution_id"]
    from management_portal.society_service import resident_conversation_stream
    state = resident_conversation_stream.get_repository().input_state(record["resident_id"], current["user_message_id"])
    assert state["state"] != "cancelled"
