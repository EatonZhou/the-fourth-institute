"""Recipient-scoped question reads and private rendering, with no live sends."""
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from management_portal.telepus.question_reminder import read_questions
from management_portal.telepus.weixin_store import chat_key
from tests.cases.test_telepus_notifications import reminder, enable, due, question  # noqa: F401
from tests.cases.test_telepus_weixin import bridge  # noqa: F401
from tests.cases.telepus_bridge_support import ACTOR, FACADE, MEMBER, telepus_schema  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401

PRIVATE = 'PRIVATE_QUESTION_BODY'


@pytest.fixture
def requests(reminder, monkeypatch):
    r = reminder
    original = r.portal.call
    r.questions = [{'title': PRIVATE, 'options': ['选项甲', '选项乙']}]
    r.reads, r.error, r.empty_approval, r.fenced = [], None, False, False

    def call(actor, instruction, arguments):
        if instruction not in ('GetResidentRequest', 'GetMessage'):
            return original(actor, instruction, arguments)
        r.reads.append((actor, instruction, arguments))
        assert actor == ACTOR and arguments['resident_id'] == FACADE
        item = next(q for q in r.box['pending_requests'] if q['message_id'] == arguments['message_id'])
        if r.error:
            raise r.error
        if instruction == 'GetResidentRequest':
            return {'request': {'kind': item['kind'], 'status': 'pending',
                'questions': [] if r.empty_approval else deepcopy(r.questions),
                'interaction': {'kind': 'command', 'request': {'reason': PRIVATE + '允许执行测试？'}}}}
        part = {'type': 'async_questions', 'questions': deepcopy(r.questions)}
        if r.fenced:
            part = {'type': 'text', 'text': '```portal-question\n' + json.dumps(r.questions) + '\n```'}
        return {'message': {'role': 'agent', 'content_parts': [part]}}

    monkeypatch.setattr(r.portal, 'call', call)
    enable(r, question=True)
    return r


@pytest.mark.parametrize('kind', ['question', 'approval', 'question_set'])
def test_question_kinds_render_titles_options_from_visible_conversation(requests, kind, capsys):
    r = requests
    if kind == 'question':
        r.questions = [{'id': 'q1', 'header': '范围', 'question': PRIVATE,
                        'options': [{'label': '选项甲', 'description': '不要代替标签'}, {'label': '选项乙'}]}]
    r.box['pending_requests'] = [{**question('source'), 'kind': kind}]
    due(r)
    assert len(r.server.sent) == 1
    text = r.server.sent[0]['item_list'][0]['text_item']['text']
    assert text.startswith('❓ **助手** 在等你回答')
    assert PRIVATE in text and '选项：选项甲；选项乙' in text
    assert text.endswith('切换对话：/route ' + FACADE + '；此问题请回 Portal 作答。')
    assert ('待批准：' in text) == (kind == 'approval')
    assert r.reads == [(ACTOR, 'GetMessage' if kind == 'question_set' else 'GetResidentRequest',
                        {'resident_id': FACADE, 'message_id': 'projected-source'})]
    with r.notices.router.connect() as c:
        for table in ('notification_deliveries', 'outbound_queue', 'audit_events', 'meta'):
            rows = [dict(row) for row in c.execute('SELECT * FROM ' + table).fetchall()]
            assert PRIVATE not in json.dumps(rows, default=str)
    assert PRIVATE not in str(capsys.readouterr())


def test_native_approval_empty_questions_describes_requested_action(requests):
    r = requests
    r.empty_approval = True
    r.box['pending_requests'] = [{**question('approve'), 'kind': 'approval'}]
    due(r)
    text = r.server.sent[0]['item_list'][0]['text_item']['text']
    assert '待批准：' + PRIVATE + '允许执行测试？' in text


def test_multiple_fenced_questions_limit_visible_title_and_count(requests):
    r = requests
    r.fenced = True
    r.questions = [{'title': '**' + '问' * 130 + '**', 'options': ['甲', '乙']},
                   {'title': '第二问'}, {'title': '第三问'}, {'title': '第四问'}, {'title': '第五问'}]
    r.box['pending_requests'] = [question('many')]
    due(r)
    text = r.server.sent[0]['item_list'][0]['text_item']['text']
    assert '问' * 120 + '…' in text and '问' * 121 not in text
    assert '第二问' in text and '第三问' in text and '另有 2 个问题' in text
    assert '第四问' not in text and '第五问' not in text


def test_summary_includes_each_request_title_and_no_links(requests):
    r = requests
    r.questions = [{'title': PRIVATE + ' [报告](https://example.com/report)'}]
    r.box['pending_requests'] = [question(str(index)) for index in range(4)]
    due(r)
    text = r.server.sent[0]['item_list'][0]['text_item']['text']
    assert '4 件事' in text and text.count(PRIVATE) == 4
    assert 'http' not in text and not r.notices.pending(ACTOR)


@pytest.mark.parametrize('kind', ['question', 'approval', 'question_set'])
@pytest.mark.parametrize('failure', ['permission', 'network', 'empty'])
def test_question_read_failure_retains_pending_and_no_private_diagnostics(requests, kind, failure, capsys):
    r = requests
    r.box['pending_requests'] = [{**question('unreadable'), 'kind': kind}]
    r.error = {'permission': PermissionError(PRIVATE), 'network': TimeoutError(PRIVATE)}.get(failure)
    if failure == 'empty':
        r.questions = [{'title': ''}]
    due(r)
    assert not r.server.sent
    rows = r.notices.pending(ACTOR)
    assert len(rows) == 1 and rows[0]['status'] == 'pending' and rows[0]['invalid_reason'] == ''
    queued = r.adapter.store.pending(chat_key('bot'))
    assert queued[0]['item']['retry_at'] > 0
    assert PRIVATE not in str(capsys.readouterr())
    r.error, r.questions = None, [{'title': '恢复后可读的问题'}]
    with r.notices.router.connect() as c:
        c.execute("UPDATE outbound_queue SET item=item-'retry_at' WHERE item->>'notification_actor'=?", (ACTOR,))
    r.adapter.flush(ACTOR)
    assert len(r.server.sent) == 1 and not r.notices.pending(ACTOR)


def test_real_kernel_default_permissions_recipient_reads_both_question_apis(permissions, monkeypatch):
    from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.society_service import isa
    from management_portal.society_service.isa import conversation
    from management_portal.permission_system.facts import Fact
    from management_portal.telepus.portal_client import PortalClient
    from management_portal.telepus import portal_client
    from management_portal import isa_client

    permissions.users('question_alice', 'question_bob')
    lineage = 'lineage_aaaaaaaaaaaa'
    permissions.place(Fact('lineage', lineage, {'personal_space': 'personal_space:question_alice', 'user': 'question_alice'}),
                      Fact('resident', FACADE, {'lineage': lineage}))
    calls = []
    questions = [{'title': PRIVATE, 'options': ['是', '否']}]
    def request(resident, actor, message):
        calls.append((resident, actor, message))
        assert actor == 'question_alice'
        return {'kind': 'question', 'status': 'pending', 'questions': deepcopy(questions)}
    def message(resident, actor, message):
        request(resident, actor, message)
        return {'role': 'agent', 'recipients': [{'kind': 'user', 'id': actor}],
                'content_parts': [{'type': 'async_questions', 'questions': deepcopy(questions)}]}
    # Only synthetic question data is stubbed; actual declarations/handlers,
    # kernel authorization and the default PAR policy remain in use.
    monkeypatch.setattr(conversation, 'protocol', lambda: SimpleNamespace(
        get_resident_request=request, get_resident_message=message))
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
    for kind in ('question', 'approval', 'question_set'):
        item = {'resident_id': FACADE, 'message_id': 'msg_aaaaaaaaaaaa', 'kind': kind}
        assert read_questions(portal, 'question_alice', item)['questions'] == questions
        with pytest.raises(InstructionError) as denied:
            read_questions(portal, 'question_bob', item)
        assert denied.value.code in ('instruction.forbidden', 'instruction.not_found')
    assert len(calls) == 3
