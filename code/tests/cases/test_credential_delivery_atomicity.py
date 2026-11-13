"""Message admission and its permissions are one commit, before execution starts.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10, decision_b820faf42218
"""
from types import SimpleNamespace

import pytest

from management_common.async_questions import CREDENTIAL_APPROVE, question_answer_id
from management_portal import permission_system
from management_portal.permission_system import temporary
from management_portal.society_service import resident_conversation_stream, resident_execution_work
from management_portal.society_service.isa import conversation
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401


@pytest.fixture
def delivery(resident_record, permissions, monkeypatch):
    from management_portal.society_service import lifecycle
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *args: None)
    resident = resident_record["resident_id"]
    permissions.users("lender")
    permissions.place(Fact("resident", resident))
    permissions.grant("lender", resident, "@admin")
    requests = [{"a": "@contributor", "resource_id": f"decision_{index:012x}"} for index in (1, 2, 3)]
    for item in requests:
        permissions.place(Fact("decision", item["resource_id"]))
        permissions.grant("lender", item["resource_id"])
    repository = resident_conversation_stream.get_repository()
    from tests.cases.test_resident_question_credentials import fence
    repository.append_message(resident, {"message_id": "msg_permission_question", "role": "agent", "author_id": resident,
        "content_parts": [{"type": "text", "text": fence({"title": "Allow?", "credentials": requests})}]})
    message_id = question_answer_id(resident, "msg_permission_question")
    arguments = {"resident_id": resident, "temporary_credentials": requests, "message": {
        "message_id": message_id, "role": "user", "content_parts": [
            {"type": "text", "text": CREDENTIAL_APPROVE},
            {"type": "async_question_answer", "source_message_id": "msg_permission_question",
             "answers": [{"question_index": 0, "answer": CREDENTIAL_APPROVE}]}]}}
    from management_portal.hatg.contracts import InstructionContext
    inv = SimpleNamespace(argument=arguments.get, caller="lender", idempotency_key="permission-delivery",
                          context=InstructionContext("lender", "web"))
    return SimpleNamespace(record=resident_record, resident=resident, repository=repository, requests=requests,
                           message_id=message_id, inv=inv, arguments=arguments, permissions=permissions)


def loans(delivery):
    return permission_system.manage("ListTemporaryCredentials", {"principal_id": delivery.resident}, actor="lender")["credentials"]


@pytest.mark.parametrize("mode", ["existing_session", "bootstrap", "queued", "info", "facade"])
def test_issue_failure_rolls_back_answer_and_entire_credential_batch(delivery, monkeypatch, mode):
    from management_portal.society_service import resident_backend
    if mode == "bootstrap":
        monkeypatch.setattr(resident_backend, "record_session_id", lambda record: "")
    elif mode == "queued":
        resident_execution_work.write(delivery.record, {
            "execution_id": "execution_previous", "user_message_id": "msg_previous", "actor": "lender",
            "submitted_at": "2026-11-02T00:00:00Z", "queue": [],
        })
    elif mode == "info":
        delivery.arguments["message"]["content_parts"] = [{"type": "text", "text": "Later task"}]
        delivery.arguments["activation_policy"] = "attach_to_next"
    elif mode == "facade":
        from tests.cases.family_test_support import set_role
        set_role(delivery.resident, family_id="fbi_permissions", actor="lender")
    original = temporary.issue
    count = 0
    def fail_second(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise temporary.LoanRefused("injected second signature failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(temporary, "issue", fail_second)
    before = resident_execution_work.read(delivery.record)
    with pytest.raises(Exception, match="injected second signature failure"):
        conversation.deliver_message(delivery.inv)
    assert delivery.repository.message(delivery.resident, delivery.message_id) is None
    assert resident_execution_work.read(delivery.record) == before
    with pytest.raises(KeyError):
        delivery.repository.input_state(delivery.resident, delivery.message_id)
    assert loans(delivery) == []


def test_replay_keeps_one_batch_and_terminal_input_ends_it(delivery):
    first = conversation.deliver_message(delivery.inv)
    second = conversation.deliver_message(delivery.inv)
    assert len(loans(delivery)) == 3
    assert first["temporary_credentials"] == second["temporary_credentials"]
    assert {entry["message_id"] for entry in loans(delivery)} == {delivery.message_id}
    assert all(delivery.permissions.holds(delivery.resident, item["a"], item["resource_id"]) for item in delivery.requests)
    delivery.repository.set_input_state(delivery.resident, delivery.message_id, "failed")
    assert loans(delivery) == []
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert loans(delivery) == []


def test_permission_is_visible_when_admission_wakes_worker(delivery, monkeypatch):
    from management_portal.society_service import lifecycle
    observed = []
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *args: observed.append(len(loans(delivery))))
    conversation.deliver_message(delivery.inv)
    assert observed and all(count == 3 for count in observed)


def test_missing_approved_permissions_refuses_before_acceptance(delivery):
    delivery.arguments.pop("temporary_credentials")
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert delivery.repository.message(delivery.resident, delivery.message_id) is None


@pytest.mark.parametrize("kind", ["declined", "plain_question", "ordinary", "info"])
def test_non_lending_inputs_still_deliver(delivery, kind):
    from management_common.async_questions import CREDENTIAL_DECLINE
    delivery.arguments.pop("temporary_credentials")
    if kind == "declined":
        delivery.arguments["message"]["content_parts"][1]["answers"][0]["answer"] = CREDENTIAL_DECLINE
    elif kind == "plain_question":
        delivery.repository.append_message(delivery.resident, {
            "message_id": "msg_plain_question", "role": "agent", "author_id": delivery.resident,
            "content_parts": [{"type": "text", "text": '```portal-question\n{"title":"Continue?"}\n```'}]})
        message = delivery.arguments["message"]
        message["message_id"] = question_answer_id(delivery.resident, "msg_plain_question")
        message["content_parts"][1]["source_message_id"] = "msg_plain_question"
    else:
        delivery.arguments["message"] = {"message_id": "msg_" + "1" * 32, "role": "user", "content_parts": [{"type": "text", "text": "hello"}]}
        if kind == "info":
            delivery.arguments["activation_policy"] = "attach_to_next"
    result = conversation.deliver_message(delivery.inv)
    assert result["message_id"] == delivery.arguments["message"]["message_id"]
    assert loans(delivery) == []


def test_info_lending_is_atomic_and_replayable(delivery, monkeypatch):
    delivery.arguments["message"] = {"message_id": "msg_" + "2" * 32, "role": "user", "content_parts": [{"type": "text", "text": "hello"}]}
    delivery.arguments["activation_policy"] = "attach_to_next"
    first = conversation.deliver_message(delivery.inv)
    assert len(first["temporary_credentials"]) == 3
    assert conversation.deliver_message(delivery.inv)["temporary_credentials"] == first["temporary_credentials"]


@pytest.mark.parametrize("invalid", ["source", "message_id", "duplicate_index"])
def test_answer_identity_and_indices_are_checked_before_lending(delivery, invalid):
    message = delivery.arguments["message"]
    if invalid == "source":
        message["content_parts"][1]["source_message_id"] = "msg_missing"
    elif invalid == "message_id":
        message["message_id"] = "msg_" + "3" * 32
    else:
        message["content_parts"][1]["answers"] *= 2
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert delivery.repository.message(delivery.resident, message["message_id"]) is None
    assert loans(delivery) == []


def test_admin_guarantee_does_not_override_lending_rules(delivery):
    delivery.permissions.administrator("lender")
    delivery.permissions.deny("lender", delivery.requests[0]["resource_id"], "@contributor")
    assert permission_system.manage("CheckPermission", {"principal_id": "lender", "instruction": "ListPermissionSchemes"},
                                    actor="lender")["portal_admin"]
    with pytest.raises(Exception):
        conversation.deliver_message(delivery.inv)
    assert delivery.repository.message(delivery.resident, delivery.message_id) is None
    assert loans(delivery) == []


def test_replay_does_not_restore_a_revoked_credential(delivery):
    result = conversation.deliver_message(delivery.inv)
    credential_id = result["temporary_credentials"][0]["credential_id"]
    permission_system.manage("RevokeTemporaryCredential", {"credential_id": credential_id}, actor="lender")
    with pytest.raises(Exception, match="原回答的凭证"):
        conversation.deliver_message(delivery.inv)
    assert len(loans(delivery)) == 2


def test_facade_admission_issues_before_router_publication(delivery, monkeypatch):
    from tests.cases.family_test_support import set_role
    from management_portal.society_service import family_recovery
    set_role(delivery.resident, family_id="fbi_permissions", actor="lender")
    observed = []
    monkeypatch.setattr(family_recovery, "handle", lambda *args: observed.append(len(loans(delivery))))
    first = conversation.deliver_message(delivery.inv)
    second = conversation.deliver_message(delivery.inv)
    assert observed == [3, 3]
    assert first["temporary_credentials"] == second["temporary_credentials"]


@pytest.fixture(params=[1, 2], ids=["facade", "nested_facade"])
def projected_delivery(delivery, request):
    from management_portal.society_service import family_facade, resident_record_domain
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.family_test_support import set_role
    from tests.cases.society_service_test_support import publish_registered_lineage_members
    from tests.cases.test_resident_question_credentials import fence

    store = get_lineage_data_store()
    parents = ["resident_bbbbbbbbbbbb", "resident_cccccccccccc"][:request.param]
    publish_registered_lineage_members({**store.lineage_definition(delivery.record["lineage_id"]),
        "nodes": [{"id": rid, "title": "Permission facade"} for rid in parents]}, actor="lender")
    child = delivery.resident
    repo = delivery.repository
    for index, parent in enumerate(parents):
        family = f"fbi_permission_projection{index}"
        set_role(parent, family_id=family, actor="lender")
        set_role(child, family_id=family, role="member", actor="lender")
        delivery.permissions.place(Fact("resident", parent))
        delivery.permissions.grant("lender", parent, "@admin")
        child = parent
    chain = [*reversed(parents), delivery.resident]
    for index, rid in enumerate(chain):
        message = {"message_id": f"msg_task_{index}", "role": "user", "author_id": "lender",
                   "content_parts": [{"type": "text", "text": "original task"}]}
        if index:
            message["business_ref"] = family_facade.relay_mark(chain[index - 1], f"msg_task_{index - 1}")
        repo.append_message(rid, message)
        repo.set_input_state(rid, message["message_id"], "queued")
        if index:
            family_facade.complete_relay((chain[index - 1], f"msg_task_{index - 1}"), rid, message["message_id"])
    repo.append_message(delivery.resident, {"message_id": "msg_projected_question", "role": "agent",
        "author_id": delivery.resident, "reply_to_message_id": f"msg_task_{len(chain) - 1}",
        "content_parts": [{"type": "text", "text": fence({"title": "Allow?", "credentials": delivery.requests})}]})
    delivery.message_id = question_answer_id(delivery.resident, "msg_projected_question")
    delivery.arguments["resident_id"] = chain[0]
    delivery.arguments["message"]["message_id"] = delivery.message_id
    delivery.arguments["message"]["content_parts"][1]["source_message_id"] = "msg_projected_question"
    delivery.parents = parents
    delivery.facade = resident_record_domain.find_resident(chain[0])
    delivery.permissions.deny("lender", delivery.resident, "@admin")
    return delivery


def test_projected_approval_lends_only_to_actual_member_and_replays(projected_delivery, monkeypatch):
    d = projected_delivery
    from management_portal.society_service import family_facade, lifecycle, resident_record_domain
    observed = []
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *args: observed.append(len(loans(d))))
    first = conversation.deliver_message(d.inv)
    second = conversation.deliver_message(d.inv)
    assert first["temporary_credentials"] == second["temporary_credentials"]
    assert len(loans(d)) == 3
    assert observed and all(count == 3 for count in observed)
    for rid in d.parents:
        assert permission_system.manage("ListTemporaryCredentials", {"principal_id": rid}, actor="lender")["credentials"] == []
        assert d.repository.message(rid, d.message_id)["author_id"] == "lender"
        assert family_facade.relay_links(resident_record_domain.find_resident(rid))[d.message_id]["message_id"] == d.message_id
    assert d.repository.message(d.resident, d.message_id)["author_id"] == "lender"
    d.repository.set_input_state(d.resident, d.message_id, "completed")
    assert loans(d) == []
    with pytest.raises(Exception):
        conversation.deliver_message(d.inv)
    assert loans(d) == []


@pytest.mark.parametrize("failure", ["signature", "denied", "closed", "wrong_id", "unrelated", "quota"])
def test_projected_failure_never_accepts_or_lends(projected_delivery, monkeypatch, failure):
    d = projected_delivery
    from management_portal.society_service import family_facade, family_quota, lifecycle, resident_record_domain
    wakes = []
    monkeypatch.setattr(lifecycle, "wake_execution_binding", lambda *args: wakes.append(args))
    if failure == "signature":
        original = temporary.issue
        calls = []
        def fail_second(*args, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                raise temporary.LoanRefused("signature failure")
            return original(*args, **kwargs)
        monkeypatch.setattr(temporary, "issue", fail_second)
    elif failure == "denied":
        d.permissions.deny("lender", d.requests[1]["resource_id"], "@contributor")
    elif failure == "closed":
        d.repository.set_input_state(d.resident, d.message_id, "cancelled", error={"code": "question_set_closed"})
    elif failure == "wrong_id":
        d.arguments["message"]["message_id"] = "msg_wrong_answer"
    elif failure == "unrelated":
        d.arguments["message"]["content_parts"][1]["source_message_id"] = "msg_permission_question"
    else:
        monkeypatch.setattr(family_quota, "reserve_remaining", lambda *_: 0)
    before = resident_execution_work.read(d.record)
    with pytest.raises(Exception):
        conversation.deliver_message(d.inv)
    assert loans(d) == []
    assert not wakes
    if failure == "signature":
        assert len(calls) == 2
    assert resident_execution_work.read(d.record) == before
    for rid in [*d.parents, d.resident]:
        assert d.repository.message(rid, d.message_id) is None
    for rid in d.parents:
        assert d.message_id not in family_facade.relay_links(resident_record_domain.find_resident(rid))
        with pytest.raises(KeyError):
            d.repository.input_state(rid, d.message_id)


def test_projected_replay_does_not_reissue_revoked_permissions(projected_delivery):
    d = projected_delivery
    result = conversation.deliver_message(d.inv)
    permission_system.manage("RevokeTemporaryCredential",
        {"credential_id": result["temporary_credentials"][0]["credential_id"]}, actor="lender")
    with pytest.raises(Exception, match="原回答的凭证"):
        conversation.deliver_message(d.inv)
    assert len(loans(d)) == 2
