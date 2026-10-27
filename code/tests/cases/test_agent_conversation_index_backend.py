from __future__ import annotations
from management_portal.society_service import resident_conversation_stream

import json

import psycopg
import pytest

from management_portal import agent_conversation_index_backend as backend
from management_portal import conversation_index_projection as lineage_projection
from management_portal.conversation_completion import READ_SIGNATURE_PREFIX, completion_signature, marker_order
from management_portal.society_service import (
    lineage_domain,
    lineage_human_notifications,
    resident_domain,
    resource_client,
    state_store,
)
from tests.cases.society_service_test_support import install_resource_boundary, publish_registered_lineage_members


from management_portal.society_service import resident_conversation_projection_domain, resident_directory_domain

def _marker(completion_id: str, completed_at: str) -> str:
    return completion_signature(
        {
            "schema_version": 1,
            "request_actor": "sample_admin",
            "completion_id": completion_id,
            "input_message_id": f"job_{completion_id}",
            "completed_at": completed_at,
        }
    )


def _viewer(rows: dict) -> dict:
    """A viewer's conversation_read_state.load() with the given rows."""
    full = {key: {"unread": False, "pinned": False, "read_revision": 0, "dismissed_through": "", **row}
            for key, row in rows.items()}
    return {"rows": full, "pinned_keys": [key for key, row in full.items() if row["pinned"]]}


def test_completion_signature_requires_the_current_explicit_schema() -> None:
    """完成签名只接受显式 schema_version 1，缺少或版本不符时为空"""
    payload = {
        "request_actor": "sample_admin",
        "completion_id": "reply_1",
        "input_message_id": "message_1",
        "completed_at": "2026-09-20T00:00:00Z",
    }
    assert completion_signature(payload) == ""
    assert completion_signature({**payload, "schema_version": 2}) == ""
    current = completion_signature({**payload, "schema_version": 1})
    assert current.startswith(READ_SIGNATURE_PREFIX)
    assert marker_order(current) == (
        "2026-09-20T00:00:00Z",
        "reply_1",
        current,
    )
    missing_version = READ_SIGNATURE_PREFIX + json.dumps(payload)
    assert marker_order(missing_version) == ("", "", "")


def _lineage_definition(lineage_id: str, nodes: list[dict]) -> dict:
    timestamp = "2026-08-16T00:00:00Z"
    return {
        "schema": "lineage-folder-v3",
        "schema_version": 1,
        "lineage_id": lineage_id,
        "lineage_membership": "formal",
        "project_id": "",
        "title": "Conversation index fixture",
        "description": "",
        "leader_resident_id": "",
        "nodes": nodes,
        "program": {"components": [], "wires": []},
        "revision": 0,
        "layout_revision": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
        "graph_updated_at": timestamp,
        "layout_updated_at": timestamp,
        "created_by": "sample_admin",
    }


def _register_protocol_resident(resident_id: str) -> None:
    lineage_id = f"lineage_{resident_id.removeprefix('resident_')}"
    store = state_store.get_lineage_data_store()
    store.put_lineage_definition(lineage_id, _lineage_definition(lineage_id, []), {})
    publish_registered_lineage_members({
        "lineage_id": lineage_id,
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "nodes": [{"id": resident_id, "title": resident_id}],
    })


def _unread_signature(resident_id: str, actor: str) -> dict:
    """The unread version the conversation index reads for one Resident."""
    summaries = resident_conversation_projection_domain.conversation_family_summaries([resident_id], actor)
    return summaries[resident_id]["unread_signature"]


def test_resident_mailbox_context_uses_current_authorized_lineage_names(monkeypatch, isolated_portal_case):
    """Resident identity names survive the activity projection, including derived rows and renames."""
    records = [
        {"resident_id": "resident_111111111111", "title": "调度", "lineage_id": "lineage_111111111111", "lineage_title": "科研"},
        {"resident_id": "resident_222222222222", "title": "开发", "lineage_id": "lineage_222222222222", "lineage_title": "Portal 团队"},
    ]
    ids = [record["resident_id"] for record in records]

    def visible(selected, actor, service_actor):
        assert selected == ids and actor == "CASE-A" and service_actor == "portal"
        return records

    with isolated_portal_case():
        monkeypatch.setattr(resident_directory_domain.resident_access, "visible_resident_records", visible)
        monkeypatch.setattr(resident_directory_domain.resident_backend, "session_routes_for_bindings", lambda _: {})
        monkeypatch.setattr(lineage_projection, "resident_state_snapshots", lambda *_: {})
        monkeypatch.setattr(lineage_projection, "get_avatar", lambda *_: {})

        def entries():
            residents = resident_directory_domain._engaged_conversation_records(ids, "CASE-A")
            for resident in residents:
                resident["actor_last_user_message_at"] = "2026-10-25T00:00:00Z"
            residents[0]["family_residents_preview"] = [{
                "resident_id": "resident_333333333333", "label": "派生成员",
                "actor_last_user_message_at": "2026-10-25T00:00:00Z",
            }]
            routes = {record["resident_id"]: record["lineage_id"] for record in records}
            return {entry["resident_id"]: entry for entry in lineage_projection.resident_entries(
                "CASE-A", {"residents": residents}, routes,
            )}

        rows = entries()
        assert rows[ids[0]]["subtitle"] == "科研"
        assert rows[ids[1]]["subtitle"] == "Portal 团队"
        assert rows["resident_333333333333"]["subtitle"] == "科研"
        records[0]["lineage_title"] = "科研新名称"
        renamed = entries()
        assert renamed[ids[0]]["subtitle"] == "科研新名称"
        assert renamed["resident_333333333333"]["subtitle"] == "科研新名称"
        assert renamed[ids[0]]["url"] == rows[ids[0]]["url"]


def test_session_index_exposes_metadata_without_transcript_content_contracts(monkeypatch, subtests, isolated_portal_case):
    """会话索引只暴露元数据，条目保留用户消息时间与最新输入结局，Family 预览不重复列出"""
    with subtests.test(contract='test_session_index_exposes_metadata_without_transcript_content'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            entry = {
                "key": "resident:resident_111111111111",
                "title": "Planning",
                "updated_at": "2026-08-02T02:00:00Z",
                "status": "idle",
                "completion_signature": _marker("reply_1", "2026-08-02T02:00:00Z"),
            }

            monkeypatch.setattr(
                backend.conversation_index_projection,
                "collect_entries",
                lambda **_kwargs: [entry],
            )
            entries = backend.build_entries("sample_admin")

            assert len(entries) == 1
            assert entries[0]["key"] == "resident:resident_111111111111"
            assert entries[0]["completion_signature"].startswith("actor-completion-v7:")
            serialized = json.dumps(entries, ensure_ascii=False)
            assert "private user prompt" not in serialized
            assert "private assistant answer" not in serialized
            assert not ({"content", "messages", "prompt", "text"} & entries[0].keys())
    with subtests.test(contract='test_full_conversation_index_does_not_reuse_navigation_sidebar'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            calls: list[tuple[str, object]] = []

            monkeypatch.setattr(
                backend.conversation_index_projection,
                "collect_entries",
                lambda *, actor, resident_payload=None, resident_ids=None, lineage_ids=None: (
                    calls.append((actor, resident_payload, resident_ids, lineage_ids)) or []
                ),
            )
            assert backend.build_entries("CASE-A", review_items=[]) == []
            assert calls == [("CASE-A", None, None, None)]
    with subtests.test(contract='test_resident_entry_keeps_user_message_time_after_running_job_ends'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident = {
                "resident_id": "resident_111111111111",
                "title": "Portal Manager",
                "last_user_actor": "CASE-A",
                "last_user_message_at": "2026-08-15T09:00:00Z",
                "actor_last_user_message_at": "2026-08-15T09:00:00Z",
            }
            state = {"work_state": "running"}

            monkeypatch.setattr(
                lineage_projection,
                "resident_state_snapshots",
                lambda _actor, resident_ids: {
                    resident_id: {"state": dict(state), "cursor": 7}
                    for resident_id in resident_ids
                },
            )

            running = lineage_projection.resident_entries(
                "CASE-A", {"residents": [resident]}
            )[0]
            assert running["status"] == "running"
            assert running["updated_at"] == "2026-08-15T09:00:00Z"

            state["work_state"] = "idle"
            idle = lineage_projection.resident_entries(
                "CASE-A", {"residents": [resident]}
            )[0]
            assert idle["status"] == "idle"
            assert idle["updated_at"] == "2026-08-15T09:00:00Z"
    with subtests.test(contract='test_resident_entry_preserves_the_latest_terminal_input_outcome'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = "resident_0123456789ab"
            _register_protocol_resident(resident_id)
            repository = resident_conversation_stream.get_repository()
            monkeypatch.setattr(
                lineage_projection,
                "resident_state_snapshots",
                lambda _actor, resident_ids: repository.state_snapshots(resident_ids),
            )
            resident = {
                "resident_id": resident_id,
                "title": "Terminal outcome Resident",
                "created_at": "2026-08-15T08:00:00Z",
                "last_user_actor": "CASE-A",
                "last_user_message_at": "2026-08-15T09:00:00Z",
                "actor_last_user_message_at": "2026-08-15T09:00:00Z",
            }

            repository.set_input_state(
                resident_id,
                "message_failed",
                "failed",
                updated_at="2026-08-15T09:01:00Z",
            )
            failed = lineage_projection.resident_entries(
                "CASE-A", {"residents": [resident]}
            )[0]
            assert failed["status"] == "failed"

            repository.set_input_state(
                resident_id,
                "message_cancelled",
                "cancelled",
                updated_at="2026-08-15T09:02:00Z",
            )
            interrupted = lineage_projection.resident_entries(
                "CASE-A", {"residents": [resident]}
            )[0]
            assert interrupted["status"] == "interrupted"

            repository.set_input_state(
                resident_id,
                "message_completed",
                "completed",
                updated_at="2026-08-15T09:03:00Z",
            )
            completed = lineage_projection.resident_entries(
                "CASE-A", {"residents": [resident]}
            )[0]
            assert completed["status"] == "idle"
    with subtests.test(contract='test_resident_target_update_replaces_the_exact_canonical_entry'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = "resident_111111111111"
            calls: list[dict] = []

            monkeypatch.setattr(
                backend.conversation_index_projection,
                "collect_entries",
                lambda **kwargs: (
                    calls.append(kwargs)
                    or [{"key": f"resident:{resident_id}", "kind": "resident", "status": "idle"}]
                ),
            )

            update = backend.build_target_update("sample_admin", "resident", resident_id)

            assert update["remove_keys"] == [f"resident:{resident_id}"]
            assert update["remove_prefixes"] == []
            assert update["entries"] == [
                {"key": f"resident:{resident_id}", "kind": "resident", "status": "idle", "section": "conversation"}
            ]
            assert calls == [
                {
                    "actor": "sample_admin",
                    "resident_ids": [resident_id],
                    "lineage_ids": [],
                }
            ]
    with subtests.test(contract='test_top_level_resident_is_not_duplicated_by_a_family_preview'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            parent_id = "resident_111111111111"
            child_id = "resident_222222222222"
            monkeypatch.setattr(
                lineage_projection,
                "resident_state_snapshots",
                lambda _actor, _resident_ids: {
                    parent_id: {"cursor": 1, "state": {"work_state": "idle"}},
                    child_id: {"cursor": 1, "state": {"work_state": "idle"}},
                },
            )
            child = {
                "resident_id": child_id,
                "title": "Child",
                "created_at": "2026-08-31T01:00:00Z",
                "last_user_actor": "sample_admin",
                "last_user_message_at": "2026-08-31T02:00:00Z",
                "actor_last_user_message_at": "2026-08-31T02:00:00Z",
            }
            parent = {
                "resident_id": parent_id,
                "title": "Parent",
                "created_at": "2026-08-31T01:00:00Z",
                "last_user_actor": "sample_admin",
                "last_user_message_at": "2026-08-31T02:00:00Z",
                "actor_last_user_message_at": "2026-08-31T02:00:00Z",
                "family_residents_preview": [{
                    **child,
                    "label": "Child preview",
                }],
            }

            entries = lineage_projection.resident_entries(
                "sample_admin",
                {"residents": [parent, child]},
            )

            assert [entry["key"] for entry in entries].count(f"resident:{child_id}") == 1


def test_decorate_entries_tracks_read_markers_and_manual_unread_override_contracts(subtests, isolated_portal_case):
    """会话索引按回执与手动未读标记计未读，新完成置顶，已忽略的会话在活动变化后重新出现"""
    with subtests.test(contract='test_decorate_entries_tracks_read_markers_and_manual_unread_override'), isolated_portal_case() as case_path:
        key = "resident:resident_111111111111"
        older = _marker("reply_1", "2026-08-02T01:00:00Z")
        current = _marker("reply_2", "2026-08-02T02:00:00Z")
        entries = [
            {
                "key": key,
                "title": "Resident",
                "subtitle": "Resident",
                "surface_label": "Lineage",
                "url": "/lineage?resident=resident_111111111111",
                "updated_at": "2026-08-02T02:00:00Z",
                "status": "idle",
                "completion_signature": current,
            }
        ]

        unread = backend.decorate_entries(entries, _viewer({key: {"unread": True, "read_revision": 3}}))
        assert unread["unread_count"] == 1
        assert unread["entries"][0]["unread"] is True
        assert unread["entries"][0]["read_revision"] == unread["read_revision"] == 3

        read = backend.decorate_entries(entries, _viewer({key: {"unread": False, "read_revision": 4}}))
        assert read["unread_count"] == 0

        overridden = backend.decorate_entries(entries, _viewer({key: {"unread": True, "read_revision": 5}}))
        assert overridden["unread_count"] == 1
        assert overridden["content_signature"] != read["content_signature"]
        # A conversation without a row is read.
        assert backend.decorate_entries(entries, _viewer({}))["entries"][0]["unread"] is False
    with subtests.test(contract='test_entries_keep_the_time_their_page_was_ordered_by'), isolated_portal_case() as case_path:
        # The page already counts the completion; a signature cannot reorder it.
        entries = backend.normalized_entries([
            {
                "key": "resident:resident_111111111111",
                "updated_at": "2026-08-02T01:00:00Z",
                "completion_signature": _marker(
                    "reply_new",
                    "2026-08-02T04:00:00Z",
                ),
            },
            {
                "key": "resident:resident_222222222222",
                "updated_at": "2026-08-02T03:00:00.250000Z",
                "completion_signature": _marker(
                    "reply_other",
                    "2026-08-02T03:00:00Z",
                ),
            },
        ])

        assert [entry["key"] for entry in entries] == [
            "resident:resident_222222222222",
            "resident:resident_111111111111",
        ]
        assert [entry["updated_at"] for entry in entries] == [
            "2026-08-02T03:00:00.250000Z", "2026-08-02T01:00:00Z",
        ]
    with subtests.test(contract='test_notice_board_mentions_use_the_shared_inbox_contract'), isolated_portal_case() as case_path:
        from management_portal.notice_board_backend import mention_entries

        entries = mention_entries([{
            "event_id": "notice_0123456789abcdef:reply:reply_0123456789abcdef",
            "post_id": "notice_0123456789abcdef",
            "title": "Release plan",
            "author": "alice",
            "context": "reply",
            "occurred_at": "2026-08-26T09:00:00Z",
        }])

        assert entries == [{
            "key": "notice-board:notice_0123456789abcdef",
            "title": "Release plan",
            "subtitle": "alice 在回复中提到了你",
            "surface": "home",
            "surface_label": "Notice Board",
            "kind": "notice-board-mention",
            "section": "notification",
            "url": "/?notice_post=notice_0123456789abcdef",
            "updated_at": "2026-08-26T09:00:00Z",
            "status": "idle",
        }]
    with subtests.test(contract='test_dismissed_conversation_returns_when_its_activity_changes'), isolated_portal_case() as case_path:
        key = "resident:resident_111111111111"
        entry = {
            "key": key,
            "title": "Resident",
            "subtitle": "Resident",
            "surface": "lineage",
            "surface_label": "Lineage",
            "url": "/lineage?resident=resident_111111111111",
            "updated_at": "2026-08-03T01:00:00Z",
            "status": "idle",
            "completion_signature": _marker("reply_1", "2026-08-03T01:00:00Z"),
        }
        preferences = _viewer({key: {"read_revision": 2, "dismissed_through": entry["updated_at"]}})

        dismissed = backend.decorate_entries([entry], preferences)
        assert dismissed["entries"] == []

        changed = dict(entry, status="failed", updated_at="2026-08-03T01:01:00Z")
        returned = backend.decorate_entries([changed], preferences)
        assert len(returned["entries"]) == 1
        assert returned["entries"][0]["status"] == "failed"
        assert returned["entries"][0]["dismissed_through"] == entry["updated_at"]
    with subtests.test(contract='test_resident_completion_cannot_be_claimed_by_another_actor'), isolated_portal_case() as case_path:
        resident = {
            "resident_id": "resident_25ce809ac82f",
            "title": "Private conversation",
            "last_user_actor": "sample_admin",
            "last_user_message_at": "2026-08-26T12:14:13Z",
            "unread_signature": {
                "request_actor": "sample_admin",
                "completion_id": "message_23394ceeedd1",
                "input_message_id": "job_0bd5140a32d31ceb",
                "completed_at": "2026-08-26T12:10:12Z",
            },
            "pending_job": {
                "actor": "sample_admin",
                "status": "running",
                "updated_at": "2026-08-26T12:14:13Z",
            },
        }

        assert lineage_projection.resident_entries(
            "sample_peer",
            {"residents": [resident]},
        ) == []

        resident["actor_last_user_message_at"] = "2026-08-26T12:00:00Z"
        owner_entries = lineage_projection.resident_entries(
            "sample_admin",
            {"residents": [resident]},
        )
        assert [entry["key"] for entry in owner_entries] == [
            "resident:resident_25ce809ac82f"
        ]
    with subtests.test(contract='test_completion_without_actor_does_not_create_conversation_engagement'), isolated_portal_case() as case_path:
        entries = lineage_projection.resident_entries(
            "sample_peer",
            {
                "residents": [{
                    "resident_id": "resident_25ce809ac82f",
                    "title": "Private conversation",
                    "last_user_actor": "sample_admin",
                    "unread_signature": {
                        "completion_id": "legacy_completion",
                        "completed_at": "2026-08-26T12:10:12Z",
                    },
                }]
            },
        )

        assert entries == []


def test_resource_conversation_url_uses_the_rpc_client_contract(monkeypatch) -> None:
    """资源会话链接通过 ResourceService RPC 客户端按地址解析"""
    class ResourceClient:
        @staticmethod
        def portal_url_for_address(address, resource_type=""):
            assert address == "article_0123456789ab"
            assert resource_type == "article"
            return "/articles/article_0123456789ab"

    monkeypatch.setattr(
        resource_client,
        "get_resource_service",
        lambda: ResourceClient(),
    )

    assert resource_client.portal_url_for_address(
        "article_0123456789ab",
        "article",
    ) == "/articles/article_0123456789ab"


def test_conversation_resident_candidates_come_from_actor_messages(
    monkeypatch,
) -> None:
    """会话列表的 Resident 候选来自当前用户发出的消息"""
    resident_id = "resident_ab0f7add58eb"

    class Repository:
        @staticmethod
        def actor_activity(actor, resident_ids=None):
            assert actor == "CASE-A"
            assert resident_ids is None
            return {resident_id: "2026-08-27T15:19:45Z"}

    monkeypatch.setattr(
        resident_conversation_stream,
        "get_repository",
        lambda: Repository(),
    )
    monkeypatch.setattr(
        resident_directory_domain,
        "_engaged_conversation_records",
        lambda candidates, actor: [
            {
                "resident_id": resident_id,
                "title": "VPS 方案",
                "created_at": "2026-08-21T16:20:30Z",
            }
        ]
        if list(candidates) == [resident_id] and actor == "CASE-A"
        else [],
    )
    monkeypatch.setattr(
        resident_conversation_projection_domain,
        "conversation_family_summaries",
        lambda resident_ids, actor: {
            resident_id: {
                "actor_last_user_message_at": "2026-08-27T15:19:45Z",
            }
        },
    )

    payload = resident_domain.list_resident_conversation_activity("CASE-A")

    assert [item["resident_id"] for item in payload["residents"]] == [
        resident_id
    ]
    assert payload["residents"][0]["actor_last_user_message_at"] == (
        "2026-08-27T15:19:45Z"
    )


@pytest.mark.parametrize('has_completion', [False, True])
@pytest.mark.parametrize('request_after_completion', [False, True])
def test_resident_entries_ignore_unrelated_registry_and_actor_activity_times(
    has_completion, request_after_completion,
) -> None:
    """Resident 会话条目按本人的请求或完成时间排序，不受他人运行和注册表更新时间影响"""
    # Another sender (including a Resident) can arrive while our new request
    # is still running. Its arrival must not erase our own sorting time.
    main_request_at = "2026-07-05T15:05:00Z" if request_after_completion else "2026-07-03T15:05:00Z"
    family_request_at = "2026-07-05T15:06:00Z" if request_after_completion else "2026-07-04T15:05:00Z"
    entries = lineage_projection.resident_entries(
        "CASE-A",
        {
            "residents": [
                {
                    "resident_id": "resident_111111111111",
                    "title": "Portal Manager",
                    "updated_at": "2026-08-02T14:16:57Z",
                    "last_token_usage_at": "2026-08-02T14:16:57Z",
                    "last_user_actor": "sample_admin",
                    "actor_last_user_message_at": main_request_at,
                    "unread_signature": {
                        "request_actor": "CASE-A",
                        "completion_id": "reply_et_old",
                        "input_message_id": "job_et_old",
                        "completed_at": "2026-07-03T15:05:26Z",
                    } if has_completion else {},
                    "pending_job": {
                        "actor": "sample_admin",
                        "status": "running",
                        "updated_at": "2026-08-02T14:16:58Z",
                    },
                    "family_residents_preview": [
                        {
                            "resident_id": "resident_222222222222",
                            "label": "Shared fork",
                            "updated_at": "2026-08-02T14:16:57Z",
                            "last_interaction_at": "2026-08-02T14:16:57Z",
                            "last_user_actor": "resident_333333333333",
                            "actor_last_user_message_at": family_request_at,
                            "unread_signature": {
                                "request_actor": "CASE-A",
                                "completion_id": "fork_reply_et_old",
                                "input_message_id": "fork_job_et_old",
                                "completed_at": "2026-07-04T15:05:26Z",
                            } if has_completion else {},
                            "pending_job": {
                                "actor": "sample_admin",
                                "status": "running",
                                "updated_at": "2026-08-02T14:16:58Z",
                            },
                        }
                    ],
                }
            ]
        },
    )

    by_key = {entry["key"]: entry for entry in entries}
    assert by_key["resident:resident_111111111111"]["updated_at"] == (
        main_request_at if request_after_completion or not has_completion else "2026-07-03T15:05:26Z"
    )
    assert by_key["resident:resident_222222222222"]["updated_at"] == (
        family_request_at if request_after_completion or not has_completion else "2026-07-04T15:05:26Z"
    )
    older = {"key": "resident:older", "updated_at": "2026-07-02T15:05:00Z"}
    assert [entry['key'] for entry in backend.normalized_entries([older, *entries])] == [
        'resident:resident_222222222222', 'resident:resident_111111111111', 'resident:older',
    ]


def test_builder_execution_without_chat_does_not_create_conversation_engagement_contracts(subtests, isolated_portal_case):
    """只有构建执行的 Resident 不进入会话索引，intake 变化不算新活动，派生成员链接回 Lineage"""
    with subtests.test(contract='test_builder_execution_without_chat_does_not_create_conversation_engagement'), isolated_portal_case() as case_path:
        entries = lineage_projection.resident_entries(
            "sample_peer",
            {
                "residents": [{
                    "resident_id": "resident_25ce809ac82f",
                    "title": "Resident built by sample_peer but chatted by sample_admin",
                    "created_by": "sample_peer",
                    "last_user_actor": "sample_admin",
                    "last_user_message_at": "2026-08-26T12:14:13Z",
                    "unread_signature": {
                        "request_actor": "sample_peer",
                        "completion_id": "builder_completion",
                        "input_message_id": "builder_job",
                        "completed_at": "2026-08-26T12:10:12Z",
                    },
                    "pending_job": {
                        "actor": "sample_peer",
                        "status": "running",
                        "updated_at": "2026-08-26T12:14:13Z",
                    },
                }]
            },
        )

        assert entries == []
    with subtests.test(contract='test_inherited_activity_belongs_to_the_forked_resident'), isolated_portal_case() as case_path:
        inherited_completion = {
            "request_actor": "sample_peer",
            "completion_id": "inherited_reply",
            "input_message_id": "inherited_job",
            "completed_at": "2026-08-26T09:04:22Z",
        }
        resident = {
            "resident_id": "resident_25ce809ac82f",
            "title": "Private fork promoted into a graph",
            "created_at": "2026-08-26T11:29:37Z",
            "last_user_actor": "sample_peer",
            "last_user_message_at": "2026-08-26T09:00:00Z",
            "actor_last_user_message_at": "2026-08-26T09:00:00Z",
            "unread_signature": inherited_completion,
            "pending_job": {"actor": "sample_admin", "status": "running"},
        }

        entries = lineage_projection.resident_entries(
            "sample_peer", {"residents": [resident]}
        )
        assert len(entries) == 1
        assert entries[0]["resident_id"] == resident["resident_id"]
        assert entries[0]["completion_signature"]

        resident["actor_last_user_message_at"] = "2026-08-26T11:30:00Z"
        assert lineage_projection.resident_entries(
            "sample_peer", {"residents": [resident]}
        )[0]["completion_signature"] == entries[0]["completion_signature"]
    with subtests.test(contract='test_intake_change_refreshes_content_without_new_activity_or_unread'), isolated_portal_case() as case_path:
        key = "resident:resident_111111111111"
        signature = _marker("reply_1", "2026-09-01T00:00:00Z")
        entry = {
            "key": key, "title": "Resident", "status": "interrupted", "intake_state": "open",
            "updated_at": "2026-09-01T00:00:00Z", "completion_signature": signature,
        }
        preferences = _viewer({key: {"read_revision": 1}})
        opened = backend.decorate_entries([entry], preferences)
        closed_entry = {**entry, "intake_state": "closed"}
        closed = backend.decorate_entries([closed_entry], preferences)
        assert opened["content_signature"] != closed["content_signature"]
        assert opened["entries"][0]["updated_at"] == closed["entries"][0]["updated_at"]
        assert closed["entries"][0]["intake_state"] == "closed"
        assert opened["entries"][0]["unread"] is False
        assert closed["entries"][0]["unread"] is False
        # A cleared conversation stays hidden: intake is not activity.
        preferences = _viewer({key: {"read_revision": 2, "dismissed_through": entry["updated_at"]}})
        assert backend.decorate_entries([closed_entry], preferences)["entries"] == []
    with subtests.test(contract='test_derived_resident_routes_back_to_its_lineage_surface'), isolated_portal_case() as case_path:
        resident_id = "resident_111111111111"
        created_resident_id = "resident_222222222222"
        lineage_id = "lineage_333333333333"
        payload = {
            "residents": [
                {
                    "resident_id": resident_id,
                    "title": "Root Resident",
                    "lineage_id": lineage_id,
                    "lineage_source": "registry",
                    "family_residents_preview": [
                        {
                                "resident_id": created_resident_id,
                                "label": "UI",
                                "last_user_actor": "CASE-A",
                                "actor_last_user_message_at": "2026-08-03T00:59:00Z",
                            "last_interaction_at": "2026-08-03T01:00:00Z",
                            "unread_signature": {
                                "completion_id": "reply_ui",
                                "input_message_id": "job_ui",
                                "completed_at": "2026-08-03T01:00:00Z",
                            },
                        }
                    ],
                }
            ]
        }

        lineage_entry = lineage_projection.resident_entries(
            "CASE-A",
            payload,
            {created_resident_id: lineage_id},
        )[0]
        assert lineage_entry["surface"] == "lineage"
        assert lineage_entry["url"] == (
            f"/lineage?lineage={lineage_id}&resident={created_resident_id}"
        )
        standalone_entry = lineage_projection.resident_entries("CASE-A", payload, {})[0]
        assert standalone_entry["surface"] == "lineage"
        assert standalone_entry["url"] == (
            f"/lineage?resident={created_resident_id}"
        )
    with subtests.test(contract='test_lineage_resident_exposes_one_canonical_session_surface'), isolated_portal_case() as case_path:
        resident_id = "resident_111111111111"
        lineage_id = "lineage_333333333333"
        payload = {
            "residents": [
                {
                    "resident_id": resident_id,
                        "title": "Root Resident",
                        "last_user_actor": "CASE-A",
                        "actor_last_user_message_at": "2026-08-03T00:59:00Z",
                        "last_interaction_at": "2026-08-03T01:00:00Z",
                }
            ]
        }

        entry = lineage_projection.resident_entries(
            "CASE-A",
            payload,
            {resident_id: lineage_id},
        )[0]

        assert entry["url"] == (
            f"/lineage?lineage={lineage_id}&resident={resident_id}"
        )


def test_starting_session_preserves_queued_status():
    """正在启动会话或等待容量的 Resident 显示为排队中，Family 成员运行时显示运行中"""
    starting = {"state": {"work_state": "queued", "work_phase": "starting_session"}}
    waiting = {"state": {"work_state": "queued", "work_phase": "waiting_capacity"}}

    assert lineage_projection._resident_status(starting) == "queued"
    assert lineage_projection._resident_status({"state": {"work_state": "idle", "family_activity": {"work_state": "running"}}}) == "running"
    assert lineage_projection._resident_status(waiting) == "queued"


def test_resident_entries_keep_intake_separate_from_activity(monkeypatch, subtests, isolated_portal_case):
    """Resident 会话条目的 intake 状态与工作状态分开显示，主 Resident 与 Fork 一致"""
    for intake_state in ["open", "closed"]:
        for work_state, terminal_state, expected_status in [
    ("idle", "", "idle"),
    ("running", "", "running"),
    ("queued", "", "queued"),
    ("stopping", "", "running"),
    ("idle", "failed", "failed"),
    ("idle", "cancelled", "interrupted"),
]:
            with subtests.test(intake_state=intake_state, work_state=work_state, terminal_state=terminal_state, expected_status=expected_status), isolated_portal_case() as case_path:
                with monkeypatch.context() as monkeypatch:
                    monkeypatch.setattr(
                        lineage_projection,
                        "resident_state_snapshots",
                        lambda _actor, resident_ids: {
                            resident_id: {
                                "state": {"intake_state": intake_state, "work_state": work_state},
                                "latest_input_state": {"state": terminal_state},
                                "cursor": 8,
                            }
                            for resident_id in resident_ids
                        },
                    )
                    entries = lineage_projection.resident_entries("CASE-A", {"residents": [{
                        "resident_id": "resident_111111111111",
                        "title": "Main Resident",
                        "actor_last_user_message_at": "2026-09-01T00:00:00Z",
                        "family_residents_preview": [{
                            "resident_id": "resident_222222222222",
                            "label": "Fork Resident",
                            "actor_last_user_message_at": "2026-09-01T00:00:00Z",
                        }],
                    }]})
                    assert {entry["resident_id"] for entry in entries} == {
                        "resident_111111111111", "resident_222222222222",
                    }
                    assert all(entry["intake_state"] == intake_state for entry in entries)
                    assert all(entry["status"] == expected_status for entry in entries)


def test_human_notifications_are_visible_in_the_conversation_index_only_to_request_actor(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """Lineage 人工通知只出现在收件人的会话索引中，并带完整正文和来源信息"""
    with subtests.test(contract='test_human_notifications_are_visible_in_the_conversation_index_only_to_request_actor'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            lineage_id = "lineage_333333333333"
            monkeypatch.setattr(lineage_domain, "LINEAGE_FOLDER_ROOT", tmp_path / "Agents")
            monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(tmp_path / "Agents"))
            lineage_domain._lineage_catalog_store().upsert(
                _lineage_definition(lineage_id, [])
            )
            install_resource_boundary(monkeypatch)
            resource_client.sync_lineage(
                {"lineage_id": lineage_id}
            )
            full_message = "请预览草案。\n" + "详细说明。" * 100 + "\n正文结尾。"
            lineage_human_notifications.create_notification(
                lineage_id,
                notification_id="human_notice_sample_admin",
                title="HATG 图架构师 Proposal",
                message=full_message,
                recipients=["sample_admin"],
                severity="warning",
                created_by="resident_111111111111",
                trigger_path_id="run_proposal",
            )
            lineage_human_notifications.create_notification(
                lineage_id,
                notification_id="human_notice_et",
                title="仅 CASE-A 可见",
                message="仅 CASE-A 可见",
                recipients=["CASE-A"],
            )

            entries = lineage_projection.lineage_human_notification_entries("sample_admin")

            assert len(entries) == 1
            entry = entries[0]
            assert entry["key"] == (
                f"lineage-notification:{lineage_id}:human_notice_sample_admin"
            )
            assert entry["title"] == "HATG 图架构师 Proposal"
            assert entry["subtitle"] == full_message[:320]
            assert entry["surface"] == "lineage"
            assert entry["url"] == f"/lineage?lineage={lineage_id}"
            # The recipient's Mailbox row is created with the notification.
            from management_portal import conversation_read_state

            assert conversation_read_state.row(conversation_read_state.load("sample_admin"), entry["key"])["unread"]
            assert not conversation_read_state.row(conversation_read_state.load("CASE-A"), entry["key"])["unread"]
            assert entry["notification"]["id"] == "human_notice_sample_admin"
            assert entry["notification"]["message"] == full_message
            assert entry["notification"]["severity"] == "warning"
            assert entry["notification"]["created_by"] == "resident_111111111111"
            assert entry["notification"]["trigger_path_id"] == "run_proposal"
            assert entry["notification"]["lineage_id"] == lineage_id
            assert entry["notification"]["created_at"] == entry["updated_at"]
            assert "recipients" not in entry["notification"]

            assert lineage_projection.lineage_human_notification_entries("CASE-A")[0]["title"] == "仅 CASE-A 可见"


def test_conversation_summary_exposes_actor_specific_chat_engagement_contracts(tmp_path, subtests, isolated_portal_case):
    """会话摘要按用户记录最后发言时间，规则回复和无回复的失败输入都计为该用户的完成"""
    with subtests.test(contract='test_conversation_summary_exposes_actor_specific_chat_engagement'), isolated_portal_case() as case_path:
        tmp_path = case_path
        resident_id = "resident_0123456789ab"
        _register_protocol_resident(resident_id)
        repository = resident_conversation_stream.get_repository()
        repository.append_message(
            resident_id,
            {
                "message_id": "user_sample_peer",
                "role": "user",
                "content_parts": [{"type": "text", "text": "hello"}],
                "author_id": "sample_peer",
                "created_at": "2026-08-15T09:00:00Z",
            },
        )
        repository.append_message(
            resident_id,
            {
                "message_id": "user_sample_admin",
                "role": "user",
                "content_parts": [{"type": "text", "text": "later message"}],
                "author_id": "sample_admin",
                "created_at": "2026-08-15T10:00:00Z",
            },
        )

        sample_peer = resident_conversation_projection_domain.conversation_family_summaries(
            [resident_id],
            "sample_peer",
        )[resident_id]
        other = resident_conversation_projection_domain.conversation_family_summaries(
            [resident_id],
            "CASE-A",
        )[resident_id]

        assert sample_peer["last_user_actor"] == "sample_admin"
        assert sample_peer["actor_last_user_message_at"] == "2026-08-15T09:00:00Z"
        assert other["actor_last_user_message_at"] == ""
    with subtests.test(contract='test_deterministic_resident_reply_is_an_actor_scoped_completion'), isolated_portal_case() as case_path:
        tmp_path = case_path
        resident_id = "resident_222222222222"
        _register_protocol_resident(resident_id)
        repository = resident_conversation_stream.get_repository()
        repository.append_message(resident_id, {
            "message_id": "message_rule_request",
            "role": "user",
            "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": "run rule"}],
            "created_at": "2026-08-16T01:00:00Z",
        })
        repository.append_message(resident_id, {
            "message_id": "message_rule_reply",
            "role": "agent",
            "reply_to_message_id": "message_rule_request",
            "content_parts": [{"type": "text", "text": "rule completed"}],
            "created_at": "2026-08-16T01:00:01Z",
        })
        repository.set_input_state(
            resident_id,
            "message_rule_request",
            "completed",
            updated_at="2026-08-16T01:00:02Z",
        )

        signature = _unread_signature(
            resident_id,
            "sample_admin",
        )

        assert signature["completion_id"] == "message_rule_reply"
        assert signature["input_message_id"] == "message_rule_request"
    with subtests.test(contract='test_failed_resident_input_without_a_reply_is_an_unread_terminal_version'), isolated_portal_case() as case_path:
        tmp_path = case_path
        resident_id = "resident_333333333333"
        _register_protocol_resident(resident_id)
        repository = resident_conversation_stream.get_repository()
        repository.append_message(resident_id, {
            "message_id": "failed_request",
            "role": "user",
            "author_id": "sample_admin",
            "content_parts": [{"type": "text", "text": "run failing task"}],
            "created_at": "2026-08-16T02:00:00Z",
        })
        repository.set_input_state(
            resident_id,
            "failed_request",
            "failed",
            error={"message": "provider failed"},
            updated_at="2026-08-16T02:01:00Z",
        )

        signature = _unread_signature(
            resident_id,
            "sample_admin",
        )

        assert signature["completion_id"] == (
            "resident-input-terminal:failed_request:failed"
        )
        assert signature["input_message_id"] == "failed_request"


def test_unread_signature_waits_for_latest_actor_turn_final_reply(tmp_path) -> None:
    """未读签名等用户最新一轮输入完成且有最终回复后才更新，新一轮运行时保留上次完成"""
    resident_id = "resident_111111111111"
    _register_protocol_resident(resident_id)
    items = [
        {
            "message_id": "user_old",
            "role": "user",
            "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "old request"}],
            "created_at": "2026-08-02T15:52:08Z",
        },
        {
            "message_id": "reply_old",
            "role": "agent",
            "reply_to_message_id": "user_old",
            "content_parts": [{"type": "text", "text": "Old final reply"}],
            "created_at": "2026-08-02T15:55:24Z",
        },
        {
            "message_id": "user_current",
            "role": "user",
            "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "current request"}],
            "created_at": "2026-08-02T16:01:35Z",
        },
        {
            "message_id": "activity_current",
            "role": "agent",
            "content_parts": [{"type": "text", "text": "Still working"}],
            "created_at": "2026-08-02T16:02:00Z",
        },
    ]
    repository = resident_conversation_stream.get_repository()
    for item in items:
        repository.append_message(resident_id, item)
    repository.set_input_state(
        resident_id,
        "user_current",
        "running",
        updated_at="2026-08-02T16:02:00Z",
    )

    running = _unread_signature(
        resident_id,
        "CASE-A",
    )
    assert running["completion_id"] == ""
    assert running["input_message_id"] == ""

    items.append(
        {
            "message_id": "reply_current",
            "role": "agent",
            "reply_to_message_id": "user_current",
            "content_parts": [{"type": "text", "text": "Current final reply"}],
            "created_at": "2026-08-02T16:05:00Z",
        }
    )
    repository.append_message(resident_id, items[-1])

    still_running = _unread_signature(
        resident_id,
        "CASE-A",
    )
    assert still_running["completion_id"] == ""

    repository.set_input_state(
        resident_id,
        "user_current",
        "completed",
        updated_at="2026-08-02T16:06:00Z",
    )

    completed = _unread_signature(
        resident_id,
        "CASE-A",
    )
    assert completed["completion_id"] == "reply_current"
    assert completed["input_message_id"] == "user_current"
    assert completed["completed_at"] == "2026-08-02T16:06:00Z"

    repository.append_message(
        resident_id,
        {
            "message_id": "user_next",
            "role": "user",
            "author_id": "CASE-A",
            "content_parts": [{"type": "text", "text": "next request"}],
            "created_at": "2026-08-02T16:07:00Z",
        },
    )
    repository.set_input_state(
        resident_id,
        "user_next",
        "running",
        updated_at="2026-08-02T16:07:01Z",
    )

    previous_completion = _unread_signature(
        resident_id,
        "CASE-A",
    )
    assert previous_completion["completion_id"] == "reply_current"
    assert previous_completion["input_message_id"] == "user_current"


def test_latest_notification_does_not_resolve_older_reply_histories(monkeypatch):
    """查找最新可通知输入时只解析最后一条请求的回复，不回溯更早的回复历史"""
    resident_id = "resident_111111111111"
    _register_protocol_resident(resident_id)
    repository = resident_conversation_stream.get_repository()
    requests = []
    for index in range(12):
        message_id = f"request_{index:02d}"
        requests.append(message_id)
        at = f"2026-09-24T01:{index:02d}:00Z"
        for identity, role, reply in [(message_id, "user", ""), (f"answer_{index:02d}", "agent", message_id)]:
            repository.append_message(resident_id, {
                "message_id": identity, "role": role, "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": identity}],
                "created_at": at, "reply_to_message_id": reply,
            })
        repository.set_input_state(resident_id, message_id, "completed", updated_at=at)
    examined = []
    original = repository.agent_data.chat_final_replies_many
    def record(wanted):
        examined.extend(value for values in wanted.values() for value in values)
        return original(wanted)
    monkeypatch.setattr(repository.agent_data, "chat_final_replies_many", record)
    result = repository.latest_notifiable_input_states_for_messages({resident_id: requests})
    assert result[resident_id]["reply_message_ids"] == ["answer_11"]
    assert examined == ["request_11"]


def test_latest_notification_bounds_round_trips_across_control_receipts(monkeypatch):
    """连续控制回执使用有界批量查询寻找最新有效通知

    连续控制回执不覆盖历史真实结果；逐步扩大候选窗口并限制每轮查询数量。

    :design: decision_b4e5ea4a47ec
    :design-point:
    """
    resident_id = "resident_111111111111"
    _register_protocol_resident(resident_id)
    repository = resident_conversation_stream.get_repository()
    requests = []
    for index in range(34):
        message_id = f"request_{index:02d}"
        requests.append(message_id)
        at = f"2026-09-24T01:{index:02d}:00Z"
        for identity, role, reply in [(message_id, "user", ""),
                                      (f"reply_{index:02d}", "agent" if index < 2 else "system", message_id)]:
            repository.append_message(resident_id, {
                "message_id": identity, "role": role, "author_id": "CASE-A",
                "content_parts": [{"type": "text", "text": identity}],
                "created_at": at, "reply_to_message_id": reply,
            })
        repository.set_input_state(resident_id, message_id, "completed", updated_at=at)
    batches = []
    original = repository.agent_data.chat_final_replies_many

    def record(wanted):
        batches.append([item for values in wanted.values() for item in values])
        return original(wanted)

    monkeypatch.setattr(repository.agent_data, "chat_final_replies_many", record)
    result = repository.latest_notifiable_input_states_for_messages({resident_id: requests})
    assert result[resident_id]["message_id"] == "request_01"
    assert result[resident_id]["reply_message_ids"] == ["reply_01"]
    # Each round costs fixed queries: 33 receipts take three, not six.
    assert [len(batch) for batch in batches] == [1, 8, 25]
    assert max(map(len, batches)) <= 64


def test_latest_notification_reads_all_input_states_in_one_statement(monkeypatch):
    """查找最新可通知输入时，所有 Resident 的请求状态用一条语句读取，语句数不随请求数量增长"""
    residents = [f"resident_{number:012d}" for number in range(3)]
    repository = resident_conversation_stream.get_repository()
    requests = {}
    for resident_id in residents:
        _register_protocol_resident(resident_id)
        requests[resident_id] = [f"{resident_id}_request_{index:03d}" for index in range(150)]
        for index, message_id in enumerate(requests[resident_id]):
            repository.set_input_state(resident_id, message_id, "failed" if index == 149 else "cancelled",
                                       updated_at=f"2026-09-24T{index // 60:02d}:{index % 60:02d}:00Z")
    statements = []
    execute = psycopg.Cursor.execute

    def record(cursor, query, params=None, **kwargs):
        statements.append(str(query))
        return execute(cursor, query, params, **kwargs)

    monkeypatch.setattr(psycopg.Cursor, "execute", record)
    result = repository.latest_notifiable_input_states_for_messages(requests)
    monkeypatch.undo()
    assert {resident_id: value["message_id"] for resident_id, value in result.items()} == {
        resident_id: f"{resident_id}_request_149" for resident_id in residents
    }
    assert len([query for query in statements if "FROM conversation_input_states" in query
                or "JOIN conversation_input_states" in query]) == 1


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_control_receipts_preserve_unread_version_until_a_real_outcome(outcome):
    """控制回执不产生未读，真实结果按用户推进未读状态

    验证注入及系统回执保持旧未读版本；真实回复、失败和中断推进通知；批量与单个摘要一致且隔离用户与Resident。

    :design: decision_b4e5ea4a47ec
    """
    resident_id = "resident_111111111111"
    other_id = "resident_222222222222"
    for target in (resident_id, other_id):
        _register_protocol_resident(target)
    repository = resident_conversation_stream.get_repository()

    def append(target, message_id, role, at, reply_to="", actor="CASE-A"):
        repository.append_message(target, {
            "message_id": message_id, "role": role, "author_id": actor,
            "content_parts": [{"type": "text", "text": message_id}],
            "created_at": at, "reply_to_message_id": reply_to,
        })

    def signature(actor="CASE-A"):
        return _unread_signature(resident_id, actor)

    append(resident_id, "request_old", "user", "2026-09-24T01:00:00Z")
    append(resident_id, "reply_old", "agent", "2026-09-24T01:01:00Z", "request_old")
    repository.set_input_state(resident_id, "request_old", "completed",
                               updated_at="2026-09-24T01:01:00Z")
    previous = signature()
    assert previous["completion_id"] == "reply_old"

    append(other_id, "foreign_reply", "agent", "2026-09-24T01:02:00Z", "control")
    append(resident_id, "wrong_reply", "agent", "2026-09-24T01:02:00Z", "another_request")
    append(resident_id, "control", "user", "2026-09-24T01:03:00Z")
    append(resident_id, "receipt", "system", "2026-09-24T01:03:01Z", "control")
    repository.set_input_state(resident_id, "control", "completed",
                               updated_at="2026-09-24T01:03:01Z")
    append(resident_id, "injected", "user", "2026-09-24T01:04:00Z")
    repository.set_input_state(resident_id, "injected", "completed",
                               updated_at="2026-09-24T01:04:01Z")
    assert signature() == previous

    # A different actor's completed turn must not replace CASE-A's unread version.
    append(resident_id, "other_request", "user", "2026-09-24T01:05:00Z", actor="sample_admin")
    append(resident_id, "other_reply", "agent", "2026-09-24T01:05:01Z", "other_request")
    repository.set_input_state(resident_id, "other_request", "completed",
                               updated_at="2026-09-24T01:05:01Z")
    assert signature() == previous
    assert signature("sample_admin")["completion_id"] == "other_reply"

    append(resident_id, "request_next", "user", "2026-09-24T01:06:00Z")
    replies = []
    if outcome == "completed":
        append(resident_id, "reply_next", "agent", "2026-09-24T01:07:00Z", "request_next")
        append(resident_id, "system_next", "system", "2026-09-24T01:07:01Z", "request_next")
        replies = ["reply_next", "system_next"]
    repository.set_input_state(resident_id, "request_next", outcome,
                               updated_at="2026-09-24T01:08:00Z")
    current = signature()
    assert current["completion_id"] == (
        "reply_next" if outcome == "completed" else f"resident-input-terminal:request_next:{outcome}"
    )
    assert marker_order(completion_signature(current)) > marker_order(completion_signature(previous))
    assert signature("sample_admin")["completion_id"] == "other_reply"


def test_derived_resident_navigation_resolves_its_canonical_surface(
    monkeypatch,
) -> None:
    """派生 Resident 的导航链接解析到其所属 Lineage 页面"""
    created_resident_id = "resident_222222222222"
    lineage_id = "lineage_333333333333"

    class Resident:
        @staticmethod
        def resolve_resident_identity(resident_id, actor):
            assert (resident_id, actor) == (created_resident_id, "CASE-A")
            return {"resident_id": created_resident_id, "lineage_id": lineage_id}

    monkeypatch.setattr(
        lineage_projection,
        "get_society_service",
        lambda: type("Service", (), {"resident": Resident()})(),
    )

    navigation = lineage_projection.get_resident_navigation(
        created_resident_id,
        actor="CASE-A",
    )

    assert navigation == {
        "resident_id": created_resident_id,
        "url": f"/lineage?lineage={lineage_id}&resident={created_resident_id}",
    }
