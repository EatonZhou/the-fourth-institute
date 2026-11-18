"""Explicit, management-authorized reads of a deleted identity's retained facts.

This is separate from live Conversation/Resident targets. A cursor carries a
position, never authority or a caller-selected Agent/Sequence identity.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from management_common.actor_identity import canonical_actor_id
from management_portal import permission_system
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.permission_system import personal_ama

from . import resident_catalog
from .resident_access_scope import action_for_parameter


ACTION = "GetDeletedResidentHistory"
SECTIONS = ("identity", "messages", "executions", "segments")
IDENTITY_FIELDS = ("resident_id", "title", "deleted_at", "deleted_by")


def _cursor(resident_id: str, section: str, position: Any) -> str:
    payload = [1, resident_id, section, position]
    return base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def _position(token: str, resident_id: str, section: str) -> Any:
    if not token:
        return None
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError
        value = json.loads(base64.b64decode(token, altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) != 4 or value[:3] != [1, resident_id, section]:
            raise ValueError
        return value[3]
    except (ValueError, TypeError, UnicodeError, binascii.Error) as exc:
        raise ValueError("History cursor must belong to this Resident and section") from exc


def read_history(
    resident_id: str, actor: str, *, section: str = "identity", cursor: str = "", limit: int = 50,
) -> dict[str, Any]:
    if not isinstance(resident_id, str) or not re.fullmatch(r"resident_[0-9a-f]{12}", resident_id):
        raise ValueError("An exact Resident ID is required")
    if section not in SECTIONS:
        raise ValueError("Unknown history section")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("History limit must be between 1 and 100")
    caller = canonical_actor_id(actor)
    # Repeated on every page, before revealing whether a tombstone exists.
    if not caller or not permission_system.allows(caller, ACTION, {"resident_id": resident_id}):
        raise PermissionError("Deleted Resident history requires target management access")
    record = resident_catalog.get_catalog().get_historical(resident_id)
    if not record.get("deleted_at"):
        raise KeyError("deleted resident not found")
    if not personal_ama.reaches(
        caller, record.get("personal_owner_id"), resident_id,
        action=action_for_parameter(ACTION, "resident_id"),
    ):
        raise personal_ama.NotFound(resident_id)
    position = _position(cursor, resident_id, section)
    identity = {field: str(record.get(field) or "")[:4096] for field in IDENTITY_FIELDS}
    if any(len(str(record.get(field) or "")) > 4096 for field in IDENTITY_FIELDS):
        identity["metadata_truncated"] = True
    result: dict[str, Any] = {
        "schema": "tfi.deleted-resident-history.v1", "resident_id": resident_id,
        "identity": identity, "section": section, "items": [], "next_cursor": "",
    }
    if section == "identity":
        if position is not None:
            raise ValueError("Identity has no continuation cursor")
        result["items"] = [identity]
        return result
    # Execution owns this bounded read projection. No live state, session or
    # filesystem/provider access is needed to discover retained facts.
    page = get_agent_data_store().retained_history_page(
        resident_id, section=section, after=position, limit=limit,
    )
    result["items"] = page["items"]
    result["availability"] = page["availability"]
    if page.get("next_after") is not None:
        result["next_cursor"] = _cursor(resident_id, section, page["next_after"])
    return result
