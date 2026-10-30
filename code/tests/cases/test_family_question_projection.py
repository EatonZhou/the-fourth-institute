"""Facade question cards retain the worker's request and answer channel.

:module: resident.conversation
"""
from unittest.mock import Mock

import pytest

from management_common.async_questions import question_message_parts
from management_common.provider_user_input import content_part
from management_portal.society_service import (
    execution_client, family_facade, resident_access, resident_conversation_stream,
    resident_input_queue_domain, resident_protocol,
)
from management_portal.society_service.resident_questions import prepare_question_answer
from tests.cases.test_family_facade import facade, MEMBER, _register_test_member  # noqa: F401
from tests.cases.resident_execution_test_support import input_delivery, resident_record  # noqa: F401
from tests.cases.test_codex_user_input import request_params


@pytest.fixture
def question_round(facade, monkeypatch):
    _register_test_member(facade, 'Question worker')
    repo = resident_conversation_stream.get_repository()
    facade_id = facade['resident_id']
    outer = resident_input_queue_domain.build_message('user', 'Check this', 'CASE-A')
    inner = resident_input_queue_domain.build_message('user', 'Check this', 'CASE-A')
    repo.append_message(facade_id, outer)
    repo.append_message(MEMBER, inner)
    repo.set_input_state(facade_id, outer['message_id'], 'queued')
    repo.set_input_state(MEMBER, inner['message_id'], 'running')
    family_facade.complete_relay((facade_id, outer['message_id']), MEMBER, inner['message_id'])
    native = {'request_id': '3' * 32, 'thread_id': 'thread-native', 'turn_id': 'turn-native',
              'item_id': 'item-native', 'is_blocking': True, 'status': 'pending', 'questions': request_params()['questions']}
    execution_id = 'execution-' + '2' * 24
    def save_native(kind='userInputRequest', **extra):
        part = content_part({'type': kind, 'user_input': {**native, **extra}}, execution_id)
        repo.agent_data.put_segment(segment_id=part['segment_id'], segment_type='tool', content=part,
            content_type='application/json', created_by_agent_id=MEMBER, created_by_actor_ref=MEMBER,
            performed_by_actor_ref=MEMBER, execution_id=execution_id)
        return part
    part = save_native()
    message = resident_input_queue_domain.build_message('agent', '', MEMBER, content_parts=[part])
    message['reply_to_message_id'] = inner['message_id']
    repo.append_message(MEMBER, message)
    service = Mock()
    service.append_input.return_value = {'status': 'delivered'}
    monkeypatch.setattr(execution_client, 'service', lambda: service)
    monkeypatch.setattr(resident_access, 'can_manage_resident', lambda record, actor, **kw:
                        record['resident_id'] == facade_id and actor == 'CASE-A')
    return facade, repo, outer, inner, message, service, save_native


def test_facade_projects_native_question_and_current_answer_status(question_round):
    facade, repo, outer, inner, message, _, save = question_round
    shown = family_facade.project_messages(facade, [outer])
    card = next(m for m in shown if m['message_id'] == message['message_id'])
    assert card['family_reply']['resident_id'] == MEMBER
    assert card['family_reply']['request_message_id'] == outer['message_id']
    assert card['content_parts'][0]['status'] == 'pending'
    save('userInputResponse', status='answered', answers={'scope': {'answers': ['定向']}})
    updated = family_facade.project_messages(facade, [outer])[-1]
    assert updated['content_parts'][0]['status'] == 'answered'
    assert family_facade.projected_reply(facade, message['message_id'])['content_parts'][0]['status'] == 'answered'


def test_facade_answer_uses_worker_control_without_member_manage_permission(question_round):
    facade, repo, outer, inner, message, service, _ = question_round
    payload = {'message_id': message['message_id'], 'answers': {'scope': {'answers': ['定向']}}}
    result = resident_protocol.respond_resident_user_input(facade['resident_id'], 'CASE-A', payload)
    assert result['status'] == 'answered'
    assert service.append_input.call_args.args == ('execution-' + '2' * 24,)
    assert service.append_input.call_args.kwargs['expected_turn_id'] == 'turn-native'
    assert service.append_input.call_args.kwargs['actor'] == 'CASE-A'
    with pytest.raises((PermissionError, KeyError)):
        resident_protocol.respond_resident_user_input(facade['resident_id'], 'someone-else', payload)


def test_facade_rejects_unrelated_or_cancelled_questions(question_round):
    facade, repo, outer, inner, message, service, _ = question_round
    payload = {'message_id': message['message_id'], 'answers': {'scope': {'answers': ['定向']}}}
    repo.set_input_state(MEMBER, inner['message_id'], 'cancelled')
    repo.set_input_state(facade['resident_id'], outer['message_id'], 'cancelled')
    with pytest.raises(ValueError):
        resident_protocol.respond_resident_user_input(facade['resident_id'], 'CASE-A', payload)
    unrelated = {**message, 'message_id': 'msg_unrelated_question', 'reply_to_message_id': 'msg_other_input'}
    repo.append_message(MEMBER, unrelated)
    with pytest.raises(ValueError):
        resident_protocol.respond_resident_user_input(facade['resident_id'], 'CASE-A', {**payload, 'message_id': unrelated['message_id']})
    service.append_input.assert_not_called()


def test_facade_async_card_keeps_original_answer_identity_and_route(question_round):
    facade, repo, outer, inner, _, _, _ = question_round
    message = resident_input_queue_domain.build_message('agent', '', MEMBER)
    message['reply_to_message_id'] = inner['message_id']
    message['content_parts'] = question_message_parts('', [{'question_index': 0, 'title': 'Which scope?', 'options': ['Small', 'All']}], MEMBER, message['message_id'])
    repo.append_message(MEMBER, message)
    card = family_facade.project_messages(facade, [outer])[-1]
    part = card['content_parts'][0]
    answer = {'message_id': part['answer_message_id'], 'content_parts': [
        {'type': 'async_question_answer', 'source_message_id': card['message_id'],
         'answers': [{'question_index': 0, 'answer': 'Small'}]}]}
    _, checked = prepare_question_answer(facade['resident_id'], answer, repo, facade=facade)
    assert checked['message_id'] == part['answer_message_id']
    assert checked['content_parts'][1]['source_message_id'] == message['message_id']
    assert family_facade.relayed_reply(facade, message['message_id'])['worker_id'] == MEMBER
