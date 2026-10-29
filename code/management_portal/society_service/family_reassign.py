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

from typing import Any

from management_common.async_questions import ANSWER_PART
from management_common.clock import utc_now_iso

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
    from management_common.operation_identity import operation_identity_scope
    from management_common.principal import OperationIdentity

    from . import changes, outward_messages, resident_protocol
    from . import resident_record_domain
    from .state_store import get_lineage_data_store

    facade = resident_record_domain.find_resident(facade_id)
    if not family_facade.is_facade(facade):
        raise ValueError("目标必须是 Family 门面")
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if not message or message.get("role") != "user":
        raise KeyError("门面中没有这条请求")
    if not _may_reassign(facade, message, actor):
        raise PermissionError("只有请求的发送者、能管理门面的身份或本 Family 的 steward 可以改派")
    target = resident_record_domain.find_resident(member_id)
    if not family_facade.is_relay_target(facade, member_id) or not target or target.get("deleted_at"):
        raise ValueError("所选 Resident 不是这个 Family 的成员")
    link = family_facade.relay_links(facade).get(message_id)
    if not link:
        raise ValueError("这条请求还没有交给成员，不能改派")
    previous_id, previous_message_id = str(link["resident_id"]), str(link["message_id"])
    if previous_id == member_id:
        raise ValueError("这条请求已经由这个成员处理")
    author = str(message["author_id"])
    # The checked reassignment acts for the request's sender, as the Router's delivery does.
    identity = OperationIdentity.for_actor(author, initiated_by=actor)
    previous_state = str(repository.input_state(previous_id, previous_message_id).get("state") or "")
    if previous_state in _BUSY_STATES:
        raise ValueError("原成员正在处理这条请求，先停止本轮再改派")
    if previous_state == "queued":
        with operation_identity_scope(identity):
            resident_protocol.cancel_resident_queue_item(previous_id, author, previous_message_id)
    history = [*(link.get("reassigned_from") or []), {
        "resident_id": previous_id, "message_id": previous_message_id,
        "relayed_at": str(link.get("relayed_at") or ""), "state": previous_state or "queued",
    }]
    delivered_id = resident_message_contract.derived_message_identity(
        "family-reassign", facade_id, message_id, member_id, str(len(history)),
    )
    # The original answer already closes its question at its original owner.
    # Another member receives the same task text; the owner's question marker
    # stays on the facade instead of becoming a question in the new recipient.
    content_parts = [part for part in message["content_parts"] if part.get("type") != ANSWER_PART]
    with operation_identity_scope(identity):
        result = outward_messages.deliver_to_resident(
            {"resident_id": member_id, "message": {
                "role": "user", "message_id": delivered_id, "content_parts": content_parts,
            }, "activation_policy": "enqueue_and_trigger"}, author,
            delivery_context=resident_protocol.ResidentDeliveryContext(
                lineage_id=facade["lineage_id"], business_ref=family_facade.relay_mark(facade_id, message_id),
            ),
        )
    delivered_id = str(result.get("message_id") or delivered_id)
    with get_lineage_data_store().family_relays.transaction(resident_lineage.lineage_id(facade), facade_id) as state:
        links = dict(state.get("links") or {})
        links[message_id] = {
            "resident_id": member_id, "message_id": delivered_id, "relayed_at": utc_now_iso(),
            "reassigned_from": history, "reassigned_by": actor,
        }
        state["links"] = links
    correction = {
        "request_message_id": message_id,
        "request": _text(message)[:MAX_REQUEST_EXCERPT],
        "from": {"resident_id": previous_id, "title": str((resident_record_domain.find_resident(previous_id) or {}).get("title") or "")},
        "to": {"resident_id": member_id, "title": str(target.get("title") or "")},
        "judged_by": actor,
        "reason": reason.strip(),
        "at": utc_now_iso(),
    }
    _record_correction(resident_lineage.lineage_id(facade), facade_id, correction)
    steward_id = family_facade.family_steward_id(facade)
    if steward_id and not family_facade.is_own_steward(facade, actor):
        family_facade.tell_steward(
            facade_id, steward_id, actor,
            f"改派：门面请求 {facade_id}/message/{message_id} 从 {correction['from']['title'] or previous_id} "
            f"改派给 {correction['to']['title'] or member_id}，由 {actor} 判断。"
            f"理由：{correction['reason'] or '（未填写）'}。请求摘录：{correction['request']}。"
            f"纠正已记入共享状态 {corrections_key(facade_id)}；按需调整成员标题和分工，不把单条纠正扩展成整类话题规则。",
            activation_policy="attach_to_next",
        )
    changes.resident_changed(facade_id)
    return {"status": "reassigned", "message_id": delivered_id, "member_id": member_id,
            "previous_member_id": previous_id}


def _record_correction(lineage_id: str, facade_id: str, correction: dict[str, Any]) -> None:
    key = corrections_key(facade_id)
    for _attempt in range(5):
        current = lineage_graph_state.read_cell(lineage_id, key)
        before = current["value"] if current["exists"] else None
        entries = [item for item in (before if isinstance(before, list) else []) if isinstance(item, dict)]
        written = lineage_graph_state.update_cell(
            lineage_id, key, [*entries, correction][-MAX_CORRECTIONS:], expected_value=before,
        )
        if written.get("matched"):
            return
    raise RuntimeError("改派纠正写入冲突，请重试")
