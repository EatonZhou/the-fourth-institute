"""Exact reply lookup retains durable Family provenance without scanning history."""
import pytest

from management_portal.society_service import family_facade, resident_conversation_stream, resident_record_domain
from management_portal.society_service.resident_input_queue_domain import build_message
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.family_test_support import set_role
from tests.cases.test_family_facade import facade, _register_test_member, MEMBER  # noqa: F401
from tests.cases.resident_execution_test_support import input_delivery, resident_record  # noqa: F401


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("history", [0, 1000])
def test_exact_reply_is_bounded_by_relay_depth(facade, monkeypatch, nested, history):
    _register_test_member(facade, "worker")
    repo = resident_conversation_stream.get_repository()
    outer = build_message("user", "task", "CASE-A")
    repo.append_message(facade["resident_id"], outer)
    repo.set_input_state(facade["resident_id"], outer["message_id"], "queued")
    worker = MEMBER
    if nested:
        set_role(MEMBER, family_id="fbi_exact_reply", actor="CASE-A")
        # The original facade remains the root of this test; its unrelated
        # direct-role metadata is immaterial to a persisted nested relay.
        inner = build_message("user", "task", "CASE-A")
        inner["business_ref"] = family_facade.relay_mark(facade["resident_id"], outer["message_id"])
        repo.append_message(MEMBER, inner)
        repo.set_input_state(MEMBER, inner["message_id"], "queued")
        family_facade.complete_relay((facade["resident_id"], outer["message_id"]), MEMBER, inner["message_id"])
        worker = "resident_cccccccccccc"
        from tests.cases.society_service_test_support import publish_registered_lineage_members
        store = get_lineage_data_store()
        lineage = store.lineage_definition(facade["lineage_id"])
        publish_registered_lineage_members({**lineage, "nodes": [{"id": worker, "title": "nested worker"}]}, actor="CASE-A")
        source = (MEMBER, inner["message_id"])
    else:
        source = (facade["resident_id"], outer["message_id"])
    request = build_message("user", "task", "CASE-A")
    request["business_ref"] = family_facade.relay_mark(*source)
    repo.append_message(worker, request)
    family_facade.complete_relay(source, worker, request["message_id"])
    answer = build_message("agent", "answer", worker)
    answer["reply_to_message_id"] = request["message_id"]
    repo.append_message(worker, answer)
    store = get_lineage_data_store()
    with store.family_relays.transaction(facade["lineage_id"], facade["resident_id"]) as ledger:
        for i in range(history):
            ledger.setdefault("links", {})[f"msg_history_{i}"] = {"resident_id": worker, "message_id": f"msg_other_{i}"}
    reads = []
    original = resident_record_domain.find_resident_for_admission
    def thin(identity):
        reads.append(identity)
        return original(identity)
    monkeypatch.setattr(resident_record_domain, "find_resident_for_admission", thin)
    monkeypatch.setattr(resident_record_domain, "find_resident", lambda *_: pytest.fail("full hydration"))
    monkeypatch.setattr(family_facade, "relay_links", lambda *_: pytest.fail("historical ledger walk"))
    found = family_facade.relayed_reply(facade, answer["message_id"])
    assert found["worker_id"] == worker
    assert found["request_message_id"] == outer["message_id"]
    assert found["member_id"] == MEMBER
    assert found["via"] == ([MEMBER] if nested else [])
    assert len(reads) == (2 if nested else 1)


def test_exact_identity_is_candidate_not_a_relay_entitlement(facade):
    _register_test_member(facade, "worker")
    repo = resident_conversation_stream.get_repository()
    request = build_message("user", "unrelated", "CASE-A")
    # A forged mark without the facade's durable accepted edge grants nothing.
    request["business_ref"] = family_facade.relay_mark(facade["resident_id"], "msg_forged")
    repo.append_message(MEMBER, request)
    answer = build_message("agent", "not a Family answer", MEMBER)
    answer["reply_to_message_id"] = request["message_id"]
    repo.append_message(MEMBER, answer)
    assert repo.agent_data.chat.message_agent_ids(answer["message_id"]) == [MEMBER]
    assert family_facade.relayed_reply(facade, answer["message_id"]) is None
    repo.agent_data.clear_chat(MEMBER, actor_ref="CASE-A")
    assert family_facade.relayed_reply(facade, answer["message_id"]) is None
