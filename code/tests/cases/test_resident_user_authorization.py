"""Current authenticated input, exact evidence and atomic credential lifetime.

:module: resident.identity
:importance: P1
:design: decision_1e2eb869bc10, decision_b820faf42218
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
import hashlib
import json

import pytest

from management_common.principal import message_origin
from management_portal import permission_system
from management_portal.execution import execution_inputs
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.permission_system import inline
from management_portal.society_service import (
    resident_conversation_stream, resident_execution_work, resident_user_authorization as authorization,
)
from management_portal.society_service.isa import conversation
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401


TEXT = "授予你 SparseTriggerArch 的 @contributor，继续把附图中的参数整理到项目。"
MESSAGE = "msg_" + "a" * 32
EXECUTION = "execution-" + "a" * 24
REQUEST = {"a": "@contributor", "resource_id": "decision_123456789abc"}


@pytest.fixture
def active(resident_record, permissions, monkeypatch):
    resident = resident_record["resident_id"]
    permissions.users("lender", category="intern")
    permissions.place(Fact("resident", resident))
    permissions.place(Fact("project", "AuthorizationFixture"),
                      Fact("decision", REQUEST["resource_id"], {"project": "AuthorizationFixture"}))
    permissions.grant("lender", REQUEST["resource_id"])
    assert not permissions.holds(resident, REQUEST["a"], REQUEST["resource_id"])
    repository = resident_conversation_stream.get_repository()
    message = {"message_id": MESSAGE, "role": "user", "author_id": "lender",
               "origin": message_origin("lender"),
               "content_parts": [{"type": "text", "text": TEXT}]}
    repository.accept_user_message(resident, message)
    job = {"execution_id": EXECUTION, "user_message_id": MESSAGE, "actor": "lender",
           "input_message": message, "submitted_at": "2026-11-03T00:00:00Z"}
    resident_execution_work.write(resident_record, job)
    agent = get_agent_data_store()
    agent.begin_execution(execution_id=EXECUTION, agent_id=resident, actor_ref="lender", provider="codex",
                          started_at="2026-11-03T00:00:00Z")
    execution_inputs.attach(agent, EXECUTION, [{"message_id": MESSAGE, "role": "primary"}])
    repository.set_input_state(resident, MESSAGE, "running")
    calls = []
    real_accept = permission_system.accept_user_authorization

    def sign(connection, **facts):
        calls.append(facts)
        return real_accept(connection, **facts)

    monkeypatch.setattr(permission_system, "accept_user_authorization", sign, raising=False)
    arguments = {"message_id": MESSAGE, "credentials": [REQUEST], "evidence": TEXT}
    inv = SimpleNamespace(caller=resident, context=SimpleNamespace(execution_id=EXECUTION), argument=arguments.get)
    return SimpleNamespace(resident=resident, record=resident_record, job=job, repository=repository,
                           arguments=arguments, inv=inv, calls=calls, sign=sign, agent=agent, permissions=permissions)


def save(active):
    resident_execution_work.write(active.record, active.job)


def loans(active):
    return permission_system.manage("ListTemporaryCredentials", {"principal_id": active.resident}, actor="lender")["credentials"]


@pytest.mark.parametrize("text", [
    TEXT, "把这个项目的协作权限交给你，接着处理图片。", "I authorize you to contribute to this project. Now update the plan.",
    "Vous avez mon autorisation pour contribuer au projet.", "我给你临时凭证", "好的，就按上述范围授权。",
])
def test_intent_is_not_a_phrase_parser_and_attachments_are_allowed(active, text):
    active.job["input_message"]["content_parts"] = [
        {"type": "text", "text": text}, {"type": "image", "resource_id": "upload_123456789abc"},
        {"type": "context_reference", "context": {"text": "not evidence"}},
    ]
    save(active)
    active.arguments["evidence"] = text
    result = conversation.accept_user_authorization(active.inv)
    assert result["credentials"] and len(loans(active)) == 1
    assert active.permissions.holds(active.resident, REQUEST["a"], REQUEST["resource_id"])
    facts = active.calls[0]
    assert facts["issuer"] == "lender" and facts["resident_id"] == active.resident
    assert facts["message_id"] == MESSAGE
    assert facts["source_digest"] == hashlib.sha256(json.dumps([text], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("source", ["quote", "context_reference", "image", "other_text", "split"])
def test_evidence_only_comes_from_a_single_direct_text_part(active, source):
    message = active.job["input_message"]
    message["content_parts"] = [{"type": "text", "text": "Please handle the attachment."}]
    if source == "quote":
        message["quote"] = {"message_id": "msg_prior", "text": TEXT}
    elif source == "other_text":
        active.arguments["evidence"] = "not in this source"
    elif source == "split":
        message["content_parts"] = [{"type": "text", "text": TEXT[:5]}, {"type": "text", "text": TEXT[5:]}]
    else:
        message["content_parts"].append({"type": source, "text": TEXT})
    save(active)
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls and not loans(active)


@pytest.mark.parametrize("forgery", ["no_origin", "resident_sender", "forged_author", "initiated_by_user",
                                    "initiated_by_other", "missing_actor", "system_role", "wrong_message"])
def test_source_uses_authenticated_sender_and_accepted_actor(active, forgery):
    item = active.job
    message = item["input_message"]
    if forgery == "no_origin":
        message.pop("origin")
    elif forgery in {"resident_sender", "initiated_by_user"}:
        item["actor"] = message["author_id"] = active.resident
        message["origin"] = message_origin(active.resident, initiated_by="lender" if forgery == "initiated_by_user" else "")
    elif forgery == "forged_author":
        message["author_id"] = "different_user"
    elif forgery == "initiated_by_other":
        message["origin"] = message_origin("lender", initiated_by=active.resident)
    elif forgery == "missing_actor":
        item["actor"] = "another_user"
    elif forgery == "system_role":
        message["role"] = "system"
    else:
        message["message_id"] = "msg_elsewhere"
    # Malformed source envelopes must not travel through normal writers; install
    # only the immutable preparation field to model corrupted/legacy records.
    from management_portal.society_service.resident_execution_storage import encode
    with active.repository.state_store.database.transaction() as connection:
        connection.execute("UPDATE resident_execution_work SET preparation_json=%s,progress_json=%s WHERE resident_id=%s",
                           (encode({"input_message": message}), encode({"actor": item["actor"], "submitted_at": item["submitted_at"]}), active.resident))
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


@pytest.mark.parametrize("condition", ["queued", "completed", "failed", "cancelled", "other_resident", "no_execution",
                                      "old_execution", "stopped", "fork", "no_relation", "execution_ended"])
def test_only_the_callers_active_business_input_is_eligible(active, condition):
    if condition in {"queued", "completed", "failed", "cancelled"}:
        active.repository.set_input_state(active.resident, MESSAGE, condition)
    elif condition == "other_resident":
        active.inv.caller = "resident_bbbbbbbbbbbb"
    elif condition == "no_execution":
        active.inv.context.execution_id = ""
    elif condition == "old_execution":
        active.inv.context.execution_id = "execution-old"
    elif condition in {"stopped", "fork"}:
        active.job.update({"stop_requested_at": "2026-11-03T00:00:01Z"} if condition == "stopped" else {"operation": "context_fork_create"})
        save(active)
    elif condition == "no_relation":
        with active.agent.database.transaction() as connection:
            connection.execute("DELETE FROM execution_inputs WHERE execution_id=%s", (EXECUTION,))
    else:
        active.agent.finish_execution(EXECUTION, status="completed")
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


@pytest.mark.parametrize("caller", ["lender", "portal", ""])
def test_only_real_resident_callers_can_accept(active, caller):
    active.inv.caller = caller
    with pytest.raises((PermissionError, ValueError)):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


@pytest.mark.parametrize("update", [{"evidence": ""}, {"credentials": []}, {"credentials": [REQUEST] * 21},
                                     {"credentials": [{**REQUEST, "issuer": "lender"}]}, {"message_id": ""}])
def test_precise_nonempty_arguments_are_required(active, update):
    active.arguments.update(update)
    with pytest.raises(ValueError):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


def test_group_member_must_be_an_original_user_input_of_the_same_execution(active):
    member = "msg_" + "b" * 32
    active.job["mailbox_members"] = [{"user_message_id": member, "actor": "lender",
        "input_message": {**deepcopy(active.job["input_message"]), "message_id": member}}]
    save(active)
    execution_inputs.attach(active.agent, EXECUTION, [{"message_id": member, "role": "member"}])
    active.repository.set_input_state(active.resident, member, "running")
    active.arguments["message_id"] = member
    conversation.accept_user_authorization(active.inv)
    assert active.calls[0]["message_id"] == member
    assert active.calls[0]["bound_input_message_id"] == MESSAGE


def test_grant_is_rolled_back_when_permission_owner_fails(active, monkeypatch):
    def fail(connection, **facts):
        active.sign(connection, **facts)
        raise RuntimeError("audit failed")
    monkeypatch.setattr(permission_system, "accept_user_authorization", fail)
    with pytest.raises(RuntimeError, match="audit failed"):
        conversation.accept_user_authorization(active.inv)
    assert not loans(active)


def test_terminal_commit_before_grant_is_rechecked_after_lifetime_lock(active, monkeypatch):
    reached, continue_grant = Event(), Event()
    original = inline.lock
    def pause_before_lock(connection, message_id):
        if not reached.is_set():
            reached.set()
            assert continue_grant.wait(5)
        original(connection, message_id)
    monkeypatch.setattr(inline, "lock", pause_before_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        grant = pool.submit(conversation.accept_user_authorization, active.inv)
        assert reached.wait(5)
        active.repository.set_input_state(active.resident, MESSAGE, "completed")
        continue_grant.set()
        with pytest.raises(PermissionError):
            grant.result(timeout=5)
    assert not active.calls and not loans(active)


def test_grant_then_terminal_commit_cleans_credentials_in_same_lifetime(active, monkeypatch):
    signed, finish_grant, ending = Event(), Event(), Event()
    def slow_sign(connection, **facts):
        result = active.sign(connection, **facts)
        signed.set()
        assert finish_grant.wait(5)
        return result
    monkeypatch.setattr(permission_system, "accept_user_authorization", slow_sign)
    original_end = permission_system.end_task_credentials
    def end(connection, *args):
        ending.set()
        return original_end(connection, *args)
    monkeypatch.setattr(permission_system, "end_task_credentials", end)
    with ThreadPoolExecutor(max_workers=2) as pool:
        grant = pool.submit(conversation.accept_user_authorization, active.inv)
        assert signed.wait(5)
        terminal = pool.submit(active.repository.set_input_state, active.resident, MESSAGE, "completed")
        assert ending.wait(5)
        assert not terminal.done()
        finish_grant.set()
        assert grant.result(timeout=5)["credentials"]
        terminal.result(timeout=5)
    assert not loans(active)


@pytest.mark.parametrize("delivered", [True, False])
def test_info_requires_consumption_and_binds_live_task_not_completed_source(active, delivered):
    mid = "msg_" + "c" * 32
    message = {**deepcopy(active.job["input_message"]), "message_id": mid}
    execution_inputs.attach(active.agent, EXECUTION, [{"message_id": mid, "role": "info"}])
    if delivered:
        active.repository.commit_user_message(active.resident, {**message, "released_with": MESSAGE})
        active.repository.set_input_state(active.resident, mid, "completed")
    else:
        active.repository.accept_user_message(active.resident, message)
        active.repository.set_input_state(active.resident, mid, "queued")
    active.arguments["message_id"] = mid
    if not delivered:
        with pytest.raises(PermissionError):
            conversation.accept_user_authorization(active.inv)
        return
    conversation.accept_user_authorization(active.inv)
    assert active.calls[0]["message_id"] == mid
    assert active.calls[0]["bound_input_message_id"] == MESSAGE
    assert {c["message_id"] for c in loans(active)} == {MESSAGE}
    active.repository.set_input_state(active.resident, MESSAGE, "completed")
    assert not loans(active)


def family_chain(active, *, depth=2):
    """Actual immutable source segments plus the Society accepted-relay ledger."""
    from management_portal.society_service.family_facade import relay_mark
    previous_resident, previous_id = active.resident, MESSAGE
    first_message = deepcopy(active.job["input_message"])
    for index in range(depth):
        facade = "resident_" + str(index + 3) * 12
        source_id = "msg_" + str(index + 3) * 32
        active.agent.ensure_agent(facade, actor_ref="lender")
        active.agent.display_chat_message(facade, {**deepcopy(first_message), "message_id": source_id})
        if index == 0:
            active.job["input_message"]["business_ref"] = relay_mark(facade, source_id)
            # In real delivery private business_ref is stored with the source.
            accepted = active.repository.stored_message(active.resident, MESSAGE)
            active.agent.display_chat_message(active.resident, accepted)
            active.agent.update_chat_message(active.resident, MESSAGE, {**accepted, "business_ref": relay_mark(facade, source_id)})
        else:
            prior = active.repository.stored_message(previous_resident, previous_id)
            active.agent.update_chat_message(previous_resident, previous_id, {**prior, "business_ref": relay_mark(facade, source_id)})
        with active.repository.state_store.family_relays.transaction(active.record["lineage_id"], facade) as ledger:
            ledger["links"] = {source_id: {"resident_id": previous_resident, "message_id": previous_id,
                                           "relayed_at": "2026-11-03T00:00:00Z"}}
        previous_resident, previous_id = facade, source_id
    active.arguments["message_id"] = previous_id
    return previous_resident, previous_id


@pytest.mark.parametrize("depth", [1, 2, 4])
def test_persisted_family_relay_chain_reaches_exact_original_user(active, depth):
    source_resident, source_id = family_chain(active, depth=depth)
    conversation.accept_user_authorization(active.inv)
    assert active.calls[0]["source_resident_id"] == source_resident
    assert active.calls[0]["message_id"] == source_id
    assert active.calls[0]["issuer"] == "lender"
    assert active.calls[0]["bound_input_message_id"] == MESSAGE


@pytest.mark.parametrize("breakage", ["missing_link", "different_recipient", "different_message", "resident_source"])
def test_family_mark_alone_or_untrusted_original_is_not_authority(active, breakage):
    source_resident, source_id = family_chain(active, depth=1)
    if breakage == "resident_source":
        # This is a real Resident-sent source despite its user conversational role.
        original = authorization._original(active.repository, source_resident, source_id)
        original.update(author_id=active.resident, origin=message_origin(active.resident, initiated_by="lender"))
        # Exercise the provenance check independently of the immutable segment writer.
        with pytest.raises(PermissionError):
            authorization._source_proof(original, source_id, TEXT)
        return
    with active.repository.state_store.family_relays.transaction(active.record["lineage_id"], source_resident) as ledger:
        if breakage == "missing_link":
            ledger.clear()
        else:
            ledger["links"][source_id]["resident_id" if breakage == "different_recipient" else "message_id"] = "unrelated"
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


def test_explicit_lifetime_is_passed_to_permission_owner_with_proven_source(active):
    active.arguments["expires_in_seconds"] = 3600
    conversation.accept_user_authorization(active.inv)
    assert active.calls[0]["expires_in_seconds"] == 3600
    assert active.calls[0]["execution_id"] == EXECUTION
    active.repository.set_input_state(active.resident, MESSAGE, "completed")
    assert active.permissions.holds(active.resident, REQUEST["a"], REQUEST["resource_id"])
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)


def test_replay_uses_same_receipt_only_while_the_bound_task_is_live(active):
    first = conversation.accept_user_authorization(active.inv)
    again = conversation.accept_user_authorization(active.inv)
    assert not first["replayed"] and again["replayed"]
    assert first["authorization_id"] == again["authorization_id"]
    assert first["credentials"] == again["credentials"]
    assert len(loans(active)) == 1
    active.repository.set_input_state(active.resident, MESSAGE, "completed")
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    assert not active.permissions.holds(active.resident, REQUEST["a"], REQUEST["resource_id"])


def test_deleted_resident_cannot_use_retained_execution_work(active):
    with active.repository.state_store.database.transaction() as connection:
        connection.execute("UPDATE residents SET deleted_at=now() WHERE resident_id=%s", (active.resident,))
    with pytest.raises(PermissionError, match="live Resident"):
        conversation.accept_user_authorization(active.inv)
    assert not active.calls


@pytest.mark.parametrize("delivered", [True, False])
@pytest.mark.parametrize("metadata_type", ["jsonb", "text"])
def test_append_requires_persisted_provider_delivery(active, delivered, metadata_type):
    from management_portal.execution.appended_input_projection import materialize_delivered_input
    from management_portal.execution.contracts import ExecutionInputControl
    mid = "msg_" + "d" * 32
    original = active.repository.commit_user_message(active.resident, {
        **deepcopy(active.job["input_message"]), "message_id": mid})
    if delivered:
        session = active.agent.ensure_provider_session(provider="codex", backend_session_id="authorization-test-session",
                                                       backend_route_id="", agent_id=active.resident, actor_ref="lender")
        active.agent.bind_execution_provider_session(EXECUTION, provider_session_ref=session["provider_session_ref"])
        active.agent.record_input_acceptance(EXECUTION, agent_id=active.resident, provider_turn_id="turn-auth")
        source = active.agent.chat_message(active.resident, mid)
        control = ExecutionInputControl.new(execution_id=EXECUTION, actor="lender", input=[{"type": "text", "text": TEXT}],
            expected_turn_id="turn-auth", source_segment_id=source["segment_id"])
        materialize_delivered_input(active.agent, {**control.to_payload(), "status": "delivered",
                                                  "updated_at": "2026-11-03T00:01:00Z"})
    active.repository.set_input_state(active.resident, mid, "completed" if delivered else "queued")
    active.arguments["message_id"] = mid
    if metadata_type == "text":
        # Existing instances retain the original TEXT column while new ones
        # create JSONB. Exercise the same authorization path against both.
        from management_portal.execution.agent_data_tables import CHAT_READ_INDEX_SQL, CHAT_STATISTICS_SQL

        with active.agent.database.transaction() as connection:
            # PostgreSQL elides redundant JSONB casts in stored index/statistic
            # expressions. Rebuild them for the historical column type.
            for name in CHAT_READ_INDEX_SQL:
                connection.execute(f"DROP INDEX {name}")
            for name in CHAT_STATISTICS_SQL:
                connection.execute(f"DROP STATISTICS {name}")
            connection.execute("ALTER TABLE segments ALTER COLUMN metadata_json TYPE text USING metadata_json::text")
            for sql in (*CHAT_READ_INDEX_SQL.values(), *CHAT_STATISTICS_SQL.values()):
                connection.execute(sql)
    if not delivered:
        with pytest.raises(PermissionError):
            conversation.accept_user_authorization(active.inv)
        return
    conversation.accept_user_authorization(active.inv)
    assert active.calls[0]["message_id"] == mid
    assert active.calls[0]["bound_input_message_id"] == MESSAGE


def test_stop_and_acceptance_share_the_current_work_row_fence(active, monkeypatch):
    entered, release, stopping = Event(), Event(), Event()
    def sign(connection, **facts):
        result = active.sign(connection, **facts)
        entered.set()
        assert release.wait(5)
        return result
    monkeypatch.setattr(permission_system, "accept_user_authorization", sign)
    def stop():
        stopping.set()
        return active.repository.state_store.request_resident_execution_stop(
            active.resident, EXECUTION, MESSAGE, "lender", "2026-11-03T00:01:00Z")
    with ThreadPoolExecutor(max_workers=2) as pool:
        grant = pool.submit(conversation.accept_user_authorization, active.inv)
        assert entered.wait(5)
        stopped = pool.submit(stop)
        assert stopping.wait(5)
        assert not stopped.done()
        release.set()
        assert grant.result(timeout=5)["credentials"]
        assert stopped.result(timeout=5)
    with pytest.raises(PermissionError):
        conversation.accept_user_authorization(active.inv)
    active.repository.set_input_state(active.resident, MESSAGE, "cancelled")
    assert not loans(active)


def test_plain_text_delivery_no_longer_mints_or_rewrites_authorization(active, monkeypatch):
    message = {"message_id": "msg_" + "9" * 32, "role": "user",
               "content_parts": [{"type": "text", "text": "我给你临时凭证"}]}
    arguments = {"resident_id": active.resident, "message": message}
    def deliver(inv, resident_id, body, **kwargs):
        assert body == message and kwargs["lending"] is None
        return {"message_id": body["message_id"]}
    monkeypatch.setattr(conversation, "deliver", deliver)
    inv = SimpleNamespace(argument=arguments.get, caller="lender", idempotency_key="ordinary-delivery")
    assert conversation.deliver_message(inv)["message_id"] == message["message_id"]
    assert not active.calls and not loans(active)
