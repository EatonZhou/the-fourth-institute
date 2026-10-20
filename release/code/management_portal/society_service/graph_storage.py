"""One identity/ownership table for graph nodes and one for graph connections.

Executable configuration belongs to its typed owner. Function Block boundaries
and element ownership are relations. The runtime consumes an assembled program,
built by this module in batch reads.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any
from management_common.database_time import decode_times

from . import edge_storage
from .definition_collections import column, declarations, decode, typed_values, upsert, upsert_many, validate_fields


NODE_TYPES = ("resident", "event_source", "effect", "seed", "loop", "filter", "selector", "reducer", "function_block")
WIRE_TYPES = ("route", "target", "watch")
RELATION_TYPES = ("inheritance", "absorption", "handoff", "candidate_fork")
COMPONENT_FIELDS = {"title": "text", "client_key": "text", "config": "object", "created_by_principal_id": "text"}
RESIDENT_FIELDS = {"origin_seed_id": "text", "operator_executor": "object"}
EDGE_FIELDS = {
    "label": "text", "client_key": "text", "config": "object",
    "revision_request_id": "text", "explanation": "text", "operation_contract": "object", "note": "text",
}
BLOCK_FIELDS = {"title": "text", "template_id": "text", "created_at": "instant",
                "created_by": "text", "updated_at": "instant", "updated_by": "text"}

COLUMNS = {
    **edge_storage.COLUMNS,
    "lineage_nodes": ("node_id", "lineage_id", "node_type", "deleted_at"),
    "lineage_node_references": ("lineage_id", "node_id"),
    "lineage_components": ("node_id", *[column(k, v) for k, v in COMPONENT_FIELDS.items()]),
    "lineage_resident_nodes": ("node_id", *[column(k, v) for k, v in RESIDENT_FIELDS.items()]),
    "lineage_edges": ("edge_id", "lineage_id", "edge_type", "source_node_id", "source_port", "target_node_id", "target_port", *[column(k, v) for k, v in EDGE_FIELDS.items()], "deleted_at"),
    "lineage_observation_targets": ("edge_id", "target_edge_id"),
    "function_blocks": ("function_block_id", "lineage_id", "title", "template_id", "x", "y", "created_at", "created_by", "updated_at", "updated_by"),
    "function_block_elements": ("function_block_id", "element_id", "element_kind", "element_key"),
}
RECOVERY_INDEXES = {
    "lineage_event_sources_idx": "ON lineage_nodes(lineage_id,node_id) WHERE node_type='event_source' AND deleted_at IS NULL",
}


def observation_lineage_ids(connection: Any, event_types: set[str]) -> set[str]:
    """Bootstrap enabled observations, including timers not yet assigned a cursor."""
    return {row["lineage_id"] for row in connection.execute("""
        SELECT n.lineage_id FROM lineage_nodes n JOIN lineage_components c USING(node_id)
        LEFT JOIN event_source_progress p ON p.lineage_id=n.lineage_id AND p.source_id=n.node_id
        WHERE n.node_type='event_source' AND n.deleted_at IS NULL
            AND c.config_json::jsonb->>'event_type'=ANY(%s)
            AND COALESCE(p.checkpoint_json::jsonb->>'paused','false')<>'true'
    """, (sorted(event_types),)).fetchall()}


def create_schema(connection: Any) -> None:
    connection.execute(f"""CREATE TABLE lineage_nodes (
        node_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        node_type TEXT CHECK(node_type IN {NODE_TYPES}), deleted_at TIMESTAMPTZ,
        CHECK(node_type <> 'resident' OR deleted_at IS NULL),
        CHECK(node_type IS NOT NULL OR deleted_at IS NOT NULL))""")
    connection.execute("CREATE INDEX lineage_nodes_lineage_idx ON lineage_nodes(lineage_id,node_type,node_id) WHERE deleted_at IS NULL")
    connection.execute("""CREATE TABLE lineage_node_references (
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        node_id TEXT NOT NULL REFERENCES lineage_nodes(node_id) DEFERRABLE INITIALLY DEFERRED,
        PRIMARY KEY(lineage_id,node_id))""")
    for table, fields in (("lineage_components", COMPONENT_FIELDS), ("lineage_resident_nodes", RESIDENT_FIELDS)):
        connection.execute(f"CREATE TABLE {table} (node_id TEXT PRIMARY KEY REFERENCES lineage_nodes(node_id), {declarations(fields)})")
    connection.execute(f"""CREATE TABLE lineage_edges (
        edge_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        edge_type TEXT CHECK(edge_type IN {WIRE_TYPES + RELATION_TYPES}),
        source_node_id TEXT REFERENCES lineage_nodes(node_id) DEFERRABLE INITIALLY DEFERRED,
        source_port TEXT, target_node_id TEXT REFERENCES lineage_nodes(node_id) DEFERRABLE INITIALLY DEFERRED,
        target_port TEXT, {declarations(EDGE_FIELDS)},
        deleted_at TIMESTAMPTZ,
        CONSTRAINT lineage_edges_endpoints_check CHECK(deleted_at IS NOT NULL OR
            (edge_type IS NOT NULL AND source_node_id IS NOT NULL AND (target_node_id IS NOT NULL OR edge_type='watch'))),
        CONSTRAINT lineage_edges_binding_ports_check CHECK(edge_type NOT IN ('watch','target') OR
            (target_port IS NULL AND source_port IS NOT NULL AND (edge_type<>'watch' OR source_port='watch'))))""")
    edge_storage.create_schema(connection)
    connection.execute("CREATE INDEX lineage_edges_lineage_idx ON lineage_edges(lineage_id,edge_type,edge_id) WHERE deleted_at IS NULL")
    connection.execute("CREATE INDEX lineage_edges_source_idx ON lineage_edges(source_node_id)")
    connection.execute("CREATE INDEX lineage_edges_target_idx ON lineage_edges(target_node_id)")
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")
    connection.execute("""CREATE TABLE lineage_observation_targets (
        edge_id TEXT PRIMARY KEY REFERENCES lineage_edges(edge_id),
        target_edge_id TEXT NOT NULL REFERENCES lineage_edges(edge_id) DEFERRABLE INITIALLY DEFERRED)""")
    create_function_block_schema(connection)


def create_function_block_schema(connection: Any) -> None:
    connection.execute("""CREATE TABLE function_blocks (
        function_block_id TEXT PRIMARY KEY REFERENCES lineage_nodes(node_id),
        lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id),
        title TEXT NOT NULL DEFAULT '', template_id TEXT, x DOUBLE PRECISION, y DOUBLE PRECISION,
        created_at TIMESTAMPTZ NOT NULL, created_by TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL)""")
    connection.execute("""CREATE TABLE function_block_elements (
        function_block_id TEXT NOT NULL REFERENCES function_blocks(function_block_id),
        element_id TEXT NOT NULL,
        element_kind TEXT NOT NULL CHECK(element_kind IN ('resident','component','wire','function_block')),
        element_key TEXT, PRIMARY KEY(function_block_id,element_id))""")
    connection.execute("CREATE UNIQUE INDEX function_block_elements_owner_idx ON function_block_elements(element_id)")


def _reconcile(connection: Any, table: str, owner_column: str, owner: str, key_columns: tuple[str, ...], rows: list[dict[str, Any]]) -> None:
    """Delete only removed relations; preserve stable rows for unchanged members."""
    current = {tuple(row[k] for k in key_columns) for row in connection.execute(f"SELECT {', '.join(key_columns)} FROM {table} WHERE {owner_column}=%s", (owner,)).fetchall()}
    desired = {tuple(row[k] for k in key_columns) for row in rows}
    if len(desired) != len(rows):
        raise ValueError(f"Duplicate {table} relation")
    for key in current - desired:
        connection.execute(f"DELETE FROM {table} WHERE {owner_column}=%s AND " + " AND ".join(f"{k}=%s" for k in key_columns), (owner, *key))
    for row in rows:
        upsert(connection, table, {owner_column: owner, **{k: row[k] for k in key_columns}}, {k: v for k, v in row.items() if k not in key_columns})


def put(connection: Any, lineage_id: str, value: Mapping[str, Any]) -> None:
    assert_writable_ids(connection, value)
    program = value.get("program") or {}
    validate_fields(program, {"schema", "components", "wires"}, "program")
    components = program.get("components") or []
    blocks = value.get("function_blocks") or []
    resident_ids = [item["id"] for item in value.get("nodes") or []]
    # Registered Residents always retain their graph identity, including after
    # deletion. A Project canvas references that identity without moving it.
    homes = {r["node_id"]: r["lineage_id"] for r in connection.execute("SELECT node_id,lineage_id FROM lineage_nodes WHERE node_id=ANY(%s)", (resident_ids,)).fetchall()}
    new_nodes = []
    resident_rows = []
    for item in value.get("nodes") or []:
        rid = item["id"]
        if rid not in homes:
            # Registration commits its graph identity and Resident facts in
            # the same transaction; the graph is published first.
            new_nodes.append({"node_id": rid, "lineage_id": lineage_id, "node_type": "resident"})
            homes[rid] = lineage_id
        if homes[rid] != lineage_id:
            continue
        validate_fields(item, {"id", *RESIDENT_FIELDS, "title", "resident_title", "backend", "created_at", "updated_at", "x", "y", "role", "workspace", "status", "last_execution"}, "Resident graph projection")
        resident_rows.append({"node_id": rid, **typed_values(item, RESIDENT_FIELDS)})
    upsert_many(connection, "lineage_nodes", ("node_id",), new_nodes)
    upsert_many(connection, "lineage_resident_nodes", ("node_id",), resident_rows)
    _reconcile(connection, "lineage_node_references", "lineage_id", lineage_id, ("node_id",), [{"node_id": rid} for rid in resident_ids if homes[rid] != lineage_id])
    component_nodes = []
    component_rows = []
    for item in components:
        validate_fields(item, {"id", "kind", "x", "y", *COMPONENT_FIELDS}, "component")
        component_nodes.append({"node_id": item["id"], "lineage_id": lineage_id, "node_type": item["kind"]})
        component_rows.append({"node_id": item["id"], **typed_values(item, COMPONENT_FIELDS)})
    upsert_many(connection, "lineage_nodes", ("node_id",), component_nodes)
    upsert_many(connection, "lineage_components", ("node_id",), component_rows)
    desired_owners = {e["id"]: b["id"] for b in blocks for e in b.get("elements") or []}
    for row in connection.execute("SELECT e.function_block_id,e.element_id FROM function_block_elements e JOIN function_blocks b USING(function_block_id) WHERE b.lineage_id=%s", (lineage_id,)).fetchall():
        if desired_owners.get(row["element_id"]) != row["function_block_id"]:
            connection.execute("DELETE FROM function_block_elements WHERE function_block_id=%s AND element_id=%s", (row["function_block_id"],row["element_id"]))
    block_ids = [b["id"] for b in blocks]
    connection.execute("DELETE FROM function_blocks WHERE lineage_id=%s AND NOT(function_block_id=ANY(%s))", (lineage_id, block_ids))
    for item in blocks:
        validate_fields(item, {"id", "x", "y", "elements", "family", *BLOCK_FIELDS}, "Function Block")
        upsert(connection, "lineage_nodes", {"node_id": item["id"]}, {"lineage_id": lineage_id, "node_type": "function_block"})
        upsert(connection, "function_blocks", {"function_block_id": item["id"]}, {"lineage_id": lineage_id, **typed_values(item, BLOCK_FIELDS), "x": item.get("x"), "y": item.get("y")})
        elements = [{"element_id": e["id"], "element_kind": e["kind"], "element_key": e.get("key")} for e in item.get("elements") or []]
        _reconcile(connection, "function_block_elements", "function_block_id", item["id"], ("element_id",), elements)
    all_edges = [*(value.get("edges") or []), *(program.get("wires") or [])]
    edge_ids = {e["id"] for e in all_edges}
    if len(edge_ids) != len(all_edges):
        raise ValueError("Graph edge IDs must be unique")
    for item in all_edges:
        validate_fields(item, {"id", "type", "kind", "family", "source", "target", "source_port", "target_port", *EDGE_FIELDS, "items", "execution"}, "edge")
        kind = item.get("kind") or item["type"]
        observed_edge = kind == "watch" and item["target"] in edge_ids
        upsert(connection, "lineage_edges", {"edge_id": item["id"]}, {"lineage_id": lineage_id, "edge_type": kind, "source_node_id": item["source"], "source_port": item.get("source_port"), "target_node_id": None if observed_edge else item["target"], "target_port": item.get("target_port"), **typed_values(item, EDGE_FIELDS)})
        edge_storage.put(connection, item)
        if observed_edge:
            upsert(connection, "lineage_observation_targets", {"edge_id": item["id"]}, {"target_edge_id": item["target"]})
        else:
            connection.execute("DELETE FROM lineage_observation_targets WHERE edge_id=%s", (item["id"],))


def read_many(connection: Any, lineage_ids: list[str]) -> dict[str, dict[str, Any]]:
    from .lineage_program import PROGRAM_SCHEMA
    from .lineage_definition_normalization import EDGE_FAMILIES
    result = {lid: {"nodes": [], "edges": [], "function_blocks": [], "program": {"schema": PROGRAM_SCHEMA, "components": [], "wires": []}} for lid in lineage_ids}
    if not result:
        return result
    nodes = active_nodes(connection, lineage_ids)
    node_ids = [n["node_id"] for n in nodes]
    resident_ids = {n["node_id"] for n in nodes if n["node_type"] == "resident"}
    residents = {r["resident_id"]: decode_times(r, ("created_at", "updated_at")) for r in connection.execute("SELECT resident_id,title,backend,created_at,updated_at FROM residents WHERE resident_id=ANY(%s)", (list(resident_ids),)).fetchall()} if resident_ids else {}
    resident_facts = {r["node_id"]: decode(r, RESIDENT_FIELDS) for r in connection.execute("SELECT * FROM lineage_resident_nodes WHERE node_id=ANY(%s)", (list(resident_ids),)).fetchall()} if resident_ids else {}
    for lid, rid in [(n["lineage_id"], n["node_id"]) for n in nodes if n["node_type"] == "resident"]:
        # Registration publishes the node before the Resident in one transaction.
        # A deferred FK checks the completed pair at commit.
        row = residents.get(rid, {})
        result[lid]["nodes"].append({"id": rid, **{k: v for k, v in row.items() if k != "resident_id" and v is not None}, **resident_facts.get(rid, {})})
    components = {r["node_id"]: decode(r, COMPONENT_FIELDS) for r in connection.execute("SELECT * FROM lineage_components WHERE node_id=ANY(%s)", (node_ids,)).fetchall()} if node_ids else {}
    blocks = {r["function_block_id"]: {"id": r["function_block_id"], **decode(r, BLOCK_FIELDS), "x": r["x"], "y": r["y"], "elements": []} for r in connection.execute("SELECT b.* FROM function_blocks b JOIN lineage_nodes n ON n.node_id=b.function_block_id WHERE b.lineage_id=ANY(%s) AND n.deleted_at IS NULL", (lineage_ids,)).fetchall()}
    if blocks:
        for row in connection.execute("SELECT * FROM function_block_elements WHERE function_block_id=ANY(%s) ORDER BY element_id", (list(blocks),)).fetchall():
            blocks[row["function_block_id"]]["elements"].append({"id": row["element_id"], "kind": row["element_kind"], "key": row["element_key"]})
    for n in nodes:
        nid = n["node_id"]
        program = result[n["lineage_id"]]["program"]
        if nid in components:
            item = {"id": nid, "kind": n["node_type"], **components[nid]}
            program["components"].append(item)
        elif nid in blocks:
            result[n["lineage_id"]]["function_blocks"].append(blocks[nid])
    rows = connection.execute("""SELECT e.*,o.target_edge_id FROM lineage_edges e
        LEFT JOIN lineage_observation_targets o USING(edge_id) WHERE e.lineage_id=ANY(%s) AND e.deleted_at IS NULL ORDER BY e.edge_id""", (lineage_ids,)).fetchall()
    details = edge_storage.read_many(connection, rows)
    for row in rows:
        kind = row["edge_type"]
        wire = kind in WIRE_TYPES
        item = {"id": row["edge_id"], "kind" if wire else "type": kind, "source": row["source_node_id"], "target": row["target_node_id"] or row["target_edge_id"], **decode(row, EDGE_FIELDS), **details.get(row["edge_id"], {})}
        for port in ("source_port", "target_port"):
            if row[port] is not None:
                item[port] = row[port]
        if wire:
            result[row["lineage_id"]]["program"]["wires"].append(item)
        else:
            item["family"] = EDGE_FAMILIES[kind]
            result[row["lineage_id"]]["edges"].append(item)
    for value in result.values():
        value["nodes"].sort(key=lambda n: n["id"])
        for name in ("components",):
            value["program"][name].sort(key=lambda n: n["id"])
    return result


def active_nodes(connection: Any, lineage_ids: list[str]) -> list:
    """Home membership uses its index; cross-Lineage display is an explicit relation.

    Resident deletion is owned exclusively by residents. Non-Resident nodes
    own their deletion state in lineage_nodes. No identity can be resurrected.
    """
    return connection.execute("""SELECT n.node_id,n.node_type,n.lineage_id
        FROM lineage_nodes n LEFT JOIN residents r ON r.resident_id=n.node_id
        WHERE n.lineage_id=ANY(%s) AND n.deleted_at IS NULL
          AND (n.node_type <> 'resident' OR r.deleted_at IS NULL)
        UNION ALL
        SELECT n.node_id,n.node_type,ref.lineage_id
        FROM lineage_node_references ref JOIN lineage_nodes n USING(node_id)
        LEFT JOIN residents r ON r.resident_id=n.node_id
        WHERE ref.lineage_id=ANY(%s) AND n.lineage_id<>ref.lineage_id
          AND n.deleted_at IS NULL AND (n.node_type <> 'resident' OR r.deleted_at IS NULL)
        """, (lineage_ids,lineage_ids)).fetchall()


def active_objects(connection: Any, lineage_id: str) -> dict[str, str]:
    objects = {n['node_id']: ('resident' if n['node_type']=='resident' else
               'function_block' if n['node_type']=='function_block' else 'component')
               for n in active_nodes(connection, [lineage_id])}
    for row in connection.execute('SELECT edge_id,edge_type FROM lineage_edges WHERE lineage_id=%s AND deleted_at IS NULL', (lineage_id,)).fetchall():
        objects[row['edge_id']] = 'wire' if row['edge_type'] in WIRE_TYPES else 'edge'
    return objects


def assert_writable_ids(connection: Any, value: Mapping[str, Any]) -> None:
    program = value.get('program') or {}
    node_ids = [n['id'] for n in program.get('components') or []]
    node_ids.extend(n['id'] for n in value.get('function_blocks') or [])
    node_ids.extend(n['id'] for n in value.get('nodes') or [])
    edge_ids = [e['id'] for e in [*(value.get('edges') or []), *(program.get('wires') or [])]]
    deleted = connection.execute("""SELECT n.node_id AS id FROM lineage_nodes n
        LEFT JOIN residents r ON r.resident_id=n.node_id
        WHERE n.node_id=ANY(%s) AND (n.deleted_at IS NOT NULL OR r.deleted_at IS NOT NULL)
        UNION ALL SELECT edge_id FROM lineage_edges WHERE edge_id=ANY(%s) AND deleted_at IS NOT NULL""", (node_ids,edge_ids)).fetchall()
    if deleted:
        raise ValueError('Deleted graph IDs cannot be reused: ' + ', '.join(r['id'] for r in deleted))


def retire_missing(connection: Any, lineage_id: str, current: set[str], desired: set[str], now: str) -> None:
    removed = sorted(current - desired)
    if not removed:
        return
    connection.execute("UPDATE lineage_nodes SET deleted_at=%s WHERE lineage_id=%s AND node_id=ANY(%s) AND node_type<>'resident' AND deleted_at IS NULL", (now,lineage_id,removed))
    connection.execute("UPDATE lineage_edges SET deleted_at=%s WHERE lineage_id=%s AND edge_id=ANY(%s) AND deleted_at IS NULL", (now,lineage_id,removed))
    connection.execute('DELETE FROM function_block_elements WHERE element_id=ANY(%s)', (removed,))
    connection.execute('DELETE FROM lineage_layout_positions WHERE lineage_id=%s AND object_id=ANY(%s)', (lineage_id,removed))
    for table in ("workspace_handoff_work", "context_absorption_work"):
        connection.execute(f"DELETE FROM {table} WHERE lineage_id=%s AND edge_id=ANY(%s)", (lineage_id,removed))


def transfer(connection: Any, source: str, target: str, object_ids: list[str]) -> None:
    """Transfer identities before reconciling either graph, inside the move transaction."""
    connection.execute('UPDATE function_blocks SET lineage_id=%s WHERE lineage_id=%s AND function_block_id=ANY(%s)', (target,source,object_ids))
    for table, key in (('lineage_nodes','node_id'), ('lineage_edges','edge_id')):
        connection.execute(f'UPDATE {table} SET lineage_id=%s WHERE lineage_id=%s AND {key}=ANY(%s) AND deleted_at IS NULL', (target,source,object_ids))
