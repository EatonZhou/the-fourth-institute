from __future__ import annotations

import pytest

from management_portal.society_service import lineage_domain
from management_portal.society_service import (
    lineage_reads,
    lineage_state,
    resident_access,
    resident_protocol,
    resident_record_domain,
)
from management_portal.society_service import lineage_aggregate


def test_internal_discovery_methods_are_service_only() -> None:
    """内部发现目录与可见性过滤只允许服务账号调用"""
    with pytest.raises(PermissionError, match="service actor"):
        lineage_reads.list_resident_discovery_catalog("ordinary-user")
    with pytest.raises(PermissionError, match="service actor"):
        lineage_reads.list_removed_discovery_residents("ordinary-user", ["resident_aaaaaaaaaaaa"])
    with pytest.raises(PermissionError, match="service actor"):
        resident_access.filter_visible_resident_ids(
            ["resident_aaaaaaaaaaaa"],
            "ordinary-user",
            "ordinary-user",
        )


def test_internal_read_access_projection_filters_for_the_requested_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务账号代查可见性时按被查询用户的 Lineage 权限过滤 Resident"""
    class AccessStore:
        @staticmethod
        def is_service_actor(actor: str) -> bool:
            return actor == "portal"

    records = {
        "resident_aaaaaaaaaaaa": {
            "resident_id": "resident_aaaaaaaaaaaa",
            "lineage_id": "lineage_aaaaaaaaaaaa",
            "registration_state": "ready",
        },
        "resident_bbbbbbbbbbbb": {
            "resident_id": "resident_bbbbbbbbbbbb",
            "lineage_id": "lineage_bbbbbbbbbbbb",
            "registration_state": "ready",
        },
    }

    class Catalog:
        @staticmethod
        def get_identity_records(resident_ids: list[str]) -> list[dict[str, str]]:
            return [records[resident_id] for resident_id in resident_ids]

    monkeypatch.setattr(resident_access, "get_portal_access_store", lambda: AccessStore())
    monkeypatch.setattr(
        resident_access.resident_catalog,
        "get_catalog",
        lambda: Catalog(),
    )
    requested_lineages: list[list[str]] = []

    def access_records(actor: str, lineage_ids: list[str]):
        requested_lineages.append(lineage_ids)
        return ([{"lineage_id": "lineage_aaaaaaaaaaaa"}] if actor == "sample_peer" else [])

    monkeypatch.setattr(
        resident_access.resident_lineage_host_port,
        "actor_lineage_access_records",
        access_records,
    )

    result = resident_access.filter_visible_resident_ids(
        list(records), "sample_peer", "portal",
    )

    assert result["resident_ids"] == ["resident_aaaaaaaaaaaa"]
    assert requested_lineages == [[
        "lineage_aaaaaaaaaaaa",
        "lineage_bbbbbbbbbbbb",
    ]]


def test_resident_principal_requires_active_matching_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 身份只在所属 Lineage 中且未删除时被认可"""
    resident_id = "resident_aaaaaaaaaaaa"
    lineage_id = "lineage_aaaaaaaaaaaa"
    record = {"resident_id": resident_id, "lineage_id": lineage_id}

    class Catalog:
        @staticmethod
        def get(requested_id: str):
            return record if requested_id == resident_id else None

    monkeypatch.setattr(resident_access.resident_catalog, "get_catalog", lambda: Catalog())
    monkeypatch.setattr(
        resident_access.resident_lineage_host_port,
        "lineage_access_projection",
        lambda requested_id: {"lineage_id": requested_id},
    )

    assert lineage_state.authorize_lineage_resident_principal(lineage_id, resident_id) == resident_id
    with pytest.raises(PermissionError, match="不属于"):
        lineage_state.authorize_lineage_resident_principal("lineage_bbbbbbbbbbbb", resident_id)
    record["deleted_at"] = "2026-09-28T00:00:00Z"
    with pytest.raises(PermissionError, match="不属于"):
        lineage_state.authorize_lineage_resident_principal(lineage_id, resident_id)


def test_internal_discovery_catalog_contains_all_formal_lineage_residents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内部发现目录列出所有正式 Lineage 中未删除的 Resident"""
    class AccessStore:
        @staticmethod
        def is_service_actor(actor: str) -> bool:
            return actor == "portal"

    class Catalog:
        @staticmethod
        def snapshot() -> list[dict[str, str]]:
            return [
                {"lineage_id": "lineage_aaaaaaaaaaaa"},
                {"lineage_id": "lineage_bbbbbbbbbbbb"},
            ]

    lineages = {
        "lineage_aaaaaaaaaaaa": {
            "lineage_id": "lineage_aaaaaaaaaaaa",
            "lineage_membership": "formal",
            "title": "小白皮书",
            "project_id": "AI员工",
            "revision": 3,
            "nodes": [
                {
                    "id": "resident_ce3e24fc60e0",
                    "title": "小白皮书",
                    "goal": "维护小白皮书",
                },
                {
                    "id": "resident_940bafd3df55",
                    "title": "小白皮书 · 分支",
                    "goal": "更新推理测试表格",
                },
            ],
        },
        "lineage_bbbbbbbbbbbb": {
            "lineage_id": "lineage_bbbbbbbbbbbb",
            "lineage_membership": "formal",
            "title": "私人会话",
            "revision": 1,
            "nodes": [{"id": "resident_bbbbbbbbbbbb"}],
        },
    }

    monkeypatch.setattr(lineage_reads, "get_portal_access_store", lambda: AccessStore())
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: Catalog())
    monkeypatch.setattr(
        lineage_aggregate,
        "read_lineage_state",
        lambda lineage_id: lineages[lineage_id],
    )
    deleted_ids = set()

    class ResidentCatalog:
        @staticmethod
        def get_many(resident_ids):
            return [
                {"resident_id": resident_id, "registration_state": "ready"}
                for resident_id in resident_ids if resident_id not in deleted_ids
            ]

    monkeypatch.setattr(lineage_state.resident_catalog, "get_catalog", lambda: ResidentCatalog())

    result = lineage_reads.list_resident_discovery_catalog("portal")

    assert result["schema"] == "tfi.resident-discovery-catalog.v1"
    assert [item["resident_id"] for item in result["residents"]] == [
        "resident_940bafd3df55",
        "resident_bbbbbbbbbbbb",
        "resident_ce3e24fc60e0",
    ]
    # Historical identity remains stored, while the active catalog excludes it.
    deleted_ids.add("resident_940bafd3df55")
    assert [item["resident_id"] for item in lineage_reads.list_resident_discovery_catalog("portal")["residents"]] == [
        "resident_bbbbbbbbbbbb",
        "resident_ce3e24fc60e0",
    ]
    assert {item["lineage_id"] for item in result["residents"]} == {
        "lineage_aaaaaaaaaaaa", "lineage_bbbbbbbbbbbb"
    }


@pytest.mark.parametrize(("actor", "require_visible"), [("portal", False), ("ordinary-user", True)])
def test_input_state_reads_follow_the_snapshot_service_actor_rule(monkeypatch, actor, require_visible) -> None:
    """读取输入状态时服务账号免可见性检查，普通用户需要能看到该 Resident"""
    class AccessStore:
        @staticmethod
        def is_service_actor(name: str) -> bool:
            return name == "portal"

    seen = []

    def resolve(resident_id, _actor, *, require_visible):
        seen.append(require_visible)
        return type("Target", (), {"resident_id": resident_id})()

    class Repository:
        from types import SimpleNamespace
        agent_data = SimpleNamespace(message_groups=lambda *_args: {})
        @staticmethod
        def input_state(resident_id, message_id):
            return {"resident_id": resident_id, "message_id": message_id}

        @staticmethod
        def message(_resident_id, _message_id):
            return None

    monkeypatch.setattr(resident_protocol, "get_portal_access_store", lambda: AccessStore())
    monkeypatch.setattr(resident_protocol.resident_input_scope, "permits", lambda *_args: False)
    monkeypatch.setattr(resident_record_domain, "resolve_resident_runtime_target", resolve)
    monkeypatch.setattr(resident_protocol.resident_conversation_stream, "get_repository", lambda: Repository())

    state = resident_protocol.get_resident_input_state("resident_aaaaaaaaaaaa", actor, "msg_1")
    assert state["message_id"] == "msg_1"
    assert seen == [require_visible]
