"""Paged, read-through projection of the AMA chat preceding personal Familize."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from . import resident_conversation_stream

TOKEN_PREFIX = "personal-history:"


def source(facade: Mapping[str, Any]) -> dict[str, Any]:
    family = facade.get("family") or {}
    ama_id = family.get("personal_ama_id")
    if family.get("role") != "facade" or not ama_id or not family.get("history_before_ordinal"):
        return {}
    from . import resident_workspace_domain
    member = resident_workspace_domain.find_resident(ama_id)
    if not member or member.get("deleted_at") or member.get("lineage_id") != facade.get("lineage_id"):
        return {}
    return {"resident_id": ama_id, "title": member.get("title") or "Ask Me Anything",
            "before_at": family["history_before"], "before_ordinal": int(family["history_before_ordinal"])}


def _project(message: dict, history: dict) -> dict:
    shown = copy.deepcopy(message)
    shown["family_history"] = {key: history[key] for key in ("resident_id", "title", "before_at")}
    shown["family_history"]["message_id"] = message["message_id"]
    return shown


def page(facade: Mapping[str, Any], token: str, *, limit: int) -> dict[str, Any]:
    history = source(facade)
    if not history or not token.startswith(TOKEN_PREFIX):
        raise ValueError("Invalid personal history page token")
    try:
        boundary = int(token[len(TOKEN_PREFIX):])
    except ValueError as exc:
        raise ValueError("Invalid personal history page token") from exc
    if boundary < 1 or boundary > history["before_ordinal"]:
        raise ValueError("Invalid personal history page token")
    result = resident_conversation_stream.get_repository().messages_page(history["resident_id"], str(boundary), limit=limit)
    result["resident_id"] = facade["resident_id"]
    result["messages"] = [_project(item, history) for item in result["messages"]]
    result["input_states"] = []  # These are past member turns, never facade requests.
    result["family_history"] = history
    result["next_page_token"] = TOKEN_PREFIX + result["next_page_token"] if result["next_page_token"] else ""
    return result


def prepend(facade: Mapping[str, Any], result: dict, *, limit: int) -> dict:
    history = source(facade)
    if not history:
        return result
    result["family_history"] = history
    if result.get("next_page_token"):
        return result
    token = TOKEN_PREFIX + str(history["before_ordinal"])
    remaining = max(0, limit - len(result["messages"]))
    if not remaining:
        result["next_page_token"] = token
        return result
    older = page(facade, token, limit=remaining)
    result["messages"] = older["messages"] + result["messages"]
    result["next_page_token"] = older["next_page_token"]
    return result


def message(facade: Mapping[str, Any], message_id: str) -> dict | None:
    history = source(facade)
    if not history:
        return None
    repository = resident_conversation_stream.get_repository()
    ordinal = repository.agent_data.chat_message_ordinal(history["resident_id"], message_id)
    if ordinal is None or ordinal >= history["before_ordinal"]:
        return None
    found = repository.message(history["resident_id"], message_id)
    return _project(found, history) if found else None
