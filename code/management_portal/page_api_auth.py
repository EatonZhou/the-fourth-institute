"""Who is calling a page's own backend API (Test, Paper, WALL).

A page backend accepts a human login session or the bearer token Portal
issued to a Resident.  The token only says which Resident is calling; what
it may do is decided by the page backend's own permission checks.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from management_common.principal import authenticated_principal

def authorize(auth: Mapping[str, Any]) -> str:
    """The caller's principal ID; a Resident must still be active in its Lineage."""

    principal = authenticated_principal(auth)
    actor = principal.id
    if principal.kind == "user":
        return actor
    lineage_id = str(auth.get("lineage_id") or "").strip()
    if not lineage_id:
        raise PermissionError("Resident 调用页面接口时必须说明所在的 Lineage")
    from management_portal.society_service import get_society_service

    try:
        get_society_service().lineage.authorize_lineage_resident_principal(lineage_id, actor)
    except (KeyError, PermissionError, ValueError) as exc:
        raise PermissionError("Resident 不在它声明的 Lineage 中") from exc
    return actor
