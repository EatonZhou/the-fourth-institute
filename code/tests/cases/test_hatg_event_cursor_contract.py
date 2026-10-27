"""WatchConversationEvents: the returned cursor resumes a Resident's event stream without loss."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from management_portal import resident_handoff
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import (
    resident_conversation_stream,
    resident_protocol,
    resident_record_domain,
)
from management_portal.society_service.isa import conversation
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.test_conversation_projection_authority import RESIDENT, _repository


class Drained(Exception):
    """The stream found no new events and would wait for more."""


@pytest.fixture()
def event_stream(tmp_path, monkeypatch):
    holder = {'repository': _repository(tmp_path)}
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: holder['repository'])
    monkeypatch.setattr(resident_record_domain, 'resolve_resident_runtime_target',
                        lambda *_args, **_kwargs: SimpleNamespace(resident_id=RESIDENT, record={"resident_id": RESIDENT}))

    def idle(_seconds):
        raise Drained()

    monkeypatch.setattr(conversation.time, 'sleep', idle)
    return holder


def append_events(repository, start, end):
    for number in range(start, end + 1):
        repository.append_message(RESIDENT, {
            'message_id': f'message_cursor_{number}',
            'role': 'agent',
            'content_parts': [{'type': 'text', 'text': f'fixture event {number}'}],
            'created_at': '2026-09-12T00:00:00Z',
        })


def stream(after_cursor, limit=2):
    arguments = {'resident_id': RESIDENT, 'after_cursor': after_cursor, 'limit': limit}
    return conversation.watch_conversation_events(SimpleNamespace(argument=arguments.get, caller=RESIDENT))


def watch(after_cursor, limit=2):
    """The next batch of the stream, or [] when it would wait."""

    try:
        return next(stream(after_cursor, limit))
    except Drained:
        return []


def drain(cursor=0, limit=2):
    collected = []
    batches = stream(cursor, limit)
    for _attempt in range(20):
        try:
            batch = next(batches)
        except Drained:
            return cursor, collected
        assert [item['cursor'] for item in batch[:-1]] == [item['event']['cursor'] for item in batch[:-1]]
        collected.extend(item['cursor'] for item in batch[:-1])
        cursor = batch[-1]['cursor']
    pytest.fail('bounded fixture did not reach its stream end')


def test_the_stream_drains_limited_batches_without_loss(event_stream):
    """WatchConversationEvents 按 limit 分批读完所有事件，无新事件时返回原游标"""
    append_events(event_stream['repository'], 1, 7)
    first = watch(0)
    assert [item['cursor'] for item in first[:-1]] == [1, 2]
    assert first[-1] == {'cursor': 2, 'high_watermark': 7}
    cursor, received = drain()
    assert received == list(range(1, 8))
    assert cursor == 7
    for _poll in range(2):
        assert watch(cursor) == [{'cursor': cursor, 'high_watermark': cursor}]


def test_reconnect_resumes_using_only_the_last_cursor(event_stream, tmp_path):
    """重连后只凭上次保存的游标即可从断点继续读取事件"""
    append_events(event_stream['repository'], 1, 5)
    checkpoint = tmp_path / 'resume-cursor.txt'
    checkpoint.write_text(str(watch(0)[-1]['cursor']))
    event_stream['repository'] = resident_conversation_stream.ResidentConversationRepository(
        LineageDataStore(), AgentDataStore(),
    )
    cursor, received = drain(int(checkpoint.read_text()))
    assert received == [3, 4, 5]
    assert cursor == 5


def test_concurrent_append_is_read_in_order_without_cursor_loss(event_stream):
    """读取期间并发追加的事件按顺序读到，游标不丢失"""
    repository = event_stream['repository']
    append_events(repository, 1, 4)
    first = watch(0)
    received = [item['cursor'] for item in first[:-1]]
    with ThreadPoolExecutor(max_workers=1) as executor:
        appended = executor.submit(append_events, repository, 5, 9)
        cursor, next_events = drain(first[-1]['cursor'])
        received.extend(next_events)
        appended.result(timeout=10)
    cursor, next_events = drain(cursor)
    received.extend(next_events)
    assert received == list(range(1, 10))
    assert cursor == 9


def test_an_empty_poll_keeps_the_checkpoint_for_a_later_append(event_stream):
    """空轮询保留游标，之后追加的事件从该游标继续读到"""
    repository = event_stream['repository']
    append_events(repository, 1, 2)
    cursor = watch(0)[-1]['cursor']
    assert watch(cursor) == [{'cursor': cursor, 'high_watermark': cursor}]
    append_events(repository, 3, 3)
    assert [item['cursor'] for item in watch(cursor)[:-1]] == [3]


def test_internal_high_watermark_and_invocation_backlog_stay_compatible(event_stream):
    """超过 500 条积压事件时内部读取的高水位与交接分页读取保持一致"""
    repository = event_stream['repository']
    append_events(repository, 1, 503)
    internal = resident_protocol.get_resident_events(RESIDENT, RESIDENT, 0, limit=500)
    assert internal['cursor'] == 503
    assert len(internal['events']) == 500
    assert repository.snapshot(RESIDENT)['cursor'] == 503
    cursor, events, gap = resident_handoff._events_after(
        SimpleNamespace(get_resident_events=resident_protocol.get_resident_events),
        actor=RESIDENT, resident_id=RESIDENT, cursor=0,
    )
    assert gap is False
    assert cursor == 503
    assert [event['cursor'] for event in events] == list(range(1, 504))
