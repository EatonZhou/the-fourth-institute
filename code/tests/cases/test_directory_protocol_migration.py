"""Provenance is evidence based and migrated only in the offline transaction.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from management_common.portal_database import portal_database
from scripts.migrate_directory_protocol import migrate_workspaces
from tests.cases.test_resident_folder_creation_contract import future_creation_store
from tests.cases.test_workspace_resource_rename import workspace


def test_history_defaults_to_adopted_and_only_evidence_changes_it():
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
        assert result['origins'] == {'created': 3, 'adopted': 1}
        rows = {row['workspace_id']: dict(row) for row in conn.execute('SELECT * FROM workspaces').fetchall()}
        assert rows['unknown']['owner_id'] == 'unknown'
        assert rows['owned']['owner_id'] == 'resident_owner'
        assert rows['handoff']['owner_id'] == 'lineage_owner'
        assert rows['deleted']['deleted_at'] is not None
        assert migrate_workspaces(conn, workspace_schema=workspaces.schema, lineage_schema=lineage.schema)['origins'] == result['origins']


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
        connection.execute(f'ALTER TABLE {workspace.store.table} DROP COLUMN origin, DROP COLUMN owner_id, DROP COLUMN operation_id')
        connection.execute(f'DROP TABLE "{_database_schema("directory_lifecycle")}".path_reservations')
        result = migrate_schema(connection)
        assert result['workspace']['origins']['adopted'] >= 1
        assert 'repo_111111111111' in result['resource_directories']
        assert lineage_data_schema_matches(connection)
        assert connection.execute(f'SELECT to_regclass(\'"{_database_schema("directory_retention")}".artifacts\')').fetchone()[0]
