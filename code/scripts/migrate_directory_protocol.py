"""Offline directory protocol migration; no service control or filesystem deletion.

The maintenance owner runs this in the offline window after merging the
matching code. Dry-run only reports counts. Application refuses any other
database client and commits all schema and provenance changes together.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql

from management_common.directory_cleanup_store import initialize_directory_schema, initialize_reservations_schema
from management_common.portal_database import _database_schema, portal_database_url
from management_common.postgres_database import PostgresConnection, _portal_row_factory
from management_portal.resources.workspace_creation_store import retire_workspace_creation_schema
from scripts.instance_storage_migration import require_database_alone


def migrate_workspaces(connection, *, workspace_schema, lineage_schema):
    """The caller owns the offline transaction; no files prove provenance by name."""
    table = sql.Identifier(workspace_schema, 'workspaces')
    qualified = table.as_string()
    columns = {row[0] for row in connection.execute(
        'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped', (qualified,),
    ).fetchall()}
    for name, definition in (('origin', 'TEXT'), ('owner_id', 'TEXT'), ('operation_id', 'TEXT')):
        if name not in columns:
            connection.execute(sql.SQL('ALTER TABLE {} ADD COLUMN {} {}').format(
                table, sql.Identifier(name), sql.SQL(definition)))
    connection.execute(sql.SQL("UPDATE {} SET origin=COALESCE(origin,'adopted'),owner_id=COALESCE(owner_id,workspace_id)")
                       .format(table))
    residents = sql.Identifier(lineage_schema, 'residents')
    proven_residents = 0
    if connection.execute('SELECT to_regclass(%s)', (residents.as_string(),)).fetchone()[0]:
        proven_residents = connection.execute(sql.SQL("UPDATE {} w SET origin='created',owner_id=r.resident_id "
            'FROM {} r WHERE r.owned_workspace_id=w.workspace_id').format(table, residents)).rowcount
    handoffs = sql.Identifier(lineage_schema, 'lineage_handoff_items')
    proven_handoffs = 0
    if connection.execute('SELECT to_regclass(%s)', (handoffs.as_string(),)).fetchone()[0]:
        proven_handoffs = connection.execute(sql.SQL("UPDATE {} w SET origin='created',owner_id=e.lineage_id "
            'FROM {} h JOIN {} e ON e.edge_id=h.edge_id '
            "WHERE h.transfer_mode='copy' AND w.origin='adopted' AND "
            "(h.target_workspace_id=w.workspace_id OR (h.target_path<>'' AND h.target_path=w.path))").format(
                table, handoffs, sql.Identifier(lineage_schema, 'lineage_edges'))).rowcount
    connection.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN origin SET NOT NULL, ALTER COLUMN owner_id SET NOT NULL').format(table))
    constraints = {row[0] for row in connection.execute(
        'SELECT conname FROM pg_constraint WHERE conrelid=%s::regclass', (qualified,)).fetchall()}
    for name, definition in (
        ('workspaces_origin_check', "CHECK(origin IN ('created','adopted'))"),
        ('workspaces_owner_id_check', "CHECK(owner_id <> '')"),
        ('workspaces_operation_id_key', 'UNIQUE(operation_id)'),
    ):
        if name not in constraints:
            connection.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} {}').format(table, sql.Identifier(name), sql.SQL(definition)))
    counts = {row[0]: row[1] for row in connection.execute(sql.SQL('SELECT origin,count(*) FROM {} GROUP BY origin').format(table)).fetchall()}
    return {'origins': counts, 'proven_resident_workspaces': proven_residents, 'proven_handoff_workspaces': proven_handoffs}


def inventory(connection):
    table = sql.Identifier(_database_schema('workspace'), 'workspaces')
    columns = [row[0] for row in connection.execute(
        'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND attnum>0 AND NOT attisdropped',
        (table.as_string(),)).fetchall()]
    count = connection.execute(sql.SQL('SELECT count(*) FROM {}').format(table)).fetchone()[0]
    return {'workspaces': count, 'workspace_columns': columns,
            'requires_offline_window': not {'origin', 'owner_id', 'operation_id'} <= set(columns)}


def migrate_account_directories(connection, *, schema):
    table = sql.Identifier(schema, 'registrations')
    if connection.execute('SELECT to_regclass(%s)', (table.as_string(),)).fetchone()[0] is None:
        return {'registrations': 0}
    columns = {row[0] for row in connection.execute(
        'SELECT attname FROM pg_attribute WHERE attrelid=%s::regclass AND NOT attisdropped',
        (table.as_string(),)).fetchall()}
    for name, value in (('directory_origin', 'adopted'), ('directory_owner', 'host')):
        if name not in columns:
            connection.execute(sql.SQL('ALTER TABLE {} ADD COLUMN {} TEXT GENERATED ALWAYS AS '
                "(CASE WHEN config ? 'account_dir' THEN {}::text ELSE NULL::text END) STORED").format(
                    table, sql.Identifier(name), sql.Literal(value)))
    return {'registrations': connection.execute(sql.SQL('SELECT count(*) FROM {} WHERE directory_origin IS NOT NULL').format(table)).fetchone()[0]}


def migrate_schema(connection):
    """Enlist all owners in the caller's already exclusive offline transaction."""
    connection.execute("SET LOCAL lock_timeout='5s'")
    report = {'workspace': migrate_workspaces(connection, workspace_schema=_database_schema('workspace'),
                                              lineage_schema=_database_schema('lineage'))}
    directory_schema = _database_schema('directory_lifecycle')
    connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(directory_schema)))
    # The initial queue was initialized before the isolated implementation was
    # complete. Repair that known partial schema only in this offline entry.
    exists = lambda name: connection.execute('SELECT to_regclass(%s)',
        (sql.Identifier(directory_schema, name).as_string(),)).fetchone()[0] is not None
    if exists('cleanup') and not exists('path_reservations'):
        initialize_reservations_schema(connection, directory_schema)
    initialize_directory_schema(connection, directory_schema)
    from management_common.owned_path_deletion import initialize_owned_deletion_schema
    deletion_schema = _database_schema('directory_deletion')
    connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(deletion_schema)))
    initialize_owned_deletion_schema(connection, deletion_schema)
    retire_workspace_creation_schema(connection, directory_schema)
    from management_portal.resources.creation import migrate_creation_schema as initialize_resource_creations
    initialize_resource_creations(connection, directory_schema)
    from management_portal.society_service.creation_intents import initialize_society_creation_schema
    initialize_society_creation_schema(connection, directory_schema)
    report['finished_legacy_deletions'] = finish_legacy_deletions(connection)
    report['account_directories'] = migrate_account_directories(connection, schema=_database_schema('execution_accounts'))
    from scripts.directory_protocol_lifecycle_migration import migrate as migrate_lifecycle
    from scripts.directory_protocol_resource_migration import migrate as migrate_resources
    from scripts.directory_protocol_retention_migration import (
        migrate as migrate_retention, backfill_workbench_builds, backfill_uploads,
        backfill_paper_drafts, backfill_known_artifacts, backfill_run_ownership, release_deleted_owners,
    )
    from management_portal.resources.config import resource_database_schema
    from management_portal.resources.registration import initialize_registration_schema
    from management_common.instance_manifest import current_instance
    from datetime import datetime, timezone
    report['objects'] = migrate_lifecycle(connection)
    resource_schema = _database_schema(resource_database_schema())
    initialize_registration_schema(connection, resource_schema)
    old_registrations = sql.Identifier(resource_schema, 'resource_registrations')
    if connection.execute('SELECT to_regclass(%s)', (old_registrations.as_string(),)).fetchone()[0]:
        report['obsolete_payload_fingerprints'] = connection.execute(
            sql.SQL('SELECT count(*) FROM {}').format(old_registrations)).fetchone()[0]
        connection.execute(sql.SQL('DROP TABLE {}').format(old_registrations))
    from management_portal.society_service.resident_workspace_storage import create_workspace_deletion_binding
    create_workspace_deletion_binding(connection)
    report['resource_directories'] = migrate_resources(connection,
        registry_schema=_database_schema('resource_registry'), index_schema=_database_schema(resource_database_schema()))
    migrate_retention(connection)
    report['workbench_builds'] = backfill_workbench_builds(connection,
        artifact_root=current_instance().area('features') / 'projection_applications', now=datetime.now(timezone.utc))
    instance = current_instance()
    papers = instance.area('papers')
    report['uploads'] = backfill_uploads(connection, upload_root=instance.area('uploads'))
    report['paper_drafts'] = backfill_paper_drafts(connection,
        draft_root=papers / '_incoming' / 'registration-drafts')
    report['artifacts'] = backfill_known_artifacts(connection,
        article_build_root=instance.directory('cache') / 'article_builds',
        ccf_root=papers / '_work' / 'ccf_dblp', collector_root=papers / '_work' / 'collector')
    report['run_artifacts'] = backfill_run_ownership(connection)
    report['already_deleted_artifacts'] = release_deleted_owners(connection)
    report['context_references'] = migrate_context_references(connection)
    return report


def finish_legacy_deletions(connection):
    """Remove obsolete intents only when identity and every recorded file are gone."""
    from management_common.json_columns import json_value
    from management_common.instance_locations import located_path
    schema = _database_schema('lineage')
    table = sql.Identifier(schema, 'lineage_deletion_operations')
    if connection.execute('SELECT to_regclass(%s)', (table.as_string(),)).fetchone()[0] is None:
        return []
    completed = []
    for row in connection.execute(sql.SQL('SELECT lineage_id,operation_json FROM {} FOR UPDATE').format(table)).fetchall():
        operation = json_value(row['operation_json'])
        if operation.get('definition') and not operation.get('target_path'):
            continue
        owner = connection.execute(sql.SQL('SELECT deleted_at FROM {} WHERE lineage_id=%s FOR UPDATE').format(
            sql.Identifier(schema, 'lineages')), (row['lineage_id'],)).fetchone()
        paths = [Path(located_path(operation[key])) for key in ('source_path', 'target_path', 'canonical_path') if operation.get(key)]
        if owner is None or not owner['deleted_at'] or not paths or any(path.exists() or path.is_symlink() for path in paths):
            raise RuntimeError('Legacy Lineage deletion needs reconciliation: ' + row['lineage_id'])
        connection.execute(sql.SQL('DELETE FROM {} WHERE lineage_id=%s').format(table), (row['lineage_id'],))
        completed.append(row['lineage_id'])
    return completed


def migrate_context_references(connection, *, lineage_schema=None):
    """Only typed context-reference fields; message bodies remain byte-for-byte."""
    from management_portal.resource_refs import migrate_context_file_references
    from management_common.json_columns import json_value, json_text
    declarations = (
        ('lineage_handoff_invocations', ('edge_id',), 'context_refs_json', None),
        ('resident_execution_work', ('resident_id',), 'preparation_json', 'context_refs'),
        ('resident_mailbox', ('resident_id', 'message_id'), 'item_json', 'context_refs'),
    )
    counts = {}
    for table, keys, column, field in declarations:
        qualified = sql.Identifier(lineage_schema or _database_schema('lineage'), table)
        if connection.execute('SELECT to_regclass(%s)', (qualified.as_string(),)).fetchone()[0] is None:
            continue
        changed = 0
        rows = connection.execute(sql.SQL('SELECT {},{} FROM {} WHERE {} IS NOT NULL FOR UPDATE').format(
            sql.SQL(',').join(map(sql.Identifier, keys)), sql.Identifier(column), qualified, sql.Identifier(column))).fetchall()
        for row in rows:
            value = json_value(row[column])
            reference = value.get(field) if field and isinstance(value, dict) else value if not field else None
            if not isinstance(reference, dict):
                continue
            migrated = migrate_context_file_references(reference)
            if reference == migrated:
                continue
            updated = {**value, field: migrated} if field else migrated
            where = sql.SQL(' AND ').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key in keys)
            connection.execute(sql.SQL('UPDATE {} SET {}=%s WHERE {}').format(
                qualified, sql.Identifier(column), where), (json_text(updated), *(row[key] for key in keys)))
            changed += 1
        counts[table] = changed
    return counts


def migrate(connection):
    require_database_alone(connection)
    return migrate_schema(connection)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with psycopg.connect(portal_database_url(), row_factory=_portal_row_factory) as raw:
        connection = PostgresConnection(raw)
        if not args.apply:
            connection.execute('SET TRANSACTION READ ONLY')
        result = migrate(connection) if args.apply else inventory(connection)
    print(json.dumps({'applied': args.apply, **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
