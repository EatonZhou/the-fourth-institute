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

import importlib
import re
import sys
import time
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from contextvars import copy_context
from pathlib import Path
from typing import Any

from management_portal import portal_features
from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import InstructionKernel, Invocation, OwnerBinding
from management_portal.hatg.maintenance import recover_projections
from management_portal.http_timing import log_slow_mailbox_read, timed_call


REPO_ROOT = Path(__file__).resolve().parents[1]
_INBOX_WATCH_EPOCH = uuid.uuid4().hex
_INBOX_NON_INDEX_EVENTS = frozenset({"resident.activity.changed", "resident.queue.changed", "resident.runtime_settings.changed"})


def web(module: str):
    """A Portal Web module, imported on first use: Web-owned instructions act on this Web process's state."""
    return importlib.import_module(f"management_portal.{module}")


# -- inbox ---------------------------------------------------------------------------


INBOX_PAGE_SIZE = 100  # the conversation index serves at most 100 entries per page
# Conversation rows and unanswered questions have independent owners. Bound
# the extra reads across requests, and keep their authorization context local.
_MAILBOX_QUESTION_READS = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mailbox-question-read")


def _inbox_entries(caller: str, resident_id: str = "") -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    token = ""
    while True:
        payload = web("web_inbox")._agent_conversation_payload(
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
        payload = web("web_inbox")._agent_conversation_payload(
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


@log_slow_mailbox_read()
def get_user_mailbox(inv: Invocation) -> dict[str, Any]:
    """GetMailbox with user_id; the resident_id form runs at SocietyService (isa.execution.get_mailbox)."""
    from management_portal.agent_conversation_index_backend import newest_first, summarize_entries
    from management_portal.society_service import get_society_service
    from urllib.parse import urlencode

    user_id = _named_user(inv)
    related = inv.argument("related_resident_id") or ""
    questions = _MAILBOX_QUESTION_READS.submit(
        copy_context().run, timed_call, "portal_mailbox_pending",
        get_society_service().resident.list_user_pending_requests, user_id,
    )
    # Page one carries every notification and Chatroom; order the pages as one
    # list so the result does not depend on the page size.
    try:
        entries = newest_first([
            {**entry, **_inbox_message_ids(entry), "version": str(entry.get("read_revision") or 0)}
            for entry in timed_call("portal_mailbox_items", _inbox_entries, user_id, related)
        ], pinned_first=True)
    except BaseException:
        # Do not leave work using this request's context after an item read
        # fails. Cancel a queued read, or join an already running one.
        if not questions.cancel():
            wait([questions])
        raise
    # Questions belong to their Conversation owner. Reading/clearing a Mailbox
    # is not an answer, so pending requests are independent of its message rows.
    requests = questions.result()["requests"]
    fields = ("key", "source_resident_id", "source_message_id", "resident_id", "message_id", "kind", "created_at", "title")
    pending = [
        {**{key: request[key] for key in fields if key in request},
         "url": "/lineage?" + urlencode({"resident": request["resident_id"], "open_chat": "1", "message": request["message_id"]})}
        for request in requests
        if not related or related in (request["resident_id"], request["source_resident_id"])
    ]
    return {"items": entries, "pending_requests": pending, **summarize_entries(entries)}


def _require_inbox_read_target(actor, key, source):
    from management_portal import conversation_read_state

    if key.startswith("resident:"):
        web("web_clients").resident_api.resolve_resident_identity(key.removeprefix("resident:"), actor)
    elif source is not None and source.contains is not None:
        if not source.contains(key.removeprefix(source.prefix), actor):
            raise not_found(f"Mailbox 条目不存在：{key}")
    elif not conversation_read_state.read_rows(actor, [key]) and _inbox_entry(actor, key) is None:
        # A notification or mention writes its recipients' rows when it is
        # created; only an older one without a row is looked up in the Mailbox.
        raise not_found(f"Mailbox 条目不存在：{key}")


def mark_inbox_item_read(inv: Invocation) -> dict[str, Any]:
    from management_portal import mailbox_sources
    from management_portal.conversation_read_state import VersionConflict

    key = str(inv.argument("key") or "")
    source = mailbox_sources.source_of(key)
    _require_inbox_read_target(inv.caller, key, source)
    expected = inv.expected_version_for(key)
    try:
        item = web("web_inbox")._set_agent_conversation_read_state(
            inv.caller, key, unread=bool(inv.argument("unread")), expected_revision=expected,
        )
    except VersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", f"{key} 的版本已经变化",
            details={"object_id": key, "expected_version": expected,
                     "current_version": str(exc.current["read_revision"])},
        ) from exc
    return {"item": item}


def _pin_target(actor, key):
    entry = _inbox_entry(actor, key)
    if entry is None:
        match = re.fullmatch(r"resident:(resident_[0-9a-f]{12})", str(key or ""))
        if match:
            from management_portal import conversation_index_projection

            try:
                entry = conversation_index_projection.resident_pin_entry(actor, match.group(1))
            except (KeyError, PermissionError):
                pass
    return entry


def pin_inbox_item(inv: Invocation) -> dict[str, Any]:
    from management_portal import conversation_read_state, agent_conversation_index_backend

    key = inv.argument("key")
    entry = _pin_target(inv.caller, key)
    if entry is None:
        raise not_found(f"Mailbox 条目不存在：{key}")
    if agent_conversation_index_backend.section_of(entry) != "conversation":
        raise invalid("通知不能置顶")
    try:
        conversation_read_state.set_pinned(inv.caller, key, bool(inv.argument("pinned")))
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    web("web_read_caches")._forget_cached_inbox(inv.caller)
    web("web_inbox")._publish_conversation_read_changes(inv.caller, {key}, bulk=True)
    return {"item": _inbox_entry(inv.caller, key) or {}}


def mute_inbox_item(inv: Invocation) -> dict[str, Any]:
    from management_portal import conversation_read_state, agent_conversation_index_backend, mailbox_sources

    key = str(inv.argument("key") or "")
    entry = _inbox_entry(inv.caller, key)
    if entry is None:
        raise not_found(f"Mailbox 条目不存在：{key}")
    if agent_conversation_index_backend.section_of(entry) != "conversation":
        raise invalid("只有对话可以设置消息免打扰")
    muted = inv.argument("muted")
    if not isinstance(muted, bool):
        raise invalid("muted 必须是布尔值")
    source = mailbox_sources.source_of(key)
    if source and source.set_muted:
        source.set_muted(inv.caller, key.removeprefix(source.prefix), muted)
    else:
        conversation_read_state.set_muted(inv.caller, key, muted)
    web("web_read_caches")._forget_cached_inbox(inv.caller)
    web("web_inbox")._publish_conversation_read_changes(inv.caller, {key}, bulk=True)
    return {"item": _inbox_entry(inv.caller, key) or {}}


def update_inbox_item(inv: Invocation) -> dict[str, Any]:
    from management_portal import agent_conversation_index_backend, conversation_read_state, mailbox_sources

    key = str(inv.argument("key") or "")
    marks = {name: inv.argument(name) for name in ("muted", "pinned", "unread")}
    supplied = [name for name, value in marks.items() if value is not None]
    if not supplied or any(not isinstance(marks[name], bool) for name in supplied):
        raise invalid("至少提供一个布尔标记 muted、pinned 或 unread")
    if len(supplied) == 1:
        return {"muted": mute_inbox_item, "pinned": pin_inbox_item, "unread": mark_inbox_item_read}[supplied[0]](inv)

    # Verify every target before writing. A visible, still-unlisted Resident
    # can be pinned, but the mute operation requires an existing conversation.
    actor = inv.caller
    source = mailbox_sources.source_of(key)
    entry = _pin_target(actor, key) if marks["muted"] is None else _inbox_entry(actor, key)
    if entry is None:
        raise not_found(f"Mailbox 条目不存在：{key}")
    if agent_conversation_index_backend.section_of(entry) != "conversation":
        raise invalid("只有对话可以置顶或设置免打扰")
    if marks["unread"] is not None:
        _require_inbox_read_target(actor, key, source)
    expected = inv.expected_version_for(key)
    try:
        if source and source.update_marks:
            state = source.update_marks(actor, key.removeprefix(source.prefix), **marks, expected_revision=expected)
        else:
            state = conversation_read_state.update_marks(actor, key, **marks, expected_revision=expected)
            state = web("web_inbox")._conversation_item(key, state)
    except conversation_read_state.VersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", f"{key} 的版本已经变化",
            details={"object_id": key, "expected_version": expected,
                     "current_version": str(exc.current["read_revision"])},
        ) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    web("web_read_caches")._forget_cached_inbox(actor)
    web("web_read_caches")._invalidate_agent_conversation_cache()
    web("web_inbox")._publish_conversation_read_changes(actor, {key}, bulk=True)
    return {"item": _inbox_entry(actor, key) or state}


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
        payload = web("web_read_caches")._portal_change_payload(user_id, notifications, {"resident", "projection"})
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
    if not _access().user(user_id).get("configured") and user_id not in web("web_accounts")._load_users():
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
    result = web("web_inbox")._clear_agent_conversations(user_id, mode=mode, entries=entries)
    return {"status": "cleared", "cleared": result["cleared_count"], "items": result["items"]}


# -- users ---------------------------------------------------------------------------


def _access():
    return web("web_accounts")._PORTAL_ACCESS


def _public(user: dict[str, Any]) -> dict[str, Any]:
    from management_portal.user_avatar import user_avatar_store

    user = {**user, "avatar": user_avatar_store().get(user["username"])}
    return user


def with_user_allows(actor: str, users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each user row says whether its edit button may call UpdateUser for this caller."""
    from management_portal import permission_system

    editable = permission_system.permitted_ids(actor, "UpdateUser", "user_id",
                                               [str(user.get("username") or "") for user in users])
    from management_portal.permission_system.administrators import is_administrator

    account_admin = is_administrator(actor)
    return [{**user, "allows": {"UpdateUser": str(user.get("username") or "") in editable,
                               "UpdateUserStatus": account_admin,
                               "UpdateUserCategory": account_admin}} for user in users]


def list_users(inv: Invocation) -> dict[str, Any]:
    users = _access().list_users(web("web_accounts")._load_users().keys())
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
def _administrator_change(caller: str, username: str, *, expected_revision: Any = None, **change: Any) -> Iterator[None]:
    """The administrator list is the Portal 管理员 guarantee's, in either mode (decision_b820faf42218 §3.1).

    The change is checked and written in the block under one account
    transaction, so concurrent changes are judged one after the other and
    none leaves the list without an active administrator.  The account
    revision the caller read (``expected_revision``) is compared in that
    same transaction, before the block writes.
    """
    from management_portal.permission_system.administrators import list_change

    try:
        with list_change(caller, username, **change):
            _access().require_revision(expected_revision)
            yield
    except PermissionError as exc:
        raise InstructionError("instruction.forbidden", str(exc)) from exc
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def _user_token(inv: Invocation) -> Any:
    """The account revision the caller expects for the User this call targets."""
    return inv.expected_version_for(str(inv.argument("user_id") or ""))


@contextmanager
def _user_version_conflict(inv: Invocation) -> Iterator[None]:
    """Report the account store's comparison failure in the kernel's version_conflict form."""
    from management_portal.portal_access_control import AccessVersionConflict

    key = str(inv.argument("user_id") or "")
    try:
        yield
    except AccessVersionConflict as exc:
        raise InstructionError(
            "instruction.version_conflict", f"{key} 的版本已经变化",
            details={"object_id": key, "expected_version": exc.expected_version,
                     "current_version": exc.current_version},
        ) from exc


def _set_user_category(inv: Invocation, username: str, category: Any) -> bool:
    """Change category through the account owner and its administrator-list transaction."""
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
        # A same-value request still answers to the version the caller expects.
        _access().require_revision(_user_token(inv))
        return False
    with _administrator_change(inv.caller, username, expected_revision=_user_token(inv), category=category):
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
    web("web_accounts")._require_authenticated_actor(inv.caller)
    service = web("web_accounts")._USER_ENROLLMENT
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
    service = web("web_accounts")._USER_ENROLLMENT
    if inv.call.instruction == "RevokeUserAccessToken":
        return {"credential": service.revoke_token(inv.caller, inv.argument("credential_id"))}
    owner = _named_user(inv)
    if inv.call.instruction == "ListUserAccessTokens":
        return {"credentials": service.list_tokens(owner)}
    return {"credential": service.create_token(
        owner, inv.argument("secret_sha256"), name=inv.argument("name", "API"),
        expires_in_days=inv.argument("expires_in_days", 0),
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
    result = web("web_accounts")._admin_access_action(inv.caller, payload)
    return {"user": result.get("user") or {}, "personal_ama": result.get("personal_ama") or {}}


def update_user(inv: Invocation) -> dict[str, Any]:
    username = inv.argument("user_id")
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - {"display_name", "recovery_email", "avatar"})
    if unknown:
        raise invalid("changes 只能包含 display_name、recovery_email、avatar")
    web("web_accounts")._require_authenticated_actor(inv.caller)
    from management_portal import permission_system

    has_avatar = "avatar" in changes
    avatar_payload = changes.pop("avatar", None)
    user: dict[str, Any] = {}
    expected = _user_token(inv)
    with _user_version_conflict(inv):
        if changes:
            result = web("web_accounts")._admin_access_action(inv.caller, {
                "action": "update", "username": username,
                "display_name": changes.get("display_name"),
                "email": changes.get("recovery_email"),
                "expected_revision": expected,
            })
            user = result.get("user") or {}
        else:
            # An avatar lives in its own store, and an empty change writes nothing: the token is only checked, under the account lock.
            _access().require_revision(expected)
    result = dict(user or {"username": username, **_access().user(username)})
    if has_avatar:
        result["avatar"] = _set_avatar(username, avatar_payload, inv.caller)
    return {"user": {**result, "category": permission_system.user_category(username)},
            "category_changed": False}


def _require_account_administrator(caller: str) -> None:
    from management_portal.permission_system.administrators import is_administrator

    web("web_accounts")._require_authenticated_actor(caller)
    if not is_administrator(caller):
        raise InstructionError("instruction.forbidden", "修改用户状态或分类需要 Portal 管理员")


def update_user_status(inv: Invocation) -> dict[str, Any]:
    _require_account_administrator(inv.caller)
    username, status = inv.argument("user_id"), inv.argument("status")
    if status not in {"active", "disabled"}:
        raise invalid("status 须为 active 或 disabled")
    with _user_version_conflict(inv):
        with _administrator_change(inv.caller, username, expected_revision=_user_token(inv), status=status):
            result = web("web_accounts")._admin_access_action(inv.caller, {
                "action": "update", "username": username, "status": status,
                "revoke_sessions": status == "disabled",
            })
    return {"user": result.get("user") or {}}


def update_user_category(inv: Invocation) -> dict[str, Any]:
    from management_portal import permission_system

    _require_account_administrator(inv.caller)
    username = inv.argument("user_id")
    with _user_version_conflict(inv):
        changed = _set_user_category(inv, username, inv.argument("category"))
    return {"user": {"username": username, **_access().user(username),
                     "category": permission_system.user_category(username)},
            "category_changed": changed}


def reset_user_password(inv: Invocation) -> dict[str, Any]:
    username = inv.argument("user_id")
    if username.casefold() == inv.caller.casefold():
        raise invalid("修改自己的密码须在 Settings 中验证当前密码")
    password = inv.argument("new_password")
    web("web_accounts")._PORTAL_CREDENTIALS.validate_password(password)
    with _user_version_conflict(inv):
        result = web("web_accounts")._admin_access_action(inv.caller, {
            "action": "reset_password", "username": username, "password": password,
            "expected_revision": _user_token(inv),
        })
    return {"user": result.get("user") or {}}


def revoke_user_sessions(inv: Invocation) -> dict[str, Any]:
    with _user_version_conflict(inv):
        result = web("web_accounts")._admin_access_action(inv.caller, {
            "action": "revoke_sessions", "username": inv.argument("user_id"),
            "expected_revision": _user_token(inv),
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
    with _user_version_conflict(inv), _administrator_change(
            inv.caller, username, expected_revision=_user_token(inv), delete=True):
        if permission_system.user_category(username) == "portal_admin":
            _access().upsert_user(username, actor=inv.caller, status="disabled", revoke_sessions=True)
    result = web("web_accounts")._admin_access_action(inv.caller, {"action": "delete", "username": inv.argument("user_id")})
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
        caller, web("user_preferences")._lineage_favorite_payload(caller),
        lambda selection: web("user_preferences")._set_lineage_favorite(caller, selection, False),
    ).get("favorites") or []
    residents = presentation_store.favorite_ids(caller, RESIDENT_FAVORITE_SURFACE)
    return list(dict.fromkeys([*(item.split(":", 1)[1] for item in favorites if ":" in item), *residents]))


def get_user_navigation(inv: Invocation) -> dict[str, Any]:
    return {"items": _navigation_items(_named_user(inv))}


def set_user_navigation_favorite(inv: Invocation) -> dict[str, Any]:
    from management_portal import presentation_store

    user_id = _named_user(inv)
    object_id = inv.argument("object_id")
    favorite = bool(inv.argument("favorite"))
    if object_id.startswith("resident_"):
        if not re.fullmatch(r"resident_[0-9a-f]{12}", object_id):
            raise invalid("object_id 不是有效的 Resident ID")
        presentation_store.set_favorite(user_id, RESIDENT_FAVORITE_SURFACE, object_id, favorite)
    else:
        web("user_preferences")._set_lineage_favorite(user_id, _navigation_address(object_id), favorite)
    return {"items": _navigation_items(user_id)}


# -- Portal services -----------------------------------------------------------------


def get_runtime_capacity(inv: Invocation) -> dict[str, Any]:
    from management_portal.runtime_capacity import runtime_capacity

    return {"capacity": runtime_capacity()}


def get_portal_status(inv: Invocation) -> dict[str, Any]:
    sys.path.insert(0, str(REPO_ROOT)) if str(REPO_ROOT) not in sys.path else None
    from scripts import portal_services

    requested = inv.argument("deployment")
    deployments = [requested] if requested else ["dev", "release"]
    result = []
    for deployment in deployments:
        try:
            status = portal_services.status(deployment)
        except Exception as exc:  # noqa: BLE001 - one deployment's status failure is reported, not raised
            status = {"ok": False, "error": str(exc)}
        result.append({"deployment": deployment, **(status if isinstance(status, dict) else {"status": status})})
    return {"deployments": result}


def restart_portal(inv: Invocation) -> dict[str, Any]:
    deployment = inv.argument("deployment")
    components = [str(item) for item in inv.argument("components") or []]
    from scripts import tfi_development_runtime as paired
    root = paired.configured_root()
    if not root:
        raise InstructionError("instruction.failed", "独立 TFI 实例的服务由 install_tfi_release 管理，不能在这里重启")
    return paired.request_restart(root, deployment, components)["request"]


def get_device_status(inv: Invocation) -> dict[str, Any]:
    from management_portal import agent_status_backend

    refresh = bool(inv.argument("refresh"))
    device_id = inv.argument("device_id") or ""
    if inv.argument("kind") in {"ssd_capacity", "physical_disk_capacity"}:
        return agent_status_backend.get_physical_disk_capacity(device_id, ssd_only=inv.argument("kind") == "ssd_capacity")
    if inv.argument("kind") == "local_disk_capacity":
        return agent_status_backend.get_local_disk_capacity(force_refresh=refresh, resource_id=device_id)
    if inv.argument("kind") == "cloud":
        return {"devices": agent_status_backend.get_google_device_status(force_refresh=refresh, device_id=device_id)}
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
    "CreateUserInvitation": user_invitation,
    "ListUserInvitations": user_invitation,
    "RevokeUserInvitation": user_invitation,
    "CreateUserAccessToken": user_access_token,
    "ListUserAccessTokens": user_access_token,
    "RevokeUserAccessToken": user_access_token,
    "GetMailbox": get_user_mailbox,
    "UpdateMailboxItem": update_inbox_item,
    "ClearMailbox": clear_user_mailbox,
    "ListUsers": list_users,
    "GetUser": get_user,
    "CreateUser": create_user,
    "UpdateUser": update_user,
    "UpdateUserStatus": update_user_status,
    "UpdateUserCategory": update_user_category,
    "ResetUserPassword": reset_user_password,
    "RevokeUserSessions": revoke_user_sessions,
    "DeleteUser": delete_user,
    "GetUserNavigation": get_user_navigation,
    "SetUserNavigationFavorite": set_user_navigation_favorite,
    "GetPortalStatus": get_portal_status,
    "GetRuntimeCapacity": get_runtime_capacity,
    "RestartPortal": restart_portal,
    "GetDeviceStatus": get_device_status,
    "CreateTerminal": open_terminal,
}


USER_RECORD_INSTRUCTIONS = frozenset({
    "GetUser", "UpdateUser", "UpdateUserStatus", "UpdateUserCategory", "ResetUserPassword", "RevokeUserSessions", "DeleteUser",
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

    if inv.name == "UpdateMailboxItem" and object_id == inv.argument("key"):
        # The handler checks the target completion with its single authoritative read.
        return None
    if inv.name in USER_RECORD_INSTRUCTIONS and object_id == inv.argument("user_id"):
        return int(_access()._revision())
    for _feature, module in portal_features.contributions("web.instructions"):
        if inv.name in module.HANDLERS:
            feature_version = getattr(module, "version_of", None)
            return feature_version(inv, object_id) if feature_version else None
    return None


def binding() -> OwnerBinding:
    # The installed features' Web instruction handlers (``web.instructions``) join the core ones.
    handlers = {**HANDLERS, **{name: handler for _feature, module in portal_features.contributions("web.instructions")
                               for name, handler in module.HANDLERS.items()}}
    return OwnerBinding(owner="web", handlers=handlers, streams={"WatchMailbox": watch_inbox}, version_of=version_of, recover_projections=recover_projections)


def build_kernel() -> InstructionKernel:
    from management_common.portal_database import portal_database
    from management_portal.application_tool_contracts import permission_declaration
    from management_portal.hatg.store import ExecutionStore

    return InstructionKernel(binding(), store=ExecutionStore(portal_database(schema="web_instructions")),
                             maintenance_lookup=permission_declaration)
