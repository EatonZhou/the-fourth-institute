"""Catalog refreshes preserve PostgreSQL tuple identity when nothing changed.

:module: resident.identity
:design: decision_8fe87d812a4c
"""
import pytest

from management_portal.society_service.resident_context.store import ResidentContextStore

A = 'resident_aaaaaaaaaaaa'
B = 'resident_bbbbbbbbbbbb'


def physical(store, rid):
    with store.read() as connection:
        row = connection.execute('SELECT ctid::text AS tuple_id, xmin::text AS transaction_id, '
                                 'metadata_fingerprint, active, discovery_visible, global_summary_managed '
                                 'FROM residents WHERE resident_id=%s', (rid,)).fetchone()
    return dict(row)


@pytest.mark.parametrize('managed', [True, False])
def test_identical_refresh_preserves_real_pg_tuple_and_job_versions(managed):
    """A repeated independent/Family catalog refresh makes no physical row update.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = ResidentContextStore()
    record = {'resident_id': A, 'title': 'catalog fixture',
              'discovery_visible': managed, 'global_summary_managed': managed}
    assert store.upsert_catalog([record])['created'] == 1
    before = physical(store, A)
    with store.read() as connection:
        jobs = {table: [tuple(row) for row in connection.execute(
            f'SELECT ctid::text, xmin::text FROM {table} ORDER BY resident_id')]
            for table in ('summary_jobs', 'embedding_jobs')}
    assert store.upsert_catalog([dict(record)]) == {'seen': 1, 'created': 0, 'changed': 0}
    assert physical(store, A) == before
    with store.read() as connection:
        assert {table: [tuple(row) for row in connection.execute(
            f'SELECT ctid::text, xmin::text FROM {table} ORDER BY resident_id')]
            for table in jobs} == jobs


def test_visibility_reactivation_and_family_responsibility_are_not_skipped_by_digest():
    """Visibility/active flags still change and Family summary ownership is enforced.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = ResidentContextStore()
    a = {'resident_id': A, 'title': 'first', 'discovery_visible': True,
         'global_summary_managed': True}
    b = {'resident_id': B, 'title': 'second'}
    store.upsert_catalog([a, b])
    before = physical(store, A)
    a['discovery_visible'] = False
    assert store.upsert_catalog([a, b])['changed'] == 0
    hidden = physical(store, A)
    assert hidden['metadata_fingerprint'] == before['metadata_fingerprint']
    assert hidden['tuple_id'] != before['tuple_id'] and not hidden['discovery_visible']
    store.upsert_catalog([a])
    inactive = physical(store, B)
    assert not inactive['active']
    store.upsert_catalog([a])
    assert physical(store, B) == inactive
    store.upsert_catalog([a, b])
    assert physical(store, B)['active']
    a['global_summary_managed'] = False
    assert store.upsert_catalog([a, b])['changed'] == 1
    assert not physical(store, A)['global_summary_managed'] and store.summary_job(A) is None
    a['global_summary_managed'] = True
    assert store.upsert_catalog([a, b])['changed'] == 1
    assert physical(store, A)['global_summary_managed'] and store.summary_job(A)
    stable = physical(store, A)
    store.upsert_catalog([a, b])
    assert physical(store, A) == stable


def test_metadata_changes_still_write_once_and_trigger_summary_and_embedding():
    """Identity/location changes update the projection and retain existing job triggers.

    :risk: no-external-side-effects
    :profiles: local.fixture
    """
    store = ResidentContextStore()
    record = {'resident_id': A, 'title': 'before', 'lineage_id': 'lineage_aaaaaaaaaaaa'}
    store.upsert_catalog([record])
    before = physical(store, A)
    record.update(title='after', lineage_id='lineage_bbbbbbbbbbbb', lineage_title='new team',
                  lineage_description='new responsibility', project_id='new-project', status='idle')
    assert store.upsert_catalog([record]) == {'seen': 1, 'created': 0, 'changed': 1}
    assert physical(store, A)['tuple_id'] != before['tuple_id']
    with store.read() as connection:
        row = connection.execute('SELECT title,lineage_id,lineage_title,lineage_description,project_id,status '
                                 'FROM residents WHERE resident_id=%s', (A,)).fetchone()
        assert tuple(row) == ('after', 'lineage_bbbbbbbbbbbb', 'new team', 'new responsibility', 'new-project', 'idle')
        for table in ('summary_jobs', 'embedding_jobs'):
            assert connection.execute(f'SELECT reason FROM {table} WHERE resident_id=%s', (A,)).fetchone()[0] == 'metadata_changed'
    stable = physical(store, A)
    store.upsert_catalog([record])
    assert physical(store, A) == stable
