"""Retain a failed relay receipt alongside a verified, completed review."""
from __future__ import annotations

import json

from management_common.clock import utc_now_iso
from management_common.database_time import instant

from . import family_facade, invariant_locks, resident_access, resident_conversation_stream, resident_record_domain
from .state_store import get_lineage_data_store


def resolve(resident_id, message_id, review_message_id, actor, *, reason):
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("需要说明复核结果与原请求的关系")
    repository = resident_conversation_stream.get_repository()
    with (resident_record_domain.state_lock(resident_id),
          invariant_locks.resident_conversation_lock(resident_id)):
        facade = resident_record_domain.find_resident_for_admission(resident_id)
        if not family_facade.is_facade(facade):
            raise ValueError("目标必须是门面")
        if not (resident_access.can_manage(facade, actor) or family_facade.is_own_steward(facade, actor)):
            raise PermissionError("只有门面管理者或本 Family Steward 可以关联复核结果")
        original = repository.message(resident_id, message_id)
        if not original or original.get("role") != "user":
            raise KeyError("门面原请求不存在")
        previous = repository.input_state(resident_id, message_id)
        if previous["state"] == "cancelled":
            raise ValueError("原请求已经取消")
        store = get_lineage_data_store()
        with store.family_relays.transaction(facade["lineage_id"], resident_id) as ledger:
            links = ledger.setdefault("links", {})
            link = links.get(message_id)
            if link:
                if link.get("message_id") != review_message_id or not link.get("archive"):
                    raise ValueError("原请求已有其他承接关系")
                return _receipt(message_id, review_message_id, link["archive"]["reply_message_ids"])
            if previous["state"] != "failed":
                raise ValueError("只能为尚未转交的失败请求关联复核结果")
            reviewer = _reviewer(facade, original, review_message_id, repository)
            review_state = repository.input_state(reviewer, review_message_id)
            replies = list(review_state.get("reply_message_ids") or ())
            if review_state["state"] != "completed" or not replies:
                raise ValueError("复核输入必须已完成并有真实回复")
            for reply_id in replies:
                reply = repository.message(reviewer, reply_id)
                if (not reply or reply.get("role") != "agent" or reply.get("author_id") != reviewer
                        or reply.get("reply_to_message_id") != review_message_id):
                    raise ValueError("复核回复不属于该复核输入")
            links[message_id] = {
                "resident_id": reviewer, "message_id": review_message_id, "relayed_at": utc_now_iso(),
                "archive": {"actor": actor, "reason": reason, "failed_receipt": previous,
                            "reply_message_ids": replies},
            }
    return _receipt(message_id, review_message_id, replies)


def _reviewer(facade, original, review_message_id, repository):
    facade_id, message_id = facade["resident_id"], original["message_id"]
    steward_id = family_facade.family_steward_id(facade)
    matches = []
    for member_id in family_facade.relay_target_ids(facade):
        source = repository.message(member_id, review_message_id)
        if (not source or source.get("role") != "user" or source.get("author_id") != facade_id
                or instant(source["created_at"]) < instant(original["created_at"])):
            continue
        ref = repository.message_business_ref(member_id, review_message_id)
        if ref == f"relay-review/{facade_id}/message/{message_id}":
            matches.append(member_id)
        elif member_id == steward_id and _legacy_review(facade, original, source, ref):
            matches.append(member_id)
    if len(matches) != 1:
        raise ValueError("复核输入必须唯一并由门面发出，且绑定同一原请求")
    return matches[0]


def _legacy_review(facade, original, source, ref):
    facade_id, message_id = facade["resident_id"], original["message_id"]
    if ref == f"family-review/{facade_id}/message/{message_id}":
        return True
    if ref != "duty/family-route_review":
        return False
    facts = []
    for part in source.get("content_parts") or ():
        if part.get("type") != "text":
            continue
        for line in str(part.get("text") or "").splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                facts.append(value)
    return len(facts) == 1 and all(facts[0].get(key) == value for key, value in {
        "message_id": message_id, "facade": facade_id, "lineage_id": facade["lineage_id"],
        "family_id": family_facade.facade_configuration(facade).get("family_id"),
    }.items())


def _receipt(message_id, review_message_id, replies):
    return {"status": "archived", "message_id": message_id,
            "review_message_id": review_message_id, "reply_message_ids": replies}
