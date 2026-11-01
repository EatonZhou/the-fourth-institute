from __future__ import annotations

import hashlib
import json
import threading
from contextlib import nullcontext
from uuid import uuid4
from collections.abc import Callable, Mapping
from typing import Any

from management_common.portal_database import portal_database


def initialize_registration_schema(connection, schema=None):
    """Initialize the current schema for explicit registration request identities."""
    table = f'"{schema}".resource_registration_operations' if schema else 'resource_registration_operations'
    connection.execute(f'''CREATE TABLE IF NOT EXISTS {table} (
        operation_id TEXT PRIMARY KEY,
        fingerprint TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        actor TEXT NOT NULL,
        resource_id TEXT NOT NULL UNIQUE,
        scope_id TEXT NOT NULL,
        completed BOOLEAN NOT NULL DEFAULT FALSE,
        deleted BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')


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
        initialize_registration_schema(connection)

    def _prepare_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            # Only the one-time DDL needs a schema-wide lock. Registrations
            # for different operations must not serialize behind it.
            with self._database.connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))"
                )
                self._ensure_schema(connection)
                connection.commit()
            self._schema_ready = True

    def existing(self, resource_type, payload, *, actor, operation_id):
        """Resolve an explicit creation retry before domain-level deduplication."""
        if not operation_id:
            return None
        self._prepare_schema()
        key = hashlib.sha256((actor + '\0' + operation_id).encode()).hexdigest()
        with self._database.read() as connection:
            row = connection.execute(
                'SELECT resource_id, fingerprint, deleted, scope_id, completed '
                'FROM resource_registration_operations WHERE operation_id=%s', (key,),
            ).fetchone()
        if row is None:
            return None
        self._validate(row, self._fingerprint(resource_type, payload, actor=actor))
        return str(row['resource_id'])

    @staticmethod
    def _validate(row, fingerprint):
        from management_common.resource_lifecycle import current_lifecycle
        if row['fingerprint'] != fingerprint:
            raise ValueError('Registration operation ID was already used with different arguments')
        if row['deleted']:
            raise KeyError(row['resource_id'])
        if not row['completed'] and row['scope_id'] != current_lifecycle():
            raise ValueError('Unfinished registration belongs to another resource lifecycle')

    def reserve(
        self,
        resource_type: str,
        payload: Mapping[str, Any],
        *,
        actor: str,
        allocate: Callable[[], str],
        operation_id: str = "",
    ) -> tuple[str, bool]:
        fingerprint = self._fingerprint(resource_type, payload, actor=actor)
        operation_id = hashlib.sha256((actor + '\0' + (operation_id or uuid4().hex)).encode()).hexdigest()
        self._prepare_schema()
        from management_common.resource_lifecycle import current_lifecycle
        with self._database.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s,0))',
                               (':resource-registration:' + operation_id,))
            row = connection.execute(
                'SELECT resource_id, fingerprint, deleted, scope_id, completed FROM resource_registration_operations WHERE operation_id = %s',
                (operation_id,),
            ).fetchone()
            created = row is None
            if row is not None:
                self._validate(row, fingerprint)
            else:
                resource_id = allocate()
                row = connection.execute(
                    'INSERT INTO resource_registration_operations '
                    '(operation_id, fingerprint, resource_type, actor, resource_id, scope_id) '
                    'VALUES (%s,%s,%s,%s,%s,%s) RETURNING resource_id',
                    (operation_id, fingerprint, resource_type, actor, resource_id, current_lifecycle()),
                ).fetchone()
            connection.commit()
            return str(row[0]), created

    def complete(self, resource_id: str, *, connection=None) -> None:
        """Preserve the original scope for providers without a filesystem intent.

        Directory creators already enlist this allocation in their owner
        transaction. The conflict-safe insertion here does not transfer it.
        """
        from management_common.resource_lifecycle import LifecycleStore
        lifecycle = LifecycleStore()
        self._prepare_schema()
        table = f'"{self._database.schema}".resource_registration_operations'
        with nullcontext(connection) if connection is not None else self._database.connect() as conn:
            row = conn.execute(f'SELECT scope_id, completed, deleted FROM {table} '
                               'WHERE resource_id=%s FOR UPDATE', (resource_id,)).fetchone()
            if row is None:
                return
            if row['deleted']:
                raise KeyError(resource_id)
            if row['completed']:
                return
            if row['scope_id']:
                conn.execute(f'INSERT INTO "{lifecycle.db.schema}".allocations(resource_id,scope_id) '
                             'VALUES (%s,%s) ON CONFLICT DO NOTHING', (resource_id, row['scope_id']))
            conn.execute(f'UPDATE {table} SET completed=TRUE WHERE resource_id=%s', (resource_id,))

    def forget(self, resource_id: str) -> None:
        clean_id = str(resource_id or "").strip()
        if not clean_id:
            return
        self._prepare_schema()
        with self._database.connect() as connection:
            connection.execute(
                'UPDATE resource_registration_operations SET deleted=TRUE WHERE resource_id = %s',
                (clean_id,),
            )
            connection.commit()
