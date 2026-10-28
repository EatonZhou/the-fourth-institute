"""Typed Family facts and the read projection on Function Blocks."""
from __future__ import annotations

from typing import Any
ROLES = ("member", "facade", "router", "steward")
COLUMNS = {
    "families": ("family_id", "lineage_id", "title", "goal", "previous_leader_id", "deleted_at", "human_familizer_id", "route_threshold", "review_interval_seconds", "lineage_scope", "resource_id", "created_at", "updated_at", "personal_ama_id", "history_before", "history_before_ordinal", "auto_migrate", "auto_migrate_priority"),
    "family_residents": ("family_id", "resident_id", "role"),
}
RECOVERY_INDEXES = {"family_steward_recovery_idx": "ON family_residents(resident_id,family_id) WHERE role='steward'"}


def operating_role_ids(connection: Any) -> list[str]:
    return [row["resident_id"] for row in connection.execute(
        "SELECT fr.resident_id FROM family_residents fr JOIN families f USING(family_id) "
        "WHERE fr.role IN ('router','steward') AND f.deleted_at IS NULL ORDER BY fr.resident_id"
    ).fetchall()]


def create_schema(connection: Any) -> None:
    connection.execute("""CREATE TABLE families (
        family_id TEXT PRIMARY KEY, lineage_id TEXT NOT NULL REFERENCES lineages(lineage_id) ON DELETE CASCADE,
        title TEXT NOT NULL, goal TEXT NOT NULL, previous_leader_id TEXT NOT NULL DEFAULT '',
        deleted_at TIMESTAMPTZ, human_familizer_id TEXT NOT NULL DEFAULT '',
        route_threshold INTEGER NOT NULL DEFAULT 60,
        review_interval_seconds INTEGER NOT NULL DEFAULT 86400,
        lineage_scope BOOLEAN NOT NULL DEFAULT FALSE,
        resource_id TEXT NOT NULL UNIQUE CHECK(resource_id ~ '^family_[0-9a-f]{12}$'),
        created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL,
        personal_ama_id TEXT NOT NULL DEFAULT '',
        history_before TEXT NOT NULL DEFAULT '',
        history_before_ordinal BIGINT NOT NULL DEFAULT 0,
        auto_migrate BOOLEAN NOT NULL DEFAULT TRUE,
        auto_migrate_priority TEXT[] NOT NULL DEFAULT '{}')
    """)
    connection.execute("CREATE INDEX families_lineage_idx ON families(lineage_id) WHERE deleted_at IS NULL")
    connection.execute("CREATE UNIQUE INDEX family_lineage_scope ON families(lineage_id) WHERE lineage_scope AND deleted_at IS NULL")
    connection.execute("""CREATE TABLE family_residents (
        family_id TEXT NOT NULL REFERENCES families(family_id) ON DELETE CASCADE,
        resident_id TEXT NOT NULL REFERENCES residents(resident_id) DEFERRABLE INITIALLY DEFERRED,
        role TEXT NOT NULL CHECK(role IN ('member','facade','router','steward')),
        PRIMARY KEY(family_id,resident_id))
    """)
    connection.execute("CREATE UNIQUE INDEX family_direct_membership ON family_residents(resident_id) WHERE role='member'")
    connection.execute("CREATE UNIQUE INDEX family_internal_identity ON family_residents(resident_id) WHERE role<>'member'")
    connection.execute("CREATE UNIQUE INDEX family_single_roles ON family_residents(family_id,role) WHERE role<>'member'")
    for name, definition in RECOVERY_INDEXES.items():
        connection.execute(f"CREATE INDEX {name} {definition}")


def store_program(connection: Any, lineage_id: str, definition: dict, now: str) -> None:
    """Persist Family facts alongside the enclosing graph transaction."""
    blocks = [item for item in definition.get("function_blocks", []) if item.get("family")]
    ids = [item["id"] for item in blocks]
    connection.execute("DELETE FROM family_residents WHERE family_id IN (SELECT family_id FROM families WHERE lineage_id=%s AND NOT(family_id=ANY(%s)))", (lineage_id, ids))
    connection.execute("UPDATE families SET deleted_at=%s,updated_at=%s WHERE lineage_id=%s AND deleted_at IS NULL AND NOT(family_id=ANY(%s))", (now, now, lineage_id, ids))
    changed_rosters = []
    for block in blocks:
        family_id = block["id"]
        family = block["family"]
        from . import identity_contract
        existing_id = connection.execute("SELECT resource_id FROM families WHERE family_id=%s", (family_id,)).fetchone()
        resource_id = (existing_id["resource_id"] if existing_id else
                       family.get("resource_id") or identity_contract.new_id("family"))
        identity_contract.validate_id(resource_id, "family")
        if family.get("resource_id") and family["resource_id"] != resource_id:
            raise ValueError("Family resource_id is immutable")
        from management_portal.resources.retired_identity_boundary import require_live_identity
        require_live_identity(connection, resource_id)
        family["resource_id"] = resource_id
        connection.execute("""INSERT INTO families(family_id,lineage_id,title,goal,previous_leader_id,human_familizer_id,route_threshold,review_interval_seconds,lineage_scope,resource_id,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(family_id) DO UPDATE SET lineage_id=excluded.lineage_id,title=excluded.title,
            goal=excluded.goal,previous_leader_id=excluded.previous_leader_id,human_familizer_id=excluded.human_familizer_id,
            route_threshold=excluded.route_threshold,review_interval_seconds=excluded.review_interval_seconds,
            lineage_scope=excluded.lineage_scope,updated_at=excluded.updated_at,deleted_at=NULL""",
            (family_id,lineage_id,family["title"],family.get("goal", ""),family.get("previous_leader_id", ""),
             family.get("human_familizer_id", ""),family.get("route_threshold", 60),family.get("review_interval_seconds", 86400),
             family.get("lineage_scope", False),resource_id,block.get("created_at") or now,now))
        connection.execute("""UPDATE families SET personal_ama_id=%s,history_before=%s,history_before_ordinal=%s,auto_migrate=%s,auto_migrate_priority=%s
            WHERE family_id=%s""", (family.get("personal_ama_id", ""), family.get("history_before", ""),
                                     family.get("history_before_ordinal", 0), family.get("auto_migrate", True), family.get("auto_migrate_priority", []), family_id))
        roster = [(rid, "member") for rid in family.get("members", [])]
        roster.extend((family[role], role) for role in ROLES[1:] if family.get(role))
        resident_ids = [rid for rid, _ in roster]
        if len(set(resident_ids)) != len(resident_ids):
            raise ValueError("A Resident may hold only one Family role")
        existing = connection.execute("SELECT resident_id,role FROM family_residents WHERE family_id=%s", (family_id,)).fetchall()
        before = {(r["resident_id"], r["role"]) for r in existing}
        after = set(roster)
        for rid, role in before - after:
            connection.execute("DELETE FROM family_residents WHERE family_id=%s AND resident_id=%s", (family_id,rid))
        changed_rosters.append((family_id, sorted(after - before)))
    for family_id, roster in changed_rosters:
        for rid, role in roster:
            connection.execute("INSERT INTO family_residents VALUES (%s,%s,%s)", (family_id,rid,role))


def hydrate_programs(connection: Any, definitions: dict[str, dict]) -> None:
    """Attach the read projection in two batch queries."""
    if not definitions:
        return
    # Named columns: a prepared SELECT * breaks on long-lived connections when a column is added.
    rows = connection.execute(f"SELECT {','.join(COLUMNS['families'])} FROM families WHERE lineage_id=ANY(%s) AND deleted_at IS NULL", (list(definitions),)).fetchall()
    families = {row["family_id"]: {key: value for key, value in dict(row).items()
                                 if key not in {"family_id", "lineage_id", "deleted_at", "created_at", "updated_at"}} for row in rows}
    for family in families.values():
        family.update(members=[], facade="", steward="", router="")
    if families:
        for row in connection.execute("SELECT * FROM family_residents WHERE family_id=ANY(%s) ORDER BY role,resident_id", (list(families),)).fetchall():
            family = families[row["family_id"]]
            if row["role"] == "member":
                family["members"].append(row["resident_id"])
            else:
                family[row["role"]] = row["resident_id"]
    for definition in definitions.values():
        for block in definition.get("function_blocks", []):
            if block["id"] in families:
                block["family"] = families[block["id"]]
                block["title"] = block["family"]["title"]


def rename_facade(connection: Any, resident_id: str, title: str) -> list[str]:
    """A facade rename changes its Family's canonical title in the same transaction.

    Returns the resource IDs of the retitled Families, whose descriptors need publishing.
    """
    renamed = connection.execute('''
        UPDATE families f SET title=%s,updated_at=date_trunc('second',clock_timestamp()) FROM family_residents fr
        WHERE fr.family_id=f.family_id AND fr.resident_id=%s AND fr.role='facade'
          AND f.deleted_at IS NULL AND f.title IS DISTINCT FROM %s
        RETURNING f.resource_id
    ''', (title, resident_id, title)).fetchall()
    connection.execute('''UPDATE function_blocks b SET title=%s FROM family_residents fr
        WHERE fr.family_id=b.function_block_id AND fr.resident_id=%s AND fr.role='facade'
    ''', (title, resident_id))
    return [row['resource_id'] for row in renamed if row['resource_id']]


def resource_records(connection: Any, *, resource_ids: list[str] | None = None,
                     lineage_id: str = "") -> list[dict]:
    """Canonical Family resources, including tombstones, without graph hydration."""
    from management_common.database_time import iso_time
    condition, params = "TRUE", []
    if resource_ids is not None:
        condition += " AND f.resource_id=ANY(%s)"
        params.append(resource_ids)
    if lineage_id:
        condition += " AND f.lineage_id=%s"
        params.append(lineage_id)
    columns = ','.join(f'f.{column}' for column in COLUMNS['families'])
    rows = connection.execute(f'''SELECT {columns},l.project_id,l.lineage_membership,
        l.created_by AS lineage_created_by,l.deleted_at AS lineage_deleted_at
        FROM families f JOIN lineages l USING(lineage_id)
        WHERE {condition} ORDER BY f.resource_id''', params).fetchall()
    records = {r['family_id']: dict(r) for r in rows}
    for value in records.values():
        value.update(members=[], facade="", steward="", router="")
        for field in ('created_at', 'updated_at', 'deleted_at', 'lineage_deleted_at'):
            value[field] = iso_time(value[field])
        value['deleted_at'] = value['deleted_at'] or value.pop('lineage_deleted_at')
        value.pop('lineage_deleted_at', None)
        if value['deleted_at']:
            value['updated_at'] = value['deleted_at']
    if records:
        for row in connection.execute('SELECT * FROM family_residents WHERE family_id=ANY(%s) ORDER BY role,resident_id',
                                      (list(records),)).fetchall():
            value = records[row['family_id']]
            if row['role'] == 'member':
                value['members'].append(row['resident_id'])
            else:
                value[row['role']] = row['resident_id']
    return list(records.values())


def resident_roles(connection: Any, resident_ids: list[str]) -> dict[str, dict]:
    return {r['resident_id']: dict(r) for r in connection.execute('''
        SELECT DISTINCT ON (fr.resident_id) fr.resident_id,fr.role,f.family_id,f.previous_leader_id,f.human_familizer_id,
            f.personal_ama_id,f.history_before,f.history_before_ordinal
        FROM family_residents fr JOIN families f USING(family_id)
        WHERE fr.resident_id=ANY(%s) AND f.deleted_at IS NULL
        ORDER BY fr.resident_id, (fr.role='member'), f.family_id''', (resident_ids,)).fetchall()}


def roles_of(connection: Any, family_id: str) -> dict[str, list[str]]:
    """Read a live Family's roster in stable Resident-ID order."""
    roles = {role: [] for role in ROLES}
    for row in connection.execute('''
        SELECT fr.resident_id,fr.role FROM family_residents fr JOIN families f USING(family_id)
        WHERE fr.family_id=%s AND f.deleted_at IS NULL ORDER BY fr.resident_id
    ''', (family_id,)).fetchall():
        roles[row['role']].append(row['resident_id'])
    return roles


def members_of(connection: Any, family_id: str) -> list[str]:
    return roles_of(connection, family_id)['member']


def routing_nodes(connection: Any, family_id: str) -> set[str]:
    """Only these Family elements own request routing and delivery failures."""
    return {row["node_id"] for row in connection.execute(
        "SELECT resident_id node_id FROM family_residents WHERE family_id=%s AND role='router' "
        "UNION SELECT element_id node_id FROM function_block_elements WHERE function_block_id=%s "
        "AND element_key IN ('inbox','router','route_relay')", (family_id, family_id),
    ).fetchall()}


def credential_peers(connection: Any, resident_ids: list[str]) -> dict[str, dict[str, tuple[str, ...]]]:
    """The live formal peers of each Resident, including every Family role.

    Credentials are shared within each directly joined Family. This reads
    domain facts, not a descriptor projection or cached routing roster.
    """
    if not resident_ids:
        return {}
    rows = connection.execute('''
        SELECT own.resident_id, peer.resident_id AS peer_id, f.family_id
        FROM family_residents own JOIN families f USING(family_id)
        JOIN lineages l ON l.lineage_id=f.lineage_id AND l.deleted_at IS NULL
        JOIN lineage_nodes n ON n.node_id=f.family_id AND n.deleted_at IS NULL
        JOIN residents caller ON caller.resident_id=own.resident_id AND caller.deleted_at IS NULL
        JOIN family_residents peer ON peer.family_id=f.family_id AND peer.resident_id<>own.resident_id
        JOIN residents holder ON holder.resident_id=peer.resident_id AND holder.deleted_at IS NULL
        WHERE own.resident_id=ANY(%s) AND f.deleted_at IS NULL
        ORDER BY own.resident_id, peer.resident_id, f.family_id
    ''', (resident_ids,)).fetchall()
    peers: dict[str, dict[str, list[str]]] = {}
    for row in rows:
        peers.setdefault(row['resident_id'], {}).setdefault(row['peer_id'], []).append(row['family_id'])
    return {rid: {peer: tuple(families) for peer, families in values.items()}
            for rid, values in peers.items()}


def families_around(connection: Any, resident_id: str, *, max_hops: int = 4) -> list[dict]:
    """Follow indexed direct membership toward the outermost facade."""
    chain = []
    seen = set()
    current = resident_id
    while current and len(chain) < max_hops:
        row = connection.execute('''
            SELECT f.family_id, facade.resident_id AS facade_id
            FROM family_residents fr JOIN families f USING(family_id)
            JOIN family_residents facade ON facade.family_id=f.family_id AND facade.role='facade'
            WHERE fr.resident_id=%s AND fr.role<>'facade' AND f.deleted_at IS NULL
            ORDER BY f.family_id LIMIT 1
        ''', (current,)).fetchone()
        if row is None or row['family_id'] in seen:
            break
        chain.append(dict(row))
        seen.add(row['family_id'])
        current = row['facade_id']
    return chain


def auto_migration_enabled(record: dict) -> bool:
    """The immediate Family owns its migration policy; ordinary Residents have none."""
    family = record.get("family") or {}
    if family.get("role") not in {"member", "router", "steward"} or record.get("deleted_at"):
        return False
    from .state_store import get_lineage_data_store
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT auto_migrate FROM families WHERE family_id=%s AND deleted_at IS NULL",
                                 (family["family_id"],)).fetchone()
    return bool(row and row["auto_migrate"])


def auto_migration_priority(record: dict) -> list[str]:
    """Read the same immediate Family policy for admission and failure recovery."""
    family = record.get("family") or {}
    if family.get("role") not in {"member", "router", "steward"} or record.get("deleted_at"):
        return []
    from .state_store import get_lineage_data_store
    with get_lineage_data_store().database.read() as connection:
        row = connection.execute("SELECT auto_migrate_priority FROM families WHERE family_id=%s AND deleted_at IS NULL",
                                 (family["family_id"],)).fetchone()
    return list(row["auto_migrate_priority"]) if row else []
