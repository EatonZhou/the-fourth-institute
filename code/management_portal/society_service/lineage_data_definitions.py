"""Lineage definitions: their rows, layout positions, and the queries over them."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any

from management_common.database_time import iso_time
from .read_snapshot import request_cached
from . import (
    definition_collections, family_storage, graph_storage, lineage_data_runtime, lineage_metadata_storage,
    lineage_state_domains, program_work_storage, trigger_path_storage, wire_delivery_storage,
)
from .lineage_data_columns import now_iso_millis
from .lineage_data_runtime import RuntimeCASConflict


def _graph_objects(value: Mapping[str, Any]) -> dict[str, str]:
    objects: dict[str, str] = {}
    for kind, collection in (
        ("resident", value.get("nodes")),
        ("edge", value.get("edges")),
    ):
        for raw in collection or []:
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip():
                objects[str(raw["id"])] = kind
    program = value.get("program") if isinstance(value.get("program"), Mapping) else {}
    for kind, collection in (
        ("component", program.get("components")),
        ("wire", program.get("wires")),
        ("function_block", value.get("function_blocks")),
    ):
        for raw in collection or []:
            if isinstance(raw, Mapping) and str(raw.get("id") or "").strip():
                objects[str(raw["id"])] = kind
    return objects


def _layout_positions(value: Mapping[str, Any]) -> dict[str, tuple[str, float, float]]:
    positions: dict[str, tuple[str, float, float]] = {}
    graph_objects = _graph_objects(value)
    collections: list[object] = [
        value.get("nodes"),
    ]
    program = value.get("program") if isinstance(value.get("program"), Mapping) else {}
    collections.append(program.get("components"))
    for collection in collections:
        for raw in collection or []:
            if not isinstance(raw, Mapping):
                continue
            object_id = str(raw.get("id") or "").strip()
            if not object_id or object_id not in graph_objects:
                continue
            positions[object_id] = (
                graph_objects[object_id],
                float(raw.get("x") or 0),
                float(raw.get("y") or 0),
            )
    return positions


def _definitions_from_connection(connection: Any, lineage_ids: list[str] | None = None) -> list[dict[str, Any]]:
    headers = lineage_metadata_storage.read_many(connection, lineage_ids)
    ids = [row['lineage_id'] for row in headers]
    graphs = graph_storage.read_many(connection, ids)
    collections = definition_collections.read_many(connection, ids)
    family_storage.hydrate_programs(connection, graphs)
    positions = {(r['lineage_id'],r['object_id']): (r['x'],r['y']) for r in connection.execute(
        'SELECT * FROM lineage_layout_positions WHERE lineage_id=ANY(%s)', (ids,)).fetchall()}
    result = []
    for header in headers:
        lid = header['lineage_id']
        value = {**header, **graphs[lid], **collections[lid]}
        for items in (value['nodes'],value['program']['components']):
            for item in items:
                position = positions.get((lid,item['id']))
                if position is not None:
                    item['x'],item['y'] = position
        result.append(lineage_state_domains.definition_projection(value))
    return result


def definition_from_connection(connection: Any, lineage_id: str) -> dict[str, Any]:
    values = _definitions_from_connection(connection, [lineage_id])
    if not values:
        raise KeyError(lineage_id)
    return values[0]


def write_definition(
    connection: Any,
    lineage_id: str,
    value: MutableMapping[str, Any],
) -> set[str]:
    """Write one Lineage definition; return the Projects whose graph it may change."""
    now = str(value.get("updated_at") or now_iso_millis())
    lineage_data_runtime.lock_live_lineage(connection, lineage_id)
    _appoint_missing_leader(connection, value)
    desired = _graph_objects(value)
    current = graph_storage.active_objects(connection, lineage_id)
    from management_portal.resources.retired_identity_boundary import require_live_identity
    require_live_identity(connection, lineage_id)
    previous_rows = lineage_metadata_storage.read_many(connection, [lineage_id], include_deleted=True)
    previous_row = previous_rows[0] if previous_rows else None
    lineage_metadata_storage.put(connection, lineage_id, dict(value))
    family_storage.store_program(connection, lineage_id, value, now)
    graph_storage.put(connection, lineage_id, value)
    graph_storage.retire_missing(connection, lineage_id, set(current), set(desired), now)
    definition_collections.put(connection, lineage_id, value)
    connection.execute('DELETE FROM lineage_layout_positions WHERE lineage_id=%s AND NOT (object_id=ANY(%s))', (lineage_id,list(desired)))
    connection.execute("DELETE FROM workspace_handoff_work WHERE lineage_id=%s AND NOT (edge_id=ANY(%s))", (lineage_id,list(desired)))
    initialize_layout_positions(connection, lineage_id, value)
    return _hierarchy_projects(previous_row, value)


def _appoint_missing_leader(connection: Any, value: MutableMapping[str, Any]) -> None:
    """A Lineage with a Resident always has a Leader (decision_6fc653d55c79).

    An empty seat goes to the earliest-created member; Function Block slot
    Residents only when no ordinary member exists, operator executors never.
    A Project-level Lineage keeps its own stricter Leader rule.
    """
    if str(value.get("leader_resident_id") or "") or (
        (value.get("principal_hierarchy") or {}).get("level") == "project"
    ):
        return
    nodes = {
        str(node.get("id") or ""): node
        for node in value.get("nodes") or []
        if isinstance(node, Mapping)
    }
    if not nodes:
        return
    # The nodes being written are the members: a Resident moving in is
    # still registered in its old Lineage, and one being created has no
    # record yet; it is the newest member.
    rows = connection.execute(
        'SELECT resident_id, deleted_at FROM residents WHERE resident_id = ANY(%s) '
        'ORDER BY created_at, resident_id',
        (sorted(nodes),),
    ).fetchall()
    registered = [str(row["resident_id"]) for row in rows]
    deleted = {str(row["resident_id"]) for row in rows if row["deleted_at"]}
    members = [
        resident_id
        for resident_id in [*registered, *(item for item in nodes if item not in registered)]
        if resident_id not in deleted
        and not isinstance(nodes[resident_id].get("operator_executor"), dict)
    ]
    owned = {e["id"] for b in value.get("function_blocks") or [] for e in b.get("elements") or []}
    ordinary = [item for item in members if item not in owned]
    if ordinary or members:
        value["leader_resident_id"] = (ordinary or members)[0]


def initialize_layout_positions(connection: Any, lineage_id: str, value: Mapping[str, Any]) -> None:
    positions = [(lineage_id, object_id, x, y)
                 for object_id, (_kind, x, y) in _layout_positions(value).items()]
    if positions:
        connection.executemany(
            'INSERT INTO lineage_layout_positions(lineage_id,object_id,x,y) VALUES (%s,%s,%s,%s) '
            'ON CONFLICT(lineage_id,object_id) DO NOTHING', positions,
        )


def _hierarchy_projects(previous: Any | None, value: Mapping[str, Any]) -> set[str]:
    """Projects whose principal graph a Lineage definition write can change.

    A write changes a Project graph when it writes the graph itself or
    changes what makes the Lineage contribute to one: its Project, Leader,
    membership or hierarchy level, including creation.
    """
    after = (
        str(value.get("project_id") or ""),
        str(value.get("leader_resident_id") or ""),
        str(value.get("lineage_membership") or ""),
        str((value.get("principal_hierarchy") or {}).get("level") or ""),
    )
    before = None
    if previous is not None:
        hierarchy = previous.get("principal_hierarchy")
        before = (
            str(previous["project_id"]),
            str(previous["leader_resident_id"]),
            str(previous["lineage_membership"]),
            str((hierarchy if isinstance(hierarchy, dict) else {}).get("level") or ""),
        )
    if before == after and after[3] != "project":
        return set()
    return {after[0], *(() if before is None else (before[0],))}


class LineageDefinitions:
    """Reads and writes of Lineage definitions.

    A write that can change a Project's principal graph re-projects it through
    ``project_graphs`` in the same transaction.
    """

    def __init__(self, database: Any, *, project_graphs: Any, deletions: Any) -> None:
        self.database = database
        self.project_graphs = project_graphs
        self.deletions = deletions

    def lineage_listing_records(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return lineage_metadata_storage.read_many(connection)

    def lineage_listing_graph_summaries(self, lineage_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(lineage_ids))
        result = {lid: {'lineage_node_ids': [], 'node_count': 0, 'edge_count': 0, 'active_seed_count': 0, 'statuses': {}} for lid in ids}
        if not ids:
            return result
        with self.database.read_transaction() as connection:
            rows = connection.execute("""SELECT lineage_id,node_id FROM lineage_nodes WHERE lineage_id=ANY(%s) AND node_type='resident'
                AND EXISTS (SELECT 1 FROM residents r WHERE r.resident_id=node_id AND r.deleted_at IS NULL)
                UNION SELECT lineage_id,node_id FROM lineage_node_references WHERE lineage_id=ANY(%s) ORDER BY lineage_id,node_id""", (ids,ids)).fetchall()
            for row in rows:
                result[row['lineage_id']]['lineage_node_ids'].append(row['node_id'])
            for lid, summary in result.items():
                summary['node_count'] = len(summary['lineage_node_ids'])
                summary['statuses'] = {'idle': summary['node_count']} if summary['node_count'] else {}
            for row in connection.execute("""SELECT e.lineage_id,COUNT(*) n FROM lineage_edges e
                WHERE e.deleted_at IS NULL AND e.lineage_id=ANY(%s) AND e.edge_type=ANY(%s) GROUP BY e.lineage_id""", (ids,list(graph_storage.RELATION_TYPES))).fetchall():
                result[row['lineage_id']]['edge_count'] = int(row['n'])
            for row in connection.execute("""SELECT n.lineage_id,COUNT(*) n FROM lineage_nodes n JOIN lineage_components c USING(node_id)
                WHERE n.deleted_at IS NULL AND n.lineage_id=ANY(%s) AND n.node_type='seed' AND COALESCE(c.config_json::jsonb->>'status','') IN ('active','','0') GROUP BY n.lineage_id""", (ids,)).fetchall():
                result[row['lineage_id']]['active_seed_count'] = int(row['n'])
        return result

    def all_lineages(self) -> list[dict[str, Any]]:
        with self.database.read_transaction() as connection:
            return _definitions_from_connection(connection)

    def lineage_definition(self, lineage_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return definition_from_connection(connection, lineage_id)

    def lock_lineage_definition(self, lineage_id: str) -> dict[str, Any] | None:
        """The committed definition, its Lineage row held until this transaction ends.

        Called inside the transaction that commits a definition write, so
        writes on one Lineage merge one after another for the duration of
        their commit only; None when the Lineage does not exist yet.
        """

        def read(connection: Any) -> dict[str, Any] | None:
            if connection.execute(
                'SELECT 1 FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE', (lineage_id,),
            ).fetchone() is None:
                return None
            return definition_from_connection(connection, lineage_id)

        return self.database.write(read)

    @request_cached
    def lineage_id_for_object(self, object_id: str) -> str:
        """Lineage that currently holds a graph object (component, wire, edge or block)."""

        with self.database.read() as connection:
            row = connection.execute(
                "SELECT n.lineage_id FROM lineage_nodes n LEFT JOIN residents r ON r.resident_id=n.node_id WHERE n.node_id=%s AND n.deleted_at IS NULL AND (n.node_type<>'resident' OR r.deleted_at IS NULL) UNION ALL SELECT lineage_id FROM lineage_edges WHERE edge_id=%s AND deleted_at IS NULL",
                (object_id,object_id),
            ).fetchone()
        return str(row["lineage_id"]) if row is not None else ""

    def lineage_revision(self, lineage_id: str) -> int:
        """Current definition version of one Lineage (its revision)."""

        with self.database.read() as connection:
            row = connection.execute(
                'SELECT revision FROM lineages WHERE lineage_id=%s AND deleted_at IS NULL', (lineage_id,),
            ).fetchone()
        if row is None:
            raise KeyError(lineage_id)
        return int(row["revision"])

    @request_cached
    def lineage_access_records(self, lineage_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            records = lineage_metadata_storage.read_many(connection, list(lineage_ids) if lineage_ids is not None else None)
            ids = [row['lineage_id'] for row in records]
            members = defaultdict(list)
            for row in connection.execute("""
                SELECT n.lineage_id,n.node_id FROM lineage_nodes n
                JOIN residents r ON r.resident_id=n.node_id AND r.deleted_at IS NULL
                WHERE n.node_type='resident' AND n.lineage_id=ANY(%s)
                UNION SELECT ref.lineage_id,ref.node_id FROM lineage_node_references ref
                JOIN residents r ON r.resident_id=ref.node_id AND r.deleted_at IS NULL
                WHERE ref.lineage_id=ANY(%s)
            """, (ids,ids)).fetchall():
                members[row['lineage_id']].append(row['node_id'])
            return [{**row, 'lineage_node_ids': sorted(members[row['lineage_id']])} for row in records]

    def program_resident_observers(self, resident_id: str, event_type: str) -> dict[str, list[str]]:
        """Locate observers using graph relations, without loading definitions."""
        with self.database.read() as connection:
            rows = connection.execute("""
                SELECT n.lineage_id, n.node_id
                FROM lineage_nodes n JOIN lineage_components c ON c.node_id=n.node_id
                JOIN lineages l ON l.lineage_id=n.lineage_id AND l.deleted_at IS NULL
                WHERE n.node_type='event_source' AND n.deleted_at IS NULL
                  AND lower(c.config_json::jsonb->>'event_type')=%s
                  AND (c.config_json::jsonb->>'resident_scope'='system'
                    OR EXISTS (SELECT 1 FROM lineage_edges e WHERE e.lineage_id=n.lineage_id
                        AND e.edge_type='watch' AND e.deleted_at IS NULL
                        AND e.target_node_id=%s AND e.source_node_id=n.node_id)
                    OR (c.config_json::jsonb->>'resident_scope'='lineage' AND (
                        EXISTS (SELECT 1 FROM lineage_nodes m WHERE m.lineage_id=n.lineage_id
                            AND m.node_id=%s AND m.node_type='resident' AND m.deleted_at IS NULL)
                        OR EXISTS (SELECT 1 FROM lineage_node_references r
                            WHERE r.lineage_id=n.lineage_id AND r.node_id=%s))))
                ORDER BY n.lineage_id,n.node_id
            """, (str(event_type or '').strip().lower(), *([str(resident_id or '').strip().lower()]*3))).fetchall()
        result: dict[str, list[str]] = {}
        for row in rows:
            result.setdefault(row['lineage_id'], []).append(row['node_id'])
        return result

    def upsert_layout_positions(
        self,
        lineage_id: str,
        positions: Iterable[Mapping[str, Any]],
        *,
        updated_at: str,
    ) -> dict[str, Any]:
        requested = [dict(item) for item in positions]

        def write(connection: Any) -> dict[str, Any]:
            # Definition commits lock the Lineage before its layout rows.
            # Keep that order and serialize the layout revision read/increment.
            lineage = connection.execute(
                'SELECT revision, layout_revision, layout_updated_at FROM lineages WHERE lineage_id=%s FOR NO KEY UPDATE',
                (lineage_id,),
            ).fetchone()
            if lineage is None:
                raise KeyError(lineage_id)
            active = graph_storage.active_objects(connection, lineage_id)
            applied: list[dict[str, Any]] = []
            missing: list[str] = []
            for item in requested:
                object_id = str(item["object_id"])
                kind = active.get(object_id)
                if kind is None:
                    missing.append(object_id)
                    continue
                x, y = float(item["x"]), float(item["y"])
                if kind == "function_block":
                    connection.execute(
                        'UPDATE function_blocks SET x=%s,y=%s WHERE function_block_id=%s AND lineage_id=%s',
                        (x,y,object_id,lineage_id),
                    )
                    applied.append({"object_id":object_id,"object_kind":kind,"x":x,"y":y})
                    continue
                connection.execute(
                    'INSERT INTO lineage_layout_positions(lineage_id,object_id,x,y) VALUES (%s,%s,%s,%s) ON CONFLICT(lineage_id,object_id) DO UPDATE SET x=excluded.x,y=excluded.y WHERE (lineage_layout_positions.x,lineage_layout_positions.y) IS DISTINCT FROM (excluded.x,excluded.y)',
                    (lineage_id, object_id, x, y),
                )
                applied.append({
                    "object_id": object_id,
                    "object_kind": kind,
                    "x": x,
                    "y": y,
                })
            layout_revision = int(lineage["layout_revision"])
            if applied:
                layout_revision += 1
                connection.execute(
                    'UPDATE lineages SET layout_revision=%s, layout_updated_at=%s WHERE lineage_id=%s',
                    (layout_revision, updated_at, lineage_id),
                )
            return {
                "lineage_id": lineage_id,
                "revision": int(lineage["revision"]),
                "layout_revision": layout_revision,
                "layout_updated_at": (
                    updated_at if applied else (iso_time(lineage["layout_updated_at"]) or "")
                ),
                "applied": applied,
                "missing_object_ids": missing,
            }

        return self.database.write(write)

    def put_lineage_definition(
        self,
        lineage_id: str,
        value: MutableMapping[str, Any],
        initial_runtime: Mapping[str, Any],
    ) -> None:
        def write(connection: Any) -> None:
            deleting = connection.execute(
                "SELECT 1 FROM lineage_deletion_operations WHERE lineage_id=%s",
                (lineage_id,),
            ).fetchone()
            if deleting is not None:
                raise RuntimeCASConflict("Lineage deletion is in progress")
            self.project_graphs.synchronize(
                connection, write_definition(connection, lineage_id, value),
            )
            exists = connection.execute(
                'SELECT 1 FROM lineages WHERE lineage_id=%s AND runtime_revision IS NOT NULL', (lineage_id,)
            ).fetchone()
            if exists is None:
                lineage_data_runtime.write_runtime(connection, lineage_id, initial_runtime)

        self.database.write(write)

    def remove_lineage(self, lineage_id: str, *, deleted_by: str) -> None:
        def write(connection: Any) -> None:
            lineage_data_runtime.lock_live_lineage(connection, lineage_id)
            from .lineage_definition_versions import check_definition_version
            check_definition_version(connection, lineage_id)
            projects = self.project_graphs.lineage_projects(connection, [lineage_id])
            deleted_at = now_iso_millis()
            connection.execute('UPDATE lineages SET deleted_at=%s,deleted_by=%s WHERE lineage_id=%s AND deleted_at IS NULL', (deleted_at,deleted_by,lineage_id))
            # The Lineage's Families go with it, as when their Function Blocks are removed.
            family_storage.store_program(connection, lineage_id, {"function_blocks": []}, deleted_at)
            for path in connection.execute(
                "SELECT trigger_path_id FROM trigger_paths WHERE lineage_id=%s AND status='active' ORDER BY trigger_path_id",
                (lineage_id,),
            ).fetchall():
                trigger_path_storage.cancel(connection, path["trigger_path_id"], at=deleted_at)
            for table in (*program_work_storage.COLUMNS, *wire_delivery_storage.COLUMNS, "lineage_shared_state", "lineage_drafts"):
                connection.execute(f'DELETE FROM {table} WHERE lineage_id=%s', (lineage_id,))
            self.deletions.delete(lineage_id)
            connection.execute('DELETE FROM project_lineage_entries WHERE lineage_id=%s', (lineage_id,))
            self.project_graphs.synchronize(connection, projects)
        self.database.write(write)

    def lineage_ids_for_resident(self, resident_id: str) -> list[str]:
        with self.database.read() as connection:
            rows = connection.execute(
                '\n                SELECT n.lineage_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE r.resident_id=%s AND r.deleted_at IS NULL\n                ORDER BY lineage_id\n                ',
                (resident_id,),
            ).fetchall()
        return [str(row["lineage_id"]) for row in rows]

    def lineage_snapshot(
        self,
        lineage_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read definition and runtime from one PostgreSQL snapshot."""

        with self.database.snapshot() as connection:
            definition = definition_from_connection(
                connection,
                lineage_id,
            )
            runtime = lineage_data_runtime.runtime_from_connection(
                connection,
                lineage_id,
            )
        return definition, runtime
