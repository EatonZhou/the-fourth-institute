"""Reassigning a Family request to another member, and the corrections it leaves (decision_44cf587c1721).

A facade request the Router gave to the wrong member is handed to the right
one: its sender, a facade owner or the Family's steward names the member.  The
original member's copy is withdrawn while it still waits; a copy in progress
is stopped first by whoever reassigns, so a reassignment never cuts a turn
short.  The new member gets the request as the Router's delivery would give
it - the same content, from the original sender - and the facade shows the
request with that member from then on.

Each reassignment is a correction the next routing can learn from: the
platform appends it to the Lineage State Cell ``family_corrections_<facade>``
(newest last, at most ``MAX_CORRECTIONS``), which the Family Router reads with
its guide, and tells the steward, who folds lasting lessons into the members'
division and keeps the corrections as examples.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import Any

from management_common.async_questions import ANSWER_PART
from management_common.clock import utc_now_iso
from management_portal.hatg.contracts import InstructionError

from . import family_facade, lineage_graph_state, resident_conversation_stream, resident_lineage, resident_message_contract

MAX_CORRECTIONS = 20
MAX_REQUEST_EXCERPT = 200
_BUSY_STATES = {"running", "stopping"}


def corrections_key(facade_id: str) -> str:
    return f"family_corrections_{facade_id}"


def _text(message: dict[str, Any]) -> str:
    return " ".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


def _may_reassign(facade: dict[str, Any], message: dict[str, Any], actor: str) -> bool:
    from management_common.actor_identity import canonical_actor_id, same_actor

    from . import resident_access

    actor_id = canonical_actor_id(actor)
    return (same_actor(message.get("author_id"), actor_id) or resident_access.can_manage(facade, actor_id)
            or family_facade.is_own_steward(facade, actor_id))


def reassign_request(facade_id: str, message_id: str, member_id: str, actor: str, *, reason: str = "") -> dict[str, Any]:
    from . import invariant_locks, resident_record_domain

    facade = resident_record_domain.find_resident(facade_id)
    if not family_facade.is_facade(facade):
        raise ValueError("目标必须是 Family 门面")
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if not message or message.get("role") != "user":
        raise KeyError("门面中没有这条请求")
    if not _may_reassign(facade, message, actor):
        raise PermissionError("只有请求的发送者、能管理门面的身份或本 Family 的 steward 可以改派")
    with invariant_locks.family_reassignment_lock(facade_id, message_id) as owned:
        if not owned:
            raise InstructionError("instruction.in_progress", "这条请求正在改派，请稍后核对同一请求", retryable=True)
        return _reassign_owned(facade, message, member_id, actor, reason)


def _ledger(facade):
    from .state_store import get_lineage_data_store
    return get_lineage_data_store().family_relays


def _entries(state, message_id):
    return state.setdefault("reassignments", {}).setdefault(message_id, [])


def _holder_state(resident_id, message_id):
    _, holder = family_facade.relay_holder(resident_id, message_id)
    return resident_conversation_stream.get_repository().input_state(*holder).get("state")


def _update(facade, message_id, intent):
    with _ledger(facade).transaction(facade["lineage_id"], facade["resident_id"]) as state:
        entries = _entries(state, message_id)
        if not entries or entries[-1]["message_id"] != intent["message_id"]:
            raise InstructionError("instruction.conflict", "改派意图已变化")
        if entries[-1]["status"] == "withdrawn" and intent["status"] != "withdrawn":
            raise _Withdrawn()
        if entries[-1].get("withdrawn_by"):
            intent["withdrawn_by"] = entries[-1]["withdrawn_by"]
        entries[-1] = copy.deepcopy(intent)


def _reassign_owned(facade, message, member_id, actor, reason):
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from . import outward_messages, resident_protocol, resident_record_domain, resident_intake_admission, resident_intake_domain, resident_queue_control_domain

    facade_id, message_id = facade["resident_id"], message["message_id"]
    lineage_id = facade["lineage_id"]
    repository = resident_conversation_stream.get_repository()
    state = _ledger(facade).get(lineage_id, facade_id)
    entries = (state.get("reassignments") or {}).get(message_id) or []
    intent = copy.deepcopy(entries[-1]) if entries else None
    if repository.input_state(facade_id, message_id).get("state") == "cancelled":
        return _withdraw(facade, message_id, intent) if intent else {"status": "withdrawn", "message_id": message_id}
    if intent and intent["status"] == "withdrawn":
        return _withdraw(facade, message_id, intent)
    link = (state.get("links") or {}).get(message_id)
    if intent and intent["status"] != "relayed" and intent["target"] != member_id:
        raise InstructionError("instruction.conflict", "这条请求已有另一目标的待恢复改派", commit_state="committed",
                               details={"pending_message_id": intent["message_id"], "member_id": intent["target"]})
    replay = intent and (intent["status"] != "relayed" or (intent["target"] == member_id and link == intent["link"]))
    if not replay:
        target = resident_record_domain.find_resident(member_id)
        if not family_facade.is_relay_target(facade, member_id) or not target or target.get("deleted_at"):
            raise ValueError("所选 Resident 不是这个 Family 的成员")
        if not link:
            raise ValueError("这条请求还没有交给成员，不能改派")
        if link["resident_id"] == member_id:
            raise ValueError("这条请求已经由这个成员处理")
        previous_state = _holder_state(link["resident_id"], link["message_id"])
        if previous_state in _BUSY_STATES:
            raise ValueError("原成员正在处理这条请求，先停止本轮再改派")
        history = [*(link.get("reassigned_from") or []), {
            "resident_id": link["resident_id"], "message_id": link["message_id"],
            "relayed_at": str(link.get("relayed_at") or ""), "state": previous_state or "queued",
        }]
        delivered_id = resident_message_contract.derived_message_identity(
            "family-reassign", facade_id, message_id, member_id, str(len(history)),
        )
        envelope = {"role": "user", "message_id": delivered_id,
                    "content_parts": [copy.deepcopy(p) for p in message["content_parts"] if p.get("type") != ANSWER_PART]}
        business_ref = family_facade.relay_mark(facade_id, message_id)
        with operation_identity_scope(OperationIdentity.for_actor(str(message["author_id"]), initiated_by=actor)):
            input_message = resident_intake_domain.preflight_resident_input(
                member_id, str(message["author_id"]), envelope, business_ref=business_ref,
            )
        at = utc_now_iso()
        intent = {"status": "pending", "target": member_id, "previous": copy.deepcopy(link),
                  "previous_state": previous_state,
                  "message_id": delivered_id, "author": str(message["author_id"]), "actor": actor,
                  "envelope": envelope, "input_message": input_message, "business_ref": business_ref,
                  "at": at, "reason": reason.strip(),
                  "steward": family_facade.family_steward_id(facade),
                  "notify": not family_facade.is_own_steward(facade, actor),
                  "link": {"resident_id": member_id, "message_id": delivered_id, "relayed_at": at,
                           "reassigned_from": history, "reassigned_by": actor}}
        intent["correction"] = _correction(message, link, target, actor, reason, at, delivered_id)
        with _ledger(facade).transaction(lineage_id, facade_id) as state:
            if (state.get("links") or {}).get(message_id) != link:
                raise InstructionError("instruction.conflict", "原请求的承接成员已变化")
            _entries(state, message_id).append(copy.deepcopy(intent))
    try:
        identity = OperationIdentity.for_actor(intent["author"], initiated_by=intent["actor"])
        with operation_identity_scope(identity):
            if not intent.get("accepted"):
                old = intent["previous"]
                _check_holder(facade, message_id, intent)
                previous_state = _holder_state(old["resident_id"], old["message_id"])
                if previous_state in _BUSY_STATES or (previous_state == "completed" and intent["previous_state"] != "completed"):
                    raise ValueError("原成员已开始或完成处理，不能改派")
                if previous_state == "queued":
                    resident_queue_control_domain.cancel_reassignment_input(
                        facade_id, message_id, old["resident_id"], old["message_id"], intent["actor"])
                # Read durable intake facts before any retry; a lost response is not a new send.
                if _accepted(intent):
                    _commit_accepted(facade, message_id, intent)
                else:
                    @contextmanager
                    def guard():
                        with _ledger(facade).transaction(lineage_id, facade_id):
                            _check_holder(facade, message_id, intent)
                            if repository.input_state(facade_id, message_id).get("state") == "cancelled":
                                raise _Withdrawn()
                            old_state = _holder_state(old["resident_id"], old["message_id"])
                            if old_state in _BUSY_STATES or (old_state == "completed" and intent["previous_state"] != "completed"):
                                raise ValueError("原成员在撤回期间开始或完成处理，不能改派")
                            current = resident_record_domain.find_resident(intent["target"])
                            if not current or current.get("deleted_at") or not family_facade.is_relay_target(facade, intent["target"]):
                                raise ValueError("改派目标已离开 Family")
                            yield
                    with resident_intake_admission.bind(intent["target"], intent["message_id"], guard,
                            lambda mid: _commit_accepted(facade, message_id, intent)):
                        outward_messages.deliver_to_resident(
                            {"resident_id": intent["target"], "message": intent["envelope"],
                             "activation_policy": "enqueue_and_trigger"}, intent["author"],
                            delivery_context=resident_protocol.ResidentDeliveryContext(
                                lineage_id=lineage_id, business_ref=family_facade.relay_mark(facade_id, message_id)),
                        )
                    if not intent.get("accepted"):
                        if not _accepted(intent):
                            raise RuntimeError("改派投递未取得持久受理回执")
                        _commit_accepted(facade, message_id, intent)
        if repository.input_state(facade_id, message_id).get("state") == "cancelled":
            return _withdraw(facade, message_id, intent)
        _finish(facade, message_id, intent)
        return {"status": "reassigned", "message_id": intent["message_id"], "member_id": intent["target"],
                "previous_member_id": intent["previous"]["resident_id"]}
    except _Withdrawn:
        return _withdraw(facade, message_id, intent)
    except Exception as error:
        raise InstructionError("instruction.failed", "改派意图已保存；请用新调用按同一原请求和目标恢复",
            commit_state="committed", details={"reason": "family.reassignment_pending",
                "request_message_id": message_id, "member_id": intent["target"],
                "message_id": intent["message_id"], "accepted": bool(intent.get("accepted")),
                "cause": str(error), "retry": "same_request_same_target"}) from error


class _Withdrawn(Exception):
    pass


def _check_holder(facade, message_id, intent):
    state = _ledger(facade).get(facade["lineage_id"], facade["resident_id"])
    entries = (state.get("reassignments") or {}).get(message_id) or []
    if entries and entries[-1]["message_id"] == intent["message_id"] and entries[-1]["status"] == "withdrawn":
        raise _Withdrawn()
    link = (state.get("links") or {}).get(message_id)
    expected = intent["link"] if intent.get("accepted") else intent["previous"]
    if link != expected:
        raise InstructionError("instruction.conflict", "原请求的承接记录已变化，不能覆盖")


def _accepted(intent):
    from . import resident_execution_work, resident_record_domain
    repository = resident_conversation_stream.get_repository()
    message = repository.message(intent["target"], intent["message_id"])
    if message is not None:
        if repository.message_business_ref(intent["target"], intent["message_id"]) != intent["business_ref"]:
            raise resident_message_contract.MessageIdentityConflict("改派副本属于其他业务")
        candidate = {**intent["input_message"], "created_at": str(message.get("created_at") or "")}
        repository.validate_message_identity(intent["target"], candidate)
        return True
    record = resident_record_domain.find_resident(intent["target"])
    location = (resident_execution_work.input_location(intent["target"], intent["message_id"],
                resident_execution_work.read(record)) if record else None)
    if location is None:
        return False
    stored = location.get("input_message") or {}
    if (str(stored.get("business_ref") or "") != intent["business_ref"]
            or ("business_ref" in location and location["business_ref"] != intent["business_ref"])):
        raise resident_message_contract.MessageIdentityConflict("排队改派副本属于其他业务")
    from .resident_conversation_records import immutable_message_fields
    candidate = {**intent["input_message"], "created_at": str(stored.get("created_at") or "")}
    if immutable_message_fields(stored) != immutable_message_fields(candidate):
        raise resident_message_contract.MessageIdentityConflict("排队改派副本身份与原意图不符")
    return True


def _commit_accepted(facade, message_id, intent):
    if not _accepted(intent):
        raise RuntimeError("改派受理事实尚未可核验")
    with _ledger(facade).transaction(facade["lineage_id"], facade["resident_id"]) as state:
        _check_holder(facade, message_id, intent)
        state.setdefault("links", {})[message_id] = copy.deepcopy(intent["link"])
        intent["accepted"] = True
        _entries(state, message_id)[-1] = copy.deepcopy(intent)


def withdraw_reassignment(facade_id, message_id, actor):
    """Cancel a pending handoff before following the facade's old terminal holder.

    The public cancellation owner must call this after checking its ordinary
    facade authority. No request ownership lock is taken: withdrawal may win
    while an external delivery is blocked.
    """
    from . import resident_record_domain
    facade = resident_record_domain.find_resident(facade_id)
    if not family_facade.is_facade(facade):
        return None
    state = _ledger(facade).get(facade["lineage_id"], facade_id)
    entries = (state.get("reassignments") or {}).get(message_id) or []
    if not entries or entries[-1]["status"] == "relayed":
        return None
    message = resident_conversation_stream.get_repository().message(facade_id, message_id)
    if not message or not _may_reassign(facade, message, actor):
        raise PermissionError("只有原请求发送者、门面管理者或本 Family steward 可以撤回改派")
    with _ledger(facade).transaction(facade["lineage_id"], facade_id) as state:
        entries = (state.get("reassignments") or {}).get(message_id) or []
        if not entries or entries[-1]["status"] == "relayed":
            return None
        entries[-1]["status"] = "withdrawn"
        entries[-1]["withdrawn_by"] = actor
        intent = copy.deepcopy(entries[-1])
    return _withdraw(facade, message_id, intent)


def _withdraw(facade, message_id, intent):
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity
    from . import resident_queue_control_domain
    if intent:
        intent["status"] = "withdrawn"
        _update(facade, message_id, intent)
        try:
            with operation_identity_scope(OperationIdentity.for_actor(intent["author"],
                    initiated_by=intent.get("withdrawn_by") or intent["actor"])):
                old = intent["previous"]
                actor = intent.get("withdrawn_by") or intent["actor"]
                resident_queue_control_domain.cancel_reassignment_input(
                    facade["resident_id"], message_id, old["resident_id"], old["message_id"], actor)
                if intent.get("accepted") or _accepted(intent):
                    resident_queue_control_domain.cancel_reassignment_input(
                        facade["resident_id"], message_id, intent["target"], intent["message_id"], actor)
        except Exception as error:
            raise InstructionError("instruction.failed", "改派已撤回；停止副本尚待核验，请恢复同一撤回操作",
                commit_state="committed", details={"reason": "family.reassignment_withdrawal_pending",
                    "request_message_id": message_id, "member_id": intent["target"],
                    "message_id": intent["message_id"], "cause": str(error)}) from error
    return {"status": "withdrawn", "message_id": message_id}


def _correction(message, link, target, actor, reason, at, delivered_id):
    from . import resident_record_domain
    return {
        "reassignment_id": delivered_id,
        "request_message_id": message["message_id"],
        "request": _text(message)[:MAX_REQUEST_EXCERPT],
        "from": {"resident_id": link["resident_id"], "title": str((resident_record_domain.find_resident(link["resident_id"]) or {}).get("title") or "")},
        "to": {"resident_id": target["resident_id"], "title": str(target.get("title") or "")},
        "judged_by": actor,
        "reason": reason.strip(),
        "at": at,
    }


def _finish(facade, message_id, intent):
    from . import changes, family_tasks, resident_record_domain
    facade_id, correction = facade["resident_id"], intent["correction"]
    _check_holder(facade, message_id, intent)
    if not _accepted(intent):
        raise RuntimeError("改派受理事实尚未可核验")
    if intent["status"] == "relayed":
        return
    if not intent.get("corrected"):
        # The bounded correction cell and this handoff's receipt commit together;
        # pruning an old example must not cause a retry to insert it again.
        with _ledger(facade).database.transaction():
            _record_correction(resident_lineage.lineage_id(facade), facade_id, correction)
            intent["corrected"] = True
            _update(facade, message_id, intent)
    steward_id = intent["steward"]
    note_id = resident_message_contract.derived_message_identity("family-reassignment-info", intent["message_id"])
    if steward_id and intent["notify"] and not intent.get("informed"):
        if not intent.get("note"):
            note = (
                f"改派：门面请求 {facade_id}/message/{message_id} 从 {correction['from']['title'] or correction['from']['resident_id']} "
                f"改派给 {correction['to']['title'] or intent['target']}，由 {intent['actor']} 判断。"
                f"理由：{correction['reason'] or '（未填写）'}。请求摘录：{correction['request']}。"
                f"纠正已记入共享状态 {corrections_key(facade_id)}；按需调整成员标题和分工，不把单条纠正扩展成整类话题规则。")
            record = resident_record_domain.find_resident(steward_id)
            if record and (record.get("family") or {}).get("role") == "steward":
                note = family_tasks.render(record, "organization", {"change": note})
            intent["note"] = note
            _update(facade, message_id, intent)
        family_facade.tell_steward(
            facade_id, steward_id, intent["actor"], intent["note"],
            activation_policy="attach_to_next",
            message_id=note_id,
            _family_rendered=True,
        )
        intent["informed"] = True
    intent["status"] = "relayed"
    _update(facade, message_id, intent)
    changes.resident_changed(facade_id)


def _record_correction(lineage_id: str, facade_id: str, correction: dict[str, Any]) -> None:
    key = corrections_key(facade_id)
    for _attempt in range(5):
        current = lineage_graph_state.read_cell(lineage_id, key)
        before = current["value"] if current["exists"] else None
        entries = [item for item in (before if isinstance(before, list) else []) if isinstance(item, dict)]
        if any(item.get("reassignment_id") == correction["reassignment_id"] for item in entries):
            return
        written = lineage_graph_state.update_cell(
            lineage_id, key, [*entries, correction][-MAX_CORRECTIONS:], expected_value=before,
        )
        if written.get("matched"):
            return
    raise RuntimeError("改派纠正写入冲突，请重试")
