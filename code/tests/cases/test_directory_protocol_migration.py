"""Ownership is relationship based and migrated only in the offline transaction.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from management_common.portal_database import portal_database
from scripts.migrate_directory_protocol import migrate_workspaces
from tests.cases.test_resident_folder_creation_contract import future_creation_store
from tests.cases.test_workspace_resource_rename import workspace
import pytest


def test_history_backfills_owners_without_retaining_classification():
    workspaces, lineage = portal_database(schema='migration_workspace'), portal_database(schema='migration_lineage')
    with workspaces.transaction() as conn:
        conn.execute('CREATE TABLE workspaces (workspace_id TEXT PRIMARY KEY,path TEXT,deleted_at TIMESTAMPTZ)')
        for identifier in ('unknown', 'owned', 'handoff', 'deleted'):
            conn.execute('INSERT INTO workspaces(workspace_id,path,deleted_at) VALUES (%s,%s,%s)',
                         (identifier, '/host/' + identifier, '2020-01-01T00:00:00Z' if identifier == 'deleted' else None))
    with lineage.transaction() as conn:
        conn.execute('CREATE TABLE residents (resident_id TEXT,owned_workspace_id TEXT)')
        conn.execute("INSERT INTO residents VALUES ('resident_owner','owned'),('resident_deleted','deleted')")
        conn.execute('CREATE TABLE lineage_edges(edge_id TEXT,lineage_id TEXT)')
        conn.execute("INSERT INTO lineage_edges VALUES ('handoff_edge','lineage_owner')")
        conn.execute('CREATE TABLE lineage_handoff_items(edge_id TEXT,transfer_mode TEXT,target_workspace_id TEXT,target_path TEXT)')
        conn.execute("INSERT INTO lineage_handoff_items VALUES ('handoff_edge','copy','handoff','/host/handoff')")
    with workspaces.transaction() as conn:
        result = migrate_workspaces(conn, workspace_schema=workspaces.schema, lineage_schema=lineage.schema)
        assert result['owned_workspaces'] == 4
        rows = {row['workspace_id']: dict(row) for row in conn.execute('SELECT * FROM workspaces').fetchall()}
        assert rows['unknown']['owner_id'] == 'unknown'
        assert rows['owned']['owner_id'] == 'resident_owner'
        assert rows['handoff']['owner_id'] == 'lineage_owner'
        assert rows['deleted']['deleted_at'] is not None
        assert all('origin' not in row for row in rows.values())
        assert migrate_workspaces(conn, workspace_schema=workspaces.schema, lineage_schema=lineage.schema)['owned_workspaces'] == 4


def test_offline_added_owner_columns_match_fresh_schema(future_creation_store):
    from management_portal.society_service.state_store import get_lineage_data_store, lineage_data_schema_matches
    from scripts.directory_protocol_lifecycle_migration import migrate
    from management_portal.resources.registry_store import get_registry_store
    get_registry_store()._ensure()
    store = get_lineage_data_store()
    with store.database.transaction() as connection:
        for table in ('residents', 'lineages'):
            connection.execute(f'ALTER TABLE {table} DROP COLUMN directory_json')
        migrate(connection)
        assert lineage_data_schema_matches(connection)


@pytest.mark.parametrize('legacy', ['created', 'adopted'])
def test_identity_upgrade_is_atomic_preserves_owners_and_reenters(future_creation_store, legacy):
    from management_portal.society_service import directory_owner, resident_domain
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_resident_folder_creation_contract import _new_session_payload
    from management_portal import project_registry
    from management_portal.resources.registry_store import get_registry_store
    from management_common.json_columns import json_value
    from psycopg.types.json import Jsonb

    resident = resident_domain.register_resident(_new_session_payload('Owner upgrade'), 'sample_admin')
    registry = get_registry_store()
    project_id = 'ownership-upgrade'
    document = {'schema_version': 1, 'projects': [{
        'id': project_id, 'path': 'projects:' + project_id,
        'description': 'origin: adopted is prose',
        'directory': {'owner_id': project_id, 'origin': legacy},
    }]}
    with registry.writer(project_registry.COLLECTION) as writer:
        writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, document)
    database = get_lineage_data_store().database
    with database.transaction() as connection:
        for table, key in (('residents', 'resident_id'), ('lineages', 'lineage_id')):
            connection.execute(f'UPDATE {table} SET directory_json=%s WHERE {key}=%s',
                (Jsonb({'owner_id': resident[key], 'origin': legacy}), resident[key]))
    revision = registry.revision(project_registry.COLLECTION)
    with pytest.raises(RuntimeError, match='rollback upgrade'):
        with database.transaction() as connection:
            assert directory_owner.upgrade_ownership(connection) == {'residents': 1, 'lineages': 1}
            assert project_registry.upgrade_directory_ownership(connection) == 1
            raise RuntimeError('rollback upgrade')
    assert registry.revision(project_registry.COLLECTION) == revision
    with database.transaction() as connection:
        assert directory_owner.upgrade_ownership(connection) == {'residents': 1, 'lineages': 1}
        assert project_registry.upgrade_directory_ownership(connection) == 1
        assert directory_owner.upgrade_ownership(connection) == {'residents': 0, 'lineages': 0}
        assert project_registry.upgrade_directory_ownership(connection) == 0
        for table, key in (('residents', 'resident_id'), ('lineages', 'lineage_id')):
            row = connection.execute(f'SELECT directory_json FROM {table} WHERE {key}=%s', (resident[key],)).fetchone()
            assert json_value(row['directory_json']) == {'owner_id': resident[key]}
        raw = connection.execute(f'SELECT document_json FROM "{registry.database.schema}".registry_documents '
            'WHERE collection=%s AND document_id=%s', (project_registry.COLLECTION, project_registry.DOCUMENT_ID)).fetchone()[0]
        import json
        if isinstance(raw, str):
            raw = json.loads(raw)
        assert raw['projects'][0] == {**document['projects'][0], 'directory': {'owner_id': project_id}}
    assert project_registry.load()['projects'][0]['directory'] == {'owner_id': project_id}
    # The running owners must also upgrade at activation, without a separate
    # installer having to call these hooks manually.
    from management_portal.society_service.state_store import LineageDataStore
    with database.transaction() as connection:
        for table, key in (('residents', 'resident_id'), ('lineages', 'lineage_id')):
            connection.execute(f'UPDATE {table} SET directory_json=%s WHERE {key}=%s',
                (Jsonb({'owner_id': resident[key], 'origin': legacy}), resident[key]))
        LineageDataStore._initialize(connection)
        assert directory_owner.upgrade_ownership(connection) == {'residents': 0, 'lineages': 0}
    with registry.writer(project_registry.COLLECTION) as writer:
        writer.put(project_registry.COLLECTION, project_registry.DOCUMENT_ID, document)
    assert project_registry.load()['projects'][0]['directory'] == {'owner_id': project_id}
    with registry.database.transaction() as connection:
        assert project_registry.upgrade_directory_ownership(connection) == 0


def test_reference_migration_preserves_mailbox_recipients_and_prose():
    from scripts.migrate_directory_protocol import migrate_context_references
    from management_common.json_columns import json_text, json_value
    store = portal_database(schema='reference_migration')
    with store.transaction() as connection:
        connection.execute('CREATE TABLE resident_mailbox(resident_id TEXT,message_id TEXT,item_json JSONB)')
        first = {'text': 'workspace_111111111111/file/plain.md',
                 'context_refs': {'resources': ['workspace_111111111111/file/first.md']}}
        second = {'context_refs': {'resources': ['workspace_222222222222/file/second.md']}}
        for rid, item in (('r1', first), ('r2', second)):
            connection.execute('INSERT INTO resident_mailbox VALUES (%s,%s,%s)', (rid, 'shared-message', json_text(item)))
        assert migrate_context_references(connection, lineage_schema=store.schema)['resident_mailbox'] == 2
        rows = {row['resident_id']:json_value(row['item_json']) for row in connection.execute('SELECT * FROM resident_mailbox').fetchall()}
        assert rows['r1']['text'] == first['text']
        assert rows['r1']['context_refs']['resources'][0]['file_reference']['relative_path'] == 'first.md'
        assert rows['r2']['context_refs']['resources'][0]['file_reference']['relative_path'] == 'second.md'
        assert migrate_context_references(connection, lineage_schema=store.schema)['resident_mailbox'] == 0


def test_full_offline_migration_from_text_registry_and_partial_queue(future_creation_store, workspace):
    from management_portal.society_service.state_store import get_lineage_data_store, lineage_data_schema_matches
    from management_portal.resources.registry_store import get_registry_store
    from management_common.portal_database import _database_schema
    from scripts.migrate_directory_protocol import migrate_schema
    from management_portal.resources.config import resource_database_schema
    index = portal_database(schema=resource_database_schema())
    with index.transaction() as connection:
        if connection.execute('SELECT to_regclass(%s)', (f'"{index.schema}".resource_index_records',)).fetchone()[0] is None:
            connection.execute('CREATE TABLE resource_index_records(record_json TEXT)')
    registry = get_registry_store()
    with registry.writer('repo') as writer:
        writer.put('repo', 'repo_111111111111', {'id':'repo_111111111111', 'checkouts':[{'path':str(workspace.root)}]})
    store = get_lineage_data_store()
    with store.database.transaction() as connection:
        connection.execute(f'ALTER TABLE "{registry.database.schema}".registry_documents ALTER COLUMN document_json TYPE TEXT USING document_json::text')
        for table in ('residents', 'lineages'):
            connection.execute(f'ALTER TABLE {table} DROP COLUMN directory_json')
        connection.execute(f'ALTER TABLE {workspace.store.table} DROP COLUMN IF EXISTS origin, DROP COLUMN owner_id, DROP COLUMN operation_id')
        connection.execute(f'DROP TABLE "{_database_schema("directory_lifecycle")}".path_reservations')
        result = migrate_schema(connection)
        assert result['workspace']['owned_workspaces'] >= 1
        assert 'repo_111111111111' in result['resource_directories']
        assert lineage_data_schema_matches(connection)
        assert connection.execute(f'SELECT to_regclass(\'"{_database_schema("directory_retention")}".artifacts\')').fetchone()[0]
