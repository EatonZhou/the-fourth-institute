"""Stored rows of the prepared Resident inputs and the approval each may carry."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from management_common.portal_database import _database_schema


@dataclass(frozen=True)
class Approval:
    route: tuple[str, ...]
    credentials: tuple[tuple[str, str], ...]

    @property
    def recipient(self) -> str:
        return self.route[-1]

    def next_hop(self, resident_id: str) -> str:
        if resident_id not in self.route[:-1]:
            return ""
        return self.route[self.route.index(resident_id) + 1]

    def stored(self) -> str:
        return json.dumps([self.route, self.credentials], separators=(",", ":"))


def ensure_schema(connection: Any) -> None:
    exists = connection.execute(
        "SELECT to_regclass(quote_ident(current_schema()) || '.resident_prepared_inputs') IS NOT NULL AS present"
    ).fetchone()["present"]
    if not exists:
        connection.execute("""
            CREATE TABLE resident_prepared_inputs (
                message_id TEXT PRIMARY KEY,
                resident_id TEXT NOT NULL,
                issuer TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                approval_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL CHECK (status IN ('prepared', 'released', 'cancelled')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                settled_at TIMESTAMPTZ
            )
        """)
    elif not connection.execute("""
        SELECT 1 FROM pg_attribute WHERE attrelid='resident_prepared_inputs'::regclass
        AND attname='approval_json' AND NOT attisdropped
    """).fetchone():
        connection.execute("ALTER TABLE resident_prepared_inputs ADD COLUMN IF NOT EXISTS approval_json TEXT NOT NULL DEFAULT ''")


def read_row(connection: Any, message_id: str) -> Mapping[str, Any] | None:
    schema = _database_schema("lineage")
    return connection.execute(
        f'SELECT resident_id, issuer, intent_json, approval_json, status FROM "{schema}".resident_prepared_inputs WHERE message_id=%s',
        (message_id,),
    ).fetchone()


def stored_approval(row: Mapping[str, Any]) -> Approval | None:
    if not row["approval_json"]:
        return None
    route, credentials = json.loads(row["approval_json"])
    return Approval(tuple(route), tuple(map(tuple, credentials)))


def open_for_issuer(connection: Any, resident_id: str, message_id: str, issuer: str) -> bool:
    row = read_row(connection, message_id)
    if not row or row["issuer"] != issuer or row["status"] != "prepared":
        return False
    approval = stored_approval(row)
    return resident_id == (approval.recipient if approval else row["resident_id"])
