from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import threading

import pytest
from management_portal.paper_translation_cache import TranslationCache
from management_portal.paper_reader_annotations import Conflict


def descriptor():
    return dict(text='Exact text \n', source_language='en', target_language='zh',
                model=dict(service='test-service', model='test-model', settings={'temperature': 0}, revision='1'),
                prompt=dict(version='v1', system='translate only'), terminology=dict(version='t1', terms={}),
                context=dict(title='Paper', abstract='Only this context'), access=dict(kind='private'))


@pytest.fixture
def owner():
    def authenticate(context):
        if context not in ('a', 'b', 'shared-a', 'shared-b'): raise PermissionError('authentication')
        return context
    def prepare(context, request):
        value = deepcopy(request)
        if value['access']['kind'] == 'shared' and not context.startswith('shared-'):
            raise PermissionError('shared text not verified')
        return value
    def authorize(context, value):
        if value['access']['kind'] == 'shared' and not context.startswith('shared-'):
            raise PermissionError('shared scope unavailable')
    return TranslationCache(authenticate=authenticate, prepare=prepare, authorize_access=authorize)


def ask(owner, op='op', value=None, context='a', refresh=False):
    return owner.request(context, operation_id=op, request=value or descriptor(), refresh=refresh)


def test_fresh_cache_does_not_install_cancelled_experiment_tables(owner):
    task = ask(owner)
    assert task['status'] == 'queued'
    with owner.database.read() as db:
        assert db.execute("SELECT to_regclass('reader_translation_verified_results')").fetchone()[0] is None
        assert db.execute("SELECT to_regclass('reader_translation_verified_lookup')").fetchone()[0] is None


def test_private_verified_admission_is_removed(owner):
    with pytest.raises(TypeError, match='_verified'):
        owner.request('a', operation_id='cancelled-private', request=descriptor(), _verified=True)


def test_cache_operation_and_private_scope(owner):
    task = ask(owner)
    assert task['cache'] == 'miss'
    assert ask(owner)['task_id'] == task['task_id']
    token, _ = owner.claim('a', task['task_id'])
    assert owner.finish('a', task['task_id'], token, result='translated')['status'] == 'succeeded'
    assert ask(owner, 'next')['cache'] == 'hit'
    assert ask(owner, 'other-account', context='b')['cache'] == 'miss'
    with pytest.raises(PermissionError): owner.get('b', task['task_id'])
    changed = descriptor(); changed['text'] += ' '
    with pytest.raises(Conflict): ask(owner, value=changed)
    assert ask(owner)['task_id'] == task['task_id']


@pytest.mark.parametrize('field', ['text', 'source_language', 'target_language', 'model', 'prompt', 'terminology', 'context'])
def test_key_covers_sent_descriptor(owner, field):
    first = ask(owner)
    changed = descriptor()
    if isinstance(changed[field], str): changed[field] += 'changed'
    else: changed[field]['changed_setting'] = 'different'
    assert ask(owner, 'changed', changed)['task_id'] != first['task_id']


def test_concurrent_dedup_and_one_execution(owner):
    barrier = threading.Barrier(2)
    def request(index):
        barrier.wait(5); return ask(owner, str(index))['task_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(request, [1, 2]))
    assert ids[0] == ids[1]
    # Only one worker owns a lease; model execution is the service's concern.
    def claim(_):
        barrier.wait(5)
        return owner.claim('a', ids[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, [1, 2]))
    assert sum(value is not None for value in claims) == 1
    token, payload = next(value for value in claims if value is not None)
    assert payload == owner.descriptor('a', ids[0])
    assert owner.get('a', ids[0])['status'] == 'running'
    assert owner.finish('a', ids[0], token, result='done')['text'] == 'done'
    assert owner.claim('a', ids[0]) is None


def test_failed_refresh_preserves_success_and_failed_operation(owner):
    first = ask(owner)
    token, _ = owner.claim('a', first['task_id'])
    owner.finish('a', first['task_id'], token, result='translated')
    refresh = ask(owner, 'refresh', refresh=True)
    assert ask(owner, 'parallel-refresh', refresh=True)['task_id'] == refresh['task_id']
    assert ask(owner, 'old-good')['task_id'] == first['task_id']
    token, _ = owner.claim('a', refresh['task_id'])
    failure = owner.finish('a', refresh['task_id'], token, error='secret must not be exposed')
    assert failure['status'] == 'failed' and failure['error'] == 'translation execution failed'
    assert ask(owner, 'refresh', refresh=True)['status'] == 'failed'
    assert ask(owner, 'still-good')['task_id'] == first['task_id']
    fresh = descriptor(); fresh['text'] = 'no success'
    failed = ask(owner, 'fail', fresh)
    token, _ = owner.claim('a', failed['task_id'])
    owner.finish('a', failed['task_id'], token, error=True)
    assert ask(owner, 'retry-new-operation', fresh)['task_id'] != failed['task_id']


def test_expired_task_recovery_and_fencing(owner):
    task = ask(owner)['task_id']; token, _ = owner.claim('a', task)
    with owner.database.transaction() as db:
        db.execute("UPDATE reader_translation_tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task,))
    token2, _ = owner.claim('a', task)
    assert token != token2
    with pytest.raises(Conflict): owner.finish('a', task, token, result='stale')
    assert owner.finish('a', task, token2, result='recovered')['text'] == 'recovered'


def test_server_verified_shared_scope(owner):
    value = descriptor(); value['access'] = dict(kind='shared', scope='verified-paper-context-v1')
    with pytest.raises(PermissionError): ask(owner, value=value)
    first = ask(owner, value=value, context='shared-a')
    assert ask(owner, value=value, context='shared-b')['task_id'] == first['task_id']
    with pytest.raises(PermissionError): owner.claim('shared-b', first['task_id'])
    with pytest.raises(PermissionError): owner.get('a', first['task_id'])
    other = deepcopy(value); other['access']['scope'] = 'other-resource'
    assert ask(owner, 'other', other, context='shared-a')['task_id'] != first['task_id']


def test_resume_binding_uses_same_task_context_and_invalid_result_fails(owner):
    task = ask(owner)['task_id']; owner.claim('a', task)
    # The cache preserves the operation/task binding across lease recovery.
    # Actual Execution receipt recovery is covered by the service tests.
    with owner.database.transaction() as db:
        db.execute("UPDATE reader_translation_tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task,))
    assert ask(owner)['task_id'] == task
    token, payload = owner.claim('a', task)
    assert payload == owner.descriptor('a', task)
    assert owner.finish('a', task, token, result='recovered')['text'] == 'recovered'
    fresh = descriptor(); fresh['text'] = 'invalid result case'
    invalid = ask(owner, 'invalid', fresh)['task_id']
    token, _ = owner.claim('a', invalid)
    with pytest.raises(ValueError, match='bounded successful translation required'):
        owner.finish('a', invalid, token, result=None)
    assert owner.get('a', invalid)['status'] == 'running'
    assert owner.finish('a', invalid, token, error=True)['status'] == 'failed'
    assert ask(owner, 'again', fresh)['cache'] == 'miss'


def test_historical_verified_task_cannot_publish_success_but_can_record_failure(owner):
    task = ask(owner)['task_id']
    with owner.database.transaction() as db:
        db.execute("UPDATE reader_translation_tasks SET publication_mode='verified' WHERE task_id=%s", (task,))
    token, _ = owner.claim('a', task)
    with pytest.raises(ValueError, match='permanently cancelled'):
        owner.finish('a', task, token, result='late experimental success')
    assert owner.get('a', task)['status'] == 'running'
    with pytest.raises(PermissionError):
        owner.finish('b', task, token, error=True)
    failed = owner.finish('a', task, token, error=True)
    assert failed['status'] == 'failed' and failed['text'] is None
    assert ask(owner)['task_id'] == task
    assert ask(owner, 'new-operation')['cache'] == 'miss'
