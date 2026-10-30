from __future__ import annotations
from management_portal.society_service import resident_conversation_stream
from management_portal.society_service.resident_domain_common import RESIDENT_RESOURCE_ACTIVITY_TOTAL_CHARS

import json

from management_portal.society_service import (
    lineage_domain,
    lineage_reads,
    lineage_state,
    resident_domain,
    resident_record_domain,
)


from management_portal.society_service import resident_directory_domain
from management_portal.society_service import lineage_aggregate

def _protocol_projection(monkeypatch, resident_id: str, state: str = "idle") -> None:
    repository = type("Repository", (), {
        "current_projection": staticmethod(lambda _resident_id: {
            "resident_id": resident_id,
            "state": {
                "work_state": state,
                "active_message_id": "message_active" if state == "running" else "",
                "queued_count": 0,
                "intake_state": "open",
            },
            "cursor": 7,
        }),
        "states": staticmethod(lambda resident_ids: {
            str(item): {
                "work_state": state,
                "active_message_id": "message_active" if state == "running" else "",
                "queued_count": 0,
                "intake_state": "open",
            }
            for item in resident_ids
        }),
        "user_message_counts": staticmethod(
            lambda resident_ids: {str(item): 0 for item in resident_ids}
        ),
    })()
    monkeypatch.setattr(
        resident_conversation_stream,
        "get_repository",
        lambda: repository,
    )


def test_resident_projection_is_bounded_and_omits_internal_fields(
    monkeypatch,
) -> None:
    """Resident 资源投影限制活动内容并省略内部字段"""
    resident_id = "resident_0123456789ab"
    lineage_id = "lineage_0123456789ab"
    record = {
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "title": "Worker",
        "description": "Investigates execution failures",
        "kind": "system",
        "lineage_membership": "formal",
        "backend": "codex",
        "session_state": "running",
        "last_error": "provider disconnected",
        "workspace_path": "/secret/workspace",
        "owner_ids": ["owner"],
        "updated_at": "2026-08-16T01:00:00Z",
    }
    target = resident_domain.ResidentRuntimeTarget(
        resident_id=resident_id,
        record=record,
    )
    monkeypatch.setattr(
        resident_record_domain,
        "resolve_resident_resource_target",
        lambda _resident_id: target,
    )
    _protocol_projection(monkeypatch, resident_id, "running")
    activity = [
        {
            "id": f"message-{index}",
            "created_at": f"2026-08-16T03:{index:02d}:00Z",
            "source": "backend_output",
            "kind": "resident_message",
            "author": "agent",
            "content": "x" * 7000,
            "event_path": "/secret/event.jsonl",
            "command_payload": {"token": "secret"},
        }
        for index in range(30)
    ]
    monkeypatch.setattr(
        resident_directory_domain,
        "_resident_conversation_activity",
        lambda _record, limit: activity,
    )

    payload = resident_domain.get_resident_resource_projection(resident_id)
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["resource_id"] == resident_id
    assert payload["state"] == "running"
    assert payload["resident_state"]["active_message_id"] == "message_active"
    assert payload["resident_cursor"] == 7
    assert "latest_job" not in payload
    assert payload["errors"] == ["provider disconnected"]
    assert len(payload["recent_activity"]) == 24
    assert payload["recent_activity_truncated"] is True
    assert sum(
        len(item.get("content") or "")
        for item in payload["recent_activity"]
    ) <= RESIDENT_RESOURCE_ACTIVITY_TOTAL_CHARS
    assert "event_path" not in encoded
    assert "command_payload" not in encoded
    assert "workspace_path" not in encoded
    assert "owner_ids" not in encoded


def test_lineage_projection_is_bounded_and_omits_graph_internals(
    monkeypatch,
) -> None:
    """Lineage 资源投影限制成员数量并省略图内部信息"""
    lineage_id = "lineage_0123456789ab"
    resident_ids = [f"resident_{index:012x}" for index in range(300)]
    lineage = {
        "lineage_id": lineage_id,
        "lineage_membership": "formal",
        "title": "Runtime Team",
        "description": "Coordinates runtime work",
        "leader_resident_id": resident_ids[0],
        "nodes": [
            {"id": resident_id, "status": "running"}
            for resident_id in resident_ids
        ],
        "edges": [{"id": "edge-1", "secret": "internal"}],
        "owner_ids": ["owner"],
        "program": {"secret": "internal"},
        "revision": 4,
        "layout_revision": 5,
        "runtime_revision": 6,
    }
    records = {
        resident_id: {
            "resident_id": resident_id,
            "title": f"Worker {index}",
            "session_state": "running",
            "workspace_path": "/secret/workspace",
        }
        for index, resident_id in enumerate(resident_ids)
    }

    class _Catalog:
        @staticmethod
        def get_many(selected_ids):
            return [records[item] for item in selected_ids]

    monkeypatch.setattr(
        lineage_aggregate,
        "read_lineage_state",
        lambda _lineage_id: lineage,
    )
    monkeypatch.setattr(
        lineage_state.resident_catalog,
        "get_catalog",
        lambda: _Catalog(),
    )
    monkeypatch.setattr(
        lineage_domain.resident_protocol,
        "read_resident_states",
        lambda resident_ids: {
            str(item): {
                "work_state": "running",
                "active_message_id": "message_active",
                "queued_count": 0,
                "intake_state": "open",
            }
            for item in resident_ids
        },
    )

    payload = lineage_reads.get_lineage_resource_projection(lineage_id)
    encoded = json.dumps(payload, ensure_ascii=False)

    assert payload["resource_id"] == lineage_id
    assert payload["member_count"] == 300
    assert len(payload["members"]) == 256
    assert payload["members_truncated"] is True
    assert payload["statuses"] == {"running": 300}
    assert payload["members"][0]["is_leader"] is True
    assert "owner_ids" not in encoded
    assert "workspace_path" not in encoded
    assert "program" not in encoded
    assert "edges" not in payload
