"""Real temporary PostgreSQL, fake external transports; no deployed data/model."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict
import threading
import time
import pytest

from management_portal.execution.contracts import SystemInstructions
from management_portal.agent_prompt_context import material_task_instructions
from management_portal.paper_reader_annotations import Conflict
from management_portal.paper_translation_google import GoogleTimeout, GoogleTransport
from management_portal.paper_translation_service import ReaderTranslationService, MODEL, SYSTEM


def request(mode='auto'):
    return dict(text='The experiment uses three samples.', source_language='en', target_language='zh-CN',
        mode=mode, document=dict(paper_id='paper-test', attachment_id='attachment-test', attachment_version='v1'))


class FakeExecution:
    def __init__(self): self.requests = {}; self.submits = 0; self.complete = True; self.cancels = []
    def submit(self, value):
        if value.execution_id in self.requests: assert self.requests[value.execution_id] == value
        else: self.submits += 1; self.requests[value.execution_id] = value
    def status(self, execution_id, **kwargs):
        if not self.complete: return {'status':'running'}
        return dict(status='completed', result=dict(output=dict(message='实验使用三个样本。', response={'model':MODEL})))
    def cancel(self, execution_id, **kwargs): self.cancels.append(execution_id)


class FakeGoogle:
    def __init__(self): self.calls = 0; self.fail = None; self.entered = None; self.release = None
    def translate(self, payload, *, timeout, cancelled):
        self.calls += 1
        if self.entered: self.entered.set(); assert self.release.wait(8)
        if cancelled(): raise InterruptedError()
        if self.fail: raise self.fail
        return dict(text='该实验使用三个样本。', provider='google', model='translate_a/single:gtx',
                    model_source='service_algorithm', google_request_ms=12)


@pytest.fixture
def owner():
    revoked = set()
    def authenticate(context):
        if context not in ('a','b') or context in revoked: raise PermissionError('disabled')
        return context
    def authorize(context, document):
        authenticate(context)
        if document['attachment_version'] not in ('v1', 'v2'): raise PermissionError()
    svc = ReaderTranslationService(authenticate=authenticate, authorize_document=authorize,
        configuration=lambda _: dict(revision=1, system_instructions=asdict(material_task_instructions(SYSTEM))),
        execution=FakeExecution(), google=FakeGoogle())
    svc.revoked = revoked
    return svc


def ask(owner, operation='first', mode='auto', actor='a', refresh=False, value=None):
    return owner.request(actor, operation_id=operation, request=value or request(mode), refresh=refresh)


def test_google_success_and_actual_source_private_cache(owner):
    first = ask(owner)
    assert first['status'] == 'succeeded' and first['provider'] == 'google'
    assert first['google_request_ms'] == 12 and first['total_ms'] >= 0
    assert ask(owner, 'second')['cache'] == 'hit'
    assert ask(owner)['cache'] == 'operation'
    assert owner.google.calls == 1 and owner.execution.submits == 0
    ask(owner, 'other', actor='b')
    assert owner.google.calls == 2
    with pytest.raises(PermissionError): owner.get('b', first['task_id'])
    with pytest.raises(Conflict): ask(owner, mode='deepseek')


def test_only_real_google_timeout_falls_back_and_preserves_source(owner):
    owner.google.fail = GoogleTimeout(3000)
    result = ask(owner)
    assert result['status'] == 'succeeded' and result['provider'] == 'deepseek'
    assert result['google_request_ms'] == 3000 and result['model'] == MODEL
    assert ask(owner, 'second')['cache'] == 'hit'
    assert owner.google.calls == 1 and owner.execution.submits == 1
    sent = next(iter(owner.execution.requests.values()))
    assert sent.provider == 'deepseek_primary' and sent.service_type == 'chat_completion'
    assert sent.runtime_options['parameters']['thinking'] == {'type':'disabled'}
    assert sent.runtime_options['timeout_seconds'] == 60 and sent.runtime_options['max_retries'] == 0
    assert sent.session.mode == 'stateless'


@pytest.mark.parametrize('mode,error,expected', [('auto', RuntimeError('HTTP 500'), 'google_failed'),
                                               ('google', GoogleTimeout(3000), 'google_timeout')])
def test_non_timeout_error_and_manual_timeout_never_fallback(owner, mode, error, expected):
    owner.google.fail = error
    value = ask(owner, mode=mode)
    assert value['status'] == 'failed' and value['error']['code'] == expected
    assert owner.execution.submits == 0
    assert ask(owner, 'retry', mode=mode)['cache'] == 'miss'


def test_manual_deepseek_has_no_google_request(owner):
    owner._plan('a', 'fixture-task', owner.prepare('a', request('deepseek')))
    value = ask(owner, mode='deepseek')
    assert value['provider'] == 'deepseek' and owner.google.calls == 0
    assert value['google_request_ms'] is None


def test_translation_final_protocol_payload_is_only_instruction_and_material(owner):
    """Capture the real Chat protocol's wire payload without network or billing."""
    from types import SimpleNamespace
    import json
    from management_portal.execution.contracts import ExecutionRequest
    from management_portal.execution.chat_completion_service import ChatCompletionService

    payload = owner.prepare('a', request('deepseek'))
    planned = ExecutionRequest.from_payload(owner._plan('a', 'wire-fixture', payload))
    calls = []
    class Registry:
        def get(self, key):
            return {'id': key, 'kind': 'openai_compatible_chat', 'default_model': MODEL}
        def request(self, key, endpoint, body, **kwargs):
            calls.append((key, endpoint, body))
            return {'choices': [{'message': {'content': '译文'}}], 'model': MODEL}
    invocation = SimpleNamespace(request=planned, user_prompt=planned.prompt,
        system_context='\n'.join(p.content for p in planned.system_instructions.parts),
        attach_system_context=True)
    context = SimpleNamespace(is_cancelled=lambda: False, emit=lambda *args: None)
    ChatCompletionService(Registry()).execute(invocation, context)
    key, endpoint, wire = calls[0]
    assert key == 'deepseek_primary' and endpoint == 'chat/completions'
    assert wire['messages'] == [
        {'role': 'system', 'content': SYSTEM},
        {'role': 'user', 'content': planned.prompt},
    ]
    assert json.loads(planned.prompt) == {
        'text': request()['text'], 'source_language': 'en',
        'target_language': 'zh-CN', 'terminology': {},
    }
    assert set(wire) == {'model', 'messages', 'thinking', 'max_tokens'}
    assert planned.session.mode == 'stateless' and not planned.metadata


def test_admitted_old_translation_recipe_retains_business_instruction(owner):
    payload = owner.prepare('a', request('deepseek'))
    payload['prompt']['version'] = 'reader-translation-google-flash-v1'
    payload['model']['settings']['system_instructions'] = asdict(SystemInstructions())
    planned = owner._plan('a', 'old-recipe', payload)
    assert planned['runtime_options']['system_prompt'] == SYSTEM


def test_preprocessing_exceeding_three_seconds_does_not_spend_network_budget(owner):
    owner.before_google = lambda: time.sleep(3.05)
    result = ask(owner)
    assert result['provider'] == 'google' and result['total_ms'] >= 3000
    assert result['google_request_ms'] == 12 and owner.execution.submits == 0


def test_concurrent_same_key_merges_and_cancel_discards_late_result(owner):
    owner.google.entered, owner.google.release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(2) as pool:
        active = pool.submit(ask, owner)
        assert owner.google.entered.wait(5)
        shared = ask(owner, 'second')
        assert shared['cache'] == 'inflight' and owner.google.calls == 1
        owner.cancel('a', shared['task_id'])
        owner.google.release.set()
        result = active.result(5)
    assert result['status'] == 'cancelled' and result['text'] is None
    assert owner.execution.submits == 0


def test_refresh_failure_keeps_good_result_and_configuration_document_miss(owner):
    first = ask(owner)
    owner.google.fail = RuntimeError('denied')
    assert ask(owner, 'refresh', refresh=True)['status'] == 'failed'
    assert ask(owner, 'cached')['task_id'] == first['task_id']
    changed = request(); changed['document']['attachment_version'] = 'v2'
    assert ask(owner, 'version', value=changed)['cache'] == 'miss'
    owner.revoked.add('a')
    with pytest.raises(PermissionError): owner.get('a', first['task_id'])


def test_accepted_before_receipt_recovers_same_execution_not_new_charge(owner):
    owner.execution.complete = False
    original_submit = owner.execution.submit
    def unknown_receipt(value): original_submit(value); raise TimeoutError('lost response')
    owner.execution.submit = unknown_receipt
    first = ask(owner, mode='deepseek')
    assert first['status'] == 'running' and owner.execution.submits == 1
    owner.execution.submit = original_submit; owner.execution.complete = True
    assert owner.get('a', first['task_id'])['status'] == 'succeeded'
    assert owner.execution.submits == 1


def test_get_never_submits_prepared_execution_and_post_resumes_same_id(owner):
    task = owner.cache.request('a', operation_id='prepared', request=request('deepseek'))['task_id']
    plan = owner._plan('a', task, owner.cache.descriptor('a', task))
    from psycopg.types.json import Jsonb
    with owner.database.transaction() as db:
        db.execute("INSERT INTO reader_translation_delivery(task_id,phase,request) VALUES (%s,'deepseek',%s)", (task, Jsonb(plan)))
    owner.execution.complete=False
    assert owner.get('a', task)['status']=='running'
    assert owner.execution.submits==0
    owner.execution.complete=True
    assert ask(owner, operation='prepared', mode='deepseek')['status']=='succeeded'
    assert list(owner.execution.requests)==[plan['execution_id']]


def test_deepseek_cancel_calls_owner_and_does_not_publish_success(owner):
    owner.execution.complete=False
    pending=ask(owner, mode='deepseek')
    result=owner.cancel('a', pending['task_id'])
    assert result['status']=='cancelled' and owner.execution.cancels
    owner.execution.complete=True
    assert owner.get('a',pending['task_id'])['status']=='cancelled'


def test_missing_wire_model_is_unknown_not_configured_identity(owner):
    owner.execution.status=lambda *args,**kw:dict(status='completed', result=dict(output=dict(message='done',response={})))
    result=ask(owner,mode='deepseek')
    assert result['model'] is None and result['requested_model']==MODEL


def test_configuration_and_recipe_version_are_cache_material(owner):
    first=ask(owner)
    old=owner.configuration
    owner.configuration=lambda context:{**old(context),'revision':2}
    assert ask(owner,'config-change')['task_id']!=first['task_id']


def test_terminal_empty_api_result_fails_instead_of_polling_forever(owner):
    owner.execution.status=lambda *args,**kw:dict(status='completed', result=dict(output=dict(message='',response={})))
    assert ask(owner,mode='deepseek')['error']['code']=='deepseek_invalid_response'


def test_google_http_error_is_explicit_without_fallback(owner):
    from urllib.error import HTTPError
    owner.google.fail=HTTPError('https://translate.google.com',429,'rate limited',{},None)
    result=ask(owner)
    assert '429' in result['error']['message'] and owner.execution.submits==0


def test_truncated_deepseek_output_is_not_success_cached(owner):
    owner.execution.status=lambda *a,**kw:dict(status='completed',result=dict(output=dict(message='partial',response={'choices':[{'finish_reason':'length'}]})))
    result=ask(owner,mode='deepseek')
    assert result['status']=='failed' and result['error']['code']=='deepseek_incomplete_response'
    assert ask(owner,'retry',mode='deepseek')['cache']=='miss'


def test_interrupted_google_not_misreported_as_timeout(owner):
    prepared = owner.cache.request('a', operation_id='crash', request=request())
    with owner.database.transaction() as db:
        db.execute("INSERT INTO reader_translation_delivery(task_id,phase) VALUES (%s,'google')", (prepared['task_id'],))
    result = owner.get('a', prepared['task_id'])
    assert result['error']['code'] == 'request_interrupted' and owner.execution.submits == 0


def test_real_google_transport_absolute_budget_discards_trickled_late_result():
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): time.sleep(.03); return b' '
    transport = GoogleTransport(opener=lambda *a, **kw: Response())
    started = time.monotonic()
    with pytest.raises(GoogleTimeout): transport.translate(request(), timeout=.08, cancelled=lambda:False)
    assert .07 <= time.monotonic() - started < .3


def test_native_google_late_success_cannot_overwrite_fallback(owner):
    done = threading.Event()
    class Response:
        status = 200
        consumed = False
        def __enter__(self): return self
        def __exit__(self,*args): done.set()
        def read(self,size):
            if self.consumed: return b''
            self.consumed=True; time.sleep(.1)
            return b'[[["late translation"]]]'
    owner.google = GoogleTransport(opener=lambda *a, **kw:Response()); owner.google_timeout=.03
    result = ask(owner)
    assert result['provider'] == 'deepseek'
    assert done.wait(1)
    assert owner.get('a', result['task_id'])['text'] == '实验使用三个样本。'
