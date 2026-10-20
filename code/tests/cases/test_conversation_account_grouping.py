from management_portal.society_service import business_fact_views
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog
from management_portal.society_service.domain_ports import resource_port
from unittest.mock import Mock

import pytest

from management_portal import agent_conversation_index_backend as index
from management_portal import conversation_index_projection as projection


from management_portal.society_service import resident_directory_domain
from management_portal.society_service.domain_ports import resident_lineage_host_port

def test_conversation_accounts_use_visible_residents_and_bulk_canonical_routes(monkeypatch):
    """会话账号仅取可见 Resident 并批量读取实际路由"""
    records = [
        {"resident_id": "resident_000000000001", "backend": "codex", "backend_route_id": "one"},
        {"resident_id": "resident_000000000002", "backend": "codex", "backend_route_id": "one"},
        {"resident_id": "resident_000000000003", "backend": "codex", "kind": "project", "project_id": "demo"},
        {"resident_id": "resident_000000000004", "backend": "claude"},
        {"resident_id": "resident_000000000005", "backend": "codex", "backend_route_id": "one"},
    ]
    catalog = Mock()
    for index, record in enumerate(records):
        record["lineage_id"] = f"lineage_{index:012x}"
    catalog.get_identity_records.return_value = records
    routes = Mock(return_value={
        "resident:resident_000000000001": {"backend_route_id": "four"},
        "resident:resident_000000000004": {"backend_route_id": "two"},
    })
    accounts = Mock(side_effect=lambda provider, route, runtime_options: {
        "account_id": f"{provider}_{route or 'one'}",
    })
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: catalog)
    monkeypatch.setattr(resident_catalog, "registration_ready", lambda record: True)
    monkeypatch.setattr(business_fact_views, 'canonical_views', lambda records, **kwargs: records)
    monkeypatch.setattr(resident_lineage_host_port, "actor_lineage_access_records",
                        lambda actor, ids: [{"lineage_id": r["lineage_id"]} for r in records[:-1]])
    monkeypatch.setattr(resident_backend, "resolve_account_configuration", accounts)
    monkeypatch.setattr(resident_backend, "session_routes_for_bindings", routes)
    monkeypatch.setattr(resident_backend, "session_record", Mock(side_effect=AssertionError("Must batch route reads")))

    result = resident_directory_domain._engaged_conversation_records([item["resident_id"] for item in records], "sample_admin")

    # Each Resident's account is its session's route, whatever its Provider.
    assert [item["account_id"] for item in result] == ["codex_four", "codex_one", "codex_one", "claude_two"]
    assert [item["resident_id"] for item in result] == [item["resident_id"] for item in records[:-1]]
    routes.assert_called_once_with([f"resident:{item['resident_id']}" for item in records[:-1]])
    assert accounts.call_count == 4
    assert "project_agent" not in accounts.call_args_list[2].kwargs["runtime_options"]


def test_conversation_index_carries_account_for_main_and_family_entries(monkeypatch):
    """会话索引为主节点及 Family 条目保留账号"""
    monkeypatch.setattr(projection, "resident_state_snapshots", lambda actor, ids: {})
    monkeypatch.setattr(projection, "get_avatar", lambda resident_id: {})
    result = projection.resident_entries("sample_admin", {"residents": [{
        "resident_id": "resident_000000000001",
        "account_id": "four",
        "actor_last_user_message_at": "2026-09-26T00:00:00Z",
        "family_residents_preview": [{
            "resident_id": "resident_000000000002",
            "account_id": "one",
            "actor_last_user_message_at": "2026-09-26T00:00:00Z",
        }],
    }]})
    assert [item["account_id"] for item in result] == ["four", "one"]


@pytest.mark.parametrize("read", [True, False])
def test_account_move_refreshes_content_without_changing_read_or_dismissal(read):
    """切换账号刷新会话内容但保留已读与忽略状态"""
    key = "resident:resident_000000000001"
    entry = {"key": key, "account_id": "one", "status": "idle", "updated_at": "2026-09-26T00:00:00Z"}
    row = {"unread": not read, "pinned": False, "read_revision": 3, "dismissed_through": ""}
    preferences = {"rows": {key: row}}
    before = index.decorate_entries([entry], preferences)
    after = index.decorate_entries([{**entry, "account_id": "four"}], preferences)
    assert before["content_signature"] != after["content_signature"]
    assert before["entries"][0]["unread"] == after["entries"][0]["unread"] == (not read)
    # Moving the account is not activity: a cleared conversation stays hidden.
    preferences = {"rows": {key: {**row, "dismissed_through": entry["updated_at"]}}}
    assert index.decorate_entries([{**entry, "account_id": "four"}], preferences)["entries"] == []
