"""Persisted directory classifications disappear without changing identities."""
import json

import pytest
from psycopg.types.json import Jsonb

from management_portal.resources.registry_store import RegistryStore, get_registry_store
from management_portal.resources.workspace_store import WorkspaceStore
from management_portal.resources.workspace_creation_store import WorkspaceCreationStore
from management_portal.resources.directory_ownership_upgrade import upgrade_creations


@pytest.mark.parametrize('already_migrated', [False, True])
@pytest.mark.parametrize('text_column', [False, True])
def test_registry_upgrade_preserves_and_repairs_stored_area_references(
        tmp_path, monkeypatch, already_migrated, text_column):
    from tests.cases.instance_test_support import select_instance
    from management_portal.resources.directory_ownership_upgrade import upgrade_registry

    instance = select_instance(monkeypatch, tmp_path / 'instance')
    registry = get_registry_store()
    rid = 'article_0123456789ab'
    relative = 'projects:Example/articles/' + rid
    absolute = str(instance.area('projects') / 'Example/articles' / rid)
    directory = {'path': absolute if already_migrated else relative, 'owner_id': rid}
    if not already_migrated:
        directory['origin'] = 'created'
    document = {'id': rid, 'canonical_root': directory['path'], 'roots': [directory],
                'description': absolute, 'external_path': str(tmp_path / 'external')}
    registry._ensure()
    with registry.database.transaction() as connection:
        if text_column:
            connection.execute('ALTER TABLE registry_documents ALTER COLUMN document_json TYPE TEXT USING document_json::text')
        connection.execute('INSERT INTO registry_documents VALUES (%s,%s,%s,%s)',
                           ('article', rid, 0, json.dumps(document)))
        connection.execute("DELETE FROM registry_meta WHERE key='directory_ownership_locations_v1'")
        if not already_migrated:
            connection.execute("DELETE FROM registry_meta WHERE key='directory_ownership_v1'")
        assert upgrade_registry(connection, registry.database.schema) == 1
        raw = connection.execute('SELECT document_json FROM registry_documents WHERE document_id=%s', (rid,)).fetchone()[0]
        stored = json.loads(raw) if isinstance(raw, str) else raw
        assert stored == {**document, 'canonical_root': relative,
                          'roots': [{'path': relative, 'owner_id': rid}]}
        assert upgrade_registry(connection, registry.database.schema) == 0
    assert RegistryStore().get('article', rid)['canonical_root'] == absolute


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
