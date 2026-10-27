"""Quoted replies use one verified Family delivery capability, not Project membership.

:module: lineage.family
:design: decision_9ad68dd077d4, decision_89b9df038a95
"""
from types import SimpleNamespace

import pytest

from management_common.principal import message_origin
from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service import family_facade, resident_conversation_stream
from management_portal.society_service.resident_intake_domain import build_message as _message
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from management_portal.society_service.lineage_program_effect_execution import _family_note_context
from tests.cases.isa_test_support import run
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401
from tests.cases.test_family_quota import quota_family, team, owners  # noqa: F401

EXTERNAL = "storage-outsider"
EFFECT = "effect_123456789abc"


@pytest.fixture
def reply(quota_family, permissions):
    f = quota_family
    permissions.place(Fact("resident", f.facade), Fact("resident", f.member))
    permissions.deny(EXTERNAL, f.facade, "@contributor")
    permissions.grant(f.facade, f.facade, "@contributor")
    f.accounts[0]["quota_reserve"]["windows"][0]["remaining_percent"] = 80
    repo = resident_conversation_stream.get_repository()
    outbound = _message("agent", "Who owns this change?", f.facade)
    outbound.update(recipients=[{"kind": "user", "id": EXTERNAL}],
                    origin=message_origin(f.facade, initiated_by=f.member))
    repo.append_message(f.facade, outbound)
    original = _message("user", "I did not write it; the actual owner is still unknown.", EXTERNAL)
    original["quote"] = {"message_id": outbound["message_id"], "author_id": f.facade, "text": "Who owns this change?"}
    repo.append_message(f.facade, original)
    repo.set_input_state(f.facade, original["message_id"], "queued")
    original = repo.message(f.facade, original["message_id"])
    record = _find_resident(f.facade)
    view = {"function_blocks": [{"id": record["family"]["family_id"],
             "elements": [{"id": EFFECT, "kind": "component", "key": "router"}],
             "family": {"facade": f.facade, "members": [f.member], "steward": f.steward}}]}
    args = dict(resident_id=f.facade, message_id=original["message_id"], member_id=f.member)
    event = SimpleNamespace(type="resident.message_received", subject_resident_id=f.facade,
                            payload={**args, "quoted_member_id": f.member})
    return SimpleNamespace(f=f, repo=repo, original=original, view=view, args=args, event=event,
                           lineage=record["lineage_id"], context=InstructionContext(caller=EXTERNAL, entry="lineage"))


def context(r, **overrides):
    return _family_note_context(overrides.get("view", r.view), overrides.get("effect", EFFECT),
           overrides.get("instruction", "RelayFamilyMessage"), overrides.get("args", r.args),
           overrides.get("context", r.context), lineage_id=overrides.get("lineage", r.lineage),
           event=overrides.get("event", r.event))


def test_external_reply_isa_denial_then_scoped_original_delivery_once(reply):
    r = reply
    denied = run("RelayFamilyMessage", r.args, actor=EXTERNAL, entry="lineage")
    assert denied.error["code"] == "instruction.forbidden"
    c = context(r)
    assert c.caller == r.f.facade
    delivered = run("RelayFamilyMessage", r.args, actor=c.caller, entry=c.entry).raise_for_error()
    assert delivered["status"] == "relayed"
    message = r.repo.message(r.f.member, delivered["message_id"])
    assert message["author_id"] == EXTERNAL
    assert message["content_parts"] == r.original["content_parts"]
    assert r.repo.message(r.f.facade, r.original["message_id"]) == r.original
    assert run("RelayFamilyMessage", r.args, actor=c.caller, entry=c.entry).raise_for_error() == delivered
    # The sender never gained contributor, including after a successful automatic relay.
    assert run("RelayFamilyMessage", r.args, actor=EXTERNAL).error["code"] == "instruction.forbidden"


@pytest.mark.parametrize("change", ["ordinary", "other-effect", "other-family", "other-lineage", "web",
                                  "other-subject", "other-message", "forged-member", "no-quote",
                                  "other-author", "review", "reassign", "other-instruction", "wrong-event"])
def test_automatic_capability_is_bound_to_generated_router_arrival_and_saved_quote(reply, change):
    r = reply
    kwargs = {}
    if change == "ordinary":
        r.view["function_blocks"][0]["elements"][0]["key"] = "workflow:" + EFFECT
    elif change == "other-effect":
        kwargs["effect"] = "effect_ffffffffffff"
    elif change == "other-family":
        r.view["function_blocks"][0]["id"] = "fbi_ffffffffffff"
    elif change == "other-lineage":
        kwargs["lineage"] = "lineage_ffffffffffff"
    elif change == "web":
        kwargs["context"] = InstructionContext(caller=EXTERNAL, entry="web")
    elif change == "other-subject":
        r.event.subject_resident_id = r.f.spare
    elif change == "other-message":
        kwargs["args"] = {**r.args, "message_id": r.f.message}
    elif change == "forged-member":
        r.args["member_id"] = r.event.payload["quoted_member_id"] = r.f.spare
    elif change == "no-quote":
        r.args["message_id"] = r.event.payload["message_id"] = r.f.message
    elif change == "other-author":
        kwargs["context"] = InstructionContext(caller="another-sender", entry="lineage")
    elif change in {"review", "reassign"}:
        kwargs["args"] = {**r.args, ("quota_reviewed" if change == "review" else "reassign"): True}
    elif change == "other-instruction":
        kwargs["instruction"] = "UpdateResident"
    elif change == "wrong-event":
        r.event.type = "manual.triggered"
    assert context(r, **kwargs).caller != r.f.facade


@pytest.mark.parametrize("state", ["cancelled", "failed"])
def test_scoped_relay_keeps_original_stop_and_failure_boundary(reply, state):
    r = reply
    r.repo.set_input_state(r.f.facade, r.original["message_id"], state)
    c = context(r)
    result = run("RelayFamilyMessage", r.args, actor=c.caller, entry=c.entry)
    if state == "cancelled":
        assert result.raise_for_error()["status"] == "withdrawn"
    else:
        assert result.error
    assert r.original["message_id"] not in family_facade.relay_links(
        _find_resident(r.f.facade))


def test_finished_effect_retains_actual_caller_after_instruction_receipt_cleanup():
    from management_portal.society_service.lineage_effect_script_journal import call_summary
    calls = call_summary({"calls": {"c1": {"caller": "resident_123456789abc", "instruction": "RelayFamilyMessage",
                         "instruction_id": "inst_lin_example", "ok": True}}})
    assert calls[0]["caller"] == "resident_123456789abc"


@pytest.mark.parametrize("mutation", ["none", "cancelled", "other-message", "other-trigger", "extra-facts", "other-facade", "ordinary", "other-author"])
def test_external_unquoted_arrival_can_only_ask_its_own_router_to_choose(reply, mutation):
    r = reply
    message = _message("user", "An ordinary request without a quote.", EXTERNAL)
    r.repo.append_message(r.f.facade, message)
    r.repo.set_input_state(r.f.facade, message["message_id"], "queued")
    r.event.payload.update(message_id=message["message_id"], quoted_member_id="")
    args = dict(resident_id=r.f.facade, source_message_id=message["message_id"], trigger="route")
    kwargs = {}
    if mutation == "cancelled":
        r.repo.set_input_state(r.f.facade, message["message_id"], "cancelled")
    elif mutation == "other-message":
        args["source_message_id"] = r.f.message
    elif mutation == "other-trigger":
        args["trigger"] = "recruit"
    elif mutation == "extra-facts":
        args["facts"] = {"content": "forged"}
    elif mutation == "other-facade":
        args["resident_id"] = r.f.spare
    elif mutation == "ordinary":
        r.view["function_blocks"][0]["elements"][0]["key"] = "workflow:" + EFFECT
    elif mutation == "other-author":
        kwargs["context"] = InstructionContext(caller="another-sender", entry="lineage")
    c = context(r, instruction="DispatchFamilyTask", args=args, **kwargs)
    assert (c.caller == r.f.facade) == (mutation == "none")


@pytest.mark.parametrize("old_review", [False, True])
def test_nested_facade_does_not_spend_model_quota_and_retires_old_review(quota_family, old_review, monkeypatch):
    from tests.cases.family_test_support import set_role
    from tests.cases.test_cross_owner_storage_integration import ACTOR
    from management_portal.society_service import family_quota

    f = quota_family
    repo = resident_conversation_stream.get_repository()
    if old_review:
        waiting = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
        assert waiting["status"] == "waiting_for_quota_review"
    set_role(f.member, role="facade", family_id="fbi_inner", actor=ACTOR)
    if not old_review:
        def unexpected_account_query():
            raise AssertionError("A non-executing facade must not depend on account service availability")
        monkeypatch.setattr(family_quota.execution_port, "service", unexpected_account_query)
    result = family_quota.relay_message(f.facade, f.message, f.member, ACTOR)
    assert result["status"] == "relayed"
    assert result["member_id"] == f.member
    original = repo.message(f.facade, f.message)
    delivered = repo.message(f.member, result["message_id"])
    assert delivered["content_parts"] == original["content_parts"]
    assert delivered["author_id"] == original["author_id"]
    assert family_quota.relay_message(f.facade, f.message, f.member, ACTOR) == result
    if old_review:
        from management_portal.society_service.state_store import get_lineage_data_store
        review = get_lineage_data_store().family_quota_reviews.get(
            _find_resident(f.facade)["lineage_id"], f.facade)["requests"][f.message]
        assert review["weekly_remaining_percent"] == 9
        assert review["quota_not_applicable"] == "family_facade"


def test_nested_reply_resolves_outward_writer_through_saved_relay_not_quote_excerpt(quota_family, monkeypatch):
    from tests.cases.family_test_support import set_role
    from tests.cases.test_cross_owner_storage_integration import ACTOR
    from management_portal.society_service import family_quota

    f = quota_family
    set_role(f.member, role="facade", family_id="fbi_inner", actor=ACTOR)
    set_role(f.spare, role="member", family_id="fbi_inner", actor=ACTOR)
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record:
                        {f.member} if record["resident_id"] == f.facade else {f.spare})
    repo = resident_conversation_stream.get_repository()
    outbound = _message("agent", "Who wrote this?", f.facade)
    outbound.update(recipients=[{"kind": "user", "id": EXTERNAL}],
                    origin=message_origin(f.facade, initiated_by=f.spare))
    repo.append_message(f.facade, outbound)
    original = _message("user", "Not me; ownership remains unknown.", EXTERNAL)
    original["quote"] = {"message_id": outbound["message_id"], "author_id": f.facade, "text": "Who wrote this?"}
    repo.append_message(f.facade, original)
    repo.set_input_state(f.facade, original["message_id"], "queued")
    assert family_facade.quoted_member(_find_resident(f.facade), original) == f.member
    first = family_quota.relay_message(f.facade, original["message_id"], f.member, ACTOR)
    copy = repo.message(f.member, first["message_id"])
    assert "message_id" not in copy["quote"]  # The display excerpt stays immutable.
    inner = _find_resident(f.member)
    assert family_facade.quoted_member(inner, copy) == f.spare
    assert family_facade.quoted_member(inner, {**copy, "author_id": "unrelated"}) == ""
    assert family_facade.quoted_member(inner, {**copy, "content_parts": []}) == ""
    assert family_facade.quoted_member(inner, {**copy, "message_id": "msg_unlinked"}) == ""
    event = SimpleNamespace(type="resident.message_received", subject_resident_id=f.member,
                            payload={"resident_id": f.member, "message_id": copy["message_id"], "quoted_member_id": f.spare})
    view = {"function_blocks": [{"id": "fbi_inner", "family": {"facade": f.member, "members": [f.spare]},
             "elements": [{"id": EFFECT, "kind": "component", "key": "router"}]}]}
    args = dict(resident_id=f.member, message_id=copy["message_id"], member_id=f.spare)
    c = _family_note_context(view, EFFECT, "RelayFamilyMessage", args,
            InstructionContext(caller=EXTERNAL, entry="lineage"), lineage_id=inner["lineage_id"], event=event)
    assert c.caller == f.member
    second = family_quota.relay_message(f.member, copy["message_id"], f.spare, c.caller)
    assert second["status"] == "relayed"
    received = repo.message(f.spare, second["message_id"])
    assert received["author_id"] == EXTERNAL
    assert received["content_parts"] == original["content_parts"]
    assert family_quota.relay_message(f.member, copy["message_id"], f.spare, c.caller) == second

