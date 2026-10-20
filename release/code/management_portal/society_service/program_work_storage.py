"""Durable Program work, partitioned by the object which owns each fact.

A transaction reads and locks only the objects it touches: one Event Source,
one wire's queued deliveries, one operator, one delivery profile. Persistence
writes only changed rows. There is no serialized Program snapshot: pending
deliveries, source positions and operator checkpoints have independent
identities. Terminal observations remain in Trigger Path steps.
"""
from __future__ import annotations

import logging
from collections.abc import MutableMapping
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator

from management_common.json_columns import json_text, json_value


# Collection names are the evaluator's contract, not an open namespace API.
MAPS = {
    "event_sources": ("event_source_progress", "source_id"),
    "operators": ("operator_work", "operator_id"),
    "delivery_profiles": ("resident_delivery_profiles", "message_id"),
}
COLUMNS = {
    table: ("lineage_id", key, "checkpoint_json")
    for table, key in MAPS.values()
}
COLUMNS.update({
    "pending_event_deliveries": ("lineage_id", "wire_id", "claim_token", "position", "delivery_json"),
    "pending_expiry_notices": ("lineage_id", "wire_id", "cursor", "position", "notice_json"),
})
RECOVERY_INDEXES = {
    "operator_pending_recovery_idx": "ON operator_work(lineage_id) WHERE COALESCE(checkpoint_json::jsonb->'pending_deliveries','{}'::jsonb)<>'{}'::jsonb",
}
# Objects are locked in this order (and by id within a collection) so that
# transactions touching several objects cannot deadlock.
LOCK_ORDER = ("event_sources", "event_wires", "expired_deliveries", "operators", "delivery_profiles")
ITEM_FIELDS = ("event_sources", "event_wires", "operators", "delivery_profiles")
LOGGER = logging.getLogger(__name__)


def recovery_lineage_ids(connection: Any) -> set[str]:
    """Discover pending Program deliveries without opening idle definitions."""
    return {row["lineage_id"] for row in connection.execute("""
        SELECT lineage_id FROM pending_event_deliveries WHERE NOT delivery_json::jsonb ? 'recovery_blocked'
        UNION SELECT lineage_id FROM pending_expiry_notices WHERE NOT notice_json::jsonb ? 'recovery_blocked'
        UNION SELECT lineage_id FROM operator_work
            WHERE COALESCE(checkpoint_json::jsonb->'pending_deliveries','{}'::jsonb)<>'{}'::jsonb
            AND EXISTS (SELECT 1 FROM jsonb_each(checkpoint_json::jsonb->'pending_deliveries') p
                        WHERE NOT p.value ? 'recovery_blocked')
    """).fetchall()}



def prune_delivery_profiles(connection: Any, *, lineage_id: str = "", message_id: str = "") -> int:
    """Delete only profiles whose input is durably terminal, including ordinary Events."""
    clauses = [
        "EXISTS (SELECT 1 FROM conversation_input_states s WHERE s.message_id=p.message_id "
        "AND s.state IN ('completed','failed','cancelled'))",
        "NOT EXISTS (SELECT 1 FROM conversation_input_states s WHERE s.message_id=p.message_id "
        "AND s.state NOT IN ('completed','failed','cancelled'))",
    ]
    parameters = []
    if lineage_id:
        clauses.append("p.lineage_id=%s")
        parameters.append(lineage_id)
    if message_id:
        clauses.append("p.message_id=%s")
        parameters.append(message_id)
    return connection.execute(
        "DELETE FROM resident_delivery_profiles p WHERE " + " AND ".join(clauses), tuple(parameters),
    ).rowcount


def encode(value: object) -> str:
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def create_schema(connection: Any) -> None:
    for table, key in MAPS.values():
        connection.execute(
            f"CREATE TABLE {table} (lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), "
            f"{key} TEXT NOT NULL, checkpoint_json JSONB NOT NULL, PRIMARY KEY(lineage_id,{key}))"
        )
    for table, left, right, payload in (
        ("pending_event_deliveries", "wire_id", "claim_token", "delivery_json"),
        ("pending_expiry_notices", "wire_id", "cursor", "notice_json"),
    ):
        connection.execute(
            f"CREATE TABLE {table} (lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), "
            f"{left} TEXT NOT NULL, {right} TEXT NOT NULL, position INTEGER NOT NULL, "
            f"{payload} JSONB NOT NULL, PRIMARY KEY(lineage_id,{left},{right}))"
        )
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def read(connection: Any, lineage_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    # One database round trip, one index range per owned collection.  The
    # payloads share one type while some tables still store JSON text.
    selections = [
        f"SELECT '{field}' collection,{key} key,'' secondary,0 position,checkpoint_json::jsonb payload FROM {table} WHERE lineage_id=%s"
        for field, (table, key) in MAPS.items()
    ]
    selections.extend([
        "SELECT 'event_wires',wire_id,claim_token,position,delivery_json::jsonb FROM pending_event_deliveries WHERE lineage_id=%s",
        "SELECT 'expired_deliveries',wire_id,cursor,position,notice_json::jsonb FROM pending_expiry_notices WHERE lineage_id=%s",
    ])
    rows = connection.execute(" UNION ALL ".join(selections) + " ORDER BY collection,position,key", (lineage_id,) * len(selections)).fetchall()
    for row in rows:
        field, key = row["collection"], row["key"]
        value = row["payload"]
        if field in MAPS:
            result.setdefault(field, {})[key] = value
        elif field == "event_wires":
            result.setdefault(field, {}).setdefault(key, {"pending_events": []})["pending_events"].append(value)
        else:
            result.setdefault(field, []).append(value)
    return result


Rows = dict[tuple[str, ...], tuple[Any, ...]]


def _table(field: str) -> str:
    return "pending_event_deliveries" if field == "event_wires" else MAPS[field][0]


def read_object(connection: Any, lineage_id: str, field: str, key: str) -> Any:
    """One stored source, operator or delivery profile, without locking; None when absent."""
    table, column = MAPS[field]
    row = connection.execute(
        f"SELECT checkpoint_json FROM {table} WHERE lineage_id=%s AND {column}=%s", (lineage_id, key),
    ).fetchone()
    return None if row is None else json_value(row["checkpoint_json"])


def _item_rows(field: str, key: str, value: Any) -> Rows:
    """The rows one object is stored as; none when it is absent."""
    if value is None:
        return {}
    if field != "event_wires":
        return {(key,): (encode(value),)}
    rows: Rows = {}
    for position, delivery in enumerate(value.get("pending_events") or []):
        identity = (key, delivery["claim_token"])
        if identity in rows:
            raise ValueError("Duplicate pending Event delivery identity")
        rows[identity] = (position, encode(delivery))
    return rows


def _expired_rows(notices: list[Any] | None) -> Rows:
    rows: Rows = {}
    for position, notice in enumerate(notices or []):
        identity = (notice["wire_id"], notice["cursor"])
        if identity in rows:
            raise ValueError("Duplicate expiry notice identity")
        rows[identity] = (position, encode(notice))
    return rows


def _write_rows(connection: Any, lineage_id: str, table: str, old: Rows, new: Rows) -> None:
    columns = COLUMNS[table]
    key_size = 1 if table in {entry[0] for entry in MAPS.values()} else 2
    keys, data = columns[:key_size + 1], columns[key_size + 1:]
    removed = old.keys() - new.keys()
    if removed:
        connection.executemany(
            f"DELETE FROM {table} WHERE " + " AND ".join(f"{column}=%s" for column in keys),
            [(lineage_id, *key) for key in removed],
        )
    changed = [(lineage_id, *key, *payload) for key, payload in new.items() if old.get(key) != payload]
    if changed:
        connection.executemany(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('%s' for _ in columns)}) "
            f"ON CONFLICT({','.join(keys)}) DO UPDATE SET " + ','.join(f"{column}=excluded.{column}" for column in data),
            changed,
        )


class _Objects(MutableMapping):
    """One collection of a Lineage's Program work; each object loads and locks on first use."""

    def __init__(self, work: "ProgramWork", field: str) -> None:
        self._work = work
        self.field = field
        self.data: dict[str, Any] = {}
        self.loaded: set[str] = set()
        self.complete = False

    def _key(self, key: Any) -> str:
        clean = str(key)
        if clean not in self.loaded:
            self._work._load(self.field, [clean])
        return clean

    def __getitem__(self, key: Any) -> Any:
        return self.data[self._key(key)]

    def __setitem__(self, key: Any, value: Any) -> None:
        self.data[self._key(key)] = value

    def __delitem__(self, key: Any) -> None:
        del self.data[self._key(key)]

    def __contains__(self, key: object) -> bool:
        return self._key(key) in self.data

    def __iter__(self) -> Iterator[str]:
        self._work._load_all(self.field)
        return iter(list(self.data))

    def __len__(self) -> int:
        self._work._load_all(self.field)
        return len(self.data)

    def __repr__(self) -> str:
        return f"<Program {self.field} of {self._work.lineage_id}: {sorted(self.loaded)} loaded>"


class ProgramWork(MutableMapping):
    """A Lineage's Program work inside one transaction.

    It reads like ``read()``'s mapping, but an object is read, and locked for
    the rest of the transaction, only when the transaction first touches it;
    iterating a collection loads all of its objects.  ``normalize(field, key,
    value)`` is applied to each object as it loads and before it is written;
    returning ``None`` removes it.  After the transaction ``changed`` tells
    whether any normalized object changed.
    """

    def __init__(self, connection: Any, lineage_id: str, normalize: Callable[[str, str, Any], Any] | None = None) -> None:
        self._connection = connection
        self.lineage_id = lineage_id
        self._normalize = normalize or (lambda _field, _key, value: value)
        self._objects = {field: _Objects(self, field) for field in ITEM_FIELDS}
        self._expired: list[Any] | None = None
        self._stored: dict[tuple[str, str], Rows] = {}
        self._signatures: dict[tuple[str, str], str | None] = {}
        self._held: tuple[int, str] = (-1, "")
        self.changed = False

    def _lock(self, field: str, keys: Iterable[str]) -> None:
        scopes = sorted(f"{self.lineage_id}:{field}:{key}" for key in keys)
        if not scopes:
            return
        rank = LOCK_ORDER.index(field)
        if (rank, scopes[0]) < self._held:
            LOGGER.warning(
                "Program work locked out of order: %s after %s", scopes[0], self._held[1], stack_info=True,
            )
        self._held = max(self._held, (rank, scopes[-1]))
        self._connection.execute(
            "SELECT count(pg_advisory_xact_lock(hashtextextended(current_schema() || ':program-work:' || scope, 0))) "
            "FROM (SELECT scope FROM unnest(%s::text[]) AS scope ORDER BY scope COLLATE \"C\") ordered",
            (scopes,),
        ).fetchone()

    def _load(self, field: str, keys: Iterable[str]) -> None:
        objects = self._objects[field]
        keys = sorted({key for key in keys if key not in objects.loaded})
        if not keys:
            return
        self._lock(field, keys)
        raw: dict[str, Any] = {}
        if field == "event_wires":
            for row in self._connection.execute(
                "SELECT wire_id, delivery_json FROM pending_event_deliveries "
                "WHERE lineage_id=%s AND wire_id = ANY(%s) ORDER BY wire_id, position",
                (self.lineage_id, keys),
            ).fetchall():
                raw.setdefault(row["wire_id"], {"pending_events": []})["pending_events"].append(json_value(row["delivery_json"]))
        else:
            table, column = MAPS[field]
            for row in self._connection.execute(
                f"SELECT {column} AS key, checkpoint_json FROM {table} WHERE lineage_id=%s AND {column} = ANY(%s)",
                (self.lineage_id, keys),
            ).fetchall():
                raw[row["key"]] = json_value(row["checkpoint_json"])
        for key in keys:
            stored = raw.get(key)
            self._stored[(field, key)] = _item_rows(field, key, stored)
            value = None if stored is None else self._normalize(field, key, stored)
            self._signatures[(field, key)] = None if value is None else encode(value)
            objects.loaded.add(key)
            if value is not None:
                objects.data[key] = value

    def _load_all(self, field: str, also: Iterable[str] = ()) -> None:
        objects = self._objects[field]
        if objects.complete:
            self._load(field, also)
            return
        column = "wire_id" if field == "event_wires" else MAPS[field][1]
        self._load(field, [*also, *(row["key"] for row in self._connection.execute(
            f"SELECT DISTINCT {column} AS key FROM {_table(field)} WHERE lineage_id=%s", (self.lineage_id,),
        ).fetchall())])
        objects.complete = True

    def _expired_deliveries(self) -> list[Any]:
        if self._expired is None:
            self._lock("expired_deliveries", [""])
            stored = [json_value(row["notice_json"]) for row in self._connection.execute(
                "SELECT notice_json FROM pending_expiry_notices WHERE lineage_id=%s ORDER BY position, wire_id, cursor",
                (self.lineage_id,),
            ).fetchall()]
            self._stored[("expired_deliveries", "")] = _expired_rows(stored)
            value = self._normalize("expired_deliveries", "", stored) if stored else []
            self._signatures[("expired_deliveries", "")] = encode(value)
            self._expired = value
        return self._expired

    def __getitem__(self, field: str) -> Any:
        if field == "expired_deliveries":
            return self._expired_deliveries()
        return self._objects[field]

    def __setitem__(self, field: str, value: Any) -> None:
        if field == "expired_deliveries":
            self._expired_deliveries()
            self._expired = list(value or [])
            return
        objects = self._objects[field]
        replacement = {str(key): item for key, item in dict(value or {}).items()}
        # Existing and new objects are locked together, in order.
        self._load_all(field, also=replacement)
        objects.data = replacement

    def __delitem__(self, field: str) -> None:
        raise TypeError("Program work collections cannot be removed")

    def __iter__(self) -> Iterator[str]:
        return iter(LOCK_ORDER)

    def __len__(self) -> int:
        return len(LOCK_ORDER)

    def flush(self) -> None:
        """Write the objects this transaction changed."""
        for field in ITEM_FIELDS:
            objects = self._objects[field]
            old: Rows = {}
            new: Rows = {}
            for key in sorted(objects.loaded):
                value = objects.data.get(key)
                value = None if value is None else self._normalize(field, key, value)
                if value is None:
                    objects.data.pop(key, None)
                else:
                    objects.data[key] = value
                if (None if value is None else encode(value)) != self._signatures[(field, key)]:
                    self.changed = True
                old.update(self._stored[(field, key)])
                new.update(_item_rows(field, key, value))
            _write_rows(self._connection, self.lineage_id, _table(field), old, new)
        if self._expired is not None:
            value = self._normalize("expired_deliveries", "", self._expired) if self._expired else []
            self._expired = value
            if encode(value) != self._signatures[("expired_deliveries", "")]:
                self.changed = True
            _write_rows(
                self._connection, self.lineage_id, "pending_expiry_notices",
                self._stored[("expired_deliveries", "")], _expired_rows(value),
            )


@contextmanager
def transaction(
    database: Any, lineage_id: str, *, normalize: Callable[[str, str, Any], Any] | None = None,
) -> Iterator[ProgramWork]:
    with database.transaction() as connection:
        work = ProgramWork(connection, lineage_id, normalize)
        yield work
        work.flush()
