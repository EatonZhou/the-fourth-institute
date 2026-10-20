"""A viewer's read, pin and dismissal state of their Mailbox conversations.

Every conversation kind keeps its state in one row per viewer
(conversation_receipt_store): unread while its latest alert stands past the
read position, with a revision that orders every state of the row.
"""
from __future__ import annotations

from typing import Any, Iterable

from management_portal import conversation_receipt_store as store
from management_portal.conversation_receipt_store import MAX_PINNED_ITEMS, VersionConflict  # noqa: F401


def _transaction(actor):
    return store.database().transaction(store.actor_key(actor))


def load(actor: str) -> dict[str, Any]:
    """The viewer's rows by conversation key and their pins in order."""
    actor_id = str(actor or "").strip()
    if not actor_id:
        return {"rows": {}, "pinned_keys": []}
    with store.database().read() as connection:
        rows = store.read_actor(connection, actor_id)
    return {"rows": rows, "pinned_keys": [key for key, row in rows.items() if row["pinned"]]}


def row(state: object, key: str) -> dict[str, Any]:
    rows = state.get("rows") if isinstance(state, dict) else None
    return (rows or {}).get(str(key or "")) or store.empty_state()


def unread(entry: dict[str, Any], state: object) -> bool:
    return row(state, str(entry.get("key") or ""))["unread"]


def unread_keys(state: object) -> list[str]:
    rows = state.get("rows") if isinstance(state, dict) else None
    return [key for key, value in (rows or {}).items() if value["unread"]]


def read_rows(actor: str, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    with store.database().read() as connection:
        return store.read_keys(connection, actor, keys)


def set_read(actor: str, key: str, *, unread_value: bool, expected_revision: object = None) -> dict[str, Any]:
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise ValueError("actor is required")
    with _transaction(actor_id) as connection:
        return store.set_read(connection, actor_id, key, unread=unread_value, expected_revision=expected_revision)


def notification_key(lineage_id: object, notification_id: object) -> str:
    """The Mailbox key of a human notification, in a Lineage or the Portal."""
    lineage = str(lineage_id or "").strip()
    return f"lineage-notification:{lineage}:{notification_id}" if lineage else f"notification:{notification_id}"


def alert(actors: Iterable[object], key: str, ref: str) -> list[str]:
    """A new event in a conversation the given viewers should notice."""
    with store.database().transaction() as connection:
        return store.alert(connection, actors, key, ref)


def set_pinned(actor: str, key: str, pinned: bool) -> dict[str, Any] | None:
    with _transaction(actor) as connection:
        return store.set_pinned(connection, actor, key, pinned)


def retain_pins(actor: str, available_keys: set[str]) -> dict[str, Any]:
    """Release pins only after every pinned target was checked for this viewer."""
    with _transaction(actor) as connection:
        store.release_pins(connection, actor, available_keys)
    return load(actor)


def read_through(actor: str, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
    with _transaction(actor) as connection:
        return store.read_through(connection, actor, keys)


def dismiss(actor: str, activity: dict[str, object]) -> dict[str, dict[str, Any]]:
    """Read and hide conversations until their activity passes ``activity[key]``."""
    with _transaction(actor) as connection:
        return store.dismiss(connection, actor, activity)


def delete_actor(actor: str) -> bool:
    with _transaction(actor) as connection:
        return store.delete_actor(connection, actor)
