"""Registry ownership upgrades preserve installed dependency fences.

:design: decision_4aa13bc21e8e
"""
import pytest

from management_common import directory_dependencies as deps
from management_common.directory_cleanup_store import DirectoryCleanupStore
from management_common.directory_paths import cleanup_location
from management_common.directory_retention import RetentionStore
from management_portal.resources import directory_ownership_upgrade as upgrade
from management_portal.resources.directory_identity_lifecycle import migrate_dependencies
from management_portal.resources.registry_store import RegistryStore, get_registry_store


def legacy_record(tmp_path, column_type='jsonb'):
    registry = get_registry_store()
    registry._ensure()
    RetentionStore()
    with registry.database.transaction() as connection:
        # Model the retained column type before installing its guards. Changing
        # it afterwards leaves an unrelated cached PL/pgSQL record descriptor.
        assert column_type in ('text', 'jsonb')
        connection.execute(f'ALTER TABLE registry_documents ALTER COLUMN document_json TYPE {column_type} '
                           f'USING document_json::{column_type}')
        migrate_dependencies(connection, identities=[])
    rid = 'skill_000000000083'
    document = {'id': rid, 'directories': [
        {'path': str(tmp_path), 'owner_id': rid, 'origin': 'adopted'}]}
    with registry.writer('knowledge') as writer:
        writer.put('knowledge', rid, document)
        writer.connection.execute("DELETE FROM registry_meta WHERE key='directory_ownership_v1'")
    return registry, rid


def snapshot(registry, rid):
    with registry.database.read() as connection:
        return (
            dict(connection.execute('SELECT * FROM registry_documents WHERE document_id=%s', (rid,)).fetchone()),
            [dict(row) for row in connection.execute('SELECT * FROM registry_meta ORDER BY key').fetchall()],
            [dict(row) for row in connection.execute('SELECT * FROM registry_collections ORDER BY collection').fetchall()],
            [dict(row) for row in connection.execute(
                f'SELECT * FROM {deps.schema()}.directory_dependencies '
                "WHERE kind='resource' AND object_id=%s ORDER BY slot", ('knowledge:' + rid,)).fetchall()],
        )


@pytest.mark.parametrize('column_type', ['text', 'jsonb'])
def test_ownership_upgrade_refreshes_dependencies_once(tmp_path, monkeypatch, column_type):
    registry, rid = legacy_record(tmp_path, column_type)
    try:
        before = registry.revision('knowledge')
        upgraded = RegistryStore()
        result = upgraded.get('knowledge', rid)
        assert result['directories'] == [{'path': str(tmp_path), 'owner_id': rid}]
        assert upgraded.revision('knowledge') > before
        with registry.database.read() as connection:
            deps.require_ready(connection)
        record, _, _, entries = snapshot(registry, rid)
        assert [(r['location'], r['disposition']) for r in entries] == [(cleanup_location(tmp_path), 'protected')]

        def no_queue_for_completed_upgrade(*args, **kwargs):
            raise AssertionError('unchanged registry read entered directory maintenance')

        monkeypatch.setattr(DirectoryCleanupStore, 'lock', no_queue_for_completed_upgrade)
        assert RegistryStore().get('knowledge', rid) == result
        assert registry.revision('knowledge') == record['revision']
    finally:
        with registry.database.transaction() as connection:
            connection.execute('ALTER TABLE registry_documents ALTER COLUMN document_json TYPE jsonb '
                               'USING document_json::jsonb')


def test_ownership_upgrade_refresh_failure_rolls_back_all_state(tmp_path, monkeypatch):
    registry, rid = legacy_record(tmp_path)
    before = snapshot(registry, rid)
    from management_portal.resources import directory_registrations
    refresh = directory_registrations.refresh_directory_dependency

    def fail_after_refresh(*args, **kwargs):
        refresh(*args, **kwargs)
        raise RuntimeError('injected after registry dependency refresh')

    monkeypatch.setattr(directory_registrations, 'refresh_directory_dependency', fail_after_refresh)
    with registry.database.transaction() as connection:
        with pytest.raises(RuntimeError, match='injected after registry dependency refresh'):
            with connection.raw.transaction():
                upgrade.upgrade_registry(connection, registry.database.schema)
    assert snapshot(registry, rid) == before
    with registry.database.read() as connection:
        deps.require_ready(connection)
