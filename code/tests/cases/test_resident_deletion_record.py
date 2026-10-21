"""A deleted Resident is recorded in the database; its conversation and trajectory stay (decision_0f57656a2acd).

:module: lineage.service
:importance: P1
:design: decision_d19b9909a674
"""
from __future__ import annotations

import pytest

from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service import lineage_domain, resident_catalog, resident_workspace_domain
from management_portal.society_service.resident_workspace_storage import BINDING_FIELDS
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401 - fixture
    _add_created_resident_to_lineage,
    _append_conversation_turn,
    _new_session_payload,
    future_creation_store,
    resident_domain,
)


def _persisted_bindings(store, resident_id):
    with store.database.read() as connection:
        return [dict(row) for row in connection.execute(
            "SELECT " + ",".join(BINDING_FIELDS)
            + " FROM resident_workspace_bindings WHERE resident_id=%s ORDER BY workspace_id",
            (resident_id,),
        ).fetchall()]


def _assert_deletion_ends_workspace_use(store, before, bindings_before, deletion, *, extras_deleted=False):
    """Unused Workspaces retire; extras still used by another Resident survive."""
    owned_id = before["owned_workspace_id"]
    extra_ids = [binding["workspace_id"] for binding in bindings_before]
    assert deletion["owned_workspace_id"] == owned_id
    assert deletion["workspace_bindings"] == []
    assert "owned_workspace" not in deletion
    assert _persisted_bindings(store, deletion["resident_id"]) == []
    workspaces = get_workspace_store().rows([owned_id, *extra_ids], include_deleted=True)
    assert (workspaces[owned_id]["deleted_at"], workspaces[owned_id]["deleted_by"]) == (
        deletion["deleted_at"], deletion["deleted_by"],
    )
    assert all(bool(workspaces[workspace_id]["deleted_at"]) == extras_deleted for workspace_id in extra_ids)


@pytest.mark.parametrize("entry", ["ordinary", "fork"])
def test_deleting_a_resident_records_it_and_keeps_its_conversation(future_creation_store, entry):
    """删除 Resident 时数据库写入删除记录，对话与轨迹保留

    删除 Resident 后，删除记录表中有它的一行（Lineage、名称、创建者、删除时间、删除者与删除前记录），Resident 记录不再存在，对话消息与执行记录仍在数据库中；重复写入保留第一次的记录。

    :design: decision_0f57656a2acd
    :design-point:
    """
    source = resident_domain.register_resident(_new_session_payload("Deletion record"), "sample_admin")
    if entry == "fork":
        child = resident_domain.create_resident_full_context_fork(
            source["resident_id"], "sample_admin", "lineage:test:deletion-record",
            defer_backend_start=True,
        )["resident_id"]
        _add_created_resident_to_lineage(source, child)
    else:
        child = lineage_domain.create_lineage_resident(
            source["lineage_id"], _new_session_payload("Deletion member"), "sample_admin",
        )["resident"]["resident_id"]
    _append_conversation_turn(child, 0)
    store = resident_catalog.get_catalog().state_store
    before = resident_catalog.get_catalog().get(child)
    bindings_before = _persisted_bindings(store, child)
    assert not before.get("deleted_at")
    agent_data = get_agent_data_store()
    agent_before = agent_data.agent(child)
    segments_before = agent_data.sequence_segment_ids(agent_before["trace_sequence_id"])
    assert before.get("fork_mode") == ("full" if entry == "fork" else None)

    lineage_domain.delete_lineage_graph_objects(
        source["lineage_id"], {"resident_ids": [child]}, "sample_admin", include_lineage=False,
    )

    with pytest.raises(KeyError):
        resident_catalog.get_catalog().get(child)
    deletion = resident_catalog.get_catalog().get_historical(child)
    assert deletion is not None
    assert (deletion["lineage_id"], deletion["deleted_by"]) == (source["lineage_id"], "sample_admin")
    assert deletion["title"] and deletion["created_by"] == "sample_admin" and deletion["deleted_at"]
    assert deletion["resident_id"] == child
    assert deletion.get("source_resident_id", "") == (source["resident_id"] if entry == "fork" else "")
    assert deletion.get("fork_mode") == before.get("fork_mode")
    assert "fork_method" not in deletion
    for key in ("title", "created_at", "created_by", "backend", "source_resident_id"):
        assert deletion.get(key) == before.get(key)
    _assert_deletion_ends_workspace_use(store, before, bindings_before, deletion)
    assert "workspace_path" not in deletion
    assert "default_cwd" not in deletion
    assert child not in {item["resident_id"] for item in store.residents_for_lineage(source["lineage_id"])}
    assert agent_data.agent(child) == agent_before
    assert agent_data.sequence_segment_ids(agent_before["trace_sequence_id"]) == segments_before
    assert agent_data.read_chat_messages(child)
    assert agent_data.execution(f"execution-fixture-{child}-0") is not None

    repeated = resident_workspace_domain.retry_deleted_resident(child, "sample_admin")
    assert repeated["deleted_at"] == deletion["deleted_at"]
    assert repeated["deleted_by"] == deletion["deleted_by"]
    store.remove_residents([{**deletion, "deleted_at": "2099-01-01T00:00:00Z", "deleted_by": "other"}])
    assert resident_catalog.get_catalog().get_historical(child) == deletion
    _assert_deletion_ends_workspace_use(store, before, bindings_before, deletion)


def test_removing_a_resident_requires_its_deletion_time(future_creation_store):
    """移除 Resident 必须带删除时间

    没有删除时间的记录不能从 Resident 目录中移除，Resident 保持原样，保证每个被移除的 Resident 都有删除记录。

    :design: decision_0f57656a2acd
    :design-point:
    """
    record = resident_domain.register_resident(_new_session_payload("No deletion time"), "sample_admin")
    with pytest.raises(ValueError, match="deleted_at"):
        resident_catalog.get_catalog().remove([record])
    assert resident_catalog.get_catalog().get(record["resident_id"])["resident_id"] == record["resident_id"]


def test_store_deletion_retains_domain_facts_without_workspace_hydration(future_creation_store, monkeypatch):
    """删除记录保留执行设置、用量与额度等字段，读取历史时不查 Workspace 注册表

    :module: resident.identity
    :importance: P2
    :design-point:
    """
    record = resident_domain.register_resident(_new_session_payload("Retained domains"), "sample_admin")
    resident_id = record["resident_id"]
    store = resident_catalog.get_catalog().state_store
    record = store.resident_record(resident_id)
    store.put_resident_record(resident_id, {
        **record,
        "last_turn_usage": {"input_tokens": 11, "output_tokens": 7},
        "quota_reserve_override": {"binding_id": resident_id, "enabled": True, "revision": 1},
    }, commit_configuration=True)
    before = store.resident_record(resident_id)
    bindings_before = _persisted_bindings(store, resident_id)
    agent_before = get_agent_data_store().agent(resident_id)

    store.remove_residents([{
        "resident_id": resident_id, "deleted_at": "2026-10-08T00:00:00Z", "deleted_by": "sample_admin",
    }])

    with monkeypatch.context() as guarded:
        guarded.setattr(
            "management_portal.resources.registry_store.get_registry_store",
            lambda: pytest.fail("Historical Resident read accessed the Workspace registry"),
        )
        historical = store.resident_record(resident_id, include_deleted=True)
        assert store.resident_deletion(resident_id) == historical
        assert resident_catalog.get_catalog().get_historical(resident_id) == historical
    for field in (
        "execution_settings", "harness_config", "backend_route_id", "quota_reserve_override",
        "last_turn_usage",
        "registration_state", "principal_hierarchy",
    ):
        assert historical.get(field) == before.get(field)
    _assert_deletion_ends_workspace_use(store, before, bindings_before, historical, extras_deleted=True)
    assert "workspace_path" not in historical
    assert "default_cwd" not in historical
    assert get_agent_data_store().agent(resident_id) == agent_before
    with pytest.raises(KeyError):
        store.resident_record(resident_id)
    with pytest.raises(ValueError, match="Deleted Resident"):
        store.put_resident_record(resident_id, before)
    assert store.resident_record(resident_id, include_deleted=True) == historical


def test_store_deletion_rejects_unknown_identity_and_rolls_back_the_batch(future_creation_store):
    """批量删除中含有不存在的 Resident 时整批回滚，已有 Resident 不被删除

    :module: resident.identity
    :importance: P2
    :design-point:
    """
    record = resident_domain.register_resident(_new_session_payload("Deletion rollback"), "sample_admin")
    resident_id = record["resident_id"]
    missing_id = "resident_ffffffffffff"
    assert resident_id < missing_id
    store = resident_catalog.get_catalog().state_store
    before = store.resident_record(resident_id)
    with pytest.raises(KeyError, match=missing_id):
        store.remove_residents([{
            "resident_id": target, "deleted_at": "2026-10-08T00:00:00Z", "deleted_by": "sample_admin",
        } for target in (resident_id, missing_id)])
    assert store.resident_record(resident_id) == before
    assert store.resident_deletion(resident_id) is None
    with pytest.raises(KeyError):
        store.resident_record(missing_id, include_deleted=True)
