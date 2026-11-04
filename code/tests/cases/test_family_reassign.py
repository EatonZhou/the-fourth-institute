"""Reassigning a Family request to the member it should have gone to (decision_44cf587c1721).

The sender, a facade owner or the Family's steward names the right member;
the original member's waiting copy is withdrawn, the new member gets the
request from its original sender, the facade shows it with the new member,
and the correction is kept for the next routing and told to the steward.

:module: lineage.team
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role

import pytest

from management_portal.society_service import (
    family_facade,
    family_reassign,
    lineage_graph_state,
    resident_access,
    resident_conversation_stream,
    resident_domain,
    resident_protocol,
)
from management_portal.society_service.resident_record_domain import find_resident as _find_resident
from tests.cases.isa_test_support import run
from tests.cases.test_resident_delivery_receipts import delivery_resident  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import (  # noqa: F401
    _new_session_payload,
    future_creation_store,
)

_REAL_TELL_STEWARD = family_facade.tell_steward
_REAL_VALIDATE_RELAY = family_facade.validate_relay


def _register(title):
    record = resident_domain.register_resident(_new_session_payload(title), "sample_admin")
    resident_protocol.change_resident_state(record["resident_id"], "sample_admin", state_domain="intake", transition="close")
    return record["resident_id"]


def _stored(resident_id, message_id):
    return resident_conversation_stream.get_repository().message(resident_id, message_id)


def _state(resident_id, message_id):
    return resident_conversation_stream.get_repository().input_state(resident_id, message_id).get("state")


@pytest.fixture
def family(delivery_resident, monkeypatch):
    """A facade whose request from sample_admin went to the design member instead of the debug one."""

    wrong = delivery_resident["resident_id"]
    right = _register("family 设计")
    steward = _register("Family · steward")
    facade = _register("Family 门面")
    set_role(facade, family_id="fbi_reassign001", actor="sample_admin")
    members = {wrong, right}
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record: set(members))
    monkeypatch.setattr(family_facade, "family_steward_id", lambda record: steward)
    marks = {}
    monkeypatch.setattr(family_facade, "validate_relay", lambda business_ref, actor: marks.get(business_ref))
    told = []
    monkeypatch.setattr(family_facade, "tell_steward", lambda *args, **options: told.append((args, options)))

    def ask(text, *, quote=None):
        request = run("DeliverMessage", {"resident_id": facade, "message": {
            "content_parts": [{"type": "text", "text": text}], **({"quote": quote} if quote else {})}}, actor="sample_admin").raise_for_error()["message_id"]
        mark = family_facade.relay_mark(facade, request)
        marks[mark] = (facade, request)
        resident_protocol.deliver_resident_input({"resident_id": wrong, "message": {
            "message_id": f"msg_{request[-12:]}0000", "role": "user",
            "content_parts": _stored(facade, request)["content_parts"]}},
            "sample_admin", delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=mark))
        return request

    return {"facade": facade, "wrong": wrong, "right": right, "steward": steward, "members": members,
            "ask": ask, "told": told}


def _reassign(family, request, member, actor="sample_admin", reason="这是 steward 设计，应交给 family 设计"):
    return run("RelayFamilyMessage", {"resident_id": family["facade"], "message_id": request, "member_id": member,
                                      "reassign": True, "reason": reason}, actor=actor)


def test_a_reassigned_request_moves_to_the_named_member_and_leaves_a_correction(family):
    """改派把门面请求交给指定成员，并留下改派纠正

    RelayFamilyMessage reassign=true 撤回原成员还在排队的副本，以原发送者把请求交给新成员；门面的转交记录指向新成员并保留改派来源；改派纠正写入 family_corrections_<门面>，并以 info 告诉 steward。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("steward 的行动逻辑可以再优化")
    first_copy = family_facade.relay_links(_find_resident(facade))[request]["message_id"]
    assert _state(wrong, first_copy) == "queued"

    result = _reassign(family, request, right).raise_for_error()

    assert result["status"] == "reassigned" and result["member_id"] == right
    assert result["previous_member_id"] == wrong
    # The waiting copy is withdrawn; the new member has the request from its original sender.
    assert _state(wrong, first_copy) == "cancelled"
    copy = _stored(right, result["message_id"])
    assert copy["content_parts"] == [{"type": "text", "text": "steward 的行动逻辑可以再优化"}]
    assert copy["author_id"] == "sample_admin"
    link = family_facade.relay_links(_find_resident(facade))[request]
    assert link["resident_id"] == right and link["message_id"] == result["message_id"]
    assert [(item["resident_id"], item["state"]) for item in link["reassigned_from"]] == [(wrong, "queued")]
    # The next routing reads the correction; the steward hears of it.
    stored = lineage_graph_state.read_cell(_find_resident(facade)["lineage_id"], family_reassign.corrections_key(facade))
    [correction] = stored["value"]
    assert correction["request_message_id"] == request and correction["request"] == "steward 的行动逻辑可以再优化"
    assert correction["from"] == {"resident_id": wrong, "title": "Delivery receipt"}
    assert correction["to"] == {"resident_id": right, "title": "family 设计"}
    assert correction["judged_by"] == "sample_admin" and correction["reason"] == "这是 steward 设计，应交给 family 设计"
    [(args, options)] = family["told"]
    assert args[0] == facade and args[1] == family["steward"] and "改派" in args[3]
    assert options == {"activation_policy": "attach_to_next", "_family_rendered": True, "message_id":
        family_reassign.resident_message_contract.derived_message_identity("family-reassignment-info", result["message_id"])}


def test_a_request_can_move_again_and_the_corrections_keep_the_latest(family, monkeypatch):
    """请求可以再次改派，改派纠正只保留最近的条目

    同一请求改派回原成员时投递新的副本、保留完整改派来源；改派纠正超过上限时去掉最早的条目。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("登录页 500 了")
    _reassign(family, request, right).raise_for_error()
    back = _reassign(family, request, wrong, reason="还是登录问题").raise_for_error()

    link = family_facade.relay_links(_find_resident(facade))[request]
    assert link["resident_id"] == wrong and back["previous_member_id"] == right
    assert [item["resident_id"] for item in link["reassigned_from"]] == [wrong, right]
    # Each move delivers a copy of its own, so moving back is not taken as a retry of the first delivery.
    assert back["message_id"] != link["reassigned_from"][0]["message_id"]
    monkeypatch.setattr(family_reassign, "MAX_CORRECTIONS", 2)
    other = family["ask"]("权限页")
    _reassign(family, other, right).raise_for_error()
    stored = lineage_graph_state.read_cell(_find_resident(facade)["lineage_id"], family_reassign.corrections_key(facade))
    assert [item["request"] for item in stored["value"]] == ["登录页 500 了", "权限页"]


def test_a_reassignment_needs_a_relayed_request_another_member_and_no_turn_in_progress(family, monkeypatch):
    """改派需要已转交的请求、另一位成员且原成员没有在处理

    改派给当前成员、非成员或还没转交的请求按参数错误拒绝；原成员正在处理时要求先停止本轮；请求发送者、门面 owner 和本 Family 的 steward 以外的人无权改派。
    """
    facade, wrong, right = family["facade"], family["wrong"], family["right"]
    request = family["ask"]("登录页 500 了")
    copy = family_facade.relay_links(_find_resident(facade))[request]["message_id"]

    assert _reassign(family, request, wrong).error["code"] == "instruction.invalid_arguments"
    assert _reassign(family, request, "resident_ffffffffffff").error["code"] == "instruction.invalid_arguments"
    waiting = run("DeliverMessage", {"resident_id": facade, "message": {
        "content_parts": [{"type": "text", "text": "还没转交"}]}}, actor="sample_admin").raise_for_error()["message_id"]
    assert _reassign(family, waiting, right).error["code"] == "instruction.invalid_arguments"
    resident_conversation_stream.get_repository().set_input_state(wrong, copy, "running")
    refused = _reassign(family, request, right)
    assert refused.error["code"] == "instruction.invalid_arguments" and "先停止本轮" in refused.error["message"]
    assert family_facade.relay_links(_find_resident(facade))[request]["resident_id"] == wrong
    # Someone else's request is not theirs to move unless they own the facade or are its steward.
    monkeypatch.setattr(resident_access, "can_manage", lambda record, actor: False)
    assert _reassign(family, request, right, actor="CASE-A").error["code"] == "instruction.forbidden"
    assert family["told"] == []


def _intent(family, request):
    record = _find_resident(family["facade"])
    return family_reassign._ledger(record).get(record["lineage_id"], family["facade"])["reassignments"][request][-1]


def test_competing_reassignment_cannot_admit_a_second_target(family, monkeypatch):
    from management_portal.society_service import outward_messages, invariant_locks
    root = family["ask"]("one original request, two competing targets")
    third = _register("third reassignment target")
    family["members"].add(third)
    deliver = outward_messages.deliver_to_resident
    competing, admitted = [], []

    def interleave(payload, *args, **kwargs):
        # The long-lived ownership lock must not defer the intake callbacks.
        assert not invariant_locks.lock_held()
        admitted.append(payload["resident_id"])
        competing.append(_reassign(family, root, third))
        return deliver(payload, *args, **kwargs)

    monkeypatch.setattr(outward_messages, "deliver_to_resident", interleave)
    result = _reassign(family, root, family["right"]).raise_for_error()
    assert competing[0].error["code"] == "instruction.in_progress"
    assert admitted == [family["right"]]
    assert _state(family["right"], result["message_id"]) == "queued"
    assert family_facade.relay_links(_find_resident(family["facade"]))[root]["resident_id"] == family["right"]
    assert not resident_conversation_stream.get_repository().read_messages(third)


def test_failed_admission_keeps_the_same_recoverable_intent(family, monkeypatch):
    from management_portal.society_service import outward_messages
    root = family["ask"]("keep original author and task through an interrupted admission")
    original = _stored(family["facade"], root)
    old = family_facade.relay_links(_find_resident(family["facade"]))[root]
    deliver = outward_messages.deliver_to_resident
    monkeypatch.setattr(outward_messages, "deliver_to_resident", lambda *a, **kw: (_ for _ in ()).throw(ValueError("target rejected before acceptance")))
    failed = _reassign(family, root, family["right"])
    assert failed.error["code"] == "instruction.failed"
    assert failed.error["commit_state"] == "committed"
    saved = _intent(family, root)
    assert saved["status"] == "pending" and not saved.get("accepted")
    assert _state(family["wrong"], old["message_id"]) == "cancelled"
    assert _stored(family["right"], saved["message_id"]) is None
    assert family_facade.relay_links(_find_resident(family["facade"]))[root] == old
    third = _register("other target must not replace the pending intent")
    family["members"].add(third)
    conflict = _reassign(family, root, third)
    assert conflict.error["code"] == "instruction.conflict"
    monkeypatch.setattr(outward_messages, "deliver_to_resident", deliver)
    result = _reassign(family, root, family["right"], reason="a retry cannot replace the saved reason").raise_for_error()
    assert result["message_id"] == saved["message_id"]
    assert _stored(family["facade"], root) == original
    copy = _stored(family["right"], result["message_id"])
    assert copy["author_id"] == original["author_id"] and copy["content_parts"] == original["content_parts"]
    assert _intent(family, root)["reason"] == saved["reason"]
    assert _state(family["right"], result["message_id"]) == "queued"


def test_accepted_delivery_is_not_repeated_after_a_lost_response(family, monkeypatch):
    from management_portal.society_service import outward_messages
    root = family["ask"]("lost target acceptance response")
    deliver = outward_messages.deliver_to_resident
    def interrupt(*args, **kwargs):
        deliver(*args, **kwargs)
        raise ValueError("response lost after durable acceptance")
    monkeypatch.setattr(outward_messages, "deliver_to_resident", interrupt)
    failed = _reassign(family, root, family["right"])
    assert failed.error["commit_state"] == "committed" and failed.error["details"]["accepted"]
    saved = _intent(family, root)
    assert family_facade.relay_links(_find_resident(family["facade"]))[root] == saved["link"]
    assert _state(family["right"], saved["message_id"]) == "queued"
    monkeypatch.setattr(outward_messages, "deliver_to_resident", lambda *a, **kw: pytest.fail("accepted input must not be delivered twice"))
    first = _reassign(family, root, family["right"]).raise_for_error()
    second = _reassign(family, root, family["right"]).raise_for_error()
    assert first == second and first["message_id"] == saved["message_id"]
    record = _find_resident(family["facade"])
    corrections = lineage_graph_state.read_cell(record["lineage_id"], family_reassign.corrections_key(family["facade"]))["value"]
    assert len(corrections) == 1 and len(family["told"]) == 1


@pytest.mark.parametrize("after_acceptance", [False, True])
def test_original_withdrawal_wins_reassignment_admission(family, monkeypatch, after_acceptance):
    from management_portal.society_service import outward_messages
    root = family["ask"]("withdraw the original during handoff")
    deliver = outward_messages.deliver_to_resident
    def withdraw(*args, **kwargs):
        if after_acceptance:
            receipt = deliver(*args, **kwargs)
        withdrawn = run("CancelInput", {"resident_id": family["facade"], "message_id": root}, actor="sample_admin").raise_for_error()
        assert withdrawn["status"] == "withdrawn"
        assert withdrawn["input_state"]["state"] == "cancelled"
        return receipt if after_acceptance else deliver(*args, **kwargs)
    monkeypatch.setattr(outward_messages, "deliver_to_resident", withdraw)
    result = _reassign(family, root, family["right"])
    assert result.error is None, result.error
    assert result.raise_for_error()["status"] == "withdrawn"
    saved = _intent(family, root)
    assert saved["status"] == "withdrawn"
    if after_acceptance:
        assert _state(family["right"], saved["message_id"]) == "cancelled"
        assert family_facade.relay_links(_find_resident(family["facade"]))[root] == saved["link"]
    else:
        assert _stored(family["right"], saved["message_id"]) is None
    assert _reassign(family, root, family["right"]).raise_for_error()["status"] == "withdrawn"
    assert family["told"] == []


def test_explicit_handoff_from_completed_holder_preserves_its_terminal_receipt(family):
    root = family["ask"]("already done")
    old = family_facade.relay_links(_find_resident(family["facade"]))[root]
    resident_conversation_stream.get_repository().set_input_state(family["wrong"], old["message_id"], "completed")
    result = _reassign(family, root, family["right"]).raise_for_error()
    assert result["status"] == "reassigned"
    assert _state(family["wrong"], old["message_id"]) == "completed"
    assert _state(family["right"], result["message_id"]) == "queued"
    assert _intent(family, root)["previous_state"] == "completed"


def test_stopped_holder_allows_a_new_explicit_handoff_without_reopening_it(family):
    root = family["ask"]("stop the old holder, then explicitly choose a new member")
    old = family_facade.relay_links(_find_resident(family["facade"]))[root]
    run("CancelInput", {"resident_id": family["facade"], "message_id": root}, actor="sample_admin").raise_for_error()
    assert _state(family["wrong"], old["message_id"]) == "cancelled"
    # The facade's completed routing receipt is distinct from its holder's cancellation.
    assert _state(family["facade"], root) == "completed"
    result = _reassign(family, root, family["right"]).raise_for_error()
    assert result["status"] == "reassigned"
    assert _state(family["wrong"], old["message_id"]) == "cancelled"
    assert _state(family["right"], result["message_id"]) == "queued"


def test_target_preflight_failure_preserves_the_queued_holder(family, monkeypatch):
    from management_portal.society_service import resident_intake_domain
    root = family["ask"]("target identity validation fails before withdrawal")
    old = family_facade.relay_links(_find_resident(family["facade"]))[root]
    resolve = resident_intake_domain._deliverable_record
    def refuse(request):
        if request.resident_id == family["right"]:
            raise ValueError("target registration not ready")
        return resolve(request)
    monkeypatch.setattr(resident_intake_domain, "_deliverable_record", refuse)
    result = _reassign(family, root, family["right"])
    assert result.error["commit_state"] == "not_committed"
    assert _state(family["wrong"], old["message_id"]) == "queued"
    assert family_facade.relay_links(_find_resident(family["facade"]))[root] == old


def test_reassignment_ownership_is_nonblocking_across_processes(tmp_path):
    import subprocess
    import sys
    from management_portal.society_service import invariant_locks
    root = str(invariant_locks._ROOT_PROVIDER())
    script = (
        "from pathlib import Path\n"
        "from management_portal.society_service import invariant_locks as locks\n"
        f"locks.configure_root_provider(lambda: Path({root!r}))\n"
        "with locks.family_reassignment_lock('facade', 'request') as owned:\n"
        " print(owned)\n"
    )
    with invariant_locks.family_reassignment_lock("facade", "request") as owned:
        assert owned
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0 and result.stdout.strip() == "False"
    with invariant_locks.family_reassignment_lock("facade", "request") as owned:
        assert owned


@pytest.mark.parametrize("wrong_business", [True, False])
def test_same_id_from_another_business_or_origin_is_not_an_acceptance_receipt(family, monkeypatch, wrong_business):
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service import outward_messages
    root = family["ask"]("same text and author do not prove the same handoff")
    deliver = outward_messages.deliver_to_resident
    monkeypatch.setattr(outward_messages, "deliver_to_resident", lambda *a, **kw: (_ for _ in ()).throw(ValueError("temporarily unavailable")))
    assert _reassign(family, root, family["right"]).error["commit_state"] == "committed"
    saved = _intent(family, root)
    with operation_identity_scope(OperationIdentity.for_actor(saved["author"],
            initiated_by=saved["actor"] if wrong_business else family["wrong"])):
        resident_protocol.deliver_resident_input({"resident_id": family["right"], "message": saved["envelope"]},
            saved["author"], delivery_context=resident_protocol.ResidentDeliveryContext(
                business_ref="unrelated-business" if wrong_business else saved["business_ref"]))
    monkeypatch.setattr(outward_messages, "deliver_to_resident", deliver)
    result = _reassign(family, root, family["right"])
    assert result.error["commit_state"] == "committed"
    assert not _intent(family, root).get("accepted")
    assert family_facade.relay_links(_find_resident(family["facade"]))[root] == saved["previous"]


def test_intake_preflight_is_read_only_and_shares_capability_checks(family, monkeypatch):
    from types import SimpleNamespace
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service import resident_execution_work, resident_intake_domain as intake
    root = family["ask"]("read-only preflight")
    record = _find_resident(family["right"])
    before = resident_execution_work.read(record)
    envelope = {"role": "user", "message_id": "msg_abcdefabcdef12345678901234567890", "content_parts": [{"type": "text", "text": "read-only preflight"}]}
    ref = family_facade.relay_mark(family["facade"], root)
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        candidate = intake.preflight_resident_input(family["right"], "sample_admin", envelope, business_ref=ref)
    assert candidate["author_id"] == "sample_admin"
    assert candidate["business_ref"] == ref
    assert _stored(family["right"], envelope["message_id"]) is None
    assert resident_execution_work.read(record) == before
    envelope["content_parts"].append({"type": "image", "path": "/preflight-must-not-stage-this-file.png"})
    monkeypatch.setattr(intake.resident_execution_provider, "provider_for", lambda _: SimpleNamespace(
        capabilities=SimpleNamespace(supports_media=False, local_tools=True)))
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")), pytest.raises(ValueError, match="图片"):
        intake.preflight_resident_input(family["right"], "sample_admin", envelope, business_ref=ref)
    assert _stored(family["right"], envelope["message_id"]) is None
    assert resident_execution_work.read(record) == before


def test_reassignment_preserves_the_original_quote_without_requiring_recipient_history(family):
    referenced = family["ask"]("original quoted context")
    root = family["ask"]("the quoted task is reassigned", quote={"message_id": referenced})
    original = _stored(family["facade"], root)
    result = _reassign(family, root, family["right"]).raise_for_error()
    delivered = _stored(family["right"], result["message_id"])
    assert delivered["quote"]["author_id"] == original["quote"]["author_id"]
    assert delivered["quote"]["text"] == original["quote"]["text"]
    assert _stored(family["facade"], root) == original


def test_steward_note_and_correction_are_idempotent_when_its_receipt_is_lost(family, monkeypatch):
    root = family["ask"]("note delivery failed after durable acceptance")
    sent = []
    def tell(facade, steward, actor, text, **options):
        result = _REAL_TELL_STEWARD(facade, steward, actor, text, **options)
        sent.append(options["message_id"])
        if len(sent) == 1:
            raise ValueError("note receipt lost")
        return result
    monkeypatch.setattr(family_facade, "tell_steward", tell)
    first = _reassign(family, root, family["right"])
    assert first.error["commit_state"] == "committed"
    saved = _intent(family, root)
    assert saved["accepted"]
    second = _reassign(family, root, family["right"]).raise_for_error()
    assert second["message_id"] == saved["message_id"]
    assert sent[0] == sent[1]
    assert _stored(family["steward"], sent[0]) is not None
    from management_portal.society_service import resident_held_infos
    notes = resident_held_infos.peek(_find_resident(family["steward"]))
    assert [message["message_id"] for message in notes] == [sent[0]]
    record = _find_resident(family["facade"])
    entries = lineage_graph_state.read_cell(record["lineage_id"], family_reassign.corrections_key(family["facade"]))["value"]
    assert len(entries) == 1


def test_queued_holder_completing_during_withdrawal_does_not_authorize_a_new_copy(family, monkeypatch):
    from management_portal.society_service import resident_queue_control_domain
    root = family["ask"]("queued at intent, completed before withdrawal")
    old = family_facade.relay_links(_find_resident(family["facade"]))[root]
    cancel = resident_queue_control_domain.cancel_reassignment_input
    def completes(*args, **kwargs):
        resident_conversation_stream.get_repository().set_input_state(family["wrong"], old["message_id"], "completed")
        return cancel(*args, **kwargs)
    monkeypatch.setattr(resident_queue_control_domain, "cancel_reassignment_input", completes)
    result = _reassign(family, root, family["right"])
    assert result.error["commit_state"] == "committed"
    saved = _intent(family, root)
    assert saved["previous_state"] == "queued" and not saved.get("accepted")
    assert _stored(family["right"], saved["message_id"]) is None
    assert family_facade.relay_links(_find_resident(family["facade"]))[root] == old


def test_facade_only_author_can_reassign_to_private_member_without_direct_visibility(family, monkeypatch):
    from tests.cases.permission_test_support import enable, Fact
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from management_portal.society_service import resident_intake_domain as intake, resident_queue_control_domain
    root = family["ask"]("only the facade is visible to the original author")
    outsider = _register("not a member")
    permissions = enable(monkeypatch)
    permissions.users("sample_admin", category="intern")
    permissions.place(*(Fact("resident", rid) for rid in (family["facade"], family["wrong"], family["right"], family["steward"], outsider)))
    permissions.grant("sample_admin", family["facade"], "@contributor")
    for rid in (family["wrong"], family["right"], family["steward"], outsider):
        permissions.deny("sample_admin", rid, "@viewer")
    monkeypatch.setattr(family_facade, "validate_relay", _REAL_VALIDATE_RELAY)
    assert resident_access.resident_visible_to_actor(_find_resident(family["facade"]), "sample_admin")
    assert not resident_access.resident_visible_to_actor(_find_resident(family["right"]), "sample_admin")
    before = family_facade.relay_links(_find_resident(family["facade"]))[root]
    envelope = {"message_id": "msg_abcdef12345678901234567890abcdef", "role": "user", "content_parts": _stored(family["facade"], root)["content_parts"]}
    mark = family_facade.relay_mark(family["facade"], root)
    with operation_identity_scope(OperationIdentity.for_actor("sample_admin")):
        intake.preflight_resident_input(family["right"], "sample_admin", envelope, business_ref=mark)
        for target, ref in ((family["right"], ""), (family["right"], "forged-reference"), (outsider, mark)):
            with pytest.raises((KeyError, PermissionError)):
                intake.preflight_resident_input(target, "sample_admin", envelope, business_ref=ref)
    assert _state(family["wrong"], before["message_id"]) == "queued"
    assert run("DeliverMessage", {"resident_id": family["right"], "message": envelope}, actor="sample_admin").error
    assert run("CancelInput", {"resident_id": family["wrong"], "message_id": before["message_id"]}, actor="sample_admin").error
    with pytest.raises(PermissionError):
        resident_queue_control_domain.cancel_reassignment_input(
            family["facade"], root, family["wrong"], before["message_id"], "sample_admin")
    assert _state(family["wrong"], before["message_id"]) == "queued"
    result = _reassign(family, root, family["right"]).raise_for_error()
    assert result["status"] == "reassigned"
    assert _state(family["wrong"], before["message_id"]) == "cancelled"
    assert _state(family["right"], result["message_id"]) == "queued"
    for source, holder, copy in ((root, outsider, result["message_id"]),
                                 (root, family["right"], "msg_abcdef12345678901234567890abcdef"),
                                 ("msg_abcdef12345678901234567890abcdef", family["right"], result["message_id"])):
        with pytest.raises((PermissionError, KeyError)):
            resident_queue_control_domain.cancel_reassignment_input(family["facade"], source, holder, copy, "sample_admin")
    assert _state(family["right"], result["message_id"]) == "queued"
    assert not resident_access.resident_visible_to_actor(_find_resident(family["right"]), "sample_admin")
