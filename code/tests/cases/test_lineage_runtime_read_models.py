"""
:module: lineage.service
:aspects: functional, behavior
"""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_portal.execution.service import ExecutionService
from management_portal.society_service.resident_conversation_event_relay import ResidentConversationEventRelay
from management_portal.society_service.state_store import LINEAGE_DATA_SCHEMA, LineageDataStore
from tests.cases.database_test_support import guard_queries
from tests.cases.test_conversation_projection_authority import RESIDENT, _message, _repository


@pytest.mark.parametrize('outcome', ['failed', 'cancelled', 'completed'])
def test_terminal_round_replaces_an_orphaned_running_input_without_overriding_requeue(tmp_path, monkeypatch, outcome):
    """终态轮次恢复输入状态并保护重新入队

    准备阶段失败、取消和完成均从归档轮次恢复；状态筛选、通知和迟到投影一致，同一时钟刻度的重试不被旧终态覆盖。

    :module: execution.persistence
    :aspects: functional
    """
    repository = _repository(tmp_path)
    store = repository.state_store
    message_id = 'message-preparation-failed'
    repository.set_input_state(RESIDENT, message_id, 'running', updated_at='2026-10-10T00:00:00Z')
    store.put_resident_execution_record(RESIDENT, {
        'execution_id': 'job-preparation', 'actor': 'CASE-A', 'user_message_id': message_id,
        'outcome': outcome, 'finished_at': '2026-10-10T00:00:01Z', 'error': 'preparation failed',
    })
    state = repository.input_state(RESIDENT, message_id)
    assert state['state'] == outcome
    if outcome == 'failed':
        assert state['error']['message'] == 'preparation failed'
    assert repository.input_states(RESIDENT)[0]['state'] == outcome
    assert repository.state_snapshots([RESIDENT])[RESIDENT]['latest_input_state']['state'] == outcome
    assert repository.input_states(RESIDENT, states=[outcome], limit=1)[0]['state'] == outcome
    assert repository.input_states(RESIDENT, states=['running', 'queued']) == []
    if outcome != 'completed':
        assert repository.latest_notifiable_input_states_for_messages({RESIDENT: [message_id]})[RESIDENT]['state'] == outcome
    assert repository.set_input_state(RESIDENT, message_id, 'running')['state'] == outcome
    repository.publish_archived_input_state(RESIDENT, message_id)
    with store.database.read() as connection:
        assert connection.execute('SELECT state FROM conversation_input_states WHERE resident_id=%s AND message_id=%s',
                                  (RESIDENT, message_id)).fetchone()['state'] == outcome
    # A requeue in the receipt's clock tick must still supersede that round.
    monkeypatch.setattr('management_portal.society_service.resident_conversation_inputs.utc_now',
                        lambda: '2026-10-10T00:00:01Z')
    assert repository.reopen_input(RESIDENT, message_id)['state'] == 'queued'
    repository.publish_archived_input_state(RESIDENT, message_id)
    assert repository.input_state(RESIDENT, message_id)['state'] == 'queued'
    # A current active round also wins when timestamps are equal/coarse.
    store.put_resident_execution_record(RESIDENT, {
        'execution_id': 'job-requeued', 'actor': 'CASE-A', 'user_message_id': message_id,
    })
    repository.set_input_state(RESIDENT, message_id, 'running', updated_at='2026-10-10T00:00:00Z')
    assert repository.input_state(RESIDENT, message_id)['state'] == 'running'


def test_control_reads_exclude_contents_and_follow_external_writes(tmp_path):
    """控制读模型排除正文并反映外部提交

    控制读模型排除大消息与执行请求正文，外部提交及时可见，事务回滚不泄漏状态。
    """
    repository = _repository(tmp_path)
    store = repository.state_store
    job = { 'execution_id': 'execution', 'actor': 'CASE-A',
           'message': 'large' * 800_000, 'actual_sent_prompt': 'large' * 800_000,
           'execution_request': {'prompt': 'opaque'},
           'queue': [{'user_message_id': 'queued', 'message': 'large' * 100_000}], "submitted_at": "2026-10-14T00:00:00Z"}
    store.put_resident_execution_record(RESIDENT, job)
    with store.database.read() as connection:
        assert connection.execute('SELECT length(progress_json::text) FROM resident_execution_work').fetchone()[0] < 300
    control = store.resident_execution_control(RESIDENT)
    assert {key:control['state'][key] for key in ('execution_id','actor','queue','submitted_at')} == {
         'execution_id': 'execution', 'actor': 'CASE-A', 'queue': [{'user_message_id': 'queued'}], "submitted_at": "2026-10-14T00:00:00Z"}
    assert not set(control['state']) & {'message','actual_sent_prompt','execution_request'}
    assert all(store.resident_execution_record(RESIDENT)[key] == value for key,value in job.items())
    assert store.resident_execution_revision(RESIDENT) == control['revision']
    external = LineageDataStore()
    job['stop_requested_at'] = 'now'
    external.database.write(lambda connection: connection.execute(
        "UPDATE resident_execution_work SET revision=nextval('resident_execution_revision'), progress_json=(progress_json::jsonb || %s::jsonb) WHERE resident_id=%s",
        (json.dumps({'stop_requested_at':'now'}), RESIDENT),
    ))
    assert store.resident_execution_control(RESIDENT)['state']['stop_requested_at'] == 'now'
    with pytest.raises(RuntimeError), store.database.transaction() as connection:
        connection.execute('DELETE FROM resident_execution_work WHERE resident_id=%s', (RESIDENT,))
        assert store.resident_execution_control(RESIDENT)['revision'] == 0
        raise RuntimeError('rollback')
    assert store.resident_execution_control(RESIDENT)['state']['stop_requested_at'] == 'now'


def test_lineage_data_health_reports_its_schema_without_reading_table_sizes(monkeypatch):
    """LineageData 健康信息只报告 schema 与数据库

    健康信息只含 schema 版本和数据库名，不报告任何表的行数；读取时只查询 lineage_meta 一条语句。

    :aspects: functional
    """
    store = LineageDataStore()
    statements = []
    original_read = store.database.read

    @contextmanager
    def traced_read():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(store.database, 'read', traced_read)
    assert store.health() == {'schema': LINEAGE_DATA_SCHEMA, 'database': store.database.display_name}
    assert len(statements) == 1 and 'lineage_meta' in statements[0]


def test_relay_uses_committed_sequence_and_never_reuses_deleted_tail(tmp_path):
    """事件中继保持已提交序号且不复用删除尾部

    投递序号保持单调，删除历史尾部不复用序号，回滚事件不对外可见。
    """
    repository = _repository(tmp_path)
    relay = ResidentConversationEventRelay(repository.state_store, SimpleNamespace(publish=lambda *args: None), batch_size=1)
    assert relay._latest_sequence() == 0
    repository.append_message(RESIDENT, _message('message-one', 'one', '2026-09-14T00:00:00Z'))
    first = relay._events_after(0)
    assert len(first) == 1 and first[0][1]['cursor'] == 1
    repository.state_store.database.write(lambda connection: connection.execute('DELETE FROM conversation_events'))
    assert relay._events_after(0) == []
    repository.append_message(RESIDENT, _message('message-two', 'two', '2026-09-14T00:00:01Z'))
    second = relay._events_after(first[0][0])
    assert len(second) == 1 and second[0][0] > first[0][0]
    assert second[0][1]['cursor'] == 2
    relay._remember(*second[0])
    with pytest.raises(RuntimeError), repository.state_store.database.transaction() as connection:
        repository.events.record_event(connection, RESIDENT, 'resident.state.changed', {'state': {}})
        raise RuntimeError('rollback')
    assert relay._events_after(second[0][0]) == []




def test_control_queue_keeps_bounded_preview_without_loading_message_body(tmp_path):
    """控制队列预览有界且不携带完整消息正文

    轻量控制队列去除完整输入正文，保持与原队列一致的预览及有界序列化大小。
    """
    from management_portal.society_service.resident_execution_work import _conversation_queue_item

    repository = _repository(tmp_path)
    queued = {'user_message_id': 'queued', 'input_message': {
        'author_id': 'CASE-A', 'created_at': 'now',
        'content_parts': [
            {'type': 'text', 'text': 'hello ' * 100_000},
            {
                'type': 'image',
                'source_type': 'local_path',
                'source': '/srv/agent-staging/shared/portal_uploads/resident_chat_images/CASE-A/queue.png',
                'name': 'queue.png',
            },
        ],
    }}
    repository.state_store.put_resident_execution_record(RESIDENT, {'execution_id': 'job', 'actor': 'CASE-A', 'queue': [queued]})
    projected = repository.state_store.resident_execution_control(RESIDENT)['state']['queue'][0]
    assert 'input_message' not in projected
    assert len(json.dumps(projected)) < 1500
    assert projected['conversation_queue_preview']['media_parts'][0]['type'] == 'image'
    assert _conversation_queue_item(repository, RESIDENT, projected) == _conversation_queue_item(repository, RESIDENT, queued)


def test_relay_retries_failed_publish_without_skipping_events(tmp_path):
    """事件中继发布失败重试且不跳过事件

    发布失败后重试同一事件，成功计数只记录实际成功投递。
    """
    import threading

    repository = _repository(tmp_path)
    succeeded = threading.Event()
    attempts = []

    def publish(subject, payload):
        attempts.append(payload['event']['cursor'])
        if len(attempts) == 1:
            raise RuntimeError('temporary transport failure')
        succeeded.set()

    relay = ResidentConversationEventRelay(repository.state_store, SimpleNamespace(publish=publish), poll_seconds=0.01)
    relay.start()
    try:
        repository.append_message(RESIDENT, _message('message-retry', 'retry', '2026-09-14T00:00:00Z'))
        assert succeeded.wait(2)
    finally:
        relay.stop()
    assert attempts == [1, 1]
    assert relay.health()['published_count'] == 1


def test_idle_relay_reads_indexed_tail_without_scanning_conversation_states(tmp_path, monkeypatch):
    """空闲事件中继通过索引读取尾部

    空闲中继不扫描会话状态，尾部查询使用索引且不发生全表扫描或临时排序。
    """
    repository = _repository(tmp_path)
    relay = ResidentConversationEventRelay(repository.state_store, SimpleNamespace(publish=lambda *args: None))
    statements = []
    original_read = repository.state_store.database.read

    @contextmanager
    def traced_read():
        with original_read() as connection:
            yield guard_queries(connection, statements=statements)

    monkeypatch.setattr(repository.state_store.database, 'read', traced_read)
    assert relay._events_after(0) == []
    assert not any('conversation_states' in statement for statement in statements)
    query = next(statement for statement in statements if 'from conversation_event_delivery' in statement)
    assert 'where sequence > %s' in query
    assert 'order by sequence limit %s' in query
    assert not any('payload_json' in statement for statement in statements)
    with repository.state_store.database.read() as connection:
        indexes = {
            row['indexname']
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname=current_schema() AND tablename='conversation_event_delivery'"
            ).fetchall()
        }
    assert 'conversation_event_delivery_pkey' in indexes


@pytest.mark.parametrize('retry_first', [False, True])
def test_execution_wait_projects_only_changes_but_keeps_stop_checks_and_event_order(retry_first):
    """执行等待抑制重复投影并保留取消检查及事件顺序

    仅投影变化或重试未确认快照，每轮仍检查取消，并保持事件与快照顺序。

    :module: operations.runtime
    """
    running = {'status': 'running', 'scheduler': {'updated_at': 'same'}, 'events': []}
    event = {'sequence': 1, 'type': 'output'}
    finished = {'status': 'completed', 'result': {'status': 'completed'}, 'events': []}
    snapshots = iter([running, running, running, {**running, 'events': [event]}, finished])
    requests, callbacks, checks = [], [], []

    def request(method, payload, **kwargs):
        requests.append(payload)
        return copy.deepcopy(next(snapshots))

    def project(snapshot):
        callbacks.append(('snapshot', snapshot['status']))
        return not (retry_first and len(callbacks) == 1)

    service = ExecutionService.__new__(ExecutionService)
    service.client = SimpleNamespace(request=request)
    assert service.wait('execution', actor='CASE-A', cancellation_requested=lambda: checks.append(True) or False,
                        on_events=lambda events: callbacks.append(('event', events[0]['sequence'])),
                        on_snapshot=project) == finished
    assert len(checks) == 5
    assert all(request['timeout_seconds'] <= 0.25 for request in requests)
    assert callbacks == ([('snapshot', 'running')] * (2 if retry_first else 1)
                         + [('event', 1), ('snapshot', 'running'), ('snapshot', 'completed')])
