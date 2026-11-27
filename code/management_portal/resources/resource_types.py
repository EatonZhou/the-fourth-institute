"""Resource type declarations and deployment state, owned by ResourceService."""
from __future__ import annotations

import threading
from functools import cache
from importlib.util import find_spec
from typing import Any, Iterable

from management_common.distribution_features import included_features
from management_common.portal_database import portal_database
from management_common.resource_identity import get_resource_type_registry
from .errors import ResourceOperationError


class ResourceTypeError(ResourceOperationError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(code, message, 409 if code == 'instruction.version_conflict' else 400, details=details)


def deployed_units() -> frozenset[str]:
    """Units this package ships: every provider module of the unit is present.

    A package that leaves a feature out leaves out its provider modules, so the
    unit's types are not installed. A missing provider of ``core`` or of a unit
    the package lists as included is a broken installation and fails loudly.
    """
    declarations = get_resource_type_registry().declarations.values()
    return _shipped_units(tuple(sorted((str(d['unit']), str(d['provider'])) for d in declarations)),
                          tuple(included_features() or ()))


@cache
def _shipped_units(providers: tuple[tuple[str, str], ...], included: tuple[str, ...]) -> frozenset[str]:
    units = {unit for unit, _provider in providers}
    for unit, provider in providers:
        module = provider.partition(':')[0]
        if not module or unit not in units or find_spec(module) is not None:
            continue
        if unit == 'core' or unit in included:
            raise ModuleNotFoundError(f'resource provider {module!r} of unit {unit!r} is missing', name=module)
        units.discard(unit)
    return frozenset(units)


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
        self._require_state(kind, row, registration=registration)

    def require_many(self, kinds: Iterable[str], *, registration: bool = False) -> None:
        kinds = tuple(dict.fromkeys(kinds))
        for kind in kinds:
            self.identity.declaration(kind)
        rows = self.all() if kinds else {}
        for kind in kinds:
            self._require_state(kind, rows[kind], registration=registration)

    @staticmethod
    def _require_state(kind: str, row: dict, *, registration: bool) -> None:
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


