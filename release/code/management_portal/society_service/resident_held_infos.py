"""info messages delivered with ``activation_policy=attach_to_next`` (concept_43c793b4f3ab).

An info message tells a Resident about a change; it never starts a turn.  It
waits in the Resident's Mailbox (``place = 'info'``, in arrival order) with
input state ``queued``.  When the next request is turned into its durable
Execution request, every held info is placed before the request text as
background, then delivered: it enters the Resident's trace marked
``released_with`` that request, leaves the Mailbox and is ``completed``.

The model reads an info and its request as one input, so the Conversation
shows a delivered info inside that request, collapsed, not as a message of
its own.  A Family facade never runs: an info sent to it goes to the
Family's steward at once and is ``completed``; the facade's own trace gets it
with the facade's next request, and until then its Conversation shows it as
handed to the steward.

A waiting info can be withdrawn before a request carries it (CancelInput),
and the infos of a stopped or failed round wait again when that round is put
back in the queue (RequeueMessage, decision_b5d349190743): they stay where
they first entered the trace, and are shown with the request that carries
them next.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Collection, Mapping

from . import resident_conversation_stream, resident_lineage
from .state_store import get_lineage_data_store

MAX_HELD_INFOS = 200
INFO_HEADER = "以下是你上次工作之后收到的背景更新（info 消息），供处理本次请求时参考，不需要单独回复："


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resident(record: Mapping[str, Any]) -> str:
    return resident_lineage.resident_id(record)


def _storage(message: Mapping[str, Any]) -> dict[str, Any]:
    public = resident_conversation_stream.protocol_message(message)
    public.pop("activated_at", None)
    return resident_conversation_stream._message_for_storage(public, private_fields=message)


def hold(record: Mapping[str, Any], message: Mapping[str, Any], *, first: bool = False) -> None:
    """Hold one accepted info message; holding the same message twice changes nothing."""

    if not str(message.get("message_id") or "").strip():
        raise ValueError("info message requires message_id")
    get_lineage_data_store().mailbox_hold_info(
        _resident(record), _storage(message), held_at=_now(), limit=MAX_HELD_INFOS, first=first,
    )


def peek(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The waiting infos, in the order they go with the next request."""

    try:
        resident_id = _resident(record)
    except (KeyError, ValueError):
        return []
    return [
        {"message_id": entry["message_id"], "author_id": entry["author_id"],
         "held_at": str(entry["item"].get("held_at") or entry["accepted_at"])}
        for entry in get_lineage_data_store().mailbox_entries([resident_id], places=("info",))
    ]


def is_info(record: Mapping[str, Any], message_id: str) -> bool:
    """Whether ``message_id`` was accepted as an info message (held, or delivered with a request)."""

    try:
        resident_id = _resident(record)
    except (KeyError, ValueError):
        return False
    entry = get_lineage_data_store().mailbox_entry(resident_id, message_id)
    if entry is not None:
        return entry["place"] == "info"
    message = resident_conversation_stream.get_repository().message(resident_id, message_id) or {}
    return bool(message.get("released_with"))


def project_snapshot(
    record: Mapping[str, Any],
    snapshot: dict[str, Any],
    *,
    handed_to_steward: bool = False,
) -> dict[str, Any]:
    """Mark info messages still waiting, and fold delivered ones into the request they came with.

    ``handed_to_steward`` marks a Family facade, whose infos already went to
    its steward and only wait to be shown with the next request: they are
    shown now, at the end of its Conversation.
    """

    held = peek(record)
    waiting = {str(item.get("message_id") or "") for item in held}
    messages = fold_into_requests(
        list(snapshot.get("messages") or []), handed_to_steward=handed_to_steward, waiting=waiting,
    )
    if held:
        if handed_to_steward:
            present = {str(item.get("message_id") or "") for item in messages if isinstance(item, Mapping)}
            repository = resident_conversation_stream.get_repository()
            for item in held:
                if item["message_id"] not in present:
                    message = repository.message(_resident(record), item["message_id"])
                    if message is not None:
                        messages.append(message)
        for message in messages:
            if isinstance(message, dict) and str(message.get("message_id") or "") in waiting:
                message["held_info"] = True
                if handed_to_steward:
                    message["held_info_steward"] = True
        snapshot["held_infos"] = held
    snapshot["messages"] = messages
    return snapshot


def project_page(record: Mapping[str, Any], messages: list[Any], *, handed_to_steward: bool = False) -> list[Any]:
    """A history page with delivered infos folded into their requests."""

    waiting = {item["message_id"] for item in peek(record)}
    return fold_into_requests(messages, handed_to_steward=handed_to_steward, waiting=waiting)


def fold_into_requests(
    messages: list[Any],
    released: Mapping[str, str] | None = None,
    *,
    handed_to_steward: bool = False,
    waiting: Collection[str] = (),
) -> list[Any]:
    """Show each delivered info inside the request it came with, as ``attached_infos``.

    Like a Mailbox group's members, the infos keep their own sender, time and
    text; they are read-only display fields.  An info whose request is not in
    ``messages`` stays where it is, marked ``released_with``.  ``released``
    maps infos to their requests where the messages do not say it themselves.
    An info ``waiting`` again (its round was requeued) stays a message of its
    own until a request carries it.
    """

    infos: dict[str, list[Mapping[str, Any]]] = {}
    present = {str(item.get("message_id") or "") for item in messages if isinstance(item, Mapping)}
    for message in messages:
        if not isinstance(message, dict):
            continue
        if str(message.get("message_id") or "") in waiting:
            message.pop("released_with", None)
            continue
        request_id = str(
            message.get("released_with") or (released or {}).get(str(message.get("message_id") or "")) or ""
        )
        if not request_id:
            continue
        message["released_with"] = request_id
        if handed_to_steward:
            message["held_info_steward"] = True
        if request_id in present:
            infos.setdefault(request_id, []).append(message)
    if not infos:
        return messages
    folded = {id(item) for group in infos.values() for item in group}
    result: list[Any] = []
    for message in messages:
        if id(message) in folded:
            continue
        attached = infos.get(str(message.get("message_id") or "")) if isinstance(message, Mapping) else None
        if attached:
            message = {**message, "attached_infos": [_info_view(item) for item in attached]}
            if handed_to_steward:
                message["attached_infos_steward"] = True
        result.append(message)
    return result


def _info_view(message: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: message.get(key)
        for key in ("message_id", "author_id", "origin", "created_at", "content_parts")
        if key in message
    }


def _text(message: Mapping[str, Any]) -> str:
    return "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and part.get("type") == "text" and part.get("text")
    ).strip()


def render(record: Mapping[str, Any], items: list[Mapping[str, Any]]) -> str:
    """The background block placed before a request; empty when no info is readable."""

    resident_id = resident_lineage.resident_id(record)
    repository = resident_conversation_stream.get_repository()
    lines: list[str] = []
    for item in items:
        message = repository.message(resident_id, str(item.get("message_id") or "")) or {}
        text = _text(message)
        if not text:
            continue
        author = str(message.get("author_id") or item.get("author_id") or "")
        created = str(message.get("created_at") or item.get("held_at") or "")
        lines.append(f"- [{author} · {created}] {text}")
    if not lines:
        return ""
    return INFO_HEADER + "\n" + "\n".join(lines)


def attach(record: Mapping[str, Any], prompt: str) -> tuple[str, list[dict[str, Any]]]:
    """Prompt with the held infos placed first, and the infos it carries."""

    items = peek(record)
    block = render(record, items) if items else ""
    if not block:
        return prompt, items
    return f"{block}\n\n---\n\n{prompt}", items


def _deliver(record: Mapping[str, Any], message_ids: list[str], request_message_id: str) -> list[str]:
    """Put waiting infos in the trace, marked with the request they go with; they leave the Mailbox."""

    resident_id = _resident(record)
    repository = resident_conversation_stream.get_repository()
    activated_at = _now()
    delivered: list[str] = []
    for message_id in message_ids:
        message = repository.message(resident_id, message_id)
        if message is None:
            continue
        if repository.agent_data.chat_message(resident_id, message_id) is None:
            repository.commit_user_message(
                resident_id, {**message, "released_with": request_message_id}, activated_at=activated_at,
            )
        elif message.get("released_with") != request_message_id:
            # Delivered again (its round was requeued): it stays where it first
            # entered the trace and is shown with the request that carried it now.
            repository.update_message(resident_id, message_id, message={**message, "released_with": request_message_id})
        delivered.append(message_id)
    get_lineage_data_store().mailbox_forget(resident_id, message_ids, places=("info",))
    return delivered


def release_with(record: Mapping[str, Any], request_message_id: str) -> list[str]:
    """Show every held info with ``request_message_id``; nothing is delivered (a Family facade).

    A facade's infos already went to its steward when they arrived; the
    facade's next request is where its Conversation shows them.
    """

    if not request_message_id:
        return []
    return _deliver(record, [item["message_id"] for item in peek(record)], request_message_id)


def consume(record: Mapping[str, Any], items: list[Mapping[str, Any]], *, request_message_id: str = "") -> None:
    """Deliver the infos a durable request now carries, with that request; repeating it changes nothing."""

    if not items:
        return
    message_ids = list(dict.fromkeys(str(item.get("message_id") or "") for item in items if item.get("message_id")))
    _deliver(record, message_ids, request_message_id)
    repository = resident_conversation_stream.get_repository()
    for message_id in message_ids:
        repository.set_input_state(_resident(record), message_id, "completed")


def restore(record: Mapping[str, Any], items: list[Mapping[str, Any]]) -> list[str]:
    """Hold these infos again, first and in their order, as if not yet delivered (a requeued round).

    Each keeps its original sender and arrival time and goes with the next
    request; its trace still shows the round it went with first.
    """

    resident_id = _resident(record)
    repository = resident_conversation_stream.get_repository()
    originals = []
    for item in items:
        message_id = str(item["message_id"])
        message = repository.message(resident_id, message_id)
        if message is None:
            raise KeyError(message_id)
        originals.append((item, message))
    for item, message in reversed(originals):
        get_lineage_data_store().mailbox_hold_info(
            resident_id, _storage(message), held_at=str(item.get("held_at") or _now()),
            limit=MAX_HELD_INFOS, first=True,
        )
    return [str(item["message_id"]) for item, _message in originals]


def withdraw(record: Mapping[str, Any], message_id: str, actor_id: str, *, manager: bool) -> dict[str, Any] | None:
    """Take a waiting info out before any request carries it; None when it is not waiting here.

    The Resident's managers and the info's sender may withdraw it.
    """

    from management_common.actor_identity import same_actor

    resident_id = _resident(record)
    target = str(message_id or "").strip()
    store = get_lineage_data_store()
    entry = store.mailbox_entry(resident_id, target)
    if entry is None or entry["place"] != "info":
        return None
    if not (manager or (entry["author_id"] and same_actor(entry["author_id"], actor_id))):
        raise PermissionError("只有能管理这个 Resident 的身份或这条 info 的发送者可以撤回")
    if not store.mailbox_forget(resident_id, [target], places=("info",)):
        return None
    return {"message_id": target, "author_id": entry["author_id"],
            "held_at": str(entry["item"].get("held_at") or entry["accepted_at"])}
