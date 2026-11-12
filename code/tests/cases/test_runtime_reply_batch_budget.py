"""Runtime round reads share exact retained-message lookup across holders.

:module: lineage.service
:importance: P1
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service.resident_conversation_inputs import ConversationInputs


def message(identity, text, reply='input'):
    return dict(message_id=identity, role='agent', reply_to_message_id=reply,
                content_parts=[dict(type='text', text=text)])


def repository(states, current, retained):
    calls = []
    value = ConversationInputs.__new__(ConversationInputs)
    value.input_states_by_id = lambda requested: {k: states[k] for k in requested if k in states}

    def lookup(requested):
        calls.append(requested)
        return {key: retained[key] for key in requested if key in retained}

    value.agent_data = SimpleNamespace(
        chat=SimpleNamespace(trace_chat_messages_many=lookup),
        chat_replies_many=lambda windows, limit: current,
        chat_reply_blocks_many=lambda windows, limit: current)
    return value, calls


def test_retained_replies_across_holders_use_one_exact_batch():
    """一百个已清聊天的承接者一次补读，同名回复按 holder 隔离。"""
    keys = [(f'resident_{i:012x}', 'input') for i in range(100)]
    states = {k: dict(state='completed', reply_message_ids=['reply']) for k in keys}
    retained = {(r, 'reply'): message('reply', r) for r, _ in keys}
    value, calls = repository(states, {}, retained)
    result = value.reply_rounds(keys + keys[:1])
    assert len(calls) == 1
    assert len(calls[0]) == 100
    assert set(calls[0]) == set(retained)
    for key in keys:
        assert result[key]['replies'][0]['content_parts'][0]['text'] == key[0]


@pytest.mark.parametrize('display', [False, True])
def test_batch_keeps_limit_order_withdrawal_and_display_ownership(display):
    """只补缺少的末尾和撤回证据，保留限额、顺序及展示归属筛选。"""
    states = {('r', 'input'): dict(state='completed', reply_message_ids=['first', 'last', 'excluded']),
              ('r', 'gone'): dict(state='cancelled'),
              ('s', 'stopped'): dict(state='cancelled')}
    current = {'r': {'input': [message('first', 'current')]}}
    retained = {('r', 'last'): message('last', 'retained'),
                ('s', 'stopped'): dict(message_id='stopped', role='user', content_parts=[])}
    value, calls = repository(states, current, retained)
    result = value.reply_rounds(list(states), limit=2, _display_blocks=display)
    assert set(calls[0]) == {('r', 'last'), ('r', 'gone'), ('s', 'stopped')}
    assert [m['message_id'] for m in result[('r', 'input')]['replies']] == ['first', 'last']
    assert result[('r', 'gone')]['withdrawn'] is True
    assert 'withdrawn' not in result[('s', 'stopped')]
    retained[('r', 'last')]['reply_to_message_id'] = 'other'
    if display:
        result = value.reply_rounds(list(states), limit=2, _display_blocks=True)
        assert [m['message_id'] for m in result[('r', 'input')]['replies']] == ['first']


def test_no_missing_replies_skips_retained_store():
    """当前窗口已含所有回复时，不读取耐久历史。"""
    value, calls = repository({('r', 'input'): dict(state='completed', reply_message_ids=['end'])},
                              {'r': {'input': [message('end', 'shown')]}}, {})
    assert value.reply_rounds([('r', 'input')])[('r', 'input')]['replies']
    assert calls == []
