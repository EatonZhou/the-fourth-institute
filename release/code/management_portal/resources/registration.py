from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from typing import Any

from management_common.portal_database import portal_database


class ResourceRegistrationStore:
    """ResourceService-owned idempotency ledger for resource creation."""

    def __init__(self) -> None:
        from .config import resource_database_schema

        self._database = portal_database(schema=resource_database_schema())
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    @staticmethod
    def _fingerprint(
        resource_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
    ) -> str:
        try:
            canonical = json.dumps(
                {
                    "actor": actor,
                    "payload": dict(payload),
                    "resource_type": resource_type,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "service-managed resource registration payload must be JSON-compatible"
            ) from exc
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _ensure_schema(connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS resource_registrations (
                fingerprint TEXT PRIMARY KEY,
                resource_type TEXT NOT NULL,
                actor TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    def _prepare_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            # Only the one-time DDL needs a schema-wide lock. Registrations
            # for different fingerprints must not serialize behind it.
            with self._database.connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))"
                )
                self._ensure_schema(connection)
                connection.commit()
            self._schema_ready = True

    def reserve(
        self,
        resource_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        allocate: Callable[[], str],
    ) -> tuple[str, bool]:
        fingerprint = self._fingerprint(resource_type, payload, actor=actor)
        self._prepare_schema()
        resource_id = allocate()
        with self._database.connect() as connection:
            row = connection.execute(
                '\n                INSERT INTO resource_registrations (\n                    fingerprint, resource_type, actor, resource_id\n                ) VALUES (%s, %s, %s, %s)\n                ON CONFLICT (fingerprint) DO NOTHING\n                RETURNING resource_id\n                ',
                (fingerprint, resource_type, actor, resource_id),
            ).fetchone()
            created = row is not None
            if row is None:
                # ON CONFLICT waits for the competing transaction to finish;
                # a new statement then sees its committed ID.
                row = connection.execute(
                    'SELECT resource_id FROM resource_registrations WHERE fingerprint = %s',
                    (fingerprint,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("registration reservation disappeared")
            connection.commit()
            return str(row[0]), created

    def forget(self, resource_id: str) -> None:
        clean_id = str(resource_id or "").strip()
        if not clean_id:
            return
        self._prepare_schema()
        with self._database.connect() as connection:
            connection.execute(
                'DELETE FROM resource_registrations WHERE resource_id = %s',
                (clean_id,),
            )
            connection.commit()
