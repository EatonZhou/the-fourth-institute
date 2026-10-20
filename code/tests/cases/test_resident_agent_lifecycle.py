"""Resident registration, recovery and execution own an independent empty main trace.

:design: decision_d19b9909a674, decision_b1ab45c4b3ec
"""
from types import SimpleNamespace

import pytest

from management_common.projection_receipts import CommittedProjectionError
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import (
    lineage_domain,
    principal_hierarchy_domain,
    resident_catalog,
    resident_domain,
    resident_execution_domain,
    resident_domain_common,
    resident_lifecycle_domain,
    resident_registration_domain,
)
from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload,
    future_creation_store,  # noqa: F401
)


def assert_main_trace(resident_id):
    store = get_agent_data_store()
    agent = store.agent(resident_id)
    assert agent["agent_id"] == resident_id
    assert agent["sequence_kind"] == "agent_trace"
    assert agent["parent_agent_id"] is None
    assert store.read_sequence(agent["trace_sequence_id"])["sequence_id"] == agent["trace_sequence_id"]
    return agent


def test_main_agent_check_rejects_provider_child_identity():
    """主 Agent 检查拒绝子 agent trace 的身份，且不改动该子 agent

    :design-point:"""
    agent_data = get_agent_data_store()
    parent_id = resident_domain_common._new_resident_id()
    child_id = resident_domain_common._new_resident_id()
    agent_data.ensure_agent(parent_id)
    child = agent_data.ensure_agent(child_id, sequence_kind="subagent_trace", parent_agent_id=parent_id)
    with pytest.raises(ValueError, match="trace purpose or parent"):
        resident_lifecycle_domain.ensure_resident_agent({"resident_id": child_id})
    assert agent_data.agent(child_id) == child


@pytest.mark.parametrize("entry", ["ordinary", "personal", "project"])
def test_registration_candidate_uses_only_declared_storage_fields(future_creation_store, monkeypatch, entry):
    """普通、个人与 Project Resident 的注册候选记录只含声明的存储字段

    :design-point:"""
    from management_portal.society_service import resident_runtime_domain
    from management_portal.society_service.resident_storage import validate_record_fields

    class CandidateValidated(Exception):
        pass

    captured = []
    def capture(record, payload, actor, now):
        validate_record_fields(record)
        retired = {
            "mode", "protocol_version", "record_type", "provider_profile", "memory_path",
            "watchboard_scope", "resident_identity_injection_enabled", "lineage_membership",
        }
        assert not set(record) & retired
        if entry == "personal":
            assert record["personal_owner_id"] == actor
        captured.append(record)
        raise CandidateValidated()

    monkeypatch.setattr(resident_runtime_domain, "_resident_runtime_settings_from_register_payload", capture)
    with pytest.raises(CandidateValidated):
        if entry == "personal":
            resident_registration_domain._register_resident({
                "title": "Personal candidate", "backend": "codex",
            }, "newcomer", personal_owner="newcomer")
        else:
            payload = _new_session_payload("Strict candidate")
            resident_registration_domain._register_resident(
                payload, "sample_admin", project_principal=entry == "project",
            )
    assert len(captured) == 1


@pytest.mark.parametrize("entry", ["ordinary", "member", "fork", "personal", "project"])
def test_registration_has_an_empty_independent_trace(future_creation_store, monkeypatch, entry):
    """各种方式创建的 Resident 都有独立的空主 trace，重复检查不新建

    :design-point:"""
    payload = _new_session_payload("Empty main trace")
    if entry in {"member", "fork"}:
        parent = resident_domain.register_resident(_new_session_payload("Parent"), "sample_admin")
        if entry == "member":
            created = lineage_domain.create_lineage_resident(parent["lineage_id"], payload, "sample_admin")["resident"]
        else:
            created = resident_domain.create_resident_full_context_fork(parent["resident_id"], "sample_admin", defer_backend_start=True)
    elif entry == "personal":
        created = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    elif entry == "project":
        created, _ = resident_registration_domain._register_resident(payload, "sample_admin", project_principal=True)
    else:
        created = resident_domain.register_resident(payload, "sample_admin")
    resident_id = created["resident_id"]
    before = assert_main_trace(resident_id)
    assert get_agent_data_store().read_chat_messages(resident_id) == []
    assert get_agent_data_store().trace_length(resident_id) == 0
    record = resident_catalog.get_catalog().get(resident_id)
    resident_lifecycle_domain.ensure_resident_agent(record)
    assert assert_main_trace(resident_id)["trace_sequence_id"] == before["trace_sequence_id"]


def test_agent_failure_keeps_registration_pending_then_retries_same_identity(future_creation_store, monkeypatch):
    """创建 Agent 失败时 Resident 注册停在 resource_pending，确认注册时以同一 ID 补建

    :design-point:"""
    agent_data = get_agent_data_store()
    ensure = agent_data.ensure_agent
    attempts = []

    def unavailable(agent_id, **kwargs):
        attempts.append(agent_id)
        raise OSError("Agent store unavailable")

    monkeypatch.setattr(agent_data, "ensure_agent", unavailable)
    with pytest.raises(CommittedProjectionError) as error:
        resident_domain.register_resident(_new_session_payload("Retry same identity"), "sample_admin")
    failure = error.value.failures[0]
    resident_id = failure["resident_id"]
    store = resident_catalog.get_catalog().state_store
    assert store.resident_record(resident_id)["registration_state"] == "resource_pending"
    assert agent_data.agent(resident_id) == {}
    assert store.resident_resource_projection_pending(resident_id)
    monkeypatch.setattr(agent_data, "ensure_agent", ensure)
    recovered = resident_domain.confirm_resident_registration(resident_id, "sample_admin")
    assert recovered["registration_state"] == "ready"
    assert [row["resident_id"] for row in store.all_residents()] == [resident_id]
    assert_main_trace(resident_id)




def test_already_registered_personal_entry_repairs_a_missing_agent(future_creation_store):
    """已注册的个人 AMA 缺少 Agent 时，再次进入会补建主 trace

    :design-point:"""
    created = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    agent_data = get_agent_data_store()
    # Reproduce a legacy registered identity with no Agent, without changing registration state.
    agent_data.database.write(lambda connection: connection.execute(
        "DELETE FROM agents WHERE agent_id=%s", (created["resident_id"],),
    ))
    assert agent_data.agent(created["resident_id"]) == {}
    recovered = principal_hierarchy_domain.ensure_personal_ama("newcomer", "newcomer")
    assert recovered["resident_id"] == created["resident_id"]
    assert recovered["created"] is False
    assert_main_trace(created["resident_id"])


def test_background_publication_cannot_mark_ready_when_agent_creation_fails(future_creation_store, monkeypatch):
    """Agent 创建失败时后台资源发布不发布 Resident，也不标为就绪

    :design-point:"""
    record = resident_domain.register_resident(_new_session_payload("Worker failure"), "sample_admin")
    store = resident_catalog.get_catalog().state_store
    store.enqueue_resident_resource_projection(record["resident_id"], {**record, "registration_state": "resource_pending"})
    claims = store.claim_resident_resource_projections(resident_id=record["resident_id"])
    published = []
    monkeypatch.setattr(resident_lifecycle_domain, "ensure_resident_agent", lambda *a, **k: (_ for _ in ()).throw(OSError("Agent offline")))
    worker = ResidentResourceProjectionWorker(store, SimpleNamespace(sync_resident_resource_bundles=lambda *a, **k: published.append(a)))
    with pytest.raises(OSError, match="Agent offline"):
        worker._publish(claims)
    assert published == []
    assert store.resident_resource_projection_pending(record["resident_id"])


@pytest.mark.parametrize("entry", ["execution", "delivery"])
def test_execution_does_not_dispatch_when_agent_check_fails(future_creation_store, monkeypatch, entry):
    """Agent 检查失败时 Resident 执行与投递都不派发

    :design-point:"""
    record = resident_domain.register_resident(_new_session_payload("Dispatch blocked"), "sample_admin")
    monkeypatch.setattr(resident_lifecycle_domain, "ensure_resident_agent", lambda *a, **k: (_ for _ in ()).throw(OSError("Agent offline")))
    forbidden = lambda *a, **k: pytest.fail("Dispatched before Agent check")
    with pytest.raises(OSError, match="Agent offline"):
        if entry == "execution":
            resident_execution_domain._bind_or_resume_resident_execution(SimpleNamespace(submit=forbidden, resume=forbidden), object(), record, {})
        else:
            monkeypatch.setattr(resident_execution_domain.resident_execution_provider, "provider_for", forbidden)
            resident_execution_domain._send_to_resident_backend(record, {})


