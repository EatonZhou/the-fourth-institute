"""Resident records: identity, configuration and membership, and their row projection."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from typing import Any

from .read_snapshot import request_cached
from . import family_storage, graph_storage, resident_storage
from .lineage_data_columns import json_column
from .resident_storage import RESIDENT_RECORD_SCHEMA


def resident_from_row(row: Any) -> dict[str, Any]:
    row = dict(row)
    value: dict[str, Any] = {"schema": RESIDENT_RECORD_SCHEMA, "folder_schema": "lineage-folder-v3"}
    for fields in resident_storage.DOMAIN_TABLES.values():
        value.update(resident_storage.decode(row, fields))
    value.update(lineage_id=str(row.get('lineage_id') or ''),
                 project_id=str(row.get('lineage_project_id') or ''),
                 lineage_title=str(row.get('lineage_title') or ''),
                 lineage_project_id=str(row.get('lineage_project_id') or ''))
    if value.get('deleted_at'):
        value['project_id'] = str(value.get('historical_project_id') or value['project_id'])
        if not row.get('lineage_id'):
            value.pop('lineage_id', None)
    if "principal_hierarchy" in value:
        from .principal_hierarchy import level_for_resident
        home = {"leader_resident_id": row.get("home_leader"), "lineage_membership": row.get("home_membership"),
                "principal_hierarchy": {"level": "project" if row.get("home_is_project") else "lineage"}}
        value["principal_hierarchy"]["level"] = level_for_resident(value, home)
    return value


def resident_select(where: str = "", *, include_deleted: bool = False) -> str:
    selections = ['r.*', 'l.title AS lineage_title', 'l.project_id AS lineage_project_id', 'l.leader_resident_id AS home_leader', 'l.lineage_membership AS home_membership', 'EXISTS(SELECT 1 FROM project_lineage_entries p WHERE p.lineage_id=l.lineage_id) AS home_is_project']
    joins = []
    for index, (table, fields) in enumerate(resident_storage.DOMAIN_TABLES.items()):
        if table == 'residents':
            continue
        alias = f'd{index}'
        selections.extend(f'{alias}.{column}' for column in resident_storage.columns(fields))
        joins.append(f'LEFT JOIN {table} AS {alias} USING(resident_id)')
    if not include_deleted:
        where = 'WHERE r.deleted_at IS NULL ' + (
            'AND ' + where[6:] if where.startswith('WHERE ') else where
        )
    membership_join = 'LEFT JOIN' if include_deleted else 'JOIN'
    return ('SELECT ' + ', '.join(selections) + f' FROM (SELECT resident.*,node.lineage_id FROM residents resident {membership_join} lineage_nodes node ON node.node_id=resident.resident_id) AS r '
            + 'LEFT JOIN lineages AS l USING(lineage_id) ' + ' '.join(joins) + ' ' + where)


def residents_from_rows(connection: Any, rows: Iterable[Any]) -> list[dict[str, Any]]:
    from . import resident_workspace_storage as workspaces, resource_client, lineage_storage
    records = [resident_from_row(row) for row in rows]
    if not records:
        return []
    # Historical identity is valid even when its Lineage/folder no longer
    # exists. Never project or recreate runtime Workspaces for it.
    active = [record for record in records if not record.get('deleted_at')]
    ids = [record['resident_id'] for record in active]
    all_ids = [record['resident_id'] for record in records]
    bindings = workspaces.bindings_for_residents(connection, all_ids)
    paths = resource_client.workspace_paths_many(
        [record['owned_workspace_id'] for record in active] +
        [binding['workspace_id'] for rid in ids for binding in bindings.get(rid, [])], connection=connection,
    )
    folders = {lineage_id: str(lineage_storage.canonical_lineage_folder(lineage_id).resolve(strict=False))
               for lineage_id in dict.fromkeys(record['lineage_id'] for record in active)}
    projected = {record['resident_id']: record for record in workspaces.project_records(active, bindings, paths, folders)}
    records = [projected.get(record['resident_id'], record) for record in records]
    roles = family_storage.resident_roles(connection, all_ids)
    for record in records:
        rid = record['resident_id']
        record['family'] = roles.get(rid, {})
        if record.get('deleted_at'):
            record['workspace_bindings'] = bindings.get(rid, [])
    return records


class ResidentRecords:
    """Reads and writes of Resident records.

    Moving a Resident re-projects the principal graphs through
    ``project_graphs``; a pending publication is coalesced through ``outbox``.
    """

    def __init__(self, database: Any, *, project_graphs: Any, outbox: Any) -> None:
        self.database = database
        self.project_graphs = project_graphs
        self.outbox = outbox

    def all_residents(self) -> list[dict[str, Any]]:
        with self.database.snapshot() as connection:
            rows = connection.execute(
                resident_select("ORDER BY r.resident_id")
            ).fetchall()
            return residents_from_rows(connection, rows)

    @request_cached
    def resident_record(self, resident_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        with self.database.snapshot() as connection:
            row = connection.execute(
                resident_select('WHERE r.resident_id=%s', include_deleted=include_deleted), (resident_id,)
            ).fetchone()
            if row is None:
                raise KeyError(resident_id)
            return residents_from_rows(connection, [row])[0]

    def resident_usage_memberships(self, resident_ids: Iterable[str]) -> list[dict[str, str]]:
        """Return only resident_id/lineage_id for the requested live subset.

        Preserve request order and omit repeated IDs, without loading Resident
        domain subrecords, Workspace projections, graph or permission state.
        """
        requested = list(dict.fromkeys(str(rid) for rid in resident_ids if str(rid)))
        if not requested:
            return []
        values: dict[str, dict[str, str]] = {}
        with self.database.snapshot() as connection:
            for start in range(0, len(requested), 500):
                for row in connection.execute(
                    'SELECT r.resident_id,n.lineage_id FROM residents r JOIN lineage_nodes n ON n.node_id=r.resident_id '
                    'WHERE r.resident_id=ANY(%s) AND r.deleted_at IS NULL',
                    (requested[start:start + 500],),
                ).fetchall():
                    values[row['resident_id']] = {'resident_id': row['resident_id'], 'lineage_id': row['lineage_id']}
        return [values[rid] for rid in requested if rid in values]

    def resident_identity_records(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Current identity and Family roles for authorization and activity reads.

        Workspace paths and Watchboards belong to their own views. Reading a
        status must not resolve every member's filesystem bindings.
        """
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return []
        with self.database.snapshot() as connection:
            rows = connection.execute(
                resident_select("WHERE r.resident_id = ANY(%s)"), (requested,),
            ).fetchall()
            records = {row['resident_id']: resident_from_row(row) for row in rows}
            roles = family_storage.resident_roles(connection, list(records))
        return [{**records[rid], 'family': roles.get(rid, {})} for rid in requested if rid in records]

    def resident_work_ids(self) -> list[str]:
        """Select identities with a turn or queued input without Workspace reads."""
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT resident_id FROM residents WHERE deleted_at IS NULL AND resident_id IN ("
                "SELECT resident_id FROM resident_execution_work UNION "
                "SELECT resident_id FROM resident_mailbox WHERE place='queue') ORDER BY resident_id"
            ).fetchall()
        return [row['resident_id'] for row in rows]

    def residents_by_id(self, resident_ids: Iterable[str]) -> list[dict[str, Any]]:
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return []
        placeholders = ",".join("%s" for _ in requested)
        with self.database.snapshot() as connection:
            rows = connection.execute(
                resident_select(f"WHERE r.resident_id IN ({placeholders})"),
                requested,
            ).fetchall()
            values = {row["resident_id"]: row for row in residents_from_rows(connection, rows)}
        return [values[item] for item in requested if item in values]

    def live_resident_ids(self, resident_ids: Iterable[str]) -> set[str]:
        """Residents not deleted that are still a node of a Lineage that is not deleted."""
        requested = list(dict.fromkeys(str(item) for item in resident_ids if str(item)))
        if not requested:
            return set()
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT r.resident_id FROM residents r "
                "JOIN lineage_nodes n ON n.node_id=r.resident_id AND n.node_type='resident' AND n.deleted_at IS NULL "
                "JOIN lineages l ON l.lineage_id=n.lineage_id AND l.deleted_at IS NULL "
                "WHERE r.resident_id = ANY(%s) AND r.deleted_at IS NULL",
                (requested,),
            ).fetchall()
        return {row['resident_id'] for row in rows}

    def residents_for_lineage(self, lineage_id: str) -> list[dict[str, Any]]:
        with self.database.snapshot() as connection:
            rows = connection.execute(
                resident_select(
                    'WHERE r.lineage_id=%s ORDER BY r.resident_id'
                ),
                (lineage_id,),
            ).fetchall()
            return residents_from_rows(connection, rows)

    def put_resident_record(
        self, resident_id: str, value: Mapping[str, Any], *, commit_configuration: bool = False, commit_intake: bool = False,
    ) -> None:
        from . import resident_workspace_storage, resource_client
        from .resident_settings_contract import split_runtime_settings
        record = deepcopy(dict(value))
        resident_storage.validate_record_fields(record)
        if record.get('resident_id') != resident_id:
            raise ValueError('Resident record identity must match its storage key')
        record['execution_settings'], record['harness_config'] = split_runtime_settings({
            **(record.get('execution_settings') or {}),
            'backend': record.get('backend') or '',
            'agent_harness': record.get('harness_config') or {},
        })
        configured_backend = record['execution_settings'].pop('backend', record.get('backend'))
        if configured_backend != record.get('backend'):
            raise ValueError('Resident backend and execution configuration differ')

        def write(connection: Any) -> None:
            from management_portal.resources.retired_identity_boundary import require_live_identity
            require_live_identity(connection, resident_id, parents=[record.get('lineage_id')])
            prior = connection.execute('SELECT n.lineage_id,r.backend,r.deleted_at,r.intake_open,r.intake_transition_id,r.registration_state FROM residents r LEFT JOIN lineage_nodes n ON n.node_id=r.resident_id WHERE r.resident_id=%s FOR UPDATE OF r', (resident_id,)).fetchone()
            if record.get('deleted_at') or (prior and prior['deleted_at'] is not None):
                raise ValueError('Deleted Resident identity cannot be updated or restored')
            for field in ('deleted_at', 'deleted_by', 'historical_project_id'):
                if record.get(field) is not None:
                    raise ValueError('Resident deletion facts require the deletion owner')
            if prior is not None and not commit_configuration:
                # Runtime/identity saves may carry an older configuration snapshot.
                # Only the configuration owner may replace it or switch backend.
                record['backend'] = prior['backend']
            if prior is not None and not commit_intake:
                for field in ('intake_open', 'intake_transition_id'):
                    record[field] = prior[field]
            if record.get('registration_state') is None:
                # Only registration starts a Resident before its resources are published.
                record['registration_state'] = prior['registration_state'] if prior is not None else 'ready'
            if record.get('registration_state') not in resident_storage.REGISTRATION_STATES:
                raise ValueError('Resident registration_state must be one of ' + ', '.join(resident_storage.REGISTRATION_STATES))
            if record['registration_state'] == 'resource_pending' and prior is not None and prior['registration_state'] == 'ready':
                record['registration_state'] = 'ready'
            if prior is None:
                # Registering a Resident publishes Lineage membership. Check
                # the caller's version in this transaction, before publishing
                # the child, rather than at the later Fork-edge commit. A
                # stale Fork must not leave a child which the same stale
                # precondition then prevents its compensation from deleting.
                from .lineage_definition_versions import check_definition_version
                check_definition_version(connection, str(record['lineage_id']))
            resource_client.ensure_workspaces(record, connection=connection)
            graph_storage.upsert(connection, 'lineage_nodes', {'node_id': resident_id}, {'lineage_id': record['lineage_id'], 'node_type': 'resident'})
            for table, fields in resident_storage.DOMAIN_TABLES.items():
                if table == 'resident_execution_configuration' and prior is not None and not commit_configuration:
                    continue
                resident_storage.put_fields(connection, table, fields, resident_id, record)
            resident_workspace_storage.replace_bindings(connection, resident_id, record['workspace_bindings'])
            lineage_id = str(record.get('lineage_id') or '')
            if lineage_id and (prior is None or prior['lineage_id'] != lineage_id):
                self.project_graphs.materialize_members(connection, lineage_id)
                self.project_graphs.synchronize(connection, self.project_graphs.lineage_projects(
                    connection, [lineage_id, *(() if prior is None else (prior['lineage_id'],))],
                ))
            if connection.execute(
                'SELECT 1 FROM resident_resource_projection_outbox WHERE resident_id=%s',
                (resident_id,),
            ).fetchone():
                # Session/configuration updates can advance identity metadata
                # while its publication is pending. Fence the old receipt and
                # coalesce the outstanding work with the committed identity.
                row = connection.execute(resident_select('WHERE r.resident_id=%s'), (resident_id,)).fetchone()
                self.outbox.enqueue(
                    connection, resident_id, residents_from_rows(connection, [row])[0],
                )
        self.database.write(write)

    def read_resident_execution_settings(self, resident_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            return resident_storage.read_execution_settings(connection, resident_id)

    def write_resident_execution_settings(self, resident_id: str, settings: Mapping[str, Any]) -> dict[str, Any]:
        from .resident_settings_contract import split_runtime_settings
        execution, harness = split_runtime_settings(settings)
        def write(connection: Any) -> dict[str, Any]:
            row = connection.execute('SELECT backend FROM residents WHERE resident_id=%s AND deleted_at IS NULL FOR UPDATE', (resident_id,)).fetchone()
            if row is None:
                raise KeyError(resident_id)
            backend = execution.pop('backend', row['backend'])
            if backend != row['backend']:
                raise ValueError('Backend changes require the Resident provider transition')
            connection.execute('UPDATE resident_execution_configuration SET execution_settings_json=%s,harness_config_json=%s WHERE resident_id=%s',
                               (json_column(execution), json_column(harness), resident_id))
            return resident_storage.read_execution_settings(connection, resident_id)
        return self.database.write(write)

    def remove_residents(self, tombstones: Iterable[Mapping[str, Any]]) -> list[str]:
        """Retain identity permanently; repeat deletion preserves the first facts.

        The lifecycle service owns quiescing work and detaching graph edges in
        the same enclosing transaction. Agent and historical records remain.
        """
        values = [dict(record) for record in tombstones]
        if any(not record.get('resident_id') or not record.get('deleted_at') for record in values):
            raise ValueError('Resident deletion requires identity and deleted_at')

        def write(connection: Any) -> list[str]:
            workspace_ids = set()
            for record in sorted(values, key=lambda value: value['resident_id']):
                rid = record['resident_id']
                prior = connection.execute(
                    'SELECT deleted_at,owned_workspace_id FROM residents WHERE resident_id=%s FOR UPDATE', (rid,),
                ).fetchone()
                if prior is None:
                    raise KeyError(rid)
                if prior['deleted_at'] is not None:
                    continue
                if prior['owned_workspace_id']:
                    workspace_ids.add(prior['owned_workspace_id'])
                workspace_ids.update(row['workspace_id'] for row in connection.execute(
                    'SELECT workspace_id FROM resident_workspace_bindings WHERE resident_id=%s', (rid,),
                ).fetchall())
                connection.execute(
                    'UPDATE residents SET deleted_at=%s,deleted_by=%s,historical_project_id='
                    'COALESCE((SELECT project_id FROM lineages WHERE lineages.lineage_id=(SELECT lineage_id FROM lineage_nodes WHERE node_id=residents.resident_id)),%s) '
                    'WHERE resident_id=%s',
                    (record['deleted_at'], record.get('deleted_by') or None, record.get('project_id') or None, rid),
                )
                connection.execute('DELETE FROM resident_workspace_bindings WHERE resident_id=%s', (rid,))
                connection.execute('DELETE FROM resident_workspace_transfers WHERE resident_id=%s', (rid,))
            if not workspace_ids:
                return []
            from .resident_workspace_retirement import retire_unreferenced
            return retire_unreferenced(connection, workspace_ids=workspace_ids,
                                       deleted_at=values[0]['deleted_at'],
                                       deleted_by=values[0].get('deleted_by') or '')
        return self.database.write(write)

    def retire_workspaces_under(self, paths, *, deleted_at: str, deleted_by: str) -> list[str]:
        """Retire unused registrations within folders being deleted."""
        from .resident_workspace_retirement import retire_unreferenced
        return self.database.write(lambda connection: retire_unreferenced(
            connection, paths=paths, deleted_at=deleted_at, deleted_by=deleted_by,
        ))

    def resident_deletion(self, resident_id: str) -> dict[str, Any] | None:
        """Historical projection of the same retained identity, never a second row."""
        with self.database.snapshot() as connection:
            row = connection.execute(resident_select(
                'WHERE r.resident_id=%s AND r.deleted_at IS NOT NULL', include_deleted=True,
            ), (resident_id,)).fetchone()
            if row is None:
                return None
            return residents_from_rows(connection, [row])[0]
