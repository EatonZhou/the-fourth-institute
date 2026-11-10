import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from management_portal.paper_reader_annotations import AnnotationStore, Attachment, Conflict


@pytest.fixture
def store():
    def authenticate(context):
        if context not in ('account-a', 'account-b'):
            raise PermissionError('not authenticated')
        return context
    def resolve(context, selector):
        # Explicit test authority: only these two version tokens exist.
        if selector['attachment_version'] not in ('v1', 'v2'):
            raise PermissionError('untrusted version')
        return Attachment(**selector)
    return AnnotationStore(authenticate=authenticate, resolve_attachment=resolve)


def selector(version='v1'):
    return dict(paper_id='paper_fixture', attachment_id='attachment_fixture', attachment_version=version)


def batch(operation='op1', revision='0', version='v1'):
    return dict(**selector(version), operation_id=operation, expected_revision=revision,
                format='zotero-reader-v1', upserts=[dict(id='NOTE0001', type='note', comment='keep', position=dict(pageIndex=0, rects=[[1, 2, 3, 4]]))], deletes=[])


def test_pg_replay_conflict_and_isolation(store):
    payload = batch()
    # Discard response after real COMMIT to simulate response loss.
    response = store.commit('account-a', payload)
    assert store.commit('account-a', payload) == response
    changed = batch(); changed['upserts'][0]['comment'] = 'different'
    with pytest.raises(Conflict, match='different body'):
        store.commit('account-a', changed)
    with pytest.raises(Conflict, match='revision'):
        store.commit('account-a', batch('op2'))
    assert store.read('account-a', selector())['revision'] == '1'
    assert store.read('account-b', selector())['annotations'] == []
    assert store.read('account-a', selector('v2'))['annotations'] == []
    store.commit('account-b', payload)
    store.commit('account-a', batch(version='v2'))
    deletion = batch('delete', '1'); deletion['upserts'] = []; deletion['deletes'] = ['NOTE0001']
    store.commit('account-a', deletion)
    view = store.read('account-a', selector())
    assert view['annotations'] == [] and view['tombstones'] == [dict(id='NOTE0001', revision='2')]
    assert store.read('account-b', selector())['annotations']


def test_pg_two_writer_cas(store):
    ready = threading.Barrier(2)
    def write(index):
        ready.wait(5)
        try:
            return store.commit('account-a', batch(f'writer{index}'))['revision']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, [1, 2])) == ['1', 'conflict']


def test_pg_atomic_rollback(store):
    store.commit('account-a', batch())
    with store.database.transaction() as db:
        db.execute("ALTER TABLE reader_annotations ADD CONSTRAINT reader_test_reject CHECK(annotation_id <> 'REJECT')")
    payload = batch('atomic', '1'); payload['deletes'] = ['NOTE0001']
    payload['upserts'][0]['id'] = 'REJECT'
    try:
        with pytest.raises(Exception, match='reader_test_reject'):
            store.commit('account-a', payload)
        assert store.read('account-a', selector())['revision'] == '1'
        assert store.read('account-a', selector())['annotations'][0]['id'] == 'NOTE0001'
        with store.database.transaction() as db:
            assert db.execute("SELECT count(*) FROM reader_annotation_operations WHERE operation_id='atomic'").fetchone()[0] == 0
    finally:
        with store.database.transaction() as db:
            db.execute('ALTER TABLE reader_annotations DROP CONSTRAINT reader_test_reject')


def test_validation_and_authority(store):
    with pytest.raises(PermissionError): store.read('browser-owner', selector())
    with pytest.raises(PermissionError): store.commit('account-a', batch(version='invented'))
    owner = batch(); owner['owner'] = 'account-b'
    with pytest.raises(ValueError): store.commit('account-a', owner)
    for mutate in [lambda b: b['upserts'][0].update(comment=float('nan')),
                   lambda b: b['upserts'][0].update(comment='x' * 300000),
                   lambda b: b['upserts'][0].update(extra=['x' * 60000] * 5),
                   lambda b: b['upserts'][0].update(extra=10 ** 400),
                   lambda b: b['deletes'].append('NOTE0001'),
                   lambda b: b['upserts'][0].update(position={'pageIndex': True})]:
        value = batch(); mutate(value)
        with pytest.raises(ValueError): store.commit('account-a', value)
    mismatch = AnnotationStore(authenticate=lambda c: 'account-a', resolve_attachment=lambda c, s: Attachment(**selector('v2')))
    with pytest.raises(Conflict, match='descriptor'): mismatch.read('account-a', selector())
