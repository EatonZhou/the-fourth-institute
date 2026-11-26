"""Read routing material on demand; task admission owns the frozen predecessor."""
from __future__ import annotations

import copy

from . import family_chat_preferences, family_chat_storage, family_facade
from . import relay_domain, resident_access, resident_conversation_stream
from .state_store import get_lineage_data_store


def read(facade_id: str, message_id: str, actor: str, *, section: str = "members") -> dict:
    if section not in {"members", "previous_round", "request"}:
        raise ValueError("section 必须为 members、previous_round 或 request")
    facade = relay_domain.visible_facade(facade_id, actor)
    repository = resident_conversation_stream.get_repository()
    message = repository.message(facade_id, message_id)
    if message is None:
        raise KeyError("门面原消息不存在")
    result = {"resident_id": facade_id, "message_id": message_id}
    if section == "previous_round":
        state = get_lineage_data_store().family_relays.get(facade["lineage_id"], facade_id)
        entry = (state.get("continuations") or {}).get(message_id) or {}
        return {**result, "previous_round": copy.deepcopy(entry.get("previous_round"))}
    with get_lineage_data_store().database.read() as connection:
        policy = family_chat_storage.read_snapshot(connection, facade_id, message_id)
        if policy is None:
            policy = family_chat_storage.read_policy(connection, facade_id, message.get('author_id', ''))
    ids = family_chat_preferences.allowed(facade, policy)
    steward = family_facade.family_steward_id(facade)
    if family_facade.is_relay_target(facade, steward):
        ids.append(steward)
    records = {r["resident_id"]: r for r in resident_access.visible_resident_records(ids, actor, "portal")}
    result.update(members=[
        {"resident_id": rid, "role": "steward" if rid == steward else "member",
         "title": records[rid].get("title", "") if rid in records else "",
         "availability": "known" if rid in records else "unavailable"}
        for rid in ids
    ], chat_preferences=policy)
    if section == "request":
        from . import resident_protocol

        result["request"] = {
            **resident_protocol.get_resident_message(facade_id, actor, message_id),
            "input_state": family_facade.project_input_state(
                facade, message_id, repository.input_state(facade_id, message_id),
            ),
        }
    return result
