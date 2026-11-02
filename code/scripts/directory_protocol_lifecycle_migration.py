"""Offline object-directory migration; the maintenance owner supplies one connection.

No command line, service control, credentials, filesystem deletion or implicit
commit. Run with the Workspace/cleanup migration before new clients start.
"""
import json
from management_common.json_columns import json_text, json_value
from management_common.portal_database import _database_schema
from scripts.directory_protocol_legacy_schema import migrate_move_safety_schema


def migrate(connection):
    lineage = '"' + _database_schema('lineage') + '"'
    registry = '"' + _database_schema('resource_registry') + '"'
    lifecycle = _database_schema('directory_lifecycle')
    connection.execute(f'CREATE SCHEMA IF NOT EXISTS "{lifecycle}"')
    migrate_move_safety_schema(connection, lifecycle)
    counts = {}
    # The Resident/Lineage creation contracts create these exact folders; that
    # is evidence of Portal creation, unlike arbitrary historical Projects.
    for table, key in (('residents', 'resident_id'), ('lineages', 'lineage_id')):
        connection.execute(f'ALTER TABLE {lineage}.{table} ADD COLUMN IF NOT EXISTS directory_json JSONB')
        result = connection.execute(f"UPDATE {lineage}.{table} SET directory_json="
            f"jsonb_build_object('origin','created','owner_id',{key}) WHERE directory_json IS NULL")
        counts[table] = result.rowcount
    row = connection.execute(f"SELECT document_json FROM {registry}.registry_documents "
                             "WHERE collection='project_registry' AND document_id='projects' FOR UPDATE").fetchone()
    if row:
        # The runtime JSON decoder expands area references. Metadata backfills
        # must keep stored references intact, including on Windows where native
        # absolute paths are not converted by json_text's POSIX path encoder.
        document = json.loads(row['document_json']) if isinstance(row['document_json'], str) else row['document_json']
        from management_common.instance_locations import located_path, stored_location
        changed = 0
        for project in document.get('projects', []):
            updated = False
            if 'directory' not in project:
                project['directory'] = {'origin': 'adopted', 'owner_id': project['id']}
                updated = True
            if project.get('path'):
                reference = stored_location(located_path(project['path']))
                if project['path'] != reference:
                    project['path'] = reference
                    updated = True
            changed += int(updated)
        if changed:
            from management_portal.resources.registry_store import RegistryWriter
            RegistryWriter(connection, ('project_registry',), _database_schema('resource_registry')).put(
                'project_registry', 'projects', document)
        counts['projects'] = changed
    counts['converted_removals'] = migrate_cleanup_steps(connection)
    return counts


def validate_project_locations(connection):
    """Check persisted values, never the runtime-decoded view of those values."""
    from management_common.instance_locations import located_path, stored_location
    registry = '"' + _database_schema('resource_registry') + '"'
    row = connection.execute(f"SELECT document_json FROM {registry}.registry_documents "
                             "WHERE collection='project_registry' AND document_id='projects'").fetchone()
    if row:
        document = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        for project in document.get('projects', []):
            value = project.get('path')
            if value and value != stored_location(located_path(value)):
                raise ValueError('Project location is not stored canonically: ' + project['id'])


def migrate_cleanup_steps(connection):
    """Convert durable old remove steps to cleanup jobs, preserving all other effects.

    Outstanding old membership-first transfers and staged trash journals require
    resolving their recorded source/target in the offline window. Refuse them
    explicitly rather than shipping a compatibility branch or guessing truth.
    """
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    from management_common.directory_paths import validate_removal
    from pathlib import Path
    lineage = '"' + _database_schema('lineage') + '"'
    pending = connection.execute(f"SELECT operation_id,resident_id,payload_json FROM {lineage}.resident_lifecycle_operations FOR UPDATE").fetchall()
    for row in pending:
        payload = json_value(row['payload_json'])
        if any(step['kind'] == 'workspace_move' for step in payload['steps'][int(payload.get('next_step', 0)):]):
            raise RuntimeError('Finish old Resident transfer before migration: ' + row['operation_id'])
    staged = connection.execute(f'SELECT lineage_id,operation_json FROM {lineage}.lineage_deletion_operations FOR UPDATE').fetchall()
    if any(json_value(row['operation_json']).get('target_path') or
           not json_value(row['operation_json']).get('definition') for row in staged):
        raise RuntimeError('Resolve old Lineage deletion intents before migration')
    # enqueue/lock only use the supplied connection and qualified table. Avoid
    # constructing a runtime store: its second connection cannot see this
    # offline transaction's newly created schema and would deadlock on DDL.
    queue = DirectoryCleanupStore.__new__(DirectoryCleanupStore)
    queue.table = '"' + _database_schema('directory_lifecycle') + '".cleanup'
    count = 0
    for row in pending:
        payload = json_value(row['payload_json'])
        remaining = []
        for index, step in enumerate(payload['steps'][int(payload.get('next_step', 0)):]):
            if step['kind'] != 'remove':
                remaining.append(step)
                continue
            source = Path(step['source'])
            validate_removal(source)
            queue.enqueue(source, owner_id=row['resident_id'], operation_id=row['operation_id'] + ':remove:' + str(index),
                          actor='directory-protocol-migration', connection=connection)
            count += 1
        payload.update(steps=remaining, next_step=0)
        connection.execute(f'UPDATE {lineage}.resident_lifecycle_operations SET payload_json=%s WHERE operation_id=%s',
                           (json_text(payload), row['operation_id']))
    return count
