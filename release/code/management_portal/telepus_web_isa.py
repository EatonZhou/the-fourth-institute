"""Portal Web handlers of the Telepus instructions (``web.instructions``).

The declarations are in ``telepus_instructions.py``.  A contact is versioned
by its User record, as the core user instructions are.
"""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import InstructionError
from management_portal.hatg.kernel import Invocation


def _require_admin(inv: Invocation) -> None:
    from management_portal import permission_system

    if permission_system.user_category(inv.caller) != "portal_admin":
        raise InstructionError("instruction.forbidden", "只有 Portal 管理员可以管理 Telepus 服务")


def get_telepus_status(inv: Invocation) -> dict[str, Any]:
    from management_portal.telepus_runtime import TelepusRuntime

    _require_admin(inv)
    return {"status": TelepusRuntime().status()}


def set_telepus_enabled(inv: Invocation) -> dict[str, Any]:
    from management_portal.telepus_runtime import TelepusRuntime

    _require_admin(inv)
    return {"status": TelepusRuntime().set_enabled(inv.argument("enabled"))}


def bind_telepus_contact_for_user(inv: Invocation) -> dict[str, Any]:
    from management_portal.watchboards.telepus import bind_contact

    return bind_contact(inv.argument("user_id"), inv.argument("id_token"), inv.argument("previous_id_token", ""))


def unbind_telepus_contact_for_user(inv: Invocation) -> dict[str, Any]:
    from management_portal.watchboards.telepus import unbind_contact

    result = unbind_contact(inv.argument("user_id"), inv.argument("id_token"))
    return {"user_id": result["username"], "telegram_user_id": result["telegram_user_id"], "removed": result["removed"]}


HANDLERS = {
    "GetTelepusStatus": get_telepus_status,
    "SetTelepusEnabled": set_telepus_enabled,
    "BindTelepusContactForUser": bind_telepus_contact_for_user,
    "UnbindTelepusContactForUser": unbind_telepus_contact_for_user,
}


def version_of(inv: Invocation, object_id: str) -> Any:
    """The User record's version token: the access store's revision."""
    from management_portal.web_isa import _access

    if object_id == inv.argument("user_id"):
        return int(_access()._revision())
    return None
