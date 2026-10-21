"""Portal Web instruction handlers and its kernel.

Portal Web owns the user instructions (decision_6eac174d85bf), the User
Mailbox, user roles and the Portal service instructions of the
runtime-environment category (decision_4527448ad07f).  The installed
features add their Web instructions (``isa.instructions``): the Notice Board
and Chatrooms (decision_3e063aac15b3, decision_a6bebf4ac283), Pulse and the
Telepus contacts.  Handlers run in the Web process; the page backends they
wrap keep their own storage and checks.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from management_portal import portal_features
from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import InstructionKernel, Invocation, OwnerBinding
from management_portal.hatg.maintenance import recover_projections


REPO_ROOT = Path(__file__).resolve().parents[1]
_INBOX_WATCH_EPOCH = uuid.uuid4().hex
_INBOX_NON_INDEX_EVENTS = frozenset({"resident.activity.changed", "resident.queue.changed", "resident.runtime_settings.changed"})


def web():
    from management_portal import serve_management_portal

    return serve_management_portal


# -- inbox ---------------------------------------------------------------------------


INBOX_PAGE_SIZE = 100  # the conversation index serves at most 100 entries per page


def _inbox_entries(caller: str, resident_id: str = "") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    token = ""
    while True:
        payload = web()._agent_conversation_payload(
            caller, resident_id=resident_id, page_size=INBOX_PAGE_SIZE, page_token=token,
        )
        entries.extend(entry for entry in payload.get("entries") or [] if isinstance(entry, dict))
        token = str(payload.get("next_page_token") or "")
        if resident_id or not token:
            return entries


def _inbox_entry(caller: str, key: str) -> dict[str, Any] | None:
    """Resolve a Resident entry at its owner instead of walking every inbox page."""

    match = re.fullmatch(r"resident:(resident_[0-9a-f]{12})", str(key or ""))
    if match:
        return next((item for item in _inbox_entries(caller, match.group(1)) if item.get("key") == key), None)
    token = ""
    while True:
        payload = web()._agent_conversation_payload(
            caller, page_size=INBOX_PAGE_SIZE, page_token=token,
        )
        entry = next((item for item in payload.get("entries") or [] if item.get("key") == key), None)
        if entry is not None:
            return entry
        token = str(payload.get("next_page_token") or "")
        if not token:
            return None


def _inbox_message_ids(entry: dict[str, Any]) -> dict[str, str]:
    """The message IDs inside an item's read version, so a client can GetMessage without decoding it."""
    from management_portal.conversation_completion import completion_payload

    # Only a Resident conversation's messages are read with GetMessage; Chatrooms have ListChatroomMessages.
    if not entry.get("resident_id"):
        return {}
    payload = completion_payload(entry.get("completion_signature")) or {}
    latest, source = payload.get("completion_id", ""), payload.get("input_message_id", "")
    return {
        **({"latest_message_id": latest} if latest.startswith("msg_") else {}),
        **({"input_message_id": source} if source else {}),
    }


def get_user_mailbox(inv: Invocation) -> dict[str, Any]:
    """GetMailbox with user_id; the resident_id form runs at SocietyService (isa.execution.get_mailbox)."""
    from management_portal.agent_conversation_index_backend import newest_first, summarize_entries

    user_id = _named_user(inv)
    # Page one carries every notification and Chatroom; order the pages as one
    # list so the result does not depend on the page size.
    entries = newest_first([
        {**entry, **_inbox_message_ids(entry), "version": str(entry.get("read_revision") or 0)}
        for entry in _inbox_entries(user_id, inv.argument("related_resident_id") or "")
    ], pinned_first=True)
    return {"items": entries, **summarize_entries(entries)}


def mark_inbox_item_read(inv: Invocation) -> dict[str, Any]:
    from management_portal import conversation_read_state, mailbox_sources
    from management_portal.conversation_read_state import VersionConflict

    key = str(inv.argument("key") or "")
    source = mailbox_sources.source_of(key)
    if key.startswith("resident:"):
        web().resident_api.resolve_resident_identity(key.removeprefix("resident:"), inv.caller)
    elif source is not None and source.contains is not None:
        if not source.contains(key.removeprefix(source.prefix), inv.caller):
            raise not_found(f"Mailbox 条目不存在：{key}")
    elif not conversation_read_state.read_rows(inv.caller, [key]) and _inbox_entry(inv.caller, key) is None:
        # A notification or mention writes its recipients' rows when it is
        # created; only an older one without a row is looked up in the Mailbox.
        raise not_found(f"Mailbox 条目不存在：{key}")
    expected = inv.expected_version_for(key)
    try:
        item = web()._set_agent_conversation_read_state(
            inv.caller, key, unread=bool(inv.argument("unread")), expected_revision=expected,
        )
    except VersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", f"{key} 的版本已经变化",
            details={"object_id": key, "expected_version": expected,
                     "current_version": str(exc.current["read_revision"])},
        ) from exc
    return {"item": item}


def pin_inbox_item(inv: Invocation) -> dict[str, Any]:
    from management_portal import conversation_read_state, agent_conversation_index_backend

    key = inv.argument("key")
    entry = _inbox_entry(inv.caller, key)
    if entry is None:
        match = re.fullmatch(r"resident:(resident_[0-9a-f]{12})", str(key or ""))
        if match:
            from management_portal import conversation_index_projection

            try:
                entry = conversation_index_projection.resident_pin_entry(inv.caller, match.group(1))
            except (KeyError, PermissionError):
                pass
    if entry is None:
        raise not_found(f"Mailbox 条目不存在：{key}")
    if agent_conversation_index_backend.section_of(entry) != "conversation":
        raise invalid("通知不能置顶")
    try:
        conversation_read_state.set_pinned(inv.caller, key, bool(inv.argument("pinned")))
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    web()._forget_cached_inbox(inv.caller)
    web()._publish_conversation_read_changes(inv.caller, {key}, bulk=True)
    return {"item": _inbox_entry(inv.caller, key) or {}}


def inbox_change_keys(payload: dict[str, Any]) -> tuple[list[str], bool]:
    """Interpret the same authorized change payload consumed by the browser."""
    keys = {
        f"resident:{event['resident_id']}"
        for event in payload.get("resident_events") or []
        if event.get("resident_id") and event.get("event_type") not in _INBOX_NON_INDEX_EVENTS
    }
    keys.update(f"resident:{resident_id}" for resident_id, scopes in (payload.get("resident_changes") or {}).items()
                if "projection" in scopes)
    projections = payload.get("projection_changes") or {}
    index_changed = bool(projections.get("agent-conversation-index"))
    details = (payload.get("projection_details") or {}).get("agent-conversation-index") or {}
    explicit = {str(key) for target in details.values() for key in target.get("keys") or []}
    keys.update(explicit)
    snapshot = bool(payload.get("snapshot_required")) or (index_changed and not explicit)
    return sorted(keys), snapshot


def watch_inbox(inv: Invocation):
    from management_portal.notifications import get_notification_hub

    user_id = _named_user(inv)
    hub = get_notification_hub()
    current = hub.sequence
    cursor = str(inv.argument("after_cursor") or "")
    prefix = f"mailbox-watch-v1:{_INBOX_WATCH_EPOCH}:"
    reset = not cursor or not cursor.startswith(prefix)
    try:
        after = current if reset else int(cursor.removeprefix(prefix))
    except ValueError as exc:
        raise invalid("无效的 WatchMailbox 游标") from exc
    reset = reset or after < 0 or after > current or hub.cursor_has_gap(after)
    if reset:
        after = current
        yield [{"keys": [], "cursor": f"{prefix}{after}", "snapshot_required": True}]
    deadline = time.monotonic() + int(inv.argument("timeout_seconds") or 30)
    while time.monotonic() < deadline:
        notifications, high_watermark = hub.wait(after, timeout=max(0, deadline - time.monotonic()))
        gap = hub.cursor_has_gap(after)
        after = high_watermark
        payload = web()._portal_change_payload(user_id, notifications, {"resident", "projection"})
        keys, snapshot = inbox_change_keys(payload)
        if keys or snapshot or gap or not notifications:
            yield [{"keys": keys, "cursor": f"{prefix}{after}", "snapshot_required": snapshot or gap}]


def _named_user(inv: Invocation) -> str:
    """The registered User a call's user_id names (its Mailbox, profile, navigation or tokens)."""
    from management_common.principal import validate_user_id

    try:
        user_id = validate_user_id(inv.argument("user_id"))
    except ValueError as exc:
        raise invalid("user_id 必须是 User") from exc
    if not _access().user(user_id).get("configured") and user_id not in web()._load_users():
        raise not_found(f"用户不存在：{user_id}")
    return user_id


def clear_user_mailbox(inv: Invocation) -> dict[str, Any]:
    """ClearMailbox with user_id; the resident_id form runs at SocietyService (isa.execution.clear_mailbox)."""
    from management_portal import conversation_read_state, mailbox_sources, agent_conversation_index_backend as index

    user_id = _named_user(inv)
    mode = inv.argument("mode")
    if not mode:
        raise invalid("清理 User Mailbox 需要 mode")
    section = inv.argument("section")
    if mode == "unread":
        # Every unread conversation has its row; clearing never walks the activity history.
        # A Chatroom reads through its own record, which writes its members' rows.
        own_read_state = [source for source in mailbox_sources.sources() if source.set_unread is not None]
        entries = [{"key": key, "unread": True}
                   for key in conversation_read_state.unread_keys(conversation_read_state.load(user_id))
                   if (not section or index.section_of_key(key) == section)
                   and not key.startswith(tuple(source.prefix for source in own_read_state))]
        entries += [entry for source in own_read_state for entry in source.entries(user_id)
                    if entry.get("unread") and (not section or index.section_of(entry) == section)]
    else:
        entries = [entry for entry in _inbox_entries(user_id)
                   if (not section or index.section_of(entry) == section) and not entry.get("pinned")]
    result = web()._clear_agent_conversations(user_id, mode=mode, entries=entries)
    return {"status": "cleared", "cleared": result["cleared_count"], "items": result["items"]}


# -- users ---------------------------------------------------------------------------


def mailbox_todo(inv: Invocation) -> dict[str, Any]:
    from management_portal import mailbox_todos

    name = inv.name
    if name == "ListMailboxTodos":
        return mailbox_todos.list_items(_named_user(inv))
    if name == "CreateMailboxTodo":
        recipient = mailbox_todos.owner(inv.caller, inv.argument("user_id"))
        user = _access().user(recipient)
        if not user.get("configured") and recipient not in web()._load_users():
            raise not_found(f"用户不存在：{recipient}")
        if user.get("status") == "disabled":
            raise invalid("待办接收用户已停用")
        return mailbox_todos.create(inv.caller, inv.argument("text"), user_id=recipient, date=inv.argument("date"))
    if name == "UpdateMailboxTodo":
        return mailbox_todos.update(inv.caller, inv.argument("todo_id"), inv.argument("changes"))
    if name == "MoveMailboxTodo":
        return mailbox_todos.move(inv.caller, inv.argument("todo_id"), inv.argument("direction"),
                                  before_todo_id=inv.argument("before_todo_id"), after_todo_id=inv.argument("after_todo_id"))
    return mailbox_todos.delete(inv.caller, inv.argument("todo_id"))


def _access():
    return web()._PORTAL_ACCESS


def _public(user: dict[str, Any]) -> dict[str, Any]:
    from management_portal.user_avatar import user_avatar_store

    user = {**user, "avatar": user_avatar_store().get(user["username"])}
    return user


def with_user_allows(actor: str, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each user row says whether its edit button may call UpdateUser for this caller."""
    from management_portal import permission_system

    editable = permission_system.permitted_ids(actor, "UpdateUser", "user_id",
                                               [str(user.get("username") or "") for user in users])
    return [{**user, "allows": {"UpdateUser": str(user.get("username") or "") in editable}} for user in users]


def list_users(inv: Invocation) -> dict[str, Any]:
    users = _access().list_users(web()._load_users().keys())
    status = inv.argument("status")
    query = str(inv.argument("q") or "").strip().casefold()
    if status:
        users = [user for user in users if user.get("status") == status]
    if query:
        users = [
            user for user in users
            if query in str(user.get("username") or "").casefold() or query in str(user.get("display_name") or "").casefold()
        ]
    users.sort(key=lambda user: str(user.get("username") or "").casefold())
    return {"users": with_user_allows(inv.caller, [_public(user) for user in users])}


def get_user(inv: Invocation) -> dict[str, Any]:
    username = _named_user(inv)
    user = _access().user(username)
    from management_portal import permission_system

    return {"user": _public({"username": username, **user, "category": permission_system.user_category(username)}),
            "version": int(_access()._revision())}


@contextmanager
def _administrator_change(caller: str, username: str, **change: Any) -> Iterator[None]:
    """The administrator list is the Portal 管理员 guarantee's, in either mode (decision_b820faf42218 §3.1).

    The change is checked and written in the block under one account
    transaction, so concurrent changes are judged one after the other and
    none leaves the list without an active administrator.
    """
    from management_portal.permission_system.administrators import list_change

    try:
        with list_change(caller, username, **change):
            yield
    except PermissionError as exc:
        raise InstructionError("instruction.forbidden", str(exc)) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def _set_user_category(inv: Invocation, username: str, category: Any) -> bool:
    """UpdateUser's category: Portal 管理员, 一般用户 or 实习用户.  Setting or clearing Portal 管理员 is
    for another Portal 管理员 only; moves between the other two are UpdateUser's own check (everyone
    signed in while the permission system is off)."""
    from management_portal import permission_system
    from management_portal.permission_system.categories import category_name

    try:
        category = category_name(category)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    user = _access().user(username)
    if not user.get("configured") or user.get("status") == "deleted":
        raise not_found(f"用户不存在：{username}")
    if category == permission_system.user_category(username):
        return False
    with _administrator_change(inv.caller, username, category=category):
        changed = permission_system.set_user_category(username, category, actor=inv.caller)
    if changed:
        _access().audit(inv.caller, "user_category_updated", target=username, details={"category": category})
    return changed


def open_terminal(inv: Invocation) -> dict[str, Any]:
    from management_common.principal import actor_principal
    from management_portal import local_terminal_backend

    if actor_principal(inv.caller).kind != "user":
        raise InstructionError("instruction.forbidden", "Terminal 由 User 在 Portal 页面中使用，Resident 不能打开")
    try:
        result = local_terminal_backend.open_terminal(
            inv.caller, device_id=inv.argument("device_id") or "", cols=inv.argument("cols"),
            rows=inv.argument("rows"), cwd=inv.argument("cwd"))
    except PermissionError as exc:
        raise InstructionError("instruction.forbidden", str(exc)) from None
    except KeyError as exc:
        raise not_found(str(exc)) from None
    return {"session_id": str(result.get("session_id") or ""), "alive": bool(result.get("alive")),
            "cwd": str(result.get("cwd") or "")}


def user_invitation(inv: Invocation) -> dict[str, Any]:
    web()._require_authenticated_actor(inv.caller)
    service = web()._USER_ENROLLMENT
    if inv.call.instruction == "ListUserInvitations":
        return {"invitations": service.list_invitations()}
    if inv.call.instruction == "RevokeUserInvitation":
        return {"invitation": service.revoke_invitation(inv.argument("invitation_id"))}
    return {"invitation": service.create_invitation(
        inv.caller, inv.argument("user_id"), inv.argument("secret_sha256"),
        display_name=inv.argument("display_name", ""), expires_in_days=inv.argument("expires_in_days", 7),
    )}


def user_access_token(inv: Invocation) -> dict[str, Any]:
    from management_common.principal import actor_principal

    if actor_principal(inv.caller).kind != "user":
        raise InstructionError("instruction.forbidden", "个人访问凭据仅适用于 User")
    service = web()._USER_ENROLLMENT
    if inv.call.instruction == "RevokeUserAccessToken":
        return {"credential": service.revoke_token(inv.caller, inv.argument("credential_id"))}
    owner = _named_user(inv)
    if inv.call.instruction == "ListUserAccessTokens":
        return {"credentials": service.list_tokens(owner)}
    return {"credential": service.create_token(
        owner, inv.argument("secret_sha256"), name=inv.argument("name", "API"),
        expires_in_days=inv.argument("expires_in_days", 90),
    )}


def create_user(inv: Invocation) -> dict[str, Any]:
    # Only the account: its credentials are issued separately once it exists.
    payload: dict[str, Any] = {
        "action": "create",
        "username": inv.argument("user_id"),
        "password": inv.argument("initial_password"),
        "display_name": inv.argument("display_name"),
        "email": inv.argument("recovery_email"),
    }
    result = web()._admin_access_action(inv.caller, payload)
    return {"user": result.get("user") or {}, "personal_ama": result.get("personal_ama") or {}}


def update_user(inv: Invocation) -> dict[str, Any]:
    username = inv.argument("user_id")
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - {"display_name", "recovery_email", "avatar", "status", "category"})
    if unknown:
        raise invalid("changes 只能包含 display_name、recovery_email、avatar、status 与 category")
    web()._require_authenticated_actor(inv.caller)
    # The account owner edits its profile; enabling or disabling an account is for whoever may delete it.
    from management_portal import permission_system

    listed: Any = nullcontext()
    if "status" in changes:
        # An administrator's account is the administrator list's; any other is for whoever may delete it.
        if permission_system.user_category(username) == "portal_admin":
            listed = _administrator_change(inv.caller, username, status=str(changes["status"] or ""))
        elif not permission_system.allows(inv.caller, "DeleteUser", {"user_id": username}):
            raise InstructionError("instruction.forbidden", "修改账号状态需要能执行 DeleteUser")
    has_avatar = "avatar" in changes
    avatar_payload = changes.pop("avatar", None)
    user: dict[str, Any] = {}
    with listed:
        category_changed = "category" in changes and _set_user_category(inv, username, changes.pop("category"))
        if changes:
            result = web()._admin_access_action(inv.caller, {
                "action": "update", "username": username,
                "display_name": changes.get("display_name"),
                "email": changes.get("recovery_email"),
                "status": changes.get("status"),
                "revoke_sessions": changes.get("status") == "disabled",
            })
            user = result.get("user") or {}
    result = dict(user or {"username": username, **_access().user(username)})
    if has_avatar:
        result["avatar"] = _set_avatar(username, avatar_payload, inv.caller)
    return {"user": {**result, "category": permission_system.user_category(username)},
            "category_changed": bool(category_changed)}


def reset_user_password(inv: Invocation) -> dict[str, Any]:
    username = inv.argument("user_id")
    if username.casefold() == inv.caller.casefold():
        raise invalid("修改自己的密码须在 Settings 中验证当前密码")
    password = inv.argument("new_password")
    web()._PORTAL_CREDENTIALS.validate_password(password)
    result = web()._admin_access_action(inv.caller, {
        "action": "reset_password", "username": username, "password": password,
    })
    return {"user": result.get("user") or {}}


def revoke_user_sessions(inv: Invocation) -> dict[str, Any]:
    result = web()._admin_access_action(inv.caller, {
        "action": "revoke_sessions", "username": inv.argument("user_id"),
    })
    return {"user": result.get("user") or {}}


def _set_avatar(username: str, avatar: Any, caller: str) -> dict[str, Any]:
    """``avatar`` is an upload reference (``{"upload_id"}``), a sprite (``{"sprite_index"}``) or null."""

    from management_portal.avatar_store import change_avatar
    from management_portal.user_avatar import user_avatar_store

    store = user_avatar_store()
    try:
        return change_avatar(store, username, avatar or None, caller) or dict(store.get(username))
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def delete_user(inv: Invocation) -> dict[str, Any]:
    # Deleting a Portal 管理员 takes it off the administrator list: the account is disabled under the
    # list's transaction, and the rest of the deletion (which calls SocietyService) runs after it.
    from management_portal import permission_system

    username = str(inv.argument("user_id") or "")
    with _administrator_change(inv.caller, username, delete=True):
        if permission_system.user_category(username) == "portal_admin":
            _access().upsert_user(username, actor=inv.caller, status="disabled", revoke_sessions=True)
    result = web()._admin_access_action(inv.caller, {"action": "delete", "username": inv.argument("user_id")})
    return {"user_id": str(result.get("username") or inv.argument("user_id"))}




def _navigation_address(object_id: str) -> str:
    if object_id.startswith("lineage_"):
        return f"lineage:{object_id}"
    return f"project:{object_id}"


# Resident favorites sit beside the Lineage and Project favorites. A favorite
# never decides what the navigation lists: it lists everything the user can access.
RESIDENT_FAVORITE_SURFACE = "resident"


def _navigation_items(caller: str) -> list[str]:
    from management_portal import presentation_store
    from management_portal.principal_favorites import project_favorite_statuses

    # A deleted Project or Lineage stops being a favorite (decision_3e07c6d35cac).
    favorites = project_favorite_statuses(
        caller, web()._lineage_favorite_payload(caller),
        lambda selection: web()._set_lineage_favorite(caller, selection, False),
    ).get("favorites") or []
    residents = presentation_store.favorite_ids(caller, RESIDENT_FAVORITE_SURFACE)
    return list(dict.fromkeys([*(item.split(":", 1)[1] for item in favorites if ":" in item), *residents]))


def get_user_navigation(inv: Invocation) -> dict[str, Any]:
    return {"items": _navigation_items(_named_user(inv))}


def update_user_navigation(inv: Invocation) -> dict[str, Any]:
    from management_portal import presentation_store

    user_id = _named_user(inv)
    object_id = inv.argument("object_id")
    favorite = bool(inv.argument("favorite"))
    if object_id.startswith("resident_"):
        if not re.fullmatch(r"resident_[0-9a-f]{12}", object_id):
            raise invalid("object_id 不是有效的 Resident ID")
        presentation_store.set_favorite(user_id, RESIDENT_FAVORITE_SURFACE, object_id, favorite)
    else:
        web()._set_lineage_favorite(user_id, _navigation_address(object_id), favorite)
    return {"items": _navigation_items(user_id)}






# -- Portal services -----------------------------------------------------------------


def _instance() -> str:
    return str(os.getenv("PORTAL_INSTANCE") or "dev").strip() or "dev"


def get_runtime_capacity(inv: Invocation) -> dict[str, Any]:
    from management_portal.runtime_capacity import runtime_capacity

    return {"capacity": runtime_capacity()}


def get_portal_status(inv: Invocation) -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT)) if str(REPO_ROOT) not in sys.path else None
    from scripts import portal_services

    requested = inv.argument("instance")
    instances = [requested] if requested else ["dev", "release"]
    result = []
    for instance in instances:
        try:
            status = portal_services.status(instance)
        except Exception as exc:  # noqa: BLE001 - one instance's status failure is reported, not raised
            status = {"ok": False, "error": str(exc)}
        result.append({"instance": instance, **(status if isinstance(status, dict) else {"status": status})})
    return {"instances": result}


def restart_portal(inv: Invocation) -> dict[str, Any]:
    import json
    import secrets

    instance = inv.argument("instance")
    components = [str(item) for item in inv.argument("components") or []]
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if root:
        return paired.request_restart(root, instance, components)["request"]
    # portal_services is a shell wrapper; run the Python entry point with this interpreter.
    command = [sys.executable, str(REPO_ROOT / "scripts" / "portal_services.py")]
    command += ["request-restart", "dev"] if instance == "dev" else ["restart", "release"]
    for component in components:
        command += ["--component", component]
    if instance == "dev":
        # The Dev request is queued and returns at once.
        completed = subprocess.run(command, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
        if completed.returncode != 0:
            raise InstructionError("instruction.failed", (completed.stderr or completed.stdout).strip()[-500:] or "重启请求没有被接受")
        try:
            request = json.loads(completed.stdout or "{}").get("request") or {}
        except (json.JSONDecodeError, AttributeError):
            request = {}
        if not request.get("request_id"):
            raise InstructionError("instruction.failed", "重启请求没有返回请求 ID")
        # An empty component list means "whatever changed since the last deployment".
        return {"request_id": str(request["request_id"]), "components": list(request.get("components") or components)}
    # Release source only changes when a release is published, and publishing
    # restarts what changed; a plain Release restart covers Web and the control plane.
    request_id = f"portal-restart-{secrets.token_hex(8)}"
    completed = subprocess.run(
        ['systemd-run', '--user', '--collect', f'--unit={request_id}',
         f'--working-directory={REPO_ROOT}', *command],
        capture_output=True, text=True, timeout=180,
    )
    if completed.returncode:
        raise InstructionError('instruction.failed', (completed.stderr or completed.stdout).strip()[-500:])
    return {
        "request_id": request_id,
        "components": components or ["web", "resource", "execution-service", "lineage"],
    }


def publish_release(inv: Invocation) -> dict[str, Any]:
    from scripts.tfi_development_runtime import configured_root
    if _instance() != "dev" and not configured_root():
        raise InstructionError("instruction.forbidden", "PublishRelease 只能在 Dev 实例执行")
    sys.path.insert(0, str(REPO_ROOT)) if str(REPO_ROOT) not in sys.path else None
    from scripts.publish_portal_release import PublishOptions, publish

    report = publish(PublishOptions(
        str(inv.argument("message") or ""),
        tuple(str(item) for item in inv.argument("paths") or ()),
        bool(inv.argument("all")),
    ))
    if not report.get("ok"):
        raise InstructionError("instruction.failed", str(report.get("error") or "发布没有完成"), details={
            "commit": report.get("commit"), "status": report.get("status"),
            "push": dict(report.get("push") or {}),
        })
    return {"commit": str(report.get("commit") or ""), "paths": list(report.get("paths") or []),
            "health": dict(report.get("health") or {}), "push": dict(report.get("push") or {})}


def get_device_status(inv: Invocation) -> dict[str, Any]:
    from management_portal import agent_status_backend

    refresh = bool(inv.argument("refresh"))
    device_id = inv.argument("device_id") or ""
    if inv.argument("kind") == "disk":
        result = agent_status_backend.get_disk_resource_status(force_refresh=refresh, resource_id=device_id)
        return {**result, "devices": list(result.get("resources") or ([result] if result.get("resource_id") else []))}
    result = agent_status_backend.get_gpu_device_status(force_refresh=refresh, device_id=device_id)
    # devices: every GPU server for a group read, the one selected device for a
    # device_id read, and nothing when the device_id is unknown.
    if isinstance(result.get("servers"), list):
        devices = list(result["servers"])
    elif "known_device_ids" in result:
        devices = []
    else:
        devices = [result]
    return {**result, "devices": devices}


HANDLERS = {
    "ListMailboxTodos": mailbox_todo,
    "CreateMailboxTodo": mailbox_todo,
    "UpdateMailboxTodo": mailbox_todo,
    "DeleteMailboxTodo": mailbox_todo,
    "MoveMailboxTodo": mailbox_todo,
    "CreateUserInvitation": user_invitation,
    "ListUserInvitations": user_invitation,
    "RevokeUserInvitation": user_invitation,
    "CreateUserAccessToken": user_access_token,
    "ListUserAccessTokens": user_access_token,
    "RevokeUserAccessToken": user_access_token,
    "GetMailbox": get_user_mailbox,
    "PinMailboxItem": pin_inbox_item,
    "MarkMailboxItemRead": mark_inbox_item_read,
    "ClearMailbox": clear_user_mailbox,
    "ListUsers": list_users,
    "GetUser": get_user,
    "CreateUser": create_user,
    "UpdateUser": update_user,
    "ResetUserPassword": reset_user_password,
    "RevokeUserSessions": revoke_user_sessions,
    "DeleteUser": delete_user,
    "GetUserNavigation": get_user_navigation,
    "UpdateUserNavigation": update_user_navigation,
    "GetPortalStatus": get_portal_status,
    "GetRuntimeCapacity": get_runtime_capacity,
    "RestartPortal": restart_portal,
    "PublishRelease": publish_release,
    "GetDeviceStatus": get_device_status,
    "OpenTerminal": open_terminal,
}


USER_RECORD_INSTRUCTIONS = frozenset({
    "GetUser", "UpdateUser", "ResetUserPassword", "RevokeUserSessions", "DeleteUser",
})


def version_of(inv: Invocation, object_id: str) -> Any:
    """Version tokens of Portal Web objects (decision_898b8911a2ff “对象版本”).

    User records share the access store's revision and an inbox entry the
    completion it shows; a feature's instructions use the feature's
    ``version_of`` (a post its notification revision, a Telepus contact its
    User record's revision).  A chatroom message's handler compares its own
    version.  Personal navigation, Pulse items and deployment requests have
    no version token.
    """

    if inv.name == "MarkMailboxItemRead" and object_id == inv.argument("key"):
        # The handler checks the target completion with its single authoritative read.
        return None
    if inv.name in USER_RECORD_INSTRUCTIONS and object_id == inv.argument("user_id"):
        return int(_access()._revision())
    for _feature, module in portal_features.contributions("web.instructions"):
        if inv.name in module.HANDLERS:
            feature_version = getattr(module, "version_of", None)
            return feature_version(inv, object_id) if feature_version else None
    return None


def _argument_not_installed(name: str, arguments: list[str]):
    def handler(inv: Invocation) -> dict[str, Any]:
        raise invalid(f"{name} 的 {'、'.join(arguments)} 由没有安装的功能处理", arguments=arguments)

    return handler


def binding() -> OwnerBinding:
    from management_portal.hatg.catalog import instruction_catalog

    # The installed features' Web instruction handlers (``web.instructions``) join the core ones.
    handlers = {**HANDLERS, **{name: handler for _feature, module in portal_features.contributions("web.instructions")
                               for name, handler in module.HANDLERS.items()}}
    # A core instruction that Web runs only for a feature's argument (argument_owners:
    # SearchMessages and ListInputHistory with chatroom_id) refuses those calls
    # when the feature is not installed.
    for name, declaration in instruction_catalog().items():
        if declaration.owner != "web" and "web" in declaration.owners and name not in handlers:
            handlers[name] = _argument_not_installed(
                name, [argument for argument, owner in declaration.argument_owners if owner == "web"])
    return OwnerBinding(owner="web", handlers=handlers, streams={"WatchMailbox": watch_inbox}, version_of=version_of, recover_projections=recover_projections)


def build_kernel() -> InstructionKernel:
    from management_common.portal_database import portal_database
    from management_portal.hatg.store import ExecutionStore

    return InstructionKernel(binding(), store=ExecutionStore(portal_database(schema="web_instructions")))
