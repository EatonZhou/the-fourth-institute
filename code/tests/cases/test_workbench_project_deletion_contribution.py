"""Installed Workbench deletion participates in its caller's transaction.

:module: workflow.project
:design: decision_4648f7fa3295, decision_4348ed990621
"""
from contextlib import nullcontext

import pytest

from management_portal import portal_features
from management_portal.resources.directory_registrations import is_deleted, PROJECTION_RELEASES
from management_portal.resources.registry_store import get_registry_store
from management_portal.workbench_projections import project_deletion
from management_portal.workbench_projections.store import ProjectionError, Store
from tests.cases.test_project_workbench_deletion import APP, setup_project

MOVED = 'workbench_bbbbbbbbbbbb'


def prepared_deletion():
    owners = portal_features.contributions('project.deletion_transaction')
    assert [callback for feature, callback in owners if feature == 'workbench'] == [project_deletion.prepare]
    return dict(owners)['workbench']()


@pytest.mark.parametrize('rollback', [False, True], ids=['commit', 'downstream-rollback'])
def test_contribution_joins_caller_transaction_with_current_members_and_actor(monkeypatch, tmp_path, rollback):
    _, store = setup_project(monkeypatch, tmp_path)
    store.publish(APP, 'alice', 1)
    store.update(APP, {'title': 'Current revision'}, 'alice', 1)
    store.create(MOVED, {'title': 'Moved', 'html': '<p>moved</p>', 'project_ids': ['Example']}, 'alice')
    store.publish(MOVED, 'alice', 1)
    with store.connect() as connection:
        connection.execute('INSERT INTO preferences VALUES (%s,%s,%s)', ('alice', APP, '{}'))
        connection.execute('INSERT INTO role_requests VALUES (%s,%s,%s,%s,%s)',
                           (APP, 'lineage_aaaaaaaaaaaa', 'test', 'alice', '{}'))
    registry = get_registry_store()
    registry._ensure()
    delete = prepared_deletion()
    # Preparation must not freeze membership or select an old published version.
    store.update(MOVED, {'project_ids': ['Other']}, 'alice', 1)

    def unexpected_preparation(*args, **kwargs):
        pytest.fail('Workbench schema preparation ran inside the deletion transaction')

    expected = pytest.raises(RuntimeError, match='later owner failed') if rollback else nullcontext()
    with expected, monkeypatch.context() as guarded:
        guarded.setattr(Store, '__init__', unexpected_preparation)
        guarded.setattr(Store, 'connect', unexpected_preparation)
        # The supplied connection belongs to a different owner/schema.
        with registry.database.transaction() as connection:
            deleted = delete('Example', connection=connection, actor='alice')
            assert deleted == [APP]
            with registry.writer('project_deletions', connection=connection) as writer:
                writer.put('project_deletions', 'Example', {
                    'project_id': 'Example', 'deleted_by': 'alice', 'workbench_ids': deleted,
                })
            # Re-entry neither rediscovers old versions nor repeats deletion.
            assert delete('Example', connection=connection, actor='alice') == []
            if rollback:
                raise RuntimeError('later owner failed')

    assert store.get(MOVED, 'alice')['project_ids'] == ['Other']
    assert is_deleted(APP) is not rollback
    receipt = registry.get('project_deletions', 'Example')
    if rollback:
        assert receipt is None
        assert registry.get(PROJECTION_RELEASES, APP) is None
        assert store.get(APP, 'alice')['revision'] == 2
    else:
        assert receipt['workbench_ids'] == [APP]
        assert receipt['deleted_by'] == registry.get(PROJECTION_RELEASES, APP)['actor'] == 'alice'
    with store.connect() as connection:
        for table, key in [('apps', 'id'), ('versions', 'app_id'), ('projects', 'app_id'),
                           ('preferences', 'app_id'), ('role_requests', 'workbench_id')]:
            exists = connection.execute(f'SELECT 1 FROM {table} WHERE {key}=%s', (APP,)).fetchone()
            assert bool(exists) == rollback


def test_contribution_propagates_entrypoint_rejection_and_rolls_back_caller_write(monkeypatch, tmp_path):
    _, store = setup_project(monkeypatch, tmp_path)
    with store.connect() as connection:
        connection.execute('INSERT INTO native_entrypoints VALUES (%s,%s)', ('test-entry', APP))
    registry = get_registry_store()
    registry._ensure()
    delete = prepared_deletion()
    with pytest.raises(ProjectionError) as failure:
        with registry.database.transaction() as connection:
            with registry.writer('project_deletions', connection=connection) as writer:
                writer.put('project_deletions', 'Example', {'project_id': 'Example'})
            delete('Example', connection=connection, actor='alice')
    assert failure.value.code == 'projection.active_entrypoint'
    assert registry.get('project_deletions', 'Example') is None
    assert registry.get(PROJECTION_RELEASES, APP) is None
    assert not is_deleted(APP)
    assert store.get(APP, 'alice')['revision'] == 1
