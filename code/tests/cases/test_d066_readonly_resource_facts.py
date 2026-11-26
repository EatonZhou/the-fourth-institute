"""Preview facts read persisted owner snapshots without bootstrap or migration."""
import json
import secrets
from unittest.mock import Mock

import pytest

from management_common.portal_database import portal_database
from management_portal import project_registry, resource_index_backend, resource_index_store
from management_portal.resources import registry_store, collaboration_registry


@pytest.fixture
def database():
    # conftest selects a disposable PostgreSQL server; this schema belongs only
    # to the case and is deliberately absent until the case installs fixtures.
    db = portal_database(schema='rf_' + secrets.token_hex(4))
    yield db
    db.close()


def install_registry(db, documents=()):
    with db.connect() as conn:
        for statement in registry_store._DDL:
            conn.execute(statement)
        conn.execute('INSERT INTO registry_meta VALUES (%s,%s)', ('schema', registry_store.SCHEMA_VERSION))
        for collection, identifier, document in documents:
            conn.execute('INSERT INTO registry_collections VALUES (%s,7) ON CONFLICT DO NOTHING', (collection,))
            conn.execute('INSERT INTO registry_documents VALUES (%s,%s,7,%s)',
                         (collection, identifier, json.dumps(document)))


def install_index(db, records=()):
    with db.connect() as conn:
        conn.execute('CREATE TABLE resource_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        conn.execute('CREATE TABLE resource_index_records (resource_id TEXT PRIMARY KEY, adapter TEXT, record_json JSONB)')
        conn.execute('INSERT INTO resource_index_meta VALUES (%s,%s),(%s,%s)',
            ('schema', resource_index_store.RESOURCE_INDEX_STORE_SCHEMA, 'index_updated_at', 'stable-revision'))
        for record in records:
            conn.execute('INSERT INTO resource_index_records VALUES (%s,%s,%s)',
                (record['resource_id'], record['source']['adapter'], json.dumps(record)))


def test_readonly_fact_entrypoints_exist_without_service_construction():
    assert callable(getattr(registry_store.RegistryStore, 'readonly_snapshots', None))
    assert callable(getattr(collaboration_registry.CollaborationDescriptorStore, 'readonly_lists', None))
    assert callable(getattr(project_registry, 'projects_readonly', None))
    assert callable(getattr(resource_index_store.IndexStore, 'readonly_authorization_snapshot', None))
    assert callable(getattr(resource_index_backend, 'readonly_authorization_snapshot', None))


def test_registry_readonly_snapshot_never_initializes_and_detaches(database, monkeypatch):
    install_registry(database, [('resident', 'resident_0123456789ab', {'answer': [1]}),
                                ('family', 'family_0123456789ab', {'answer': [2]})])
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(store, '_ensure', Mock(side_effect=AssertionError('must not initialize')))
    monkeypatch.setattr(database, '_ensure_schema', Mock(side_effect=AssertionError('must not bootstrap')))
    result = store.readonly_snapshots('resident', 'family', 'lineage')
    assert result == {'resident': (7, {'resident_0123456789ab': {'answer': [1]}}),
                      'family': (7, {'family_0123456789ab': {'answer': [2]}}), 'lineage': (0, {})}
    result['resident'][1]['resident_0123456789ab']['answer'].append(99)
    assert store.readonly_snapshot('resident')[1]['resident_0123456789ab']['answer'] == [1]
    assert not store._ready


def test_collaboration_and_project_facts_do_not_migrate_or_cache(database, monkeypatch):
    rid = 'resident_0123456789ab'
    descriptor = {'schema': collaboration_registry.COLLABORATION_DESCRIPTOR_SCHEMA, 'resource_id': rid,
                  'title': 'Author', 'status': 'active', 'project_ids': ['Alpha'],
                  'attributes': {}, 'relations': [], 'updated_at': '2026-11-26T00:00:00Z'}
    project = {'id': 'Alpha', 'status': 'active', 'created_by': {'user': 'alice'}}
    document = {'schema_version': 1, 'status_vocab': [], 'tag_vocab': [{'legacy': 'unchanged'}],
                'projects': [project]}
    install_registry(database, [('resident', rid, descriptor),
                                (project_registry.COLLECTION, project_registry.DOCUMENT_ID, document)])
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(project_registry, 'get_registry_store', lambda: store)
    monkeypatch.setattr(store, '_ensure', Mock(side_effect=AssertionError('bootstrap')))
    monkeypatch.setattr(store, 'writer', Mock(side_effect=AssertionError('writer')))
    monkeypatch.setattr(project_registry, 'load', Mock(side_effect=AssertionError('load migration')))
    monkeypatch.setattr(project_registry, '_migrate_tags', Mock(side_effect=AssertionError('tags migration')))
    monkeypatch.setattr(project_registry, 'upgrade_directory_ownership', Mock(side_effect=AssertionError('directory migration')))
    cache = (999, {'projects': [{'id': 'stale'}]})
    monkeypatch.setattr(project_registry, '_cache', cache)
    descriptors = collaboration_registry.CollaborationDescriptorStore(registry=store)
    result = descriptors.readonly_lists('resident', 'lineage', 'family')
    assert result['resident'][0]['project_ids'] == ['Alpha']
    assert result['lineage'] == result['family'] == []
    assert descriptors._records == descriptors._revisions == {}
    assert project_registry.projects_readonly() == [project]
    assert project_registry._cache is cache
    assert store.readonly_snapshot(project_registry.COLLECTION)[1] == {project_registry.DOCUMENT_ID: document}
    with pytest.raises(ValueError, match='workspace'):
        descriptors.readonly_list('workspace')


def test_index_readonly_facts_use_persisted_snapshot_without_workers(database, monkeypatch):
    record = {'resource_id': 'article_0123456789ab', 'resource_type': 'article', 'title': 'Paper',
              'source': {'adapter': 'article'},
              'attributes': {'project_ids': ['Alpha'], 'relations': [], 'created_by': 'alice'}}
    install_index(database, [record])
    monkeypatch.setattr(resource_index_store, 'portal_database', lambda **kw: database)
    monkeypatch.setattr(resource_index_store.IndexStore, '__init__', Mock(side_effect=AssertionError('store constructor')))
    monkeypatch.setattr(resource_index_backend.ResourceIndexService, '__init__', Mock(side_effect=AssertionError('index worker')))
    monkeypatch.setattr(resource_index_backend, 'get_service', Mock(side_effect=AssertionError('service singleton')))
    monkeypatch.setattr(database, '_ensure_schema', Mock(side_effect=AssertionError('schema bootstrap')))
    expected = {'revision': 'stable-revision', 'records': [
        {k: record[k] for k in ('resource_id', 'resource_type', 'title', 'attributes')}]}
    result = resource_index_backend.readonly_authorization_snapshot()
    assert result == expected
    result['records'][0]['attributes']['project_ids'].append('unpersisted')
    assert resource_index_store.IndexStore.readonly_authorization_snapshot() == expected
    # The existing in-memory projection has the same public contract.
    import threading
    warm = object.__new__(resource_index_store.IndexStore)
    warm._lock = threading.RLock()
    warm._index_updated_at = 'stable-revision'
    warm._records = {record['resource_id']: record}
    assert warm.authorization_snapshot() == expected


@pytest.mark.parametrize('target', ['registry', 'index'])
def test_cold_missing_schema_is_error_without_creation(database, monkeypatch, target):
    import psycopg
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(resource_index_store, 'portal_database', lambda **kw: database)
    read = (lambda: store.readonly_snapshot('resident')) if target == 'registry' else resource_index_store.IndexStore.readonly_authorization_snapshot
    with pytest.raises(Exception, match='(?i)(schema|namespace|does not exist|不存在)'):
        read()
    with psycopg.connect(database.dsn) as conn:
        assert conn.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s', (database.schema,)).fetchone() is None


@pytest.mark.parametrize('target', ['registry', 'index'])
def test_existing_schema_missing_tables_is_error_without_creation(database, monkeypatch, target):
    import psycopg
    with database.connect() as conn:
        conn.execute('CREATE TABLE sentinel (value TEXT)')
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(resource_index_store, 'portal_database', lambda **kw: database)
    read = (lambda: store.readonly_snapshot('resident')) if target == 'registry' else resource_index_store.IndexStore.readonly_authorization_snapshot
    with pytest.raises(psycopg.errors.UndefinedTable):
        read()
    with database.readonly_snapshot() as conn:
        tables = conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()').fetchall()
    assert [row[0] for row in tables] == ['sentinel']


@pytest.mark.parametrize('target', ['registry', 'index'])
def test_old_incompatible_columns_fail_without_upgrade(database, monkeypatch, target):
    import psycopg
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(resource_index_store, 'portal_database', lambda **kw: database)
    if target == 'registry':
        install_registry(database)
        table, column = 'registry_documents', 'document_json'
        read = lambda: store.readonly_snapshot('resident')
    else:
        install_index(database)
        table, column = 'resource_index_records', 'record_json'
        read = resource_index_store.IndexStore.readonly_authorization_snapshot
    with database.connect() as conn:
        conn.execute(f'ALTER TABLE {table} RENAME COLUMN {column} TO legacy_payload')
    with pytest.raises(psycopg.errors.UndefinedColumn):
        read()
    with database.readonly_snapshot() as conn:
        columns = [row[0] for row in conn.execute(
            'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
            (table,)).fetchall()]
    assert 'legacy_payload' in columns and column not in columns


@pytest.mark.parametrize('target', ['registry', 'index', 'project'])
def test_unsupported_persisted_format_is_explicit_and_unchanged(database, monkeypatch, target):
    store = registry_store.RegistryStore()
    store.database = database
    monkeypatch.setattr(project_registry, 'get_registry_store', lambda: store)
    monkeypatch.setattr(resource_index_store, 'portal_database', lambda **kw: database)
    if target == 'project':
        old = {'schema_version': 0, 'projects': [{'id': 'Alpha'}]}
        install_registry(database, [(project_registry.COLLECTION, project_registry.DOCUMENT_ID, old)])
        read = project_registry.projects_readonly
        sql = 'SELECT document_json FROM registry_documents'
    else:
        if target == 'registry':
            install_registry(database)
            table = 'registry_meta'
            read = lambda: store.readonly_snapshot('resident')
        else:
            install_index(database)
            table = 'resource_index_meta'
            read = resource_index_store.IndexStore.readonly_authorization_snapshot
        with database.connect() as conn:
            conn.execute(f"UPDATE {table} SET value='old-unsupported' WHERE key='schema'")
        sql = f"SELECT value FROM {table} WHERE key='schema'"
    with database.readonly_snapshot() as conn:
        before = conn.execute(sql).fetchone()[0]
    with pytest.raises(RuntimeError, match='Unsupported'):
        read()
    with database.readonly_snapshot() as conn:
        assert conn.execute(sql).fetchone()[0] == before


def test_registry_preview_ignores_ambient_uncommitted_writer(database):
    install_registry(database, [('resident', 'resident_0123456789ab', {'value': 'committed'})])
    store = registry_store.RegistryStore()
    store.database = database
    with database.transaction() as conn:
        conn.execute("UPDATE registry_documents SET document_json=%s", (json.dumps({'value': 'pending'}),))
        conn.execute('UPDATE registry_collections SET revision=8')
        assert store.readonly_snapshot('resident') == (7, {'resident_0123456789ab': {'value': 'committed'}})
    assert store.readonly_snapshot('resident') == (8, {'resident_0123456789ab': {'value': 'pending'}})
