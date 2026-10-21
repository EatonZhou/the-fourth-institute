"""Real v31 historical reads use identity facts without surviving runtime paths.

:module: resident.workspace-memory
:design: decision_d19b9909a674, decision_d918ebebc062
"""
from types import SimpleNamespace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import (
    lineage_storage, resident_catalog, resident_conversation_stream,
    resident_directory_domain, resource_client,
)
from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
from management_portal.society_service.state_store import LineageDataStore

RID = "resident_333333333333"
DELETED = "2026-10-08T00:00:00Z"


@pytest.fixture
def sparse_history(tmp_path, monkeypatch):
    store = LineageDataStore()
    store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id,title,deleted_at,deleted_by) VALUES (%s,%s,%s,%s)",
        (RID, "Known historical title", DELETED, "sample_admin"),
    ))
    agents = AgentDataStore()
    agents.ensure_agent(RID, actor_ref="fixture")
    agents.display_chat_message(RID, {
        "message_id": "msg_33333333333333333333333333333333", "role": "user", "author_id": "sample_admin",
        "content_parts": [{"type": "text", "text": "Stored historical evidence"}],
        "created_at": "2026-10-07T00:00:00Z",
    })
    catalog = resident_catalog.ResidentCatalog(tmp_path, store)
    repository = ResidentConversationRepository(store, agents)
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: repository)
    def paths(ids, *, connection=None):
        assert not list(ids), "Deleted identity must not hydrate Workspace paths"
        return {}
    def unexpected_folder(*args, **kwargs):
        raise AssertionError("Deleted identity must not derive a Lineage folder")
    monkeypatch.setattr(resource_client, "workspace_paths_many", paths)
    monkeypatch.setattr(lineage_storage, "canonical_lineage_folder", unexpected_folder)
    return SimpleNamespace(store=store, catalog=catalog, agents=agents,
                           trace=agents.trace_sequence_id(RID), trace_length=agents.trace_length(RID))


def test_sparse_deleted_history_uses_explicit_id_read_and_keeps_trace(sparse_history):
    """已删除的 Resident 只能按 ID 显式读取历史记录，trace 保持不变

    :design-point:"""
    fixture = sparse_history
    with pytest.raises(KeyError):
        fixture.catalog.get(RID)
    assert fixture.store.all_residents() == []
    assert fixture.store.residents_by_id([RID]) == []
    assert fixture.store.residents_for_lineage("lineage_333333333333") == []
    historical = fixture.catalog.get(RID, include_deleted=True)
    assert fixture.catalog.get_historical(RID) == historical
    assert historical["deleted_at"] == DELETED
    assert historical["deleted_by"] == "sample_admin"
    assert historical["title"] == "Known historical title"
    assert historical["workspace_bindings"] == []
    assert "lineage_id" not in historical
    assert "workspace_path" not in historical
    assert fixture.agents.trace_sequence_id(RID) == fixture.trace
    assert fixture.agents.trace_length(RID) == fixture.trace_length == 1


def test_sparse_deleted_resource_content_does_not_invent_backend(sparse_history):
    """已删除 Resident 的资源内容保留标题与消息数，后端留空

    :design-point:"""
    fixture = sparse_history
    content = resident_directory_domain.get_resident_resource_projection(RID)
    assert content["resident_id"] == RID
    assert content["title"] == "Known historical title"
    assert content["user_message_count"] == 1
    assert content["backend"] == ""
    assert fixture.agents.trace_sequence_id(RID) == fixture.trace
    assert fixture.agents.trace_length(RID) == fixture.trace_length
