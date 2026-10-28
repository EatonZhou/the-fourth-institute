"""Runtime of each Lineage's graph objects: header columns and per-edge state.

Writers hold the Lineage row (``lock_live_lineage``) for their transaction,
in the same order as definition writes.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from management_common.database_time import instant, iso_time
from . import graph_storage, lineage_state_domains, relation_work_storage


RUNTIME_STRUCTURAL_FIELDS = frozenset({
    "nodes",
    "edges",
})
RUNTIME_COLUMN_FIELDS = frozenset({
    "schema",
    "lineage_id",
    "runtime_revision",
    "updated_at",
})


def _runtime_objects(value: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    objects: dict[tuple[str, str], dict[str, Any]] = {}
    meta = {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in RUNTIME_STRUCTURAL_FIELDS and key not in RUNTIME_COLUMN_FIELDS
    }
    if meta:
        objects[("meta", "root")] = meta
    for namespace in ("edges",):
        for object_id, state in (value.get(namespace) or {}).items():
            objects[(namespace[:-1], str(object_id))] = deepcopy(dict(state))
    return objects


def _runtime_from_objects(
    base: Mapping[str, Any],
    objects: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    value = {
        "schema": str(base.get("schema") or ""),
        "lineage_id": str(base.get("lineage_id") or ""),
        "runtime_revision": int(base.get("runtime_revision") or 0),
    }
    if str(base.get("updated_at") or ""):
        value["updated_at"] = str(base["updated_at"])
    value.update(deepcopy(dict(objects.get(("meta", "root"), {}))))
    for namespace in ("node", "edge"):
        rows = {
            object_id: deepcopy(dict(state))
            for (kind, object_id), state in objects.items()
            if kind == namespace
        }
        if rows:
            value[f"{namespace}s"] = rows
    return value


def lock_live_lineage(connection: Any, lineage_id: str) -> None:
    """Hold the Lineage row for this transaction; a deleted Lineage takes no more writes."""

    from .state_store import RuntimeCASConflict

    row = connection.execute('SELECT deleted_at FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,)).fetchone()
    if row is not None and row['deleted_at'] is not None:
        raise RuntimeCASConflict('Lineage has been deleted')


def runtime_from_connection(connection: Any, lineage_id: str) -> dict[str, Any]:
    header = connection.execute('SELECT runtime_revision,runtime_updated_at FROM lineages WHERE lineage_id=%s', (lineage_id,)).fetchone()
    if header is None:
        return {}
    return {'schema': lineage_state_domains.RUNTIME_STATE_SCHEMA, 'lineage_id': lineage_id,
            'runtime_revision': int(header['runtime_revision'] or 0), 'updated_at': iso_time(header['runtime_updated_at']) or '',
            'nodes': relation_work_storage.resident_observations(connection, lineage_id),
            'edges': relation_work_storage.read(connection, lineage_id)}


def _write_runtime_header(connection, lineage_id, value):
    unknown = set(value) - RUNTIME_STRUCTURAL_FIELDS - RUNTIME_COLUMN_FIELDS
    if unknown:
        raise ValueError('Unknown runtime projection fields: ' + ', '.join(sorted(unknown)))
    connection.execute('UPDATE lineages SET runtime_revision=%s,runtime_updated_at=%s WHERE lineage_id=%s',
                       (int(value.get('runtime_revision') or 0), instant(value.get('updated_at')), lineage_id))


def _replace_runtime_objects(connection, lineage_id, value):
    lock_live_lineage(connection, lineage_id)
    active = graph_storage.active_objects(connection, lineage_id)
    after = {key: state for key,state in (value.get('edges') or {}).items()
             if key in active or isinstance(state.get('absorption'), dict)}
    relation_work_storage.put(connection, lineage_id, relation_work_storage.read(connection,lineage_id), after)


def write_runtime(
    connection: Any,
    lineage_id: str,
    value: Mapping[str, Any],
) -> None:
    _write_runtime_header(connection, lineage_id, value)
    _replace_runtime_objects(connection, lineage_id, value)


class LineageRuntimes:
    """Writes of Lineage runtime state."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def put_lineage_runtime(self, lineage_id: str, value: Mapping[str, Any]) -> None:
        self.database.write(
            lambda connection: write_runtime(connection, lineage_id, value)
        )

    def put_lineage_runtime_changes(
        self,
        lineage_id: str,
        base: Mapping[str, Any],
        candidate: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Apply the runtime objects a definition write changed onto the committed runtime.

        The write's other runtime objects keep their committed state; an
        object whose graph node no longer exists is not written.
        """

        base_objects = _runtime_objects(base)
        candidate_objects = _runtime_objects(candidate)
        changed = {
            key
            for key in set(base_objects) | set(candidate_objects)
            if base_objects.get(key) != candidate_objects.get(key)
        }

        def write(connection: Any) -> dict[str, Any]:
            lock_live_lineage(connection, lineage_id)
            current = runtime_from_connection(connection, lineage_id)
            if not changed:
                return current
            merged_objects = _runtime_objects(current)
            for key in changed:
                if key in candidate_objects:
                    merged_objects[key] = deepcopy(candidate_objects[key])
                else:
                    merged_objects.pop(key, None)
            merged = _runtime_from_objects(current, merged_objects)
            merged["schema"] = str(candidate.get("schema") or current.get("schema") or "")
            merged["lineage_id"] = lineage_id
            merged["runtime_revision"] = int(current.get("runtime_revision") or 0) + 1
            merged["updated_at"] = str(candidate.get("updated_at") or current.get("updated_at") or "")
            write_runtime(connection, lineage_id, merged)
            return merged

        return self.database.write(write)

    def put_lineage_runtime_cas(self, lineage_id, expected, candidate):
        before = expected.get('edges') or {}
        after = candidate.get('edges') or {}
        changed = {key for key in set(before) | set(after) if before.get(key) != after.get(key)}
        from .state_store import RuntimeCASConflict

        def write(connection):
            lock_live_lineage(connection, lineage_id)
            current = runtime_from_connection(connection, lineage_id)
            if not changed:
                return current
            edges = current['edges']
            active = graph_storage.active_objects(connection, lineage_id)
            for key in changed:
                if key in after and key not in active and not isinstance(after[key].get('absorption'),dict):
                    raise RuntimeCASConflict('graph object is deleted or moved: edge/' + key)
                if edges.get(key) != before.get(key) and not (key in active and key not in edges):
                    raise RuntimeCASConflict('runtime object changed: edge/' + key)
            merged = deepcopy(edges)
            for key in changed:
                if key in after:
                    merged[key] = deepcopy(after[key])
                else:
                    merged.pop(key,None)
            current.update(edges=merged, runtime_revision=current['runtime_revision']+1, updated_at=str(candidate.get('updated_at') or ''))
            relation_work_storage.put(connection,lineage_id,edges,merged)
            _write_runtime_header(connection,lineage_id,current)
            return current
        return self.database.write(write)
