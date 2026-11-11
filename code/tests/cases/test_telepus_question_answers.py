"""Quoted reminder answers; isolated iLink/Portal fixtures, no live user data."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.telepus.question_answer import AnswerFormatError, parse_answers
from tests.cases.test_telepus_notifications import reminder, enable, due, question  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.test_telepus_mention_reply import deliver, sent_text
from tests.cases.telepus_bridge_support import ACTOR, FACADE, MEMBER, telepus_schema  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401

PRIVATE_ANSWER = 'PRIVATE_ANSWER_不得落库'
PRIVATE_QUESTION = 'PRIVATE_QUESTION_不得落库'


@pytest.fixture
def answers(reminder):
    r = reminder
    original = r.portal.call
    r.request = {'kind': 'question', 'status': 'pending', 'questions': [
        {'id': 'scope', 'question': PRIVATE_QUESTION, 'options': [{'label': '定向'}, {'label': '全部'}]}]}
    r.submissions, r.request_reads = [], []
    r.submit_error = r.read_error = None
    r.result_status = 'answered'

    def call(actor, instruction, arguments, **kwargs):
        if instruction == 'GetResidentRequest':
            assert actor == ACTOR and arguments['resident_id'] == FACADE
            r.request_reads.append(deepcopy(arguments))
            if r.read_error:
                raise r.read_error
            return {'request': deepcopy(r.request)}
        if instruction == 'AnswerResidentRequest':
            if r.submit_error:
                raise r.submit_error
            r.submissions.append((actor, instruction, deepcopy(arguments), kwargs))
            r.request['status'] = r.result_status
            return {'status': r.result_status}
        return original(actor, instruction, arguments)

    r.portal.call = call
    enable(r, question=True)
    return r


def remind(r, *, count=1):
    r.box['pending_requests'] = [{**question(str(i)), 'kind': r.request['kind']} for i in range(count)]
    due(r)
    return {'svr_id': str(r.server.server_id + 1)}


@pytest.mark.parametrize(('text', 'answer'), [('1', '定向'), ('全部', '全部'), (PRIVATE_ANSWER, PRIVATE_ANSWER)])
def test_native_quote_answer_uses_user_entry_and_never_persists_content(answers, text, answer, capsys):
    r = answers
    reference = remind(r)
    assert '1. ' + PRIVATE_QUESTION in sent_text(r)
    assert '选项：1. 定向；2. 全部' in sent_text(r)
    assert '引用这条消息回答' in sent_text(r)
    deliver(r, reference, text=text)
    actor, instruction, args, kwargs = r.submissions[0]
    assert actor == ACTOR and instruction == 'AnswerResidentRequest'
    assert args == {'resident_id': FACADE, 'message_id': 'projected-0', 'answers': {'scope': {'answers': [answer]}}}
    assert kwargs['key'].startswith('weixin-question:')
    assert '✅ 已回答' in sent_text(r) and answer in sent_text(r)
    assert not r.portal.delivered
    deliver(r, reference, text=text)  # same inbound id: no second submission
    assert len(r.submissions) == 1
    with r.notices.router.connect() as c:
        for table in ('notification_deliveries', 'outbound_queue', 'audit_events', 'meta'):
            raw = json.dumps([dict(row) for row in c.execute('SELECT * FROM ' + table).fetchall()], default=str)
            assert PRIVATE_ANSWER not in raw and PRIVATE_QUESTION not in raw
        audits = c.execute("SELECT payload_json FROM audit_events WHERE event_type='question_answer'").fetchall()
        assert audits[-1]['payload_json'] == {'resident_id': MEMBER, 'message_id': '0', 'result': 'answered', 'reason': ''}
    assert PRIVATE_ANSWER not in str(capsys.readouterr())


def test_question_quote_fallback_is_scoped_to_bound_user(answers):
    r = answers
    reference = remind(r)
    text = sent_text(r)
    assert r.notices.quoted('other-user', reference) is None
    deliver(r, {'title': text}, text='2')
    assert len(r.submissions) == 1


@pytest.mark.parametrize(('text', 'action'), [('同意', 'accept'), ('YES', 'accept'), ('拒绝', 'decline'), ('n', 'decline')])
def test_approval_accept_and_decline(answers, text, action):
    r = answers
    r.request.update(kind='approval', questions=[], interaction={'request': {'reason': PRIVATE_QUESTION}})
    r.result_status = 'answered' if action == 'accept' else 'declined'
    reference = remind(r)
    deliver(r, reference, text=text)
    assert r.submissions[0][2]['answers'] == {'action': action}
    assert '✅ 已回答' in sent_text(r)


@pytest.mark.parametrize('text', ['不确定', '1', 'yes please'])
def test_invalid_approval_not_submitted(answers, text):
    r = answers
    r.request.update(kind='approval', questions=[], interaction={'request': {'reason': PRIVATE_QUESTION}})
    deliver(r, remind(r), text=text)
    assert not r.submissions and '同意' in sent_text(r) and '拒绝' in sent_text(r)


@pytest.mark.parametrize(('text', 'valid', 'hint'), [
    ('1 2\n2 自由答案', True, ''), ('1 2', False, '第 2 题'),
    ('1 2\n坏行', False, '第 2 行'), ('1 2\n1 全部', False, '重复'),
    ('1 错误选项\n2 自由答案', False, '请选择'),
])
def test_multiple_questions_all_or_nothing(answers, text, valid, hint):
    r = answers
    r.request['questions'][0]['allow_free_text'] = False
    r.request['questions'].append({'id': 'note', 'question': '备注'})
    deliver(r, remind(r), text=text)
    assert bool(r.submissions) == valid
    if valid:
        assert r.submissions[0][2]['answers'] == {'scope': {'answers': ['全部']}, 'note': {'answers': ['自由答案']}}
    else:
        assert hint in sent_text(r)


@pytest.mark.parametrize('status', ['answered', 'declined', 'cancelled', 'expired'])
def test_answered_elsewhere_not_submitted(answers, status):
    r = answers
    reference = remind(r)
    r.request['status'] = status
    deliver(r, reference, text='1')
    assert not r.submissions and '已经处理过' in sent_text(r)


@pytest.mark.parametrize('mode', ['permission', 'read', 'missing_field', 'removed', 'pending'])
def test_failure_or_queued_outcome_is_accurate(answers, mode, capsys):
    r = answers
    reference = remind(r)
    if mode == 'permission': r.submit_error = PermissionError(PRIVATE_ANSWER)
    if mode == 'read': r.read_error = TimeoutError(PRIVATE_ANSWER)
    if mode == 'missing_field': del r.box['pending_requests']
    if mode == 'removed': r.box['pending_requests'] = []
    if mode == 'pending': r.result_status = 'pending'
    deliver(r, reference, text=PRIVATE_ANSWER)
    expected = {'permission': '权限', 'read': '稍后重试', 'missing_field': '稍后重试', 'removed': '已经处理过', 'pending': '等待确认'}[mode]
    assert expected in sent_text(r) and '✅ 已回答' not in sent_text(r)
    assert PRIVATE_ANSWER not in str(capsys.readouterr())


@pytest.mark.parametrize('mode', ['summary', 'too_many'])
def test_summary_and_incomplete_reminders_consumed_without_assistant_routing(answers, mode):
    r = answers
    if mode == 'too_many':
        r.request['questions'] = [{'id': str(i), 'question': '问题' + str(i)} for i in range(4)]
    reference = remind(r, count=4 if mode == 'summary' else 1)
    assert '问题较多，请回 Portal 回答' in sent_text(r)
    reads = len(r.request_reads)
    deliver(r, reference, text='1')
    assert '请回 Portal 回答' in sent_text(r)
    assert len(r.request_reads) == reads and not r.submissions and not r.portal.delivered


def test_options_only_permission_question_rejects_free_text():
    with pytest.raises(AnswerFormatError, match='请选择'):
        parse_answers([{'options': ['同意', '拒绝'], 'credentials': [{'a': '@viewer', 'resource_id': FACADE}]}], '随便')


@pytest.fixture
def question_set_answers(answers):
    from management_portal.hatg.contracts import InstructionError
    r = answers
    original = r.portal.call
    r.request['kind'] = 'question_set'
    r.group = {'type': 'async_questions', 'answer_message_id': 'msg_answer_slot',
               'questions': [{'question_index': 0, 'title': PRIVATE_QUESTION, 'options': ['定向', '全部']}]}
    r.answer_message = None
    r.answer_state = {}
    r.accept_only = False
    def call(actor, instruction, arguments, **kwargs):
        if instruction == 'GetMessage':
            if r.read_error:
                raise r.read_error
            if arguments['message_id'] == r.group['answer_message_id']:
                if r.answer_message is None:
                    raise InstructionError('instruction.not_found', 'Synthetic missing answer')
                return {'message': deepcopy(r.answer_message)}
            return {'message': {'role': 'agent', 'content_parts': [deepcopy(r.group)]}}
        if instruction == 'GetInputState':
            return {'input_state': deepcopy(r.answer_state)}
        if instruction == 'DeliverMessage':
            if r.submit_error:
                raise r.submit_error
            r.submissions.append((actor, instruction, deepcopy(arguments), kwargs))
            if not r.accept_only:
                r.answer_message = deepcopy(arguments['message'])
            return {'accepted': True, 'resident_id': FACADE, 'message_id': r.group['answer_message_id']}
        return original(actor, instruction, arguments, **kwargs)
    r.portal.call = call
    return r


@pytest.mark.parametrize('multiple', [False, True])
def test_question_set_uses_web_answer_slot_and_exact_question_indices(question_set_answers, multiple):
    r = question_set_answers
    if multiple:
        r.group['questions'].append({'question_index': 7, 'title': '补充说明'})
    reference = remind(r)
    deliver(r, reference, text='1 2\n2 ' + PRIVATE_ANSWER if multiple else '2')
    actor, instruction, args, kwargs = r.submissions[0]
    assert (actor, instruction, args['resident_id']) == (ACTOR, 'DeliverMessage', FACADE)
    assert 'temporary_credentials' not in args
    assert args['message']['message_id'] == 'msg_answer_slot'
    assert args['message']['content_parts'][1] == {
        'type': 'async_question_answer', 'source_message_id': 'projected-0',
        'answers': [{'question_index': 0, 'answer': '全部'}] +
                   ([{'question_index': 7, 'answer': PRIVATE_ANSWER}] if multiple else [])}
    assert '✅ 已回答' in sent_text(r)
    deliver(r, reference, text='1', message_id=901)
    assert len(r.submissions) == 1 and '已经处理过' in sent_text(r)
    with r.notices.router.connect() as c:
        for table in ('outbound_queue', 'notification_deliveries', 'audit_events'):
            raw = json.dumps([dict(row) for row in c.execute('SELECT * FROM ' + table).fetchall()], default=str)
            assert PRIVATE_ANSWER not in raw and PRIVATE_QUESTION not in raw


@pytest.mark.parametrize('mode', ['closed', 'answered', 'queued', 'read_failure', 'permission', 'accepted', 'timeout'])
def test_question_set_state_and_uncertain_submission(question_set_answers, mode):
    r = question_set_answers
    reference = remind(r)
    if mode == 'closed': r.group['state'] = 'closed'
    if mode == 'answered': r.answer_message = {'role': 'user'}
    if mode == 'queued': r.answer_state = {'state': 'queued'}
    if mode == 'read_failure': r.read_error = TimeoutError(PRIVATE_ANSWER)
    if mode == 'permission': r.submit_error = PermissionError(PRIVATE_ANSWER)
    if mode == 'accepted': r.accept_only = True
    if mode == 'timeout': r.submit_error = TimeoutError(PRIVATE_ANSWER)
    deliver(r, reference, text='1')
    assert '✅ 已回答' not in sent_text(r) and not r.portal.delivered
    if mode in ('closed', 'answered', 'queued'):
        assert '已经处理过' in sent_text(r) and not r.submissions
    if mode == 'accepted': assert '等待确认' in sent_text(r)
    if mode == 'timeout': assert '无法确认' in sent_text(r)


def test_question_set_authorization_requires_portal_even_when_declining(question_set_answers):
    r = question_set_answers
    r.group['questions'][0]['credentials'] = [{'a': '@viewer', 'resource_id': FACADE}]
    r.group['questions'][0]['options'] = ['同意交出这些权限', '不交出']
    reference = remind(r)
    assert '这个问题要授权，请回 Portal 回答' in sent_text(r)
    assert '引用这条消息回答' not in sent_text(r)
    deliver(r, reference, text='2')
    assert not r.submissions and '这个问题要授权，请回 Portal 回答' in sent_text(r)


@pytest.mark.parametrize('expired', [False, True])
def test_receipt_failure_does_not_persist_or_log_answer(answers, monkeypatch, capsys, expired):
    from management_portal.telepus.weixin_client import WeixinError
    r = answers
    reference = remind(r)
    client_factory = r.adapter.client_factory
    def client(account):
        result = client_factory(account)
        def fail(*args, **kwargs):
            if expired:
                raise WeixinError(-14, PRIVATE_ANSWER)
            raise TimeoutError(PRIVATE_ANSWER)
        result.send = fail
        return result
    monkeypatch.setattr(r.adapter, 'client_factory', client)
    with pytest.raises(RuntimeError, match='answer_receipt' if expired else 'Weixin answer receipt unavailable'):
        deliver(r, reference, text=PRIVATE_ANSWER)
    assert len(r.submissions) == 1
    assert (r.adapter.store.account(ACTOR)['status'] == 'expired') == expired
    with r.notices.router.connect() as c:
        raw = json.dumps([dict(row) for row in c.execute('SELECT * FROM outbound_queue').fetchall()], default=str)
        assert PRIVATE_ANSWER not in raw
    assert PRIVATE_ANSWER not in str(capsys.readouterr())


def test_real_kernel_default_policy_allows_recipient_answer_and_denies_other_user(permissions, monkeypatch):
    from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service import isa, family_quota, resident_conversation_stream
    from management_portal.society_service.isa import conversation, execution
    from management_portal.telepus.question_answer import reply
    from management_portal.permission_system.facts import Fact
    from management_portal.telepus.portal_client import PortalClient
    from management_portal.telepus import portal_client
    from management_portal import isa_client

    permissions.users('answer_alice', 'answer_bob')
    lineage = 'lineage_aaaaaaaaaaaa'
    permissions.place(Fact('lineage', lineage, {'personal_space': 'personal_space:answer_alice', 'user': 'answer_alice'}),
                      Fact('resident', FACADE, {'lineage': lineage}))
    calls, stored = [], {}
    request = {'kind': 'question', 'status': 'pending', 'questions': [{'id': 'q', 'question': '合成问题'}]}
    def native(resident, actor, payload):
        calls.append((actor, 'native'))
        return {'status': 'answered'}
    def delivery(inv, resident, message, **kwargs):
        calls.append((inv.caller, 'async'))
        stored[message['message_id']] = deepcopy(message)
        return {'resident_id': resident, 'message_id': message['message_id'], 'accepted': True}
    source = {'role': 'agent', 'content_parts': [{'type': 'async_questions',
              'answer_message_id': 'msg_answer_slot', 'questions': [{'question_index': 0, 'title': '合成问题'}]}]}
    def read(resident, actor, message_id):
        if message_id == 'msg_answer_slot':
            if message_id not in stored:
                raise KeyError(message_id)
            return stored[message_id]
        return source
    monkeypatch.setattr(conversation, 'protocol', lambda: SimpleNamespace(respond_resident_user_input=native,
        get_resident_request=lambda *a: request, get_resident_message=read))
    monkeypatch.setattr(execution, 'protocol', lambda: SimpleNamespace(get_resident_input_state=lambda *a, **k: {}))
    monkeypatch.setattr(conversation, 'deliver', delivery)
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(message=lambda *a: source))
    monkeypatch.setattr(family_quota, 'answer_role_quota_question', lambda *a: None)
    kernel = InstructionKernel(isa.binding(), store=None)
    def call(actor, instruction, arguments, *, entry, key, channel):
        assert entry == 'web' and channel == 'weixin'
        result = kernel.execute(InstructionCall.from_payload({'instruction': instruction, 'arguments': arguments}),
                                InstructionContext(caller=actor, entry=entry, channel=channel))
        if result.status != 'committed':
            raise InstructionError(**{k: result.error[k] for k in ('code', 'message')})
        return result.result
    monkeypatch.setattr(isa_client, 'call', call)
    monkeypatch.setattr(portal_client, '_prepare_portal_runtime', lambda: None)
    portal = PortalClient(channel='weixin')
    audits = []
    item = {'resident_id': FACADE, 'message_id': 'msg_aaaaaaaaaaaa', 'title': '合成助手'}
    adapter = SimpleNamespace(notifications=SimpleNamespace(snapshot=lambda actor: {'question': {'key': item}}),
        router=SimpleNamespace(portal=portal, store=SimpleNamespace(append_audit=lambda *a, **k: audits.append(k))))
    match = {'key': 'key', 'is_summary': False, 'source_resident_id': FACADE, 'source_message_id': item['message_id']}
    for kind in ('question', 'approval', 'question_set'):
        item['kind'] = request['kind'] = kind
        message = {'message_id': kind, 'item_list': [{'type': 1, 'text_item': {'text': '同意' if kind == 'approval' else '合成回答'}}]}
        for actor in ('answer_alice', 'answer_bob'):
            receipt = reply(adapter, {'portal_actor': actor, 'ilink_bot_id': 'bot'}, message, match)
            if actor == 'answer_alice':
                assert '✅ 已回答' in receipt, receipt
            else:
                assert '✅ 已回答' not in receipt and audits[-1]['payload']['reason'] in ('permission', 'unavailable')
    assert calls == [('answer_alice', 'native'), ('answer_alice', 'native'), ('answer_alice', 'async')]
