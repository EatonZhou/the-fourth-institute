"""Resident Mailboxes: every accepted message that is not in its trace yet.

The table and the meaning of ``place`` are described with ``MAILBOX_DDL``
in state_store.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from management_common.database_time import iso_time
from .lineage_data_columns import json_column, now_iso_millis, stored_object


def _real_message(value: object) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("content_parts"), list)


def _encode_mailbox_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Pending input uses the same lossless envelope before entering a trace."""
    from management_portal.execution.message_projection import wrap_message
    stored = deepcopy(dict(message))
    for part in stored.get("content_parts", []):
        if part.get("type") == "text":
            part["text"] = wrap_message(str(part.get("text") or ""))
    return stored


def _decode_mailbox_message(raw: object, identity: str) -> dict[str, Any]:
    from management_portal.execution.message_projection import unwrap_message
    message = stored_object(raw, identity)
    for part in message.get("content_parts", []):
        if part.get("type") == "text":
            part["text"] = unwrap_message(part.get("text"))
    return message


def _mailbox_item(row: Any) -> dict[str, Any]:
    from .state_store import STORED_INPUT_MESSAGE

    item = stored_object(row["item_json"], str(row["message_id"]))
    if item.pop(STORED_INPUT_MESSAGE, False):
        item["input_message"] = _decode_mailbox_message(row["message_json"], str(row["message_id"]))
    return item


def _mailbox_row(row: Any) -> dict[str, Any]:
    return {
        "resident_id": str(row["resident_id"]),
        "message_id": str(row["message_id"]),
        "place": str(row["place"]),
        "position": int(row["position"]),
        "group_id": str(row["group_id"]),
        "author_id": str(row["author_id"]),
        "accepted_at": iso_time(row["accepted_at"]),
        "updated_at": iso_time(row["updated_at"]),
        "message": _decode_mailbox_message(row["message_json"], str(row["message_id"])),
        "item": stored_object(row["item_json"], str(row["message_id"])),
    }


def lock_active_resident(connection: Any, resident_id: str) -> None:
    # Holding this lock through admission serializes with deletion. An
    # earlier service lookup alone cannot protect the retained identity.
    row = connection.execute(
        'SELECT 1 FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR SHARE',
        (resident_id,),
    ).fetchone()
    if row is None:
        raise KeyError(resident_id)


def mailbox_queues(connection: Any, resident_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Each Resident's queue, as its Job carries it, from its Mailbox."""

    queues: dict[str, list[dict[str, Any]]] = {}
    members: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for offset in range(0, len(resident_ids), 500):
        for row in connection.execute(
            "SELECT resident_id, message_id, place, group_id, message_json, item_json "
            "FROM resident_mailbox WHERE resident_id = ANY(%s) AND place IN ('queue', 'group') "
            "ORDER BY resident_id, place, group_id, position",
            (resident_ids[offset:offset + 500],),
        ).fetchall():
            resident_id = str(row["resident_id"])
            if row["place"] == "group":
                members[(resident_id, str(row["group_id"]))].append(_mailbox_item(row))
            else:
                queues.setdefault(resident_id, []).append(_mailbox_item(row))
    for resident_id, items in queues.items():
        for item in items:
            group = members.get((resident_id, str(item.get("user_message_id") or "")))
            if group:
                item["mailbox_members"] = group
    return queues


def sync_mailbox_queue(connection: Any, resident_id: str, queue: object) -> None:
    """Make the Mailbox's queue and group rows the Job's queue.

    A message that leaves the queue stays in the Mailbox, ``pending``,
    until it enters the trace or its input ends.
    """

    from .state_store import STORED_INPUT_MESSAGE

    now = now_iso_millis()
    desired: list[tuple[str, str, int, str, dict[str, Any]]] = []
    for index, raw in enumerate(queue if isinstance(queue, list) else []):
        if not isinstance(raw, Mapping):
            raise ValueError("Resident Queue items must be objects")
        item = deepcopy(dict(raw))
        members = item.pop("mailbox_members", None) or []
        message_id = str(item.get("user_message_id") or "").strip()
        if not message_id:
            raise ValueError("Resident Queue item requires user_message_id")
        desired.append((message_id, "queue", index, "", item))
        for position, member in enumerate(members):
            member_item = deepcopy(dict(member))
            member_id = str(member_item.get("user_message_id") or "").strip()
            if not member_id:
                raise ValueError("Mailbox group member requires user_message_id")
            desired.append((member_id, "group", position, message_id, member_item))
    existing = {
        str(row["message_id"]): row
        for row in connection.execute(
            "SELECT message_id, place, message_json FROM resident_mailbox WHERE resident_id = %s FOR UPDATE",
            (resident_id,),
        ).fetchall()
    }
    values = []
    for message_id, place, position, group_id, item in desired:
        row = existing.get(message_id)
        stored = _decode_mailbox_message(row["message_json"], message_id) if row is not None else None
        message = item.get("input_message")
        if not _real_message(stored):
            stored = deepcopy(dict(message)) if _real_message(message) else {}
        if stored and message == stored:
            item.pop("input_message", None)
            item[STORED_INPUT_MESSAGE] = True
        values.append((
            resident_id, message_id, place, position, group_id,
            str(stored.get("author_id") or item.get("actor") or ""),
            str(stored.get("created_at") or item.get("enqueued_at") or now),
            now, json_column(_encode_mailbox_message(stored)), json_column(item),
        ))
    if values:
        connection.executemany(
            "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
            "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (resident_id, message_id) DO UPDATE SET place = excluded.place, "
            "position = excluded.position, group_id = excluded.group_id, updated_at = excluded.updated_at, "
            "item_json = excluded.item_json, message_json = CASE WHEN resident_mailbox.message_json = '{}' "
            "THEN excluded.message_json ELSE resident_mailbox.message_json END",
            values,
        )
    kept = {item[0] for item in desired}
    left = [
        message_id for message_id, row in existing.items()
        if row["place"] in ("queue", "group") and message_id not in kept
    ]
    if left:
        # An input that already ended (a withdrawn item) leaves the Mailbox.
        connection.execute(
            "DELETE FROM resident_mailbox mailbox USING conversation_input_states input "
            "WHERE mailbox.resident_id = %s AND mailbox.message_id = ANY(%s) "
            "AND input.resident_id = mailbox.resident_id AND input.message_id = mailbox.message_id "
            "AND input.state IN ('completed', 'failed', 'cancelled')",
            (resident_id, left),
        )
        connection.execute(
            "UPDATE resident_mailbox SET place = 'pending', updated_at = %s "
            "WHERE resident_id = %s AND message_id = ANY(%s)",
            (now, resident_id, left),
        )


class ResidentMailboxes:
    """Reads and writes of Resident Mailboxes."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def mailbox_accept(self, resident_id: str, message: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
        """Keep one accepted message in the Mailbox, ``pending`` until it is queued or delivered.

        Returns the message the Mailbox holds and whether it was new; one
        already there is returned as it is.
        """

        stored = deepcopy(dict(message))
        message_id = str(stored.get("message_id") or "").strip()
        if not message_id or not _real_message(stored):
            raise ValueError("Mailbox message requires message_id and content_parts")
        now = now_iso_millis()

        def write(connection: Any) -> tuple[dict[str, Any], bool]:
            lock_active_resident(connection, resident_id)
            inserted = connection.execute(
                "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
                "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, 'pending', 0, '', %s, %s, %s, %s, '{}') "
                "ON CONFLICT (resident_id, message_id) DO NOTHING RETURNING message_id",
                (resident_id, message_id, str(stored.get("author_id") or ""),
                 str(stored.get("created_at") or now), now, json_column(_encode_mailbox_message(stored))),
            ).fetchone()
            if inserted is not None:
                return stored, True
            row = connection.execute(
                "SELECT message_json FROM resident_mailbox WHERE resident_id = %s AND message_id = %s FOR UPDATE",
                (resident_id, message_id),
            ).fetchone()
            existing = _decode_mailbox_message(row["message_json"], message_id)
            if not _real_message(existing):
                connection.execute(
                    "UPDATE resident_mailbox SET message_json = %s, author_id = %s, updated_at = %s "
                    "WHERE resident_id = %s AND message_id = %s",
                    (json_column(_encode_mailbox_message(stored)), str(stored.get("author_id") or ""), now, resident_id, message_id),
                )
                return stored, False
            return existing, False

        return self.database.write(write)

    def mailbox_entries(
        self,
        resident_ids: Iterable[str],
        *,
        message_ids: Iterable[str] | None = None,
        places: Iterable[str] | None = None,
        author: str = "",
    ) -> list[dict[str, Any]]:
        """Mailbox rows, by Resident and place order."""

        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        clauses = ["resident_id = ANY(%s)"]
        parameters: list[Any] = [requested]
        if message_ids is not None:
            clauses.append("message_id = ANY(%s)")
            parameters.append(list(dict.fromkeys(str(item) for item in message_ids)))
        if places is not None:
            clauses.append("place = ANY(%s)")
            parameters.append(list(places))
        if author:
            clauses.append("lower(trim(ltrim(author_id, '@＠'))) = lower(%s)")
            parameters.append(str(author).strip().lstrip("@＠").strip())
        if not requested:
            return []
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM resident_mailbox WHERE " + " AND ".join(clauses)
                + " ORDER BY resident_id, place, group_id, position, accepted_at, message_id",
                parameters,
            ).fetchall()
        return [_mailbox_row(row) for row in rows]

    def mailbox_entries_by_author(self, author: str) -> list[dict[str, Any]]:
        """Every Resident's waiting messages from one sender."""

        clean = str(author or "").strip().lstrip("@＠").strip()
        if not clean:
            return []
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM resident_mailbox WHERE lower(trim(ltrim(author_id, '@＠'))) = lower(%s)",
                (clean,),
            ).fetchall()
        return [_mailbox_row(row) for row in rows]

    def mailbox_actor_activity(self, author: str, *, excluded_business_ref: str = "") -> dict[str, str]:
        """Latest valid waiting input per Resident, without decoding every message."""
        clean = str(author or "").strip().lstrip("@＠").strip()
        if not clean:
            return {}
        exclusion = (
            " AND coalesce(message_json::jsonb ->> 'business_ref', '') !~ %s"
            if excluded_business_ref else ""
        )
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT resident_id, MAX(coalesce(nullif(message_json::jsonb ->> 'created_at', '')::timestamptz, "
                "accepted_at)) AS latest_at FROM resident_mailbox "
                "WHERE lower(trim(ltrim(author_id, '@＠'))) = lower(%s) "
                "AND jsonb_typeof(message_json::jsonb -> 'content_parts') = 'array'"
                + exclusion + " GROUP BY resident_id",
                (clean, *([excluded_business_ref] if excluded_business_ref else [])),
            ).fetchall()
        return {str(row["resident_id"]): iso_time(row["latest_at"]) for row in rows}

    def mailbox_forget(
        self,
        resident_id: str,
        message_ids: Iterable[str],
        *,
        places: Iterable[str] = ("pending", "info"),
        connection: Any | None = None,
    ) -> list[str]:
        """Take messages out of the Mailbox (delivered, withdrawn or ended); queued ones stay."""

        ids = list(dict.fromkeys(str(item) for item in message_ids if str(item)))
        if not ids:
            return []

        def write(current: Any) -> list[str]:
            return [
                str(row["message_id"])
                for row in current.execute(
                    "DELETE FROM resident_mailbox WHERE resident_id = %s AND message_id = ANY(%s) "
                    "AND place = ANY(%s) RETURNING message_id",
                    (resident_id, ids, list(places)),
                ).fetchall()
            ]

        if connection is not None:
            return write(connection)
        return self.database.write(write)

    def mailbox_hold_info(
        self,
        resident_id: str,
        message: Mapping[str, Any],
        *,
        held_at: str,
        limit: int,
        first: bool = False,
        item: Mapping[str, Any] | None = None,
    ) -> bool:
        """Hold one info message (at the end, or ``first``); False when it was already held."""

        stored = deepcopy(dict(message))
        message_id = str(stored.get("message_id") or "").strip()
        if not message_id or not _real_message(stored):
            raise ValueError("info message requires message_id and content_parts")
        now = now_iso_millis()

        def write(connection: Any) -> bool:
            lock_active_resident(connection, resident_id)
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('resident_mailbox_info:' || %s, 0))", (resident_id,),
            )
            row = connection.execute(
                "SELECT place FROM resident_mailbox WHERE resident_id = %s AND message_id = %s FOR UPDATE",
                (resident_id, message_id),
            ).fetchone()
            if row is not None and row["place"] == "info":
                return False
            bounds = connection.execute(
                "SELECT COUNT(*) AS count, COALESCE(MIN(position), 0) AS first, COALESCE(MAX(position), 0) AS last "
                "FROM resident_mailbox WHERE resident_id = %s AND place = 'info'",
                (resident_id,),
            ).fetchone()
            if int(bounds["count"]) >= limit:
                raise ValueError(f"Resident 已有 {limit} 条未送达的 info 消息，请先让它处理一条请求")
            position = int(bounds["first"]) - 1 if first else int(bounds["last"]) + 1
            details = json_column({**dict(item or {}), "held_at": held_at})
            connection.execute(
                "INSERT INTO resident_mailbox(resident_id, message_id, place, position, group_id, author_id, "
                "accepted_at, updated_at, message_json, item_json) VALUES (%s, %s, 'info', %s, '', %s, %s, %s, %s, %s) "
                "ON CONFLICT (resident_id, message_id) DO UPDATE SET place = 'info', position = excluded.position, "
                "group_id = '', updated_at = excluded.updated_at, item_json = excluded.item_json, "
                "message_json = CASE WHEN resident_mailbox.message_json = '{}' THEN excluded.message_json "
                "ELSE resident_mailbox.message_json END",
                (resident_id, message_id, position, str(stored.get("author_id") or ""),
                 str(stored.get("created_at") or now), now, json_column(_encode_mailbox_message(stored)), details),
            )
            return True

        return bool(self.database.write(write))
