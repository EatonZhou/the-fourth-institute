"""Persisted directory classifications disappear without changing identities."""
from psycopg.types.json import Jsonb

from management_portal.resources.registry_store import RegistryStore, get_registry_store
from management_portal.resources.workspace_store import WorkspaceStore
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.resources.directory_ownership_upgrade import upgrade_creations


def test_registry_upgrade_revises_changed_documents_once(tmp_path):
    registry = get_registry_store()
    rid = 'skill_0123456789ab'
    document = {'id': rid, 'directories': [{'path': str(tmp_path), 'origin': 'adopted', 'owner_id': rid}],
                'directory': {'origin': 'adopted', 'owner_id': rid}}
    with registry.writer('knowledge') as writer:
        writer.put('knowledge', rid, document)
        writer.connection.execute("DELETE FROM registry_meta WHERE key='directory_ownership_v1'")
    before = registry.revision('knowledge')
    upgraded = RegistryStore()
    result = upgraded.get('knowledge', rid)
    assert result['directories'] == [{'path': str(tmp_path), 'owner_id': rid}]
    assert result['directory'] == {'owner_id': rid}
    assert upgraded.revision('knowledge') > before
    assert RegistryStore().revision('knowledge') == upgraded.revision('knowledge')


def test_workspace_upgrade_drops_column_and_preserves_identity(tmp_path):
    store = WorkspaceStore()
    rid = 'workspace_0123456789ab'
    store.ensure(rid, str(tmp_path))
    with store.database.transaction() as connection:
        connection.execute(f"ALTER TABLE {store.table} ADD COLUMN origin TEXT DEFAULT 'adopted'")
    reopened = WorkspaceStore()
    row = reopened.rows([rid])[rid]
    assert row['owner_id'] == rid and row['path'] == str(tmp_path)
    assert 'origin' not in row


def test_creation_receipt_upgrade_retains_retry_and_existing_files(tmp_path):
    creator = WorkspaceCreationStore()
    path = tmp_path / 'existing'
    path.mkdir()
    (path / 'data').write_text('keep')
    args = dict(path=str(path), actor='alice', owner_id='', operation_id='register')
    row = creator.register(**args)
    from management_common.portal_database import portal_database
    db = portal_database(schema='directory_lifecycle')
    with db.transaction() as connection:
        receipt = connection.execute('SELECT operation_id,payload,artifacts FROM resource_creations').fetchone()
        payload, artifacts = dict(receipt['payload']), receipt['artifacts']
        payload.pop('artifact_kind')
        payload['origin'] = 'adopted'
        artifacts[0]['kind'] = 'adopted'
        connection.execute('UPDATE resource_creations SET payload=%s,artifacts=%s WHERE operation_id=%s',
                           (Jsonb(payload), Jsonb(artifacts), receipt['operation_id']))
        connection.execute('DELETE FROM directory_schema_migrations')
        assert upgrade_creations(connection, db.schema) == 1
        assert upgrade_creations(connection, db.schema) == 0
    assert WorkspaceCreationStore().register(**args)['workspace_id'] == row['workspace_id']
    assert (path / 'data').read_text() == 'keep'


def test_index_upgrade_removes_classification_before_loading():
    from management_portal.resource_index_store import IndexStore
    from management_portal.resource_index_records import ChangeSet
    from tests.cases.test_resource_index_backend import _record
    store = IndexStore()
    rid = 'skill_0123456789ab'
    store.apply(ChangeSet('skill', upserts=(_record(rid, 'skill', adapter='skill',
        attributes={'directory': {'owner_id': rid, 'origin': 'adopted'}}),)))
    with store.database.transaction() as connection:
        connection.execute("DELETE FROM resource_index_meta WHERE key='directory_ownership_v1'")
    reloaded = IndexStore()
    assert reloaded.internal_get(rid)['attributes']['directory'] == {'owner_id': rid}
