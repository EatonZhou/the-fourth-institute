"""An authenticated business caller's grant for one isolated Resident input.

Grants contain input ownership and integrity, never execution configuration or state.
Message, Input, Queue and Event remain owned by Resident Conversation.
"""
from __future__ import annotations

import copy
from management_common.json_columns import json_text, json_value
from management_portal.portal_access_control import get_portal_access_store
from .state_store import get_lineage_data_store


def _database():
    return get_lineage_data_store().database


def register(*, resident_id: str, message_id: str, actor: str, profile: dict) -> None:
    """Called by the business domain after checking its published Invoke grant."""
    descriptor = json_text(profile, sort_keys=True, ensure_ascii=False)
    def commit(connection):
        row = connection.execute('SELECT actor,descriptor FROM input_scopes WHERE resident_id=%s AND message_id=%s',
                                 (resident_id, message_id)).fetchone()
        if row and (row["actor"] != actor or json_value(row["descriptor"]) != json_value(descriptor)):
            raise ValueError("Resident input scope is immutable")
        connection.execute('INSERT INTO input_scopes VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                           (resident_id, message_id, actor, descriptor))
    _database().write(commit)


def get(resident_id: str, message_id: str) -> dict:
    with _database().read() as connection:
        row = connection.execute('SELECT actor,descriptor FROM input_scopes WHERE resident_id=%s AND message_id=%s',
                                 (resident_id, message_id)).fetchone()
    return {"actor": row["actor"], "profile": json_value(row["descriptor"])} if row else {}


def scoped_message_ids(resident_id: str, message_ids: list[str]) -> set[str]:
    """Identify isolated caller inputs without one database round trip per input."""
    if not message_ids:
        return set()
    with _database().read() as connection:
        rows = connection.execute(
            'SELECT message_id FROM input_scopes WHERE resident_id=%s AND message_id=ANY(%s)',
            (resident_id, message_ids),
        ).fetchall()
    return {str(row["message_id"]) for row in rows}


def permits(resident_id: str, actor: str, message_id: str) -> bool:
    scope = get(resident_id, message_id)
    if not scope:
        return False
    if scope["actor"] != actor or not get_portal_access_store().is_active(actor):
        raise PermissionError("Resident input belongs to another caller or the caller is inactive")
    return True


def permits_message(repository, resident_id: str, actor: str, message_id: str) -> bool:
    message = repository.message(resident_id, message_id) or {}
    input_id = message_id if message.get("role") == "user" else message.get("reply_to_message_id", "")
    return bool(input_id) and permits(resident_id, actor, input_id)


def select_snapshot(snapshot: dict, message_id: str, repository, resident_id: str) -> dict:
    value = copy.deepcopy(snapshot)
    state = repository.input_state(resident_id, message_id)
    ids = [message_id, *state.get("reply_message_ids", [])]
    value["messages"] = [repository.public_message(resident_id, mid) for mid in ids]
    value["input_states"] = [state]
    value["activities"] = []
    value["queue"]["items"] = [item for item in value["queue"].get("items", []) if item.get("message_id") == message_id]
    if value["state"].get("active_message_id") != message_id:
        value["state"]["active_message_id"] = ""
    value["next_page_token"] = ""
    return value


def select_events(result: dict, message_id: str) -> dict:
    # Preserve cursor envelopes so a filtered caller can still detect transport gaps.
    result = copy.deepcopy(result)
    for event in result.get("events", []):
        payload = event.get("payload") or {}
        item = payload.get("input_state") or payload.get("message") or {}
        if item.get("message_id") != message_id and item.get("reply_to_message_id") != message_id:
            event.pop("payload", None)
            event["payload_omitted"] = True
    return result
