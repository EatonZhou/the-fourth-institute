"""Resident resource publications waiting in the outbox, with their leases.

Every writer locks the Resident identity before its outbox row.
"""
from __future__ import annotations

import secrets
import time
from collections.abc import Iterable, Mapping
from typing import Any

from management_common.database_time import instant
from . import lineage_data_residents
from .lineage_data_columns import json_column, stored_object


class ResidentProjectionOutbox:
    """Queue of Resident records to publish as resources."""

    def __init__(self, database: Any) -> None:
        self.database = database

    @staticmethod
    def enqueue(
        connection: Any,
        resident_id: str,
        value: Mapping[str, Any],
    ) -> int:
        clean_id = str(resident_id or "").strip()
        payload = json_column(dict(value))
        enqueued_at = str(value.get("updated_at") or value.get("deleted_at") or "")
        connection.execute(
            "\n            INSERT INTO resident_resource_projection_outbox(\n                resident_id, revision, payload_json, enqueued_at,\n                attempt_count, available_at, lease_token, lease_until,\n                last_error\n            ) VALUES (\n                %s, 1, %s, %s, 0, 'epoch', '', NULL, ''\n            )\n            ON CONFLICT(resident_id) DO UPDATE SET\n                revision=resident_resource_projection_outbox.revision + 1,\n                payload_json=excluded.payload_json,\n                enqueued_at=excluded.enqueued_at,\n                attempt_count=0,\n                available_at='epoch',\n                lease_token='',\n                lease_until=NULL,\n                last_error=''\n            ",
            (clean_id, payload, instant(enqueued_at)),
        )
        row = connection.execute(
            'SELECT revision FROM resident_resource_projection_outbox WHERE resident_id=%s',
            (clean_id,),
        ).fetchone()
        return int(row["revision"])

    def enqueue_resident_resource_projection(
        self,
        resident_id: str,
        value: Mapping[str, Any],
    ) -> int:
        def write(connection: Any) -> int:
            return self.enqueue(
                connection,
                resident_id,
                value,
            )

        return self.database.write(write)

    def claim_resident_resource_projections(
        self,
        *,
        limit: int = 50,
        lease_seconds: float = 90.0,
        resident_id: str = "",
    ) -> list[dict[str, Any]]:
        now = time.time()
        lease_token = secrets.token_hex(16)
        batch_limit = max(1, min(int(limit), 500))
        target_resident_id = str(resident_id or "").strip()

        def write(connection: Any) -> list[dict[str, Any]]:
            selection = "resident_id = %s" if target_resident_id else "available_at <= %s"
            rows = connection.execute(
                "SELECT resident_id, revision, payload_json, attempt_count "
                "FROM resident_resource_projection_outbox "
                f"WHERE {selection} AND (lease_token='' OR lease_until <= %s) "
                "ORDER BY available_at, resident_id LIMIT %s",
                (target_resident_id or instant(now), instant(now), batch_limit),
            ).fetchall()
            if not rows:
                return []
            ids = [str(row['resident_id']) for row in rows]
            # Keep the identity -> outbox lock order used by saves and ACKs.
            # Rebuild old outstanding intents from canonical facts on retry;
            # the Resource boundary still rejects stale publication payloads.
            connection.execute(
                'SELECT resident_id FROM residents WHERE resident_id=ANY(%s) ORDER BY resident_id FOR SHARE',
                (ids,),
            ).fetchall()
            current = {record['resident_id']: record for record in lineage_data_residents.residents_from_rows(
                connection, connection.execute(
                    lineage_data_residents.resident_select('WHERE r.resident_id=ANY(%s)', include_deleted=True), (ids,),
                ).fetchall(),
            )}
            claimed: list[dict[str, Any]] = []
            for row in rows:
                resident_id = str(row["resident_id"])
                revision = int(row["revision"])
                payload = current.get(resident_id, stored_object(row['payload_json'], resident_id))
                changed = connection.execute(
                    "UPDATE resident_resource_projection_outbox SET payload_json=%s, lease_token=%s, lease_until=%s, attempt_count=attempt_count + 1 WHERE resident_id=%s AND revision=%s AND (lease_token='' OR lease_until <= %s)",
                    (
                        json_column(payload),
                        lease_token,
                        instant(now + max(1.0, float(lease_seconds))),
                        resident_id,
                        revision,
                        instant(now),
                    ),
                ).rowcount
                if not changed:
                    continue
                claimed.append({
                    "resident_id": resident_id,
                    "revision": revision,
                    "payload": payload,
                    "attempt_count": int(row["attempt_count"] or 0) + 1,
                    "lease_token": lease_token,
                })
            return claimed

        return self.database.write(write)

    def resident_resource_projection_pending(self, resident_id: str) -> bool:
        with self.database.read() as connection:
            return connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone() is not None

    def ensure_resident_registration_projection(self, resident_id: str) -> None:
        """Repair missing publication work, never manufacture an acknowledgement."""
        def write(connection: Any) -> None:
            row = connection.execute(
                lineage_data_residents.resident_select('WHERE r.resident_id=%s'), (resident_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resident_id)
            record = lineage_data_residents.resident_from_row(row)
            if record.get("deleted_at"):
                raise KeyError(resident_id)
            if record.get("registration_state") != "resource_pending":
                return
            existing = connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone()
            if existing is None:
                # Use the current identity in the same transaction as the
                # absence check; preserve any existing publisher's CAS lease.
                self.enqueue(connection, resident_id, record)

        self.database.write(write)

    def complete_resident_resource_projections(
        self,
        claims: Iterable[Mapping[str, Any]],
    ) -> dict[str, Any]:
        values = [dict(item) for item in claims]

        def write(connection: Any) -> dict[str, Any]:
            completed = 0
            ready_resident_ids: list[str] = []
            for item in values:
                resident_id = str(item.get("resident_id") or "")
                # Match the identity -> outbox lock order of registration and
                # deletion. A late publication receipt must not make a deleted
                # identity ready, even while its outbox claim is still valid.
                identity = connection.execute(
                    'SELECT deleted_at FROM residents WHERE resident_id=%s FOR SHARE',
                    (resident_id,),
                ).fetchone()
                acknowledged = connection.execute(
                    'DELETE FROM resident_resource_projection_outbox WHERE resident_id=%s AND revision=%s AND lease_token=%s',
                    (
                        resident_id,
                        int(item.get("revision") or 0),
                        str(item.get("lease_token") or ""),
                    ),
                ).rowcount
                completed += acknowledged
                if not acknowledged or identity is None or identity['deleted_at'] is not None:
                    continue
                row = connection.execute(
                    "UPDATE residents SET registration_state='ready' WHERE resident_id=%s "
                    "AND registration_state='resource_pending' RETURNING resident_id", (resident_id,),
                ).fetchone()
                if row:
                    ready_resident_ids.append(resident_id)
            return {"completed": completed, "ready_resident_ids": ready_resident_ids}

        return self.database.write(write)

    def fail_resident_resource_projections(
        self,
        claims: Iterable[Mapping[str, Any]],
        error: object,
    ) -> int:
        values = [dict(item) for item in claims]
        available_at = time.time() + min(
            60.0,
            max(1.0, max((int(item.get("attempt_count") or 1) for item in values), default=1) * 2.0),
        )
        message = f"{error.__class__.__name__}: {error}"[:1000] if isinstance(error, BaseException) else str(error)[:1000]

        def write(connection: Any) -> int:
            failed = 0
            for item in values:
                failed += connection.execute(
                    "UPDATE resident_resource_projection_outbox SET available_at=%s, lease_token='', lease_until=NULL, last_error=%s WHERE resident_id=%s AND revision=%s AND lease_token=%s",
                    (
                        instant(available_at),
                        message,
                        str(item.get("resident_id") or ""),
                        int(item.get("revision") or 0),
                        str(item.get("lease_token") or ""),
                    ),
                ).rowcount
            return failed

        return self.database.write(write)

    def resident_resource_projection_health(self) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS pending_count, "
                "COALESCE(MAX(attempt_count), 0) AS max_attempt_count, "
                "COALESCE(MAX(last_error), '') AS last_error "
                "FROM resident_resource_projection_outbox"
            ).fetchone()
        return {
            "pending_count": int(row["pending_count"] or 0),
            "max_attempt_count": int(row["max_attempt_count"] or 0),
            "last_error": str(row["last_error"] or ""),
        }
