"""A projected approval grants only to the worker that asked the question."""

from unittest.mock import Mock

import pytest

from management_common.async_questions import CREDENTIAL_APPROVE, question_message_parts
from management_portal import society_service
from management_portal.execution.service import ExecutionService
from management_portal.permission_system.facts import Fact
from management_portal.society_service import (
    contracts, facade_members, family_facade, lineage_message_events, resident_conversation_stream, resident_domain,
    resident_input_queue_domain, resident_protocol, resident_record_domain, server,
)
from management_portal.society_service.transport import SocietyServiceClient
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.family_test_support import set_role
from tests.cases.isa_test_support import run
from tests.cases.permission_test_support import enable
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import _new_session_payload, future_creation_store  # noqa: F401


@pytest.fixture
def approval(delivery_resident, monkeypatch, request):
    facade_id = delivery_resident["resident_id"]
    lineage_id = delivery_resident["lineage_id"]

    def register(title):
        return resident_domain.register_resident({
            **_new_session_payload(title), "lineage_id": lineage_id,
            "lineage_folder_path": delivery_resident["workspace_path"],
        }, "sample_admin", lineage_node={})["resident_id"]

    worker_id = register("Question worker")
    nested_id = register("Nested facade") if getattr(request, "param", False) else ""
    resident_protocol.change_resident_state(worker_id, "sample_admin", state_domain="intake", transition="close")
    set_role(facade_id, family_id="fbi_family0001", actor="sample_admin")
    set_role(worker_id, family_id="fbi_family0001", role="member", actor="sample_admin")
    with get_lineage_data_store().database.transaction() as connection:
        facade_members.replace(connection, facade_id, [nested_id or worker_id])
        if nested_id:
            facade_members.replace(connection, nested_id, [worker_id])
    repo = resident_conversation_stream.get_repository()
    user = "family-approval-user"
    permissions = enable(monkeypatch)
    permissions.users(user)
    permissions.place(Fact("resident", facade_id))
    permissions.place(Fact("resident", worker_id))
    permissions.grant(user, facade_id, "@message")
    permissions.deny(user, worker_id, "@message")
    issuer_grant = permissions.grant(user, "@shared", "@admin")

    def dispatch(_client, method, params=None, **kwargs):
        assert method == "domain.call"
        operation = contracts.operation_contract(params["domain"], params["name"])
        handler, bound = server._bind_domain_call(params["domain"], params["name"], params["args"], params["kwargs"])
        return server._invoke_domain(operation, handler, bound)

    client = SocietyServiceClient()
    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    monkeypatch.setattr(society_service, "get_society_service", lambda: client)
    monkeypatch.setattr(ExecutionService, "accounts", lambda _service: {"accounts": []})

    permission = {"a": "@admin", "resource_id": "@shared"}
    outer = resident_input_queue_domain.build_message("user", "Check this", user)
    inner = resident_input_queue_domain.build_message("user", "Check this", user)
    repo.append_message(facade_id, outer)
    repo.append_message(worker_id, inner)
    repo.set_input_state(facade_id, outer["message_id"], "queued")
    repo.set_input_state(worker_id, inner["message_id"], "running")
    if nested_id:
        middle = resident_input_queue_domain.build_message("user", "Check this", user)
        repo.append_message(nested_id, middle)
        repo.set_input_state(nested_id, middle["message_id"], "queued")
        family_facade.complete_relay((nested_id, middle["message_id"]), worker_id, inner["message_id"])
        family_facade.complete_relay((facade_id, outer["message_id"]), nested_id, middle["message_id"])
    else:
        family_facade.complete_relay((facade_id, outer["message_id"]), worker_id, inner["message_id"])
    question = resident_input_queue_domain.build_message("agent", "", worker_id)
    question["reply_to_message_id"] = inner["message_id"]
    question["content_parts"] = question_message_parts("", [{
        "question_index": 0, "title": "May I use the shared authority?", "credentials": [permission],
    }], worker_id, question["message_id"])
    repo.append_message(worker_id, question)
    assert family_facade.relayed_reply(delivery_resident, question["message_id"])["worker_id"] == worker_id
    message_id = question["content_parts"][0]["answer_message_id"]
    args = {"resident_id": facade_id, "message": {"message_id": message_id, "content_parts": [
        {"type": "text", "text": CREDENTIAL_APPROVE}, {
        "type": "async_question_answer", "source_message_id": question["message_id"],
        "answers": [{"question_index": 0, "answer": CREDENTIAL_APPROVE}],
    }]}}
    credential = {"principal_id": worker_id, "message_id": message_id, **permission}
    return user, permissions, repo, args, credential, issuer_grant, nested_id


@pytest.mark.parametrize("approval", [False, True], indirect=True, ids=["one-facade", "nested-facades"])
def test_facade_approval_uses_real_user_and_persisted_worker(approval):
    user, permissions, repo, args, credential, _, nested_id = approval
    facade_id, message_id, worker_id = args["resident_id"], credential["message_id"], credential["principal_id"]
    assert not permissions.holds(user, "@message", worker_id)
    receipt = run("PrepareResidentInput", args, actor=user, key="facade-prepare").raise_for_error()
    assert receipt["resident_id"] == facade_id
    assert receipt["credential_recipient_id"] == worker_id
    assert receipt["credential_message_id"] == message_id
    assert repo.message(facade_id, message_id) is None and repo.message(worker_id, message_id) is None
    assert run("DeliverMessage", {**args, "resident_id": worker_id}, actor=user,
               key="send-direct-to-worker").status != "committed"
    assert run("CreateTemporaryCredential", {**credential, "principal_id": facade_id},
               actor=user, key="stage-at-facade").status != "committed"
    assert run("CreateTemporaryCredential", credential, actor=user, key="stage-at-worker").raise_for_error()["state"] == "pending"
    assert not permissions.holds(worker_id, "@admin", "@shared")
    assert run("DeliverMessage", args, actor=user, key="send-facade-approval").raise_for_error()["accepted"]
    for target in filter(None, (facade_id, nested_id, worker_id)):
        message = repo.message(target, message_id)
        assert message["author_id"] == user
        assert message["origin"]["sender"] == {"kind": "user", "id": user}
        assert permissions.holds(target, "@admin", "@shared") == (target == worker_id)
    assert family_facade.relay_links(resident_record_domain.find_resident(facade_id))[message_id]["resident_id"] == (nested_id or worker_id)
    if nested_id:
        assert family_facade.relay_links(resident_record_domain.find_resident(nested_id))[message_id]["resident_id"] == worker_id
    assert run("DeliverMessage", args, actor=user, key="resend-facade-approval").raise_for_error()["accepted"]
    repo.set_input_state(worker_id, message_id, "completed")
    assert not permissions.holds(worker_id, "@admin", "@shared")


@pytest.mark.parametrize("invalid", ["unprepared", "missing", "extra", "revoked", "issuer-revoked", "target-changed", "closed"])
def test_invalid_facade_approval_does_not_admit_or_wake(approval, monkeypatch, invalid):
    user, permissions, repo, args, credential, issuer_grant, _ = approval
    facade_id, message_id, worker_id = args["resident_id"], credential["message_id"], credential["principal_id"]
    if invalid != "unprepared":
        run("PrepareResidentInput", args, actor=user, key="facade-prepare").raise_for_error()
    if invalid not in {"unprepared", "missing"}:
        staged = run("CreateTemporaryCredential", credential, actor=user, key="stage-worker").raise_for_error()
    if invalid == "extra":
        result = run("CreateTemporaryCredential", {**credential, "a": "@viewer"}, actor=user, key="stage-extra")
        if result.status != "committed":
            assert "批准" in result.error["message"]
            return  # This request was stopped at staging, before delivery was attempted.
    elif invalid == "revoked":
        run("RevokeTemporaryCredential", {"credential_id": staged["credential_id"]}, actor=user,
            key="revoke-staged").raise_for_error()
    elif invalid == "issuer-revoked":
        with permissions.store.transaction() as connection:
            permissions.store.delete(connection, where="entry_id=%s", parameters=(issuer_grant.entry_id,))
    elif invalid == "target-changed":
        with get_lineage_data_store().database.transaction() as connection:
            facade_members.replace(connection, facade_id, [])
    elif invalid == "closed":
        source_id = args["message"]["content_parts"][1]["source_message_id"]
        repo.close_question_set(worker_id, source_id, actor=user)

    wake = Mock()
    monkeypatch.setattr(lineage_message_events, "wake_watchers", wake)
    assert run("DeliverMessage", args, actor=user, key="send-invalid-approval").status != "committed"
    assert repo.message(facade_id, message_id) is None
    assert repo.message(worker_id, message_id) is None
    assert message_id not in family_facade.relay_links(resident_record_domain.find_resident(facade_id))
    assert not permissions.holds(worker_id, "@admin", "@shared")
    wake.assert_not_called()


def test_cancelling_at_facade_ends_unreleased_worker_credentials(approval):
    user, permissions, repo, args, credential, _, _ = approval
    facade_id, message_id, worker_id = args["resident_id"], credential["message_id"], credential["principal_id"]
    run("PrepareResidentInput", args, actor=user, key="facade-prepare").raise_for_error()
    assert run("CreateTemporaryCredential", credential, actor=user, key="worker-stage").raise_for_error()["state"] == "pending"
    cancellation = run("CancelInput", {"resident_id": facade_id, "message_id": message_id}, actor=user,
                       key="cancel-prepared-facade-approval").raise_for_error()
    assert cancellation["input_state"] == {"state": "cancelled", "message_id": message_id}
    assert run("CreateTemporaryCredential", credential, actor=user, key="worker-restage").status != "committed"
    assert run("DeliverMessage", args, actor=user, key="send-cancelled-approval").status != "committed"
    assert not permissions.holds(worker_id, "@admin", "@shared")
    assert repo.message(facade_id, message_id) is None and repo.message(worker_id, message_id) is None
