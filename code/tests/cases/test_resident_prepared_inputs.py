"""Prepared input admission and permission lifetime share the canonical message ID."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.society_service import (
    resident_conversation_stream,
    resident_domain,
    resident_intake_domain,
    resident_policy_facts,
    resident_prepared_inputs as prepared,
    resident_protocol,
)
from management_portal.society_service.resident_message_contract import MessageIdentityConflict
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import _new_session_payload, future_creation_store  # noqa: F401


MID = "msg_66de4ea6d0a103cd5ba43bf5dc20d9ab"
OTHER = "resident_111111111111"


@pytest.fixture
def parties(delivery_resident):
    sender = resident_domain.register_resident(_new_session_payload("Prepared sender"), "sample_admin")
    return delivery_resident["resident_id"], sender["resident_id"]


def _message(text="Prepared task", message_id=MID):
    return {"message_id": message_id, "content_parts": [{"type": "text", "text": text}]}


def _deliver(resident_id, actor, message):
    return resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": message}, actor)


def test_prepare_writes_no_conversation_and_release_replays_once(parties):
    resident_id, issuer = parties
    message = _message()
    repository = resident_conversation_stream.get_repository()
    receipt = prepared.prepare(resident_id, issuer, message)
    assert receipt == {"resident_id": resident_id, "message_id": MID, "status": "prepared"}
    assert prepared.prepare(resident_id, issuer, {**message, "role": "user"}, context_policy="session") == receipt
    assert repository.message(resident_id, MID) is None
    assert repository.input_states(resident_id) == []
    assert resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, issuer)
    assert not resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, OTHER)
    assert not resident_policy_facts.is_resident_input_open(resident_id, MID)

    with pytest.raises(MessageIdentityConflict):
        _deliver(resident_id, issuer, message)
    with prepared.release(resident_id, issuer, message), pytest.raises(MessageIdentityConflict):
        _deliver(resident_id, issuer, _message("Changed inside release"))
    for proposed_recipient, proposed_issuer, proposed_body, options in (
        (OTHER, issuer, message, {}), (resident_id, OTHER, message, {}),
        (resident_id, issuer, _message("Changed body"), {}),
        (resident_id, issuer, message, {"activation_policy": "attach_to_next"}),
        (resident_id, issuer, message, {"business_ref": "different"}),
        (resident_id, issuer, message, {"context_policy": "fresh"}),
    ):
        with pytest.raises(MessageIdentityConflict), prepared.release(
            proposed_recipient, proposed_issuer, proposed_body, **options,
        ):
            pytest.fail("mismatched reservation entered release")

    for _ in range(2):
        with prepared.release(resident_id, issuer, message):
            assert _deliver(resident_id, issuer, message)["message_id"] == MID
    assert [row["message_id"] for row in repository.input_states(resident_id)] == [MID]
    assert prepared.prepare(resident_id, issuer, message)["status"] == "released"
    assert not resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, issuer)
    repository.set_input_state(resident_id, MID, "completed")
    assert not resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, issuer)


def test_cancel_revokes_in_same_transaction_and_prevents_in_flight_release(parties, monkeypatch):
    from management_portal import permission_system

    resident_id, issuer = parties
    message = _message()
    prepared.prepare(resident_id, issuer, message)
    ended = []

    def revoke(connection, recipient, message_id):
        assert not resident_policy_facts.is_resident_input_open_for_issuer(recipient, message_id, issuer, connection=connection)
        ended.append((recipient, message_id))

    monkeypatch.setattr(permission_system, "end_task_credentials", revoke)
    assert prepared.cancel(resident_id, OTHER, MID) is None
    with prepared.release(resident_id, issuer, message):
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(prepared.cancel, resident_id, issuer, MID).result(timeout=10)["status"] == "cancelled"
        with pytest.raises(MessageIdentityConflict):
            _deliver(resident_id, issuer, message)
    assert prepared.cancel(resident_id, issuer, MID)["status"] == "cancelled"
    assert ended == [(resident_id, MID)]
    assert resident_conversation_stream.get_repository().message(resident_id, MID) is None


def test_uncommitted_acceptance_keeps_prepare_live(parties):
    resident_id, issuer = parties
    message = _message()
    prepared.prepare(resident_id, issuer, message)
    with prepared.release(resident_id, issuer, message):
        with pytest.raises(RuntimeError, match="durably accept"):
            with prepared.accepting(resident_id, MID, issuer, activation_policy="enqueue_and_trigger"):
                pass
    assert prepared.prepare(resident_id, issuer, message)["status"] == "prepared"


def test_used_legacy_id_cannot_become_prepared(parties):
    resident_id, issuer = parties
    message = _message()
    assert _deliver(resident_id, issuer, message)["message_id"] == MID
    with pytest.raises(MessageIdentityConflict):
        prepared.prepare(resident_id, issuer, message)


def test_first_commit_rechecks_global_identity_after_outside_preflight(parties, monkeypatch):
    resident_id, issuer = parties
    original = resident_intake_domain._work_ahead
    monkeypatch.setattr(resident_intake_domain, "preflight_resident_input", lambda *args, **kwargs: None)

    def prepare_first(record):
        assert prepared.prepare(OTHER, issuer, _message())["status"] == "prepared"
        return original(record)

    monkeypatch.setattr(resident_intake_domain, "_work_ahead", prepare_first)
    with prepared.release(resident_id, issuer, _message()), pytest.raises(MessageIdentityConflict):
        _deliver(resident_id, issuer, _message())
    assert resident_conversation_stream.get_repository().message(resident_id, MID) is None
    assert resident_policy_facts.is_resident_input_open_for_issuer(OTHER, MID, issuer)


def test_prepared_messages_reject_service_and_require_fixed_identity_and_real_work(parties):
    resident_id, issuer = parties
    with pytest.raises(PermissionError):
        prepared.prepare(resident_id, "portal", _message())
    with pytest.raises(ValueError, match="固定"):
        prepared.prepare(resident_id, issuer, _message(message_id=""))
    with pytest.raises(ValueError, match="即时命令"):
        prepared.prepare(resident_id, issuer, _message("/status"))
    with pytest.raises(ValueError, match="v1"):
        prepared.prepare(resident_id, issuer, _message(), context_policy="fresh",
                         execution_constraints={"version": True, "context": "fresh", "text_only": True})


def test_concurrent_prepare_has_global_winner(parties, monkeypatch):
    recipient, issuer = parties
    # The repository is initialized on the fixture thread. Check the global
    # persistence constraint with two independently admitted recipient IDs.
    get_lineage_data_store()
    monkeypatch.setattr(resident_intake_domain, "preflight_resident_input", lambda *args, **kwargs: None)

    def attempt(target):
        try:
            return prepared.prepare(target, issuer, _message())["resident_id"]
        except MessageIdentityConflict:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = [future.result(timeout=10) for future in
                   [pool.submit(attempt, target) for target in (recipient, OTHER)]]
    assert results.count(None) == 1


def test_formal_instruction_releases_under_actor_and_can_read_facts_from_shared_transaction(parties, monkeypatch):
    from management_common.portal_database import portal_database
    from management_portal.permission_system import inline
    from management_portal.society_service import outward_messages
    from tests.cases.isa_test_support import run

    resident_id, issuer = parties
    arguments = {"resident_id": resident_id, "message": _message()}
    assert run("PrepareResidentInput", arguments, actor=issuer, key="prepare-input").raise_for_error()["status"] == "prepared"
    with portal_database(schema="permission").transaction() as connection:
        inline.lock(connection, MID)
        assert resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, issuer, connection=connection)
        assert not resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, resident_id, connection=connection)
    def unexpected_implicit_quote(*_):
        pytest.fail("prepared delivery must not add an implicit reply")

    monkeypatch.setattr(outward_messages, "reply_quote", unexpected_implicit_quote)
    assert run("DeliverMessage", arguments, actor=issuer, key="release-input").raise_for_error()["message_id"] == MID
    assert prepared.cancel(resident_id, issuer, MID) is None


def test_formal_cancel_closes_unused_preparation_without_queuing(parties):
    from tests.cases.isa_test_support import run

    resident_id, issuer = parties
    arguments = {"resident_id": resident_id, "message": _message()}
    run("PrepareResidentInput", arguments, actor=issuer, key="prepare-input").raise_for_error()
    receipt = run("CancelInput", {"resident_id": resident_id, "message_id": MID},
                  actor=issuer, key="cancel-prepared-input").raise_for_error()
    assert receipt["input_state"] == {"state": "cancelled", "message_id": MID}
    assert not resident_policy_facts.is_resident_input_open_for_issuer(resident_id, MID, issuer)


def test_admission_activates_staged_permission_and_terminal_input_revokes_it(parties, monkeypatch):
    from types import SimpleNamespace

    from management_portal import society_service
    from management_portal.permission_system.facts import Fact
    from tests.cases.permission_test_support import enable

    recipient, issuer = parties
    permissions = enable(monkeypatch)
    permissions.place(Fact("resident", issuer), Fact("resident", recipient))
    permissions.grant(issuer, "@shared", "@admin")
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(resident=resident_policy_facts))

    message = _message()
    prepared.prepare(recipient, issuer, message)
    arguments = {"principal_id": recipient, "message_id": MID, "a": "@admin", "resource_id": "@shared"}

    def issue():
        return permissions.implementation.manage("CreateTemporaryCredential", arguments, actor=issuer, enforce=False)

    pending = issue()
    assert pending["state"] == "pending"
    assert not permissions.holds(recipient, "@admin", "@shared")
    with prepared.release(recipient, issuer, message):
        _deliver(recipient, issuer, message)
    assert permissions.holds(recipient, "@admin", "@shared")
    active = issue()
    assert active["state"] == "active" and active["credential_id"] == pending["credential_id"]
    with prepared.release(recipient, issuer, message):
        _deliver(recipient, issuer, message)
    resident_conversation_stream.get_repository().set_input_state(recipient, MID, "completed")
    assert not permissions.holds(recipient, "@admin", "@shared")
    from management_portal.permission_system.temporary import LoanRefused
    with pytest.raises(LoanRefused):
        issue()


def test_ordinary_facade_info_still_forwards_after_acceptance(parties, monkeypatch):
    from management_portal.society_service import facade_members, family_facade, resident_held_infos

    resident_id, issuer = parties
    store = get_lineage_data_store()
    with store.database.transaction() as connection:
        facade_members.replace(connection, resident_id, [issuer])
    repository = resident_conversation_stream.get_repository()
    calls = []

    def forward(record, message, actor):
        assert repository.input_state(record["resident_id"], message["message_id"])["state"] == "completed"
        calls.append((record, message["message_id"], actor))

    monkeypatch.setattr(family_facade, "forward_info", forward)
    payload = {"resident_id": resident_id, "message": _message(), "activation_policy": "attach_to_next"}
    for _ in range(2):
        resident_protocol.deliver_resident_input(payload, issuer)
    assert [(message_id, actor) for _, message_id, actor in calls] == [(MID, issuer)] * 2
    assert [item["message_id"] for item in resident_held_infos.peek(calls[0][0])] == [MID]


def test_authenticated_user_prepares_stages_and_delivers_under_own_identity(delivery_resident, monkeypatch):
    from management_portal import society_service
    from management_portal.permission_system.facts import Fact
    from management_portal.society_service import contracts, server
    from management_portal.society_service.transport import SocietyServiceClient
    from tests.cases.isa_test_support import run
    from tests.cases.permission_test_support import enable

    recipient = delivery_resident["resident_id"]
    owner, other = "approval-owner", "approval-other"
    permissions = enable(monkeypatch)
    permissions.users(owner, other)
    permissions.place(Fact("resident", recipient))
    for user in (owner, other):
        permissions.grant(user, recipient, "@message")
        permissions.grant(user, "@shared", "@admin")

    def dispatch(_client, method, params=None, **kwargs):
        assert method == "domain.call"
        operation = contracts.operation_contract(params["domain"], params["name"])
        handler, bound = server._bind_domain_call(params["domain"], params["name"], params["args"], params["kwargs"])
        return server._invoke_domain(operation, handler, bound)

    client = SocietyServiceClient()
    monkeypatch.setattr(SocietyServiceClient, "_request", dispatch)
    monkeypatch.setattr(society_service, "get_society_service", lambda: client)

    arguments = {"resident_id": recipient, "message": _message()}
    credential = {"principal_id": recipient, "message_id": MID, "a": "@admin", "resource_id": "@shared"}
    assert run("PrepareResidentInput", arguments, actor=owner, key="user-prepare").raise_for_error()["status"] == "prepared"
    assert not permissions.holds(recipient, "@admin", "@shared")
    assert run("CreateTemporaryCredential", credential, actor=other, key="other-stage").status != "committed"
    pending = run("CreateTemporaryCredential", credential, actor=owner, key="user-stage").raise_for_error()
    assert pending["state"] == "pending"
    assert pending["issued_by"] == owner
    assert not permissions.holds(recipient, "@admin", "@shared")
    assert run("DeliverMessage", arguments, actor=other, key="other-release").error["code"] == "instruction.conflict"
    released = run("DeliverMessage", arguments, actor=owner, key="user-release").raise_for_error()
    assert released["message_id"] == MID
    assert permissions.holds(recipient, "@admin", "@shared")
    repository = resident_conversation_stream.get_repository()
    accepted = repository.message(recipient, MID)
    assert accepted["author_id"] == owner
    assert accepted["origin"]["sender"] == {"kind": "user", "id": owner}
    repository.set_input_state(recipient, MID, "completed")
    assert not permissions.holds(recipient, "@admin", "@shared")

    ordinary_id = "msg_43c96c8596b223bcf6c2a1ab58d08cc2"
    ordinary = {"resident_id": recipient, "message": _message("Ordinary input", ordinary_id)}
    assert run("DeliverMessage", ordinary, actor=other, key="ordinary-user").raise_for_error()["message_id"] == ordinary_id
    assert repository.message(recipient, ordinary_id)["author_id"] == other
