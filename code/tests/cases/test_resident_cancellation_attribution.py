"""A cancelled Execution does not identify the person who requested a Stop.

:importance: P1
"""
from unittest import mock

import pytest

from management_portal.society_service import (
    lifecycle,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_domain,
    resident_execution_work,
    resident_input_queue_domain,
    resident_lifecycle_domain,
    resident_protocol,
)
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401

MID = 'msg_' + 'a' * 32
EID = 'execution-' + 'a' * 24


def notice_for(repo, resident_id):
    notices = [item for item in repo.read_messages(resident_id)
               if item.get('role') == 'system' and item.get('reply_to_message_id') == MID]
    assert len(notices) == 1
    return notices[0]


def text_of(message):
    return ''.join(part.get('text', '') for part in message['content_parts'])


@pytest.fixture
def cancelling(resident_record, monkeypatch, request):
    rid = resident_record['resident_id']
    submitter = getattr(request, 'param', 'CASE-A')
    repo = resident_conversation_stream.get_repository()
    repo.append_message(rid, {'message_id': MID, 'role': 'user', 'author_id': submitter,
        'created_at': '2026-10-19T01:00:00Z', 'content_parts': [{'type': 'text', 'text': 'research'}]})
    resident_execution_work.write(resident_record, {
        'execution_id': EID, 'user_message_id': MID, 'actor': submitter,
        'submitted_at': '2026-10-19T01:00:00Z', 'continuation_id': 'continuation-stop',
        'continuation_started_at': '2026-10-19T01:00:00Z', 'queue': [],
    })
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', mock.Mock())
    monkeypatch.setattr(lifecycle, 'notify_execution_cancellation', mock.Mock())
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: False)
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_terminal', mock.Mock())
    monkeypatch.setattr(resident_lifecycle_domain, 'append_conversation_message',
                        mock.Mock(wraps=resident_lifecycle_domain.append_conversation_message))
    return resident_record, repo


@pytest.mark.parametrize('cancelling', ['CASE-A', 'resident_aaaaaaaaaaaa'], indirect=True)
def test_unrequested_cancellation_does_not_name_submitter(cancelling):
    """没有持久停止意图的取消不指认用户或 Resident 主动停止。"""
    record, repo = cancelling
    rid = record['resident_id']

    resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')

    assert text_of(notice_for(repo, rid)) == '当前 Codex 运行已取消。'
    assert repo.input_state(rid, MID)['state'] == 'cancelled'
    current = resident_execution_work.read(record)
    assert current['stopped_by'] == ''
    assert current['execution_terminal_status'] == 'cancelled'
    assert current['execution_id'] == EID


def test_manual_stop_keeps_first_operator_distinct_from_submitter(cancelling):
    """人工停止仍指认真实请求者，重复停止不改写首次请求者。"""
    record, repo = cancelling
    rid = record['resident_id']
    assert resident_protocol.cancel_resident_input(rid, 'sample_admin', MID)['cancellation_requested']
    assert resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)['cancellation_requested']

    resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')

    assert text_of(notice_for(repo, rid)) == 'sample_admin 已停止当前 Codex 运行。'
    assert resident_execution_work.read(record)['stopped_by'] == 'sample_admin'
    assert repo.input_state(rid, MID)['state'] == 'cancelled'


def test_unattributed_notice_survives_publication_and_ack_retry(cancelling, monkeypatch):
    """无停止者的通知仍与精确输入/执行绑定，发布和确认重试不重复提示。

    重试比第一次晚，也沿用第一次授权的通知（通知身份包含写入时间）。
    """
    record, repo = cancelling
    rid = record['resident_id']
    # Each attempt runs in a later second than the one before.
    seconds = iter(range(10, 60))
    monkeypatch.setattr(resident_domain_common, 'now_iso', lambda: f'2026-10-19T01:23:{next(seconds)}Z')
    original = repo.set_input_state
    def fail_terminal(*args, **kwargs):
        if args[2] == 'cancelled':
            raise RuntimeError('terminal publication unavailable')
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'set_input_state', fail_terminal)
    with pytest.raises(RuntimeError, match='terminal publication unavailable'):
        resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')
    first = notice_for(repo, rid)
    assert repo.input_state(rid, MID)['state'] == 'running'

    monkeypatch.setattr(repo, 'set_input_state', original)
    resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')
    pending = resident_execution_work.read(record)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: True)
    resident_execution_domain._finalize_applied_execution(rid, 'continuation-stop', pending)

    notice = notice_for(repo, rid)
    assert notice['message_id'] == first['message_id']
    assert notice['reply_to_message_id'] == MID
    projected_notice = resident_lifecycle_domain.append_conversation_message.call_args.args[1]
    assert projected_notice['execution_id'] == EID
    assert notice['author_id'] == 'portal'
    assert text_of(notice) == '当前 Codex 运行已取消。'
    assert repo.input_state(rid, MID)['state'] == 'cancelled'
    assert not resident_execution_work.read(record)


@pytest.mark.parametrize('stopper,expected', [
    ('', '当前 Claude 运行已取消。'),
    ('CASE-A', 'CASE-A 已停止当前 Claude 运行。'),
])
def test_queue_notice_and_identity_do_not_depend_on_attribution(stopper, expected):
    """归因只改变通知文字，不改变队列后续说明及精确执行的消息身份。"""
    job = {'user_message_id': MID, 'execution_id': EID, 'submitted_at': '2026-10-19T01:00:00Z',
           'stopped_at': '2026-10-19T01:23:27Z'}
    event = resident_input_queue_domain.resident_execution_stopped_event(
        stopper, job, queue_continues=True, backend_label='Claude')
    neutral = resident_input_queue_domain.resident_execution_stopped_event('', job)
    assert text_of(event) == expected + '下一条排队消息将继续执行。'
    assert event['message_id'] == neutral['message_id']
    assert event['created_at'] == job['stopped_at']
    assert event['reply_to_message_id'] == MID
    assert event['execution_id'] == EID
