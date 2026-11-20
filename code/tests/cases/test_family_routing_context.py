"""Compact Router tasks and permission-checked, on-demand routing material.

:module: lineage.team
:design: decision_89b9df038a95
"""
import json

import pytest

from management_portal.society_service import family_tasks, family_routing_context
from management_portal.society_service import family_chat_preferences, family_chat_storage, resident_access
from management_portal.society_service import resident_conversation_stream, resident_input_queue_domain
from management_portal.society_service.resident_record_domain import find_resident
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.isa_test_support import ACTOR, ok
from tests.cases.test_family_tasks import charters, configured_family, future_creation_store, lineage  # noqa: F401


def request(family):
    mid = "msg_10000000000000000000000000000001"
    message = resident_input_queue_domain.build_message("user", "请查看 ${facts}", ACTOR, message_id=mid)
    resident_conversation_stream.get_repository().accept_user_message(family["facade"], message)
    return {"resident_id": family["facade"], "message_id": mid}


def test_route_size_independent_of_roster_and_previous_text(configured_family):
    _, block = configured_family
    family = block["family"]
    router = find_resident(family["router"])
    facts = {"message_id": "original", "facade": family["facade"], "sender": ACTOR,
             "content": [{"type": "text", "text": "hello"}]}
    lean = family_tasks.render(router, "route", facts)
    full = {**facts, "routing": {"members": ["resident_abcd" + str(i) for i in range(500)],
             "candidates": [{"current_focus": "大段无关工作" * 5000}]},
            "previous_round": {"request": "旧请求" * 6000, "reply": "旧答复" * 16000}}
    assert family_tasks.render(router, "route", full) == lean
    assert len(lean) < 700


def test_dispatch_does_not_prefetch_summaries_and_keeps_retry_text(configured_family, monkeypatch):
    from management_portal.society_service.resident_context.store import ResidentContextStore
    monkeypatch.setattr(ResidentContextStore, "documents", lambda *a: pytest.fail("automatic summary prefetch"))
    _, block = configured_family
    family = block["family"]
    source = request(family)
    args = {"resident_id": family["facade"], "source_message_id": source["message_id"],
            "trigger": "route", "message_id": "msg_20000000000000000000000000000001"}
    ok("DispatchFamilyTask", args)
    def intent():
        with get_lineage_data_store().database.read() as connection:
            row = connection.execute("SELECT intent_json FROM family_task_dispatches WHERE message_id=%s", (args["message_id"],)).fetchone()
        return json.loads(row["intent_json"]) if isinstance(row["intent_json"], str) else row["intent_json"]
    original = intent()["text"]
    assert "routing" not in original and "previous_round" not in original
    assert all(rid not in original for rid in [*family["members"], family["steward"]])
    monkeypatch.setattr(family_tasks, "render", lambda *a, **k: pytest.fail("replay rerendered"))
    ok("DispatchFamilyTask", args)
    assert intent()["text"] == original


def test_member_reader_honors_scope_and_is_read_only(configured_family):
    body, block = configured_family
    family = block["family"]
    args = request(family)
    a, b = family["members"]
    family_chat_preferences.change(family["facade"], ACTOR, b, "mute", True)
    result = ok("GetFamilyRoutingContext", args, actor=family["router"])
    assert [m["resident_id"] for m in result["members"]] == [a, family["steward"]]
    assert "previous_round" not in result and "summary" not in str(result)
    result = ok("GetFamilyRoutingContext", {**args, "section": "previous_round"})
    assert result["previous_round"] is None
    state = get_lineage_data_store().family_relays.get(body["lineage_id"], family["facade"])
    assert args["message_id"] not in state.get("continuations", {})
    with get_lineage_data_store().database.read() as connection:
        assert family_chat_storage.read_snapshot(connection, family["facade"], args["message_id"]) is None

    # After admission the reader observes the request's frozen sender policy.
    from management_portal.society_service import family_facade
    family_facade.routing_snapshot(find_resident(family["facade"]),
        resident_conversation_stream.get_repository().message(family["facade"], args["message_id"]))
    family_chat_preferences.change(family["facade"], ACTOR, b, "mute", False)
    frozen = ok("GetFamilyRoutingContext", args, actor=family["router"])
    assert [m["resident_id"] for m in frozen["members"]] == [a, family["steward"]]


def test_previous_reader_returns_frozen_round_without_scanning_history(configured_family, monkeypatch):
    body, block = configured_family
    family = block["family"]
    args = request(family)
    prior = {"message_id": "old", "request": {"text": "唯一旧问题"}, "reply": {"text": "唯一旧答案"}}
    with get_lineage_data_store().family_relays.transaction(body["lineage_id"], family["facade"]) as ledger:
        ledger.setdefault("continuations", {})[args["message_id"]] = {"previous_round": prior}
    repository = resident_conversation_stream.get_repository()
    monkeypatch.setattr(repository, "snapshot", lambda *a, **k: pytest.fail("reader rescanned history"))
    assert ok("GetFamilyRoutingContext", {**args, "section": "previous_round"})["previous_round"] == prior
    monkeypatch.setattr(resident_access, "visible_resident_records", lambda *a: [])
    with pytest.raises(PermissionError):
        family_routing_context.read(family["facade"], args["message_id"], "unauthorized", section="previous_round")


def test_read_permission_is_enforced_before_accessing_messages(configured_family, monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    from tests.cases.isa_test_support import run
    body, block = configured_family
    family = block["family"]
    args = request(family)
    permissions = enable(monkeypatch)
    permissions.place(Fact('lineage', body['lineage_id']), Fact('resident', family['facade']))
    permissions.grant(family['router'], body['lineage_id'], '@viewer')
    permissions.grant(family['router'], family['facade'], '@viewer')
    assert ok('GetFamilyRoutingContext', args, actor=family['router'])['members']
    permissions.deny(family['router'], family['facade'], '@viewer')
    monkeypatch.setattr(resident_conversation_stream.get_repository(), 'message',
                        lambda *a: pytest.fail('unauthorized message read'))
    assert run('GetFamilyRoutingContext', args, actor=family['router']).status != 'committed'
