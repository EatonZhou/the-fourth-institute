"""Independent facade membership, relay and recovery.

:module: lineage.family
"""
from copy import deepcopy

import pytest

from management_common.principal import message_origin
from management_portal.execution.transport import ExecutionResponseUnknown
from management_portal.society_service import (
    facade_members, family_facade, family_routing_context, family_storage, lineage_message_events, outward_messages,
    relay_domain, resident_account_policy, resident_conversation_stream, resident_domain, resident_input_queue_domain,
    resident_record_domain,
)
from management_portal.society_service.resident_mailbox import MailboxConflict
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.family_test_support import set_role
from tests.cases.isa_test_support import run
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.resident_execution_test_support import input_delivery, resident_record  # noqa: F401
from tests.cases.society_service_test_support import publish_registered_lineage_members

ACTOR = "CASE-A"
MEMBER = "resident_bbbbbbbbbbbb"
SPARE = "resident_cccccccccccc"


@pytest.fixture
def team(input_delivery):
    store = get_lineage_data_store()
    lineage = store.lineage_definition(input_delivery["lineage_id"])
    publish_registered_lineage_members({
        **lineage,
        "created_at": "2026-10-05T00:00:00Z",
        "updated_at": "2026-10-05T00:00:00Z",
        "nodes": [{"id": MEMBER, "title": "Member"}, {"id": SPARE, "title": "Spare"}],
    }, actor=ACTOR)
    return input_delivery["resident_id"], store


def test_replace_is_atomic_and_rejects_cycles(team):
    facade, store = team
    assert relay_domain.update_facade_members(facade, [SPARE, MEMBER, MEMBER], ACTOR) == {
        "resident_id": facade, "member_ids": [MEMBER, SPARE],
    }
    with pytest.raises(ValueError, match="闭环"):
        store.database.write(lambda c: facade_members.replace(c, MEMBER, [facade]))
    with pytest.raises(ValueError, match="自己"):
        relay_domain.update_facade_members(facade, [facade], ACTOR)
    with store.database.read() as connection:
        assert facade_members.read(connection, MEMBER) == []
        assert facade_members.read(connection, facade) == [MEMBER, SPARE]
    assert relay_domain.update_facade_members(facade, [], ACTOR)["member_ids"] == []
    assert not family_facade.is_facade({"resident_id": facade})


def test_legacy_family_backfill_does_not_revive_empty_registry(team):
    facade, store = team
    for resident_id, role in [(facade, "facade"), (MEMBER, "member"), (SPARE, "steward")]:
        set_role(resident_id, family_id="fbi_family0001", role=role, actor=ACTOR)

    def migrate(connection):
        connection.execute("DROP TABLE facade_members")
        facade_members.ensure_schema(connection)
        assert facade_members.read(connection, facade) == [MEMBER, SPARE]
        facade_members.replace(connection, facade, [])
        facade_members.ensure_schema(connection)
        assert facade_members.read(connection, facade) == []

    store.database.write(migrate)


def test_family_roster_change_and_facade_replacement_share_a_transaction(team):
    facade, store = team
    lineage_id = "lineage_000000000001"
    family = {"title": "Family", "facade": facade, "members": [MEMBER]}
    definition = {"function_blocks": [{"id": "fbi_family0001", "family": family}]}

    def store_family(connection):
        family_storage.store_program(connection, lineage_id, definition, "2026-11-26T00:00:00Z")

    with store.database.transaction() as connection:
        store_family(connection)
        assert facade_members.read(connection, facade) == [MEMBER]
    family["steward"] = SPARE
    with pytest.raises(RuntimeError, match="rollback"):
        with store.database.transaction() as connection:
            store_family(connection)
            assert facade_members.read(connection, facade) == [MEMBER, SPARE]
            raise RuntimeError("rollback")
    with store.database.read() as connection:
        assert facade_members.read(connection, facade) == [MEMBER]
        assert family_storage.roles_of(connection, "fbi_family0001")["steward"] == []
    store.database.write(store_family)
    store.database.write(lambda c: family_storage.store_program(
        c, lineage_id, {"function_blocks": []}, "2026-11-26T00:00:00Z",
    ))
    with store.database.read() as connection:
        assert facade_members.read(connection, facade) == []


def _request(facade, text):
    return resident_domain.deliver_resident_message_content(facade, ACTOR, text)["message_id"]


def test_generic_relay_keeps_the_real_author_content_and_receipt(team):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER, SPARE]))
    source = _request(facade, "这是原始消息")
    repository = resident_conversation_stream.get_repository()
    original = deepcopy(repository.message(facade, source))
    context = family_routing_context.read(facade, source, ACTOR, section="request")
    assert context["request"]["content_parts"] == original["content_parts"]
    assert context["request"]["input_state"]["state"] == "queued"
    assert [entry["resident_id"] for entry in context["members"]] == [MEMBER, SPARE]
    assert not store.family_relays.get("lineage_000000000001", facade).get("links")

    delivered = relay_domain.relay_message(facade, source, MEMBER, ACTOR)
    assert delivered["status"] == "relayed"
    copy = repository.message(MEMBER, delivered["message_id"])
    assert copy["author_id"] == original["author_id"]
    assert copy["content_parts"] == original["content_parts"]
    assert repository.message(facade, source) == original
    assert relay_domain.relay_message(facade, source, MEMBER, ACTOR) == delivered
    with pytest.raises(ValueError, match="其他承接"):
        relay_domain.relay_message(facade, source, SPARE, ACTOR)


def test_requeue_reopens_only_the_failed_original(team, monkeypatch):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER]))
    message_id = _request(facade, "重开这条原请求")
    repository = resident_conversation_stream.get_repository()
    original = deepcopy(repository.message(facade, message_id))
    wakes = []
    monkeypatch.setattr(lineage_message_events, "wake_watchers", lambda *args: wakes.append(args))
    repository.set_input_state(facade, message_id, "failed")
    retry = relay_domain.requeue_failed(facade, message_id, ACTOR)
    assert retry["input_state"]["state"] == "queued"
    assert retry["message_id"] == message_id
    assert repository.message(facade, message_id) == original
    events = repository.events_after(facade, 0)["events"]
    first, reopened = [arrival for arrival in lineage_message_events.message_arrivals(events)
                       if arrival["message_id"] == message_id]
    assert "occurrence_id" not in first
    assert reopened["occurrence_id"] == f"{message_id}:retry:{reopened['cursor']}"
    details = lineage_message_events.arrival_details(
        repository, facade, message_id, ACTOR, resident_record_domain.find_resident(facade),
        routing_occurrence=reopened["occurrence_id"],
    )
    event = lineage_message_events.arrival_event("event_source_000000000001", facade, {**reopened, **details})
    original_event = lineage_message_events.arrival_event(
        "event_source_000000000001", facade, {**first, **details, "occurrence_id": message_id},
    )
    assert event.id != original_event.id and event.payload["message_id"] == message_id
    assert len(wakes) == 1
    assert relay_domain.requeue_failed(facade, message_id, ACTOR)["status"] == "queued"
    assert len(wakes) == 1
    repository.set_input_state(facade, message_id, "cancelled")
    with pytest.raises(MailboxConflict, match="已经取消"):
        relay_domain.requeue_failed(facade, message_id, ACTOR)


def test_uncertain_relay_cannot_open_another_holder(team, monkeypatch):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER, SPARE]))
    message_id = _request(facade, "核对转交回执")
    original_delivery = outward_messages.deliver_to_resident

    def unknown(*args, **kwargs):
        raise ExecutionResponseUnknown("delivery response unavailable")

    monkeypatch.setattr(outward_messages, "deliver_to_resident", unknown)
    with pytest.raises(ExecutionResponseUnknown):
        relay_domain.relay_message(facade, message_id, MEMBER, ACTOR)
    with pytest.raises(ValueError, match="待确认"):
        relay_domain.relay_message(facade, message_id, SPARE, ACTOR)
    monkeypatch.setattr(outward_messages, "deliver_to_resident", original_delivery)
    receipt = relay_domain.relay_message(facade, message_id, MEMBER, ACTOR)
    assert receipt["member_id"] == MEMBER
    assert not store.family_relays.get("lineage_000000000001", facade).get("pending_deliveries")


@pytest.mark.parametrize("permission_mode", [False, True], ids=["source", "message-permission"])
def test_relay_reply_uses_received_source_and_retains_message_only_permission(team, request, permission_mode):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER]))
    if permission_mode:
        permission_fixture = request.getfixturevalue("permissions")
        permission_fixture.place(Fact("resident", facade), Fact("resident", SPARE))
        permission_fixture.grant(SPARE, facade, "@message")
        permission_fixture.deny(SPARE, facade, "RelayMessage:relay")
    repository = resident_conversation_stream.get_repository()
    source = resident_input_queue_domain.build_message("user", "接口何时冻结？", facade)
    repository.append_message(SPARE, source)
    record = resident_input_queue_domain.build_message("agent", "接口何时冻结？", facade)
    record.update(message_id=outward_messages.outward_record_id(facade, source["message_id"]),
                  recipients=[{"kind": "resident", "id": SPARE}],
                  origin=message_origin(facade, initiated_by=MEMBER))
    repository.append_message(facade, record)
    arguments = {
        "resident_id": facade,
        "reply_to_message_id": source["message_id"],
        "message": {"content_parts": [{"type": "text", "text": "今天冻结。"}]},
    }
    reply = run("RelayMessage", arguments, actor=SPARE, key="same-relay-reply").raise_for_error()
    assert reply["status"] == "replied"
    stored = repository.message(facade, reply["message_id"])
    assert stored["author_id"] == SPARE
    assert stored["quote"]["message_id"] == record["message_id"]
    assert family_facade.quoted_member({"resident_id": facade}, stored) == MEMBER
    replay = run("RelayMessage", arguments, actor=SPARE, key="same-relay-reply").raise_for_error()
    assert replay["status"] == "replied"
    assert replay["message_id"] == reply["message_id"]
    if permission_mode:
        denied = run("RelayMessage", {"resident_id": facade, "message_id": reply["message_id"],
                                       "member_id": MEMBER}, actor=SPARE)
        assert denied.error["code"] == "instruction.forbidden"


def test_check_target_keeps_unknown_quota_distinct_from_zero(team, monkeypatch):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER]))
    account = {"provider": "codex", "enabled": True}
    monkeypatch.setattr(resident_account_policy, "resident_account", lambda *_: account)
    assert relay_domain.check_target(facade, MEMBER, ACTOR) == {
        "status": "unknown", "reason": "quota_unknown", "remaining_percent": None,
    }
    account["quota_reserve"] = {"fresh": True, "remaining": 0, "windows": [{"remaining_percent": 0}]}
    assert relay_domain.check_target(facade, MEMBER, ACTOR)["status"] == "quota_low"
    account["quota_reserve"]["windows"][0]["remaining_percent"] = 90
    account["quota_reserve"]["remaining"] = 90
    assert relay_domain.check_target(facade, MEMBER, ACTOR)["status"] == "ready"
    monkeypatch.setattr(resident_record_domain, "lifecycle_operations", lambda: type(
        "Lifecycle", (), {"pending": lambda self, rid: rid == MEMBER},
    )())
    assert relay_domain.check_target(facade, MEMBER, ACTOR) == {
        "status": "unavailable", "reason": "lifecycle_pending", "remaining_percent": None,
    }


def test_resolve_binds_review_reply_to_the_failed_request(team):
    facade, store = team
    store.database.write(lambda c: facade_members.replace(c, facade, [MEMBER]))
    message_id = _request(facade, "请核对旧请求")
    repository = resident_conversation_stream.get_repository()
    repository.set_input_state(facade, message_id, "failed")
    candidate = resident_input_queue_domain.build_message("user", "review", facade)
    candidate["business_ref"] = f"relay-review/{facade}/message/{message_id}"
    candidate["origin"] = message_origin(facade)
    repository.append_message(MEMBER, candidate)
    review_id = candidate["message_id"]
    repository.set_input_state(MEMBER, review_id, "queued")
    with pytest.raises(ValueError):
        relay_domain.resolve_request(facade, message_id, review_id, ACTOR, reason="完成复核")
    reply = resident_input_queue_domain.build_message("agent", "核对完成", MEMBER)
    reply["reply_to_message_id"] = review_id
    repository.append_message(MEMBER, reply)
    repository.set_input_state(MEMBER, review_id, "completed")
    receipt = relay_domain.resolve_request(facade, message_id, review_id, ACTOR, reason="完成复核")
    assert receipt["review_message_id"] == review_id
    assert receipt["reply_message_ids"] == [reply["message_id"]]
    assert repository.input_state(facade, message_id)["state"] == "failed"
