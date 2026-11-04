"""A conservative one-turn relation carries trusted background, never user text."""
from types import SimpleNamespace
import uuid

import pytest

from management_portal.society_service import (
    family_continuation, family_facade, resident_conversation_stream,
    resident_execution_work, resident_prompt_domain, resident_record_domain,
)
from management_portal.society_service.domain_ports import execution_port
from tests.cases.isa_test_support import run
from tests.cases.test_family_reassign import family, delivery_resident, future_creation_store  # noqa: F401


def _request(family, text, author='sample_admin'):
    return run('DeliverMessage', {'resident_id': family['facade'],
        'message': {'content_parts': [{'type': 'text', 'text': text}]}}, actor=author).raise_for_error()['message_id']


def _finish(family, source, text):
    facade = resident_record_domain.find_resident(family['facade'])
    link = family_facade.relay_links(facade)[source]
    repo = resident_conversation_stream.get_repository()
    reply = 'msg_' + uuid.uuid4().hex
    repo.append_message(link['resident_id'], {'message_id': reply, 'role': 'agent',
        'content_parts': [{'type': 'text', 'text': text}], 'author_id': link['resident_id'],
        'created_at': repo.message(family['facade'], source)['created_at'],
        'reply_to_message_id': link['message_id']})
    repo.set_input_state(link['resident_id'], link['message_id'], 'completed')
    return reply


@pytest.fixture
def exchange(family, monkeypatch):
    # The imported fixture's map is intended for its custom reassign cases;
    # these cases use the real source-author validation and public relay ISA.
    from tests.cases.test_family_reassign import _REAL_VALIDATE_RELAY
    source = family['ask']('方案1能否四路流水？')
    reply = _finish(family, source, '方案1也使用四路流水，固定token保留率。')
    monkeypatch.setattr(family_facade, 'validate_relay', _REAL_VALIDATE_RELAY)
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(accounts=lambda: {'accounts': []}))
    return family, source, reply


def _candidate(family, mid):
    facade = resident_record_domain.find_resident(family['facade'])
    message = resident_conversation_stream.get_repository().message(family['facade'], mid)
    return family_continuation.previous_round(facade, message)


def _relay(family, mid, **extra):
    return run('RelayFamilyMessage', {'resident_id': family['facade'], 'message_id': mid,
        'member_id': family['right'], **extra})


@pytest.mark.parametrize('continue_previous', [False, True])
def test_only_positive_relation_injects_previous_round_and_keeps_original(exchange, continue_previous):
    family, previous, reply = exchange
    mid = _request(family, '请这样修改')
    prior = _candidate(family, mid)
    assert prior['message_id'] == previous and prior['reply']['message_id'] == reply
    before = resident_conversation_stream.get_repository().message(family['facade'], mid)
    result = _relay(family, mid, continue_previous=continue_previous).raise_for_error()
    record = resident_record_domain.find_resident(family['right'])
    job = resident_execution_work.read(record)
    queued = next(item for item in [job, *resident_execution_work.normalized_queue(job)]
                  if item['user_message_id'] == result['message_id'])
    assert queued['raw_message'] == '请这样修改'
    copied = resident_conversation_stream.get_repository().message(family['right'], result['message_id'])
    assert copied['content_parts'] == before['content_parts'] and copied['author_id'] == before['author_id']
    context = queued.get('runtime_context') or ''
    assert (reply in context) is continue_previous
    assert ('固定token保留率' in context) is continue_previous
    if continue_previous:
        prompt = resident_prompt_domain.build_resident_prompt(record, queued)
        assert '<runtime-context>' in prompt and '<user-message>\n请这样修改\n</user-message>' in prompt
        assert '不是本轮用户 Prompt' in prompt
    # Omitted fields on an ordinary quota/recovery retry cannot drop context.
    assert _relay(family, mid).raise_for_error()['message_id'] == result['message_id']
    assert resident_conversation_stream.get_repository().message(family['facade'], mid) == before


def test_last_unanswered_turn_blocks_older_context(exchange):
    family, previous, _ = exchange
    _request(family, '另一件独立工作')
    mid = _request(family, '请这样修改')
    assert _candidate(family, mid) is None
    failed = _relay(family, mid, continue_previous=True)
    assert failed.status == 'failed' and '上一轮' in failed.error['message']


def test_candidate_is_frozen_despite_later_messages(exchange):
    family, previous, _ = exchange
    mid = _request(family, '请这样修改')
    before = _candidate(family, mid)
    _request(family, '后来的新问题')
    assert _candidate(family, mid) == before
    assert before['message_id'] == previous


def test_independent_decision_cannot_be_rewritten_after_quota_wait(exchange):
    family, _, _ = exchange
    mid = _request(family, '新任务')
    _candidate(family, mid)
    facade = resident_record_domain.find_resident(family['facade'])
    message = resident_conversation_stream.get_repository().message(family['facade'], mid)
    family_continuation.decide(facade, message, False)
    with pytest.raises(ValueError, match='独立任务'):
        family_continuation.decide(facade, message, True)


def test_cancelled_original_does_not_get_a_new_copy(exchange):
    family, _, _ = exchange
    mid = _request(family, '请这样修改')
    _candidate(family, mid)
    run('CancelInput', {'resident_id': family['facade'], 'message_id': mid}).raise_for_error()
    assert _relay(family, mid, continue_previous=True).raise_for_error()['status'] == 'withdrawn'


def test_other_users_latest_turn_is_not_a_candidate(exchange):
    from management_portal.society_service.resident_input_queue_domain import build_message
    family, previous, _ = exchange
    repo = resident_conversation_stream.get_repository()
    other = build_message('user', '另一个用户的请求', 'other-user')
    repo.accept_user_message(family['facade'], other)
    mid = _request(family, '请这样修改')
    assert _candidate(family, mid)['message_id'] == previous


def test_empty_candidate_is_frozen_and_does_not_search_older_turns(exchange):
    family, _, _ = exchange
    source = family['ask']('这个请求还没有答复')
    mid = _request(family, '请这样修改')
    assert _candidate(family, mid) is None
    _finish(family, source, '迟到的答复不能改写原判断候选')
    assert _candidate(family, mid) is None


def test_reply_created_after_current_input_is_not_background(exchange):
    family, previous, _ = exchange
    repo = resident_conversation_stream.get_repository()
    mid = _request(family, '请这样修改')
    request = repo.message(family['facade'], mid)
    # Keep the actual persisted before-message boundary, but force the cutoff
    # earlier than any source reply without changing stored timestamps.
    request['created_at'] = '2000-01-01T00:00:00Z'
    facade = resident_record_domain.find_resident(family['facade'])
    assert family_continuation.previous_round(facade, request) is None


def test_reassignment_carries_same_confirmed_background(exchange):
    family, _, reply = exchange
    mid = _request(family, '请这样修改')
    _candidate(family, mid)
    _relay(family, mid, continue_previous=True).raise_for_error()
    result = run('RelayFamilyMessage', {'resident_id': family['facade'], 'message_id': mid,
        'member_id': family['wrong'], 'reassign': True, 'reason': '改交原方案成员落实'}).raise_for_error()
    record = resident_record_domain.find_resident(family['wrong'])
    job = resident_execution_work.read(record)
    selected = next(item for item in [job, *resident_execution_work.normalized_queue(job)]
                    if item['user_message_id'] == result['message_id'])
    assert reply in selected['runtime_context'] and selected['raw_message'] == '请这样修改'


def test_nested_relay_retains_one_outer_previous_round(exchange):
    from tests.cases.family_test_support import set_role
    family, previous, reply = exchange
    inner = family['right']
    set_role(inner, family_id='fbi_998877665544', actor='sample_admin')
    mid = _request(family, '请这样修改')
    _candidate(family, mid)
    inner_mid = _relay(family, mid, continue_previous=True).raise_for_error()['message_id']
    result = run('RelayFamilyMessage', {'resident_id': inner, 'message_id': inner_mid,
        'member_id': family['wrong']}).raise_for_error()
    record = resident_record_domain.find_resident(family['wrong'])
    job = resident_execution_work.read(record)
    selected = next(item for item in [job, *resident_execution_work.normalized_queue(job)]
                    if item['user_message_id'] == result['message_id'])
    assert reply in selected['runtime_context'] and previous in selected['runtime_context']
    assert selected['runtime_context'].count('Family 已确认本请求') == 1
