"""Project deletion includes pathless Workbench owners and their revisions.

:design: decision_4648f7fa3295, decision_4348ed990621
"""
import pytest

from management_portal.society_service import project_domain
from management_portal.workbench_projections.store import Store, ProjectionError
from management_portal.resources.directory_registrations import is_deleted, PROJECTION_RELEASES
from management_portal.resources.registry_store import get_registry_store
from tests.cases.test_project_deletion import _configure_project

APP = 'workbench_aaaaaaaaaaaa'


def setup_project(monkeypatch, tmp_path):
    root, *_ = _configure_project(monkeypatch, tmp_path)
    store = Store()
    store.create(APP, {'title': 'Music', 'html': '<p>music</p>', 'project_ids': ['Example']}, 'alice')
    return root, store


def test_pathless_workbench_and_all_versions_commit_with_project(monkeypatch, tmp_path):
    _, store = setup_project(monkeypatch, tmp_path)
    store.publish(APP, 'alice', 1)
    store.update(APP, {'title': 'Music v2'}, 'alice', 1)
    with store.connect() as connection:
        connection.execute('INSERT INTO preferences VALUES (%s,%s,%s)', ('alice', APP, '{}'))
        connection.execute('INSERT INTO role_requests VALUES (%s,%s,%s,%s,%s)', (APP, 'lineage_aaaaaaaaaaaa', 'test', 'alice', '{}'))
    result = project_domain.delete_project('Example', 'alice')
    assert result['workbench_ids'] == [APP]
    assert APP in result['resource_ids'] and is_deleted(APP)
    assert get_registry_store().get(PROJECTION_RELEASES, APP)['actor'] == 'alice'
    with store.connect() as connection:
        for table, key in [('apps', 'id'), ('versions', 'app_id'), ('projects', 'app_id'),
                           ('preferences', 'app_id'), ('role_requests', 'workbench_id')]:
            assert not connection.execute(f'SELECT 1 FROM {table} WHERE {key}=%s', (APP,)).fetchone()


def test_current_membership_selects_owner_not_old_published_version(monkeypatch, tmp_path):
    _, store = setup_project(monkeypatch, tmp_path)
    store.publish(APP, 'alice', 1)
    store.update(APP, {'project_ids': ['Other']}, 'alice', 1)
    other = 'workbench_bbbbbbbbbbbb'
    store.create(other, {'title': 'Other', 'html': '<p>other</p>', 'project_ids': ['Other']}, 'alice')
    result = project_domain.delete_project('Example', 'alice')
    assert not result['workbench_ids']
    assert store.get(APP, 'alice')['revision'] == 2
    assert store.get(other, 'alice')['project_ids'] == ['Other']


@pytest.mark.parametrize('failure', ['entrypoint', 'directory'])
def test_rejected_project_delete_rolls_back_workbench_and_identity(monkeypatch, tmp_path, failure):
    from management_portal import project_registry
    from management_common.directory_cleanup_store import DirectoryCleanupStore
    from management_portal.hatg.contracts import InstructionError
    root, store = setup_project(monkeypatch, tmp_path)
    if failure == 'entrypoint':
        with store.connect() as connection:
            connection.execute('INSERT INTO native_entrypoints VALUES (%s,%s)', ('test-entry', APP))
    else:
        def unavailable(*args, **kwargs):
            raise OSError('cleanup queue unavailable')
        monkeypatch.setattr(DirectoryCleanupStore, 'enqueue', unavailable)
    with pytest.raises(InstructionError) as error:
        project_domain.delete_project('Example', 'alice')
    assert error.value.commit_state == 'not_committed'
    assert project_registry.project('Example') and root.exists()
    assert store.get(APP, 'alice')['revision'] == 1
    assert not is_deleted(APP)
    assert get_registry_store().get('project_deletions', 'Example') is None


def test_deleted_project_cannot_gain_new_workbench_and_direct_delete_tombstones(monkeypatch, tmp_path):
    _, store = setup_project(monkeypatch, tmp_path)
    store.delete(APP, 'alice')
    assert is_deleted(APP)
    project_domain.delete_project('Example', 'alice')
    with pytest.raises(PermissionError, match='deleted identity'):
        store.create('workbench_cccccccccccc', {'title': 'Stale project', 'project_ids': ['Example']}, 'alice')
