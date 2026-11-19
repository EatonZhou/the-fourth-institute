"""Registry-owned directory facts remain transactional without policy callbacks.

:module: resources.directory-lifecycle
:design: decision_4aa13bc21e8e
"""
from pathlib import Path
import pytest
from management_common import directory_dependencies as deps
from management_common.directory_retention import RetentionStore
from management_portal.resources.registry_store import get_registry_store
from tests.cases.directory_fixture_support import directory_owners  # noqa: F401

pytestmark = pytest.mark.usefixtures('directory_owners')
COLLECTIONS = ('article', 'repo', 'paper', 'knowledge', 'report')


def document(collection, resource_id, root):
    entry = {'path': str(root), 'owner_id': resource_id}
    if collection == 'article':
        return {'id': resource_id, 'roots': [entry], 'canonical_root': str(root)}
    if collection == 'repo':
        return {'id': resource_id, 'checkouts': [entry], 'canonical_checkout': str(root)}
    return {'id': resource_id, 'directories': [entry]}


def dependency_rows(connection):
    return [dict(r) for r in connection.execute(
        f"SELECT object_id,owner_id,disposition FROM {deps.schema()}.directory_dependencies "
        "WHERE kind='resource' ORDER BY object_id").fetchall()]


@pytest.mark.parametrize('text_column', [False, True])
def test_registry_locations_and_dependency_rows_share_authoritative_transaction(tmp_path, text_column):
    from management_portal.resources.registry_store import registered_paths
    registry = get_registry_store()
    if text_column:
        with registry.database.transaction() as connection:
            connection.execute('ALTER TABLE registry_documents ALTER COLUMN document_json TYPE TEXT USING document_json::text')
    expected = []
    with registry.database.transaction() as connection:
        with registry.writer(*COLLECTIONS, connection=connection) as writer:
            for index, collection in enumerate(COLLECTIONS):
                rid = collection + '_' + str(index + 1) * 12
                path = tmp_path/collection;path.mkdir()
                writer.put(collection, rid, document(collection, rid, path))
                expected.append({'object_id': collection+':'+rid, 'owner_id':rid, 'disposition':'protected'})
        assert set(registered_paths(connection=connection)) == {tmp_path/x for x in COLLECTIONS}
        assert dependency_rows(connection) == sorted(expected, key=lambda row:row['object_id'])
    before = registry.get('article', 'article_'+'1'*12)
    with pytest.raises(RuntimeError, match='abort resource retirement'):
        with registry.database.transaction() as connection:
            with registry.writer('article', connection=connection) as writer:
                assert writer.delete('article', 'article_'+'1'*12)
            assert tmp_path/'article' not in registered_paths(connection=connection)
            assert len(dependency_rows(connection)) == 4
            raise RuntimeError('abort resource retirement')
    assert registry.get('article', 'article_'+'1'*12) == before
    with registry.database.transaction() as connection:
        assert len(dependency_rows(connection)) == 5
        with registry.writer('article', connection=connection) as writer:
            assert writer.delete('article', 'article_'+'1'*12)
        assert len(dependency_rows(connection)) == 4
    assert registry.get('article', 'article_'+'1'*12) is None


def test_user_retirement_preserves_all_independent_registered_content(tmp_path):
    from management_portal.portal_access_control import PortalAccessStore
    from management_portal.resources.directory_registrations import unregister_registry
    root=tmp_path/'owned-upload';root.mkdir()
    (root/'private').write_text('user-owned data')
    store=RetentionStore();store.declare(root, category='object_data', owner_id='descriptor-owner')
    registry=get_registry_store(); records={}
    with registry.writer(*COLLECTIONS) as writer:
        for index,collection in enumerate(COLLECTIONS):
            rid=collection+'_'+str(index+1)*12
            path=root/collection;path.mkdir();(path/'keep').write_text(collection)
            records[collection]=(rid,document(collection,rid,path))
            writer.put(collection,rid,records[collection][1])
    users=PortalAccessStore();users.upsert_user('descriptor-owner',actor='sample_admin')
    users.delete_user('descriptor-owner',actor='sample_admin')
    store.queue.run_pending()
    assert users.user('descriptor-owner')['status']=='deleted'
    assert not (root/'private').exists()
    for collection,(rid,doc) in records.items():
        assert registry.get(collection,rid)==doc
        assert (root/collection/'keep').read_text()==collection
    article_id=records['article'][0]
    unregister_registry('article',article_id,actor='sample_admin')
    store.queue.run_pending()
    assert registry.get('article',article_id) is None
    assert not (root/'article').exists()
    for collection in COLLECTIONS[1:]:
        assert (root/collection/'keep').read_text()==collection


@pytest.mark.parametrize('text_column', [False, True])
def test_registry_upgrade_refreshes_dependencies_and_rolls_back_with_source(tmp_path, text_column):
    from management_portal.resources.directory_ownership_upgrade import upgrade_registry
    registry = get_registry_store()
    rid = 'article_999999999999'; path = tmp_path/'old-root';path.mkdir()
    legacy = document('article', rid, path)
    legacy['roots'][0]['origin'] = 'created'
    # Seed retained legacy data through the authoritative writer before upgrade.
    # Do not grant a writer token around the production upgrade being tested.
    if text_column:
        with registry.database.transaction() as connection:
            connection.execute('ALTER TABLE registry_documents ALTER COLUMN document_json TYPE TEXT USING document_json::text')
    with registry.writer('article') as writer:
        writer.put('article', rid, legacy)
    with registry.database.transaction() as connection:
        connection.execute("DELETE FROM registry_meta WHERE key IN ('directory_ownership_v1','directory_ownership_locations_v2')")
    before_revision = registry.revision('article')
    with pytest.raises(RuntimeError, match='abort upgrade'):
        with registry.database.transaction() as connection:
            assert upgrade_registry(connection, registry.database.schema) == 1
            assert dependency_rows(connection) == [{'object_id': 'article:'+rid, 'owner_id': rid, 'disposition':'protected'}]
            raise RuntimeError('abort upgrade')
    assert registry.get('article', rid) == legacy
    assert registry.revision('article') == before_revision
    with registry.database.transaction() as connection:
        assert upgrade_registry(connection, registry.database.schema) == 1
        assert dependency_rows(connection) == [{'object_id': 'article:'+rid, 'owner_id': rid, 'disposition':'protected'}]
        assert upgrade_registry(connection, registry.database.schema) == 0
    assert registry.get('article', rid) == document('article', rid, path)
    assert registry.revision('article') > before_revision
