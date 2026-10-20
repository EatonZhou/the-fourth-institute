"""Resource type declarations and deployment state, owned by ResourceService."""
from __future__ import annotations

import threading
from typing import Any

from management_common.portal_database import portal_database
from management_common.resource_identity import get_resource_type_registry
from .errors import ResourceOperationError


class ResourceTypeError(ResourceOperationError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(code, message, 409 if code == 'instruction.version_conflict' else 400, details=details)


def deployed_units() -> frozenset[str]:
    # decision_15aab18c0ab3's deployment selection will own this boundary.
    return frozenset(str(d['unit']) for d in get_resource_type_registry().declarations.values())


class ResourceTypeStateStore:
    def __init__(self):
        self.database = portal_database(schema='resource_types')
        self._ready = False
        self._lock = threading.Lock()

    def _prepare(self):
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.database.connect() as connection:
                connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))')
                connection.execute('''CREATE TABLE IF NOT EXISTS state (
                    type TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL, version INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL)''')
                connection.commit()
            self._ready = True

    def rows(self) -> dict[str, dict[str, Any]]:
        self._prepare()
        with self.database.connect() as connection:
            return {row['type']: dict(row) for row in connection.execute('SELECT * FROM state').fetchall()}

    def update(self, kind: str, enabled: bool, expected_version: int | None, actor: str) -> dict:
        self._prepare()
        with self.database.connect() as connection:
            connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))', (kind,))
            row = connection.execute('SELECT version FROM state WHERE type = %s', (kind,)).fetchone()
            current = int(row['version']) if row else 0
            if expected_version is not None and current != expected_version:
                raise ResourceTypeError(
                    'instruction.version_conflict', f'{kind}: expected version {expected_version}, current {current}',
                    details={'object_id': kind, 'expected_version': expected_version, 'current_version': current},
                )
            row = connection.execute('''INSERT INTO state(type, enabled, version, updated_at, updated_by)
                VALUES (%s, %s, %s, CURRENT_TIMESTAMP, %s) ON CONFLICT(type) DO UPDATE SET
                enabled=excluded.enabled, version=excluded.version,
                updated_at=excluded.updated_at, updated_by=excluded.updated_by RETURNING *''',
                (kind, enabled, current + 1, actor)).fetchone()
            connection.commit()
            return dict(row)


class ResourceTypes:
    def __init__(self, *, identity=None, store=None, units=None):
        self.identity = identity or get_resource_type_registry()
        self.store = store or ResourceTypeStateStore()
        self.units = units

    def all(self) -> dict[str, dict]:
        rows = self.store.rows()
        units = deployed_units() if self.units is None else self.units
        result = {}
        for kind, declaration in self.identity.declarations.items():
            row = rows.get(kind, {})
            installed = declaration['unit'] in units
            enabled = installed and bool(row.get('enabled', declaration['enabled_by_default']))
            result[kind] = {**declaration, 'installed': installed, 'enabled': enabled,
                            'state': 'not_installed' if not installed else 'enabled' if enabled else 'disabled',
                            'version': int(row.get('version', 0))}
        return result

    def get(self, kind: str) -> dict:
        self.identity.declaration(kind)
        return self.all()[kind]

    def enabled_types(self) -> frozenset[str]:
        return frozenset(kind for kind, row in self.all().items() if row['enabled'])

    def require(self, kind: str, *, registration: bool = False) -> None:
        row = self.get(kind)
        if not row['installed'] or (registration and not row['enabled']):
            raise ResourceTypeError('resource_type.' + row['state'], f"{kind}: {row['state']}")
        if registration and not row['registerable']:
            raise ResourceTypeError('resource_type.not_registerable', f'{kind}: registration is disabled by declaration')

    def update(self, kind: str, enabled: bool, *, expected_version: int | None = None, actor: str) -> dict:
        self.require(kind)
        if type(enabled) is not bool or (expected_version is not None and (type(expected_version) is not int or expected_version < 0)):
            raise ValueError('enabled must be boolean; expected_version must be absent or a nonnegative integer')
        row = self.store.update(kind, enabled, expected_version, actor)
        return {'type': kind, 'enabled': enabled, 'state': 'enabled' if enabled else 'disabled', 'version': row['version']}


