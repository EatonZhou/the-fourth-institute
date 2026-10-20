"""Private row mechanics for the explicitly owned Lineage work repositories.

Each repository has a fixed table and key. Callers cannot invent namespaces;
the owning module validates its document and controls its lifetime. Empty work
is removed in the same transaction as its completion record.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from copy import deepcopy
from functools import cached_property

from management_common.json_columns import json_text, json_value


TABLES = {
    "effect_work": ("activation_key", "checkpoint_json"),
    "lineage_drafts": ("draft_id", "draft_json"),
    "lineage_move_operations": ("operation_id", "operation_json"),
    "resident_creation_operations": ("creation_key", "operation_json"),
    "family_relays": ("resident_id", "relay_json"),
    "family_quota_reviews": ("resident_id", "review_json"),
    "family_quota_migrations": ("resident_id", "migration_json"),
    "resident_mailbox_progress": ("resident_id", "progress_json"),
    "lineage_shared_state": (None, "cells_json"),
    "lineage_human_notifications": (None, "notifications_json"),
    "lineage_deletion_operations": (None, "operation_json"),
    "lineage_resource_projection": (None, "projection_json"),
}
COLUMNS = {table: ("lineage_id", *((key,) if key else ()), payload)
           for table, (key, payload) in TABLES.items()}


def create_schema(connection):
    for table, (key, payload) in TABLES.items():
        keys = "lineage_id" + ("," + key if key else "")
        connection.execute(
            f"CREATE TABLE {table}(lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id), "
            + (f"{key} TEXT NOT NULL, " if key else "")
            + f"{payload} JSONB NOT NULL, PRIMARY KEY({keys}))"
        )
        if key:
            connection.execute(f"CREATE INDEX {table}_key_idx ON {table}({key})")


class WorkRepository:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.key, self.payload = TABLES[table]

    def _identity(self, lineage_id, key):
        if not lineage_id or (self.key and not key) or (not self.key and key is not None):
            raise ValueError(f"Invalid {self.table} identity")
        return (lineage_id, key) if self.key else (lineage_id,)

    @property
    def _where(self):
        return "lineage_id=%s" + (f" AND {self.key}=%s" if self.key else "")

    def get(self, lineage_id, key=None):
        with self.database.read() as connection:
            row = connection.execute(f"SELECT {self.payload} FROM {self.table} WHERE {self._where}",
                                     self._identity(lineage_id, key)).fetchone()
        return json_value(row[self.payload]) if row else {}

    def list(self, lineage_id):
        if not self.key:
            raise ValueError("Singleton repository has no keyed collection")
        with self.database.read() as connection:
            rows = connection.execute(f"SELECT {self.key},{self.payload} FROM {self.table} WHERE lineage_id=%s ORDER BY {self.key}", (lineage_id,)).fetchall()
        return {row[self.key]: json_value(row[self.payload]) for row in rows}

    def by_keys(self, keys):
        if not self.key:
            raise ValueError("Singleton repository has no keyed collection")
        with self.database.read() as connection:
            rows = connection.execute(f"SELECT {self.key},{self.payload} FROM {self.table} WHERE {self.key}=ANY(%s)", (list(set(keys)),)).fetchall()
        return {row[self.key]: json_value(row[self.payload]) for row in rows}

    def lineage_id(self, key):
        if not self.key:
            raise ValueError("Singleton repository has no keyed collection")
        with self.database.read() as connection:
            rows = connection.execute(f"SELECT lineage_id FROM {self.table} WHERE {self.key}=%s", (key,)).fetchall()
        if len(rows) > 1:
            raise ValueError(f"Ambiguous {self.table} identity: {key}")
        return rows[0]["lineage_id"] if rows else ""

    def delete(self, lineage_id, key=None):
        with self.transaction(lineage_id, key) as state:
            state.clear()

    @contextmanager
    def transaction(self, lineage_id, key=None):
        identity = self._identity(lineage_id, key)
        with self.database.transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s,0))",
                               (":" + self.table + ":" + json.dumps(identity),))
            row = connection.execute(f"SELECT {self.payload} FROM {self.table} WHERE {self._where}", identity).fetchone()
            original = json_value(row[self.payload]) if row else {}
            state = deepcopy(original)
            yield state
            if state == original:
                return
            if self.table == "effect_work":
                path = state.get("trigger_path_id") or original.get("trigger_path_id")
                if path:
                    connection.execute("UPDATE trigger_paths SET revision=revision+1 WHERE trigger_path_id=%s", (path,))
            if not state:
                connection.execute(f"DELETE FROM {self.table} WHERE {self._where}", identity)
                return
            columns = COLUMNS[self.table]
            connection.execute(
                f"INSERT INTO {self.table}({','.join(columns)}) VALUES ({','.join('%s' for _ in columns)}) "
                f"ON CONFLICT({','.join(columns[:-1])}) DO UPDATE SET {self.payload}=excluded.{self.payload}",
                (*identity, json_text(state, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)),
            )


def repository(table):
    """Declare a fixed owner on LineageDataStore; no public namespace argument."""
    if table not in TABLES:
        raise ValueError(table)
    return cached_property(lambda store: WorkRepository(store.database, table))
