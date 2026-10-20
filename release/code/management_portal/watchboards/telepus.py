from __future__ import annotations

import re

from management_common.site_config import read_site_config

from management_common.actor_identity import canonical_actor_id, same_actor
from management_portal import telepus_contacts_store
from management_portal.portal_access_control import get_portal_access_store


APPLICATION_ID = "telepus"
# Its operations manage the caller's own contact, not the board's commands.
ACTS_LIKE_COMMANDS = False


def _read_telepus_telegram_user_contacts() -> dict[str, dict[str, str]]:
    """Every binding the Telegram router authorizes, keyed by Telegram user ID."""
    with telepus_contacts_store.database().read() as connection:
        return telepus_contacts_store.contacts(connection)


def _telegram_user_public_id(user_id: str) -> str:
    text = str(user_id or "").strip()
    if len(text) <= 4:
        return text
    return f"{text[:2]}...{text[-2:]}"


def matches(context) -> bool:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("telepus", {}), dict):
        raise ValueError("deployment.telepus must be an object")
    resident_id = deployment.get("telepus", {}).get("resident_id", "")
    if not isinstance(resident_id, str) or (resident_id and not re.fullmatch(r"resident_[0-9a-f]{12}", resident_id)):
        raise ValueError("deployment.telepus.resident_id must be a Resident ID")
    return bool(resident_id and context.resident_id == resident_id)


def _signed_in_user(actor: str) -> bool:
    """Every User manages their own contact here, a fixed-role rule (decision_b820faf42218 §4);
    Bind/UnbindTelepusContactForUser stay the administrators' instructions for anyone's contact."""
    from management_common.principal import actor_principal

    return actor_principal(actor).kind == "user"


def project(context) -> dict:
    users = _read_telepus_telegram_user_contacts()
    items = [
        {"telegram_user_id": user_id, "display_id": _telegram_user_public_id(user_id),
         "name": contact["name"], "portal_actor": contact["portal_actor"],
         "current_actor": same_actor(contact["portal_actor"], context.actor)}
        for user_id, contact in sorted(users.items(), key=lambda item: (item[1]["name"].casefold(), item[0]))
    ]
    return {"telegram_users": {
        "enabled": True, "count": len(items), "users": items,
        "current_user": canonical_actor_id(context.actor),
        "permissions": {"can_manage": _signed_in_user(context.actor)},
    }}


def act(context, operation: str, arguments: dict) -> dict:
    allowed = {"id_token", "previous_id_token"} if operation == "save" else {"id_token"}
    if operation not in {"save", "delete"} or set(arguments) - allowed:
        raise ValueError("Unsupported Telepus contact operation")
    from management_common.principal import actor_principal

    user = actor_principal(context.actor)
    if user.kind != "user":
        raise ValueError("Telepus 联系人操作需要认证 User 身份")
    if operation == "save":
        return bind_contact(user.id, arguments.get("id_token"), arguments.get("previous_id_token") or "")
    return unbind_contact(user.id, arguments.get("id_token"))


def _contact_user(username: str, *, require_active: bool = True) -> str:
    user = get_portal_access_store().user(canonical_actor_id(username))
    allowed_statuses = {"active"} if require_active else {"active", "disabled"}
    if not user.get("configured") or user.get("status") not in allowed_statuses:
        raise ValueError("Portal 用户必须已存在" + ("且处于活动状态" if require_active else "") + "；请通过 CreateUser 创建账号")
    return user["username"]


def _contact_token(id_token: str) -> str:
    token = str(id_token or "").strip()
    if not re.fullmatch(r"\d{5,32}", token):
        raise ValueError("Telegram id token 必须是 5-32 位数字")
    return token


def bind_contact(username: str, id_token: str, previous_id_token: str = "") -> dict:
    """Save an admitted binding; old binding expectations prevent stale edits."""
    from management_common.operation_errors import OperationError

    portal_actor = _contact_user(username)
    token = _contact_token(id_token)
    previous = str(previous_id_token or "").strip()
    if previous and not re.fullmatch(r"\d{5,32}", previous):
        raise ValueError("原 Telegram id token 格式无效")
    with telepus_contacts_store.database().transaction(telepus_contacts_store.LOCK) as connection:
        users = telepus_contacts_store.contacts(connection)
        if previous:
            old = users.get(previous)
            if not old or not same_actor(old["portal_actor"], portal_actor):
                raise OperationError("原联系人绑定已变化，请重新读取联系人", code="telepus.binding_conflict", http_status=409, commit_state="not_committed")
        if token in users and token != previous:
            raise ValueError("这个 Telegram id token 已存在")
        if any(existing != previous and same_actor(contact["portal_actor"], portal_actor) for existing, contact in users.items()):
            raise ValueError("这个 Portal user 已绑定到其它 Telepus contact")
        if previous and previous != token:
            telepus_contacts_store.delete(connection, previous)
        contact = {"telegram_user_id": token, "name": portal_actor, "portal_actor": portal_actor}
        telepus_contacts_store.save(connection, contact)
    return {"contact": contact}


def unbind_contact(username: str, id_token: str) -> dict:
    """Remove only the expected user's binding, including a disabled account's."""
    from management_common.operation_errors import OperationError

    portal_actor = _contact_user(username, require_active=False)
    token = _contact_token(id_token)
    with telepus_contacts_store.database().transaction(telepus_contacts_store.LOCK) as connection:
        old = telepus_contacts_store.contacts(connection).get(token)
        if old and not same_actor(old["portal_actor"], portal_actor):
            raise OperationError("原联系人绑定已变化，请重新读取联系人", code="telepus.binding_conflict", http_status=409, commit_state="not_committed")
        if old:
            telepus_contacts_store.delete(connection, token)
    return {"username": portal_actor, "telegram_user_id": token, "removed": old is not None}


def remove_user_contacts(username: str) -> list[str]:
    """Remove a deleted User's bindings so Telegram stops acting as that User."""
    with telepus_contacts_store.database().transaction(telepus_contacts_store.LOCK) as connection:
        removed = [token for token, contact in telepus_contacts_store.contacts(connection).items()
                   if same_actor(contact["portal_actor"], username)]
        for token in removed:
            telepus_contacts_store.delete(connection, token)
    return removed
