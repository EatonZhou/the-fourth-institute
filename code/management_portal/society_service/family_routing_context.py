"""Read routing material on demand; task admission owns the frozen predecessor."""
from __future__ import annotations

import copy

from . import family_chat_preferences, family_chat_storage, family_facade, family_familize
from . import resident_access, resident_conversation_stream
from .state_store import get_lineage_data_store


def read(facade_id: str, message_id: str, actor: str, *, section: str = "members") -> dict:
    if section not in {"members", "previous_round"}:
        raise ValueError("section 必须为 members 或 previous_round")
    visible = resident_access.visible_resident_records([facade_id], actor, "portal")
    if not visible:
        raise PermissionError("无权读取此 Family 的路由上下文")
    facade = visible[0]
    if not family_facade.is_facade(facade):
        raise ValueError("resident_id 必须为 Family 门面")
    message = resident_conversation_stream.get_repository().message(facade_id, message_id)
    if message is None:
        raise KeyError("门面原消息不存在")
    result = {"resident_id": facade_id, "message_id": message_id}
    if section == "previous_round":
        state = get_lineage_data_store().family_relays.get(facade["lineage_id"], facade_id)
        entry = (state.get("continuations") or {}).get(message_id) or {}
        return {**result, "previous_round": copy.deepcopy(entry.get("previous_round"))}
    _, block = family_familize.read_family(
        facade["lineage_id"], facade["family"]["family_id"], actor)
    family = block["family"]
    with get_lineage_data_store().database.read() as connection:
        policy = family_chat_storage.read_snapshot(connection, facade_id, message_id)
        if policy is None:
            policy = family_chat_storage.read_policy(connection, facade_id, message.get('author_id', ''))
    ids = list(dict.fromkeys([*family_chat_preferences.allowed(facade, policy), family["steward"]]))
    records = {r["resident_id"]: r for r in resident_access.visible_resident_records(ids, actor, "portal")}
    return {**result, "members": [
        {"resident_id": rid, "role": "steward" if rid == family["steward"] else "member",
         "title": records[rid].get("title", "") if rid in records else "",
         "availability": "known" if rid in records else "unavailable"}
        for rid in ids if rid], "chat_preferences": policy}
