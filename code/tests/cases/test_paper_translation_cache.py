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
    return TranslationCache(authenticate=authenticate, prepare=prepare, authorize_access=authorize,
                            execute=lambda context, envelope: 'translated:' + envelope['descriptor']['text'])


def ask(owner, op='op', value=None, context='a', refresh=False):
    return owner.request(context, operation_id=op, request=value or descriptor(), refresh=refresh)


def test_cache_operation_and_private_scope(owner):
    task = ask(owner)
    assert task['cache'] == 'miss'
    assert ask(owner)['task_id'] == task['task_id']
    assert owner.run('a', task['task_id'])['status'] == 'succeeded'
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
    entered, release = threading.Event(), threading.Event()
    calls = []
    def execute(context, envelope):
        calls.append(envelope); entered.set(); assert release.wait(5); return 'done'
    owner.execute = execute
    with ThreadPoolExecutor(max_workers=2) as pool:
        active = pool.submit(owner.run, 'a', ids[0]); assert entered.wait(5)
        assert pool.submit(owner.run, 'a', ids[0]).result()['status'] == 'running'
        release.set(); assert active.result()['text'] == 'done'
    assert len(calls) == 1 and calls[0]['history'] == []
    assert calls[0]['fresh_context_id'] == ids[0]


def test_failed_refresh_preserves_success_and_failed_operation(owner):
    first = ask(owner); owner.run('a', first['task_id'])
    refresh = ask(owner, 'refresh', refresh=True)
    assert ask(owner, 'parallel-refresh', refresh=True)['task_id'] == refresh['task_id']
    assert ask(owner, 'old-good')['task_id'] == first['task_id']
    owner.execute = lambda *args: (_ for _ in ()).throw(RuntimeError('secret must not be exposed'))
    assert owner.run('a', refresh['task_id'])['status'] == 'failed'
    assert ask(owner, 'refresh', refresh=True)['status'] == 'failed'
    assert ask(owner, 'still-good')['task_id'] == first['task_id']
    fresh = descriptor(); fresh['text'] = 'no success'
    failed = ask(owner, 'fail', fresh); owner.run('a', failed['task_id'])
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
    with pytest.raises(PermissionError): owner.run('shared-b', first['task_id'])
    with pytest.raises(PermissionError): owner.get('a', first['task_id'])
    other = deepcopy(value); other['access']['scope'] = 'other-resource'
    assert ask(owner, 'other', other, context='shared-a')['task_id'] != first['task_id']


def test_resume_binding_uses_same_task_context_and_invalid_result_fails(owner):
    task = ask(owner)['task_id']; owner.claim('a', task)
    # Simulate canonical execution already submitted before owner process interruption.
    binding = {task: 'completed by existing execution'}
    seen = []
    def resume(context, envelope):
        seen.append(envelope['fresh_context_id'])
        return binding[envelope['task_id']]
    owner.execute = resume
    with owner.database.transaction() as db:
        db.execute("UPDATE reader_translation_tasks SET lease_until=clock_timestamp()-interval '1 second' WHERE task_id=%s", (task,))
    assert owner.run('a', task)['text'] == binding[task]
    assert seen == [task]
    fresh = descriptor(); fresh['text'] = 'invalid result case'
    invalid = ask(owner, 'invalid', fresh)
    owner.execute = lambda *args: None
    assert owner.run('a', invalid['task_id'])['status'] == 'failed'
    assert ask(owner, 'again', fresh)['cache'] == 'miss'
