"""Bound Home index assembly; all identities and receipts use isolated PostgreSQL.

:importance: P1
"""
from __future__ import annotations

import json
import time

import pytest

from management_portal import serve_management_portal as portal
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import lineage_aggregate
from tests.cases.society_service_test_support import (
    _install_isolated_portal_access,
    publish_registered_lineage_members,
)
from tests.cases.test_agent_conversation_index_backend import _lineage_definition
from tests.cases.test_agent_data_store import _chat


@pytest.mark.parametrize("count", [4, 50])
def test_page_residents_are_not_hydrated_again_for_overlapping_pins(monkeypatch, count):
    """置顶的 Resident 已在当前页时 Home 对话列表不再重复加载它们

    :importance: P2"""
    actor = "home-perf-isolated-viewer"
    _install_isolated_portal_access(monkeypatch)
    ids = [f"resident_{i + 1:012x}" for i in range(count)]
    definition = _lineage_definition("lineage_123456789abc", [])
    definition["created_by"] = actor
    lineage_aggregate.lineage_catalog_store().upsert(definition)
    publish_registered_lineage_members({**definition,
        "nodes": [{"id": rid, "title": rid} for rid in ids]}, actor)
    data = AgentDataStore()
    for i, rid in enumerate(ids):
        data.display_chat_message(rid, _chat(
            f"request-{i}", "user", "isolated fixture", author_id=actor,
        ))
    # Initialize cold readers and portraits before timing a repeat read.
    head = portal._agent_conversation_payload(actor)
    assert {entry["resident_id"] for entry in head["entries"]} == set(ids)
    for rid in ids[:10]:
        portal.conversation_read_state.set_pinned(actor, "resident:" + rid, True)
    redundant = []
    original = portal._agent_conversation_entries
    def track(actor, resident_ids=None):
        redundant.extend(resident_ids or [])
        return original(actor, resident_ids)
    monkeypatch.setattr(portal, "_agent_conversation_entries", track)
    start = time.perf_counter()
    result = portal._agent_conversation_payload(actor)
    print(json.dumps({"environment": "isolated-pg", "actor": actor, "count": count,
        "overlapping_pins": min(count, 10), "redundant_residents": redundant,
        "elapsed_ms": round((time.perf_counter()-start)*1000, 3)}))
    assert {entry["resident_id"] for entry in result["entries"]} == set(ids)
    assert sum(entry["pinned"] for entry in result["entries"]) == min(count, 10)
    assert result["ready"] and not result["refreshing"] and not result["error"]
    assert redundant == [], "on-page pins must reuse this request's authorized projection"


def _entry(rid):
    return {"key": "resident:" + rid, "resident_id": rid, "kind": "resident-main",
            "title": rid, "updated_at": "2026-10-01T00:00:00Z", "cursor": 7}


def test_off_page_pins_still_load_and_release_only_missing_targets(monkeypatch):
    """当前页之外的置顶对话仍加载，只取消已不存在或无权访问的置顶"""
    actor, other = "home-perf-viewer", "home-perf-other"
    on_page, older, no_chat, hidden, deleted = [f"resident_{i:012x}" for i in range(1, 6)]
    for rid in (on_page, older, no_chat, hidden, deleted):
        portal.conversation_read_state.set_pinned(actor, "resident:" + rid, True)
    portal.conversation_read_state.set_pinned(other, "resident:" + hidden, True)
    portal.conversation_read_state.set_read(actor, "resident:" + older, unread_value=True)
    monkeypatch.setattr(portal.agent_conversation_index_backend, "build_entries_page",
        lambda who, **options: {"entries": [_entry(on_page)], "paged": True,
                               "page_size": 1, "next_page_token": "older-page"})
    monkeypatch.setattr(portal, "_mailbox_source_entries", lambda who: [])
    calls = []
    def pinned(who, ids):
        calls.append((who, set(ids)))
        # The authorized source confirms absent/forbidden targets by omission,
        # while a visible node with no Chat still has its pin entry.
        return [_entry(rid) for rid in ids if rid in {older, no_chat}]
    monkeypatch.setattr(portal, "_agent_conversation_entries", pinned)
    monkeypatch.setattr(portal.agent_conversation_index_backend, "unread_resident_entries",
        lambda who, state: [])
    result = portal._agent_conversation_payload(actor, page_size=1)
    assert calls == [(actor, {older, no_chat, hidden, deleted})]
    assert {e["resident_id"] for e in result["entries"]} == {on_page, older, no_chat}
    assert result["next_page_token"] == "older-page" and result["page_size"] == 1
    assert result["unread_count"] == result["mailbox_summary"]["unread_count"] == 1
    assert set(portal.conversation_read_state.load(actor)["pinned_keys"]) == {
        "resident:" + rid for rid in (on_page, older, no_chat)}
    assert portal.conversation_read_state.load(other)["pinned_keys"] == ["resident:" + hidden]
