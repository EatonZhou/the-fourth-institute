"""Stop commits exact-input intent independently of slow Conversation storage.

:importance: P2
"""
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import mock

import pytest

from management_portal.society_service import (
    invariant_locks,
    lifecycle,
    lineage_data_residents,
    lineage_message_events,
    resident_catalog,
    resident_conversation_stream,
    resident_delivery_storage,
    resident_delivery_work,
    resident_execution_domain,
    resident_execution_work,
    resident_protocol,
    resident_lifecycle_domain,
    resident_record_domain,
    resident_input_queue_domain,
)
from tests.cases.resident_execution_test_support import resident_record  # noqa: F401

MID = 'msg_' + 'a' * 32
EID = 'execution-' + 'a' * 24


@pytest.fixture
def running(resident_record, monkeypatch):
    rid = resident_record['resident_id']
    repo = resident_conversation_stream.get_repository()
    repo.append_message(rid, {'message_id': MID, 'role': 'user', 'author_id': 'CASE-A',
        'created_at': '2026-10-16T00:00:00Z', 'content_parts': [{'type': 'text', 'text': 'quiet tool'}]})
    resident_execution_work.write(resident_record, {
        'execution_id': EID, 'user_message_id': MID, 'actor': 'CASE-A',
        'submitted_at': '2026-10-16T00:00:00Z', 'continuation_id': 'continuation-stop',
        'continuation_started_at': '2026-10-16T00:00:00Z', 'queue': [],
    })
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', mock.Mock())
    monkeypatch.setattr(lifecycle, 'notify_execution_cancellation', mock.Mock())
    return resident_record, repo


def test_stop_commit_does_not_wait_for_conversation_or_workspace(running, monkeypatch):
    """停止输入时立即提交停止意图并返回回执，不等待对话锁也不加载 Workspace"""
    record, repo = running
    rid = record['resident_id']
    monkeypatch.setattr(lineage_data_residents, 'residents_from_rows', lambda *a, **k: pytest.fail('Workspace hydration on Stop'))
    monkeypatch.setattr(repo, 'current_projection', lambda *a, **k: pytest.fail('synchronous Stop snapshot'))
    held, release = threading.Event(), threading.Event()
    def slow_projection():
        with invariant_locks.resident_conversation_lock(rid):
            held.set()
            assert release.wait(5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(slow_projection)
        assert held.wait(2)
        try:
            receipt = pool.submit(resident_protocol.cancel_resident_input, rid, 'CASE-A', MID).result(2)
            assert receipt['cancellation_requested'] and not receipt['backend_cancelled']
            assert receipt['input_state']['state'] == 'running'
            assert 'state' not in receipt and 'cursor' not in receipt
            assert resident_execution_work.read_control(rid)['state']['stop_requested_at']
            assert resident_delivery_work.pending_ids() == [rid]
            lifecycle.notify_execution_cancellation.assert_called_once_with(EID, 'CASE-A')
        finally:
            release.set()
        pending.result(2)


def test_lost_wake_recovers_state_without_redelivery_and_keeps_newer_revision(running, monkeypatch):
    """停止通知丢失后由恢复流程补发状态，不重复投递，保留较新的状态版本"""
    record, repo = running
    rid = record['resident_id']
    monkeypatch.setattr(lifecycle, 'notify_execution_binding', mock.Mock(side_effect=RuntimeError('lost wake')))
    assert resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)['cancellation_requested']
    watch = mock.Mock()
    monkeypatch.setattr(lineage_message_events, 'wake_watchers', watch)
    publish = resident_execution_work.project_latest_conversation_state
    def publish_and_repeat(*args, **kwargs):
        publish(*args, **kwargs)
        resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    monkeypatch.setattr(resident_execution_work, 'project_latest_conversation_state', publish_and_repeat)
    resident_delivery_work.drain(rid)
    assert resident_delivery_work.pending_ids() == [rid]
    monkeypatch.setattr(resident_execution_work, 'project_latest_conversation_state', publish)
    resident_delivery_work.recover()
    assert resident_delivery_work.pending_ids() == []
    assert repo.current_projection(rid)['state']['work_state'] == 'stopping'
    assert repo.input_state(rid, MID)['state'] == 'running'
    watch.assert_not_called()


def test_exact_stop_rejects_replacement_and_preserves_first_intent(running):
    """停止请求保留第一次的发起者和时间，普通进度写入不清除，目标输入已被替换时被拒绝"""
    record, _ = running
    rid = record['resident_id']
    store = resident_catalog.get_catalog().state_store
    old = resident_execution_work.read(record)
    assert store.request_resident_execution_stop(rid, EID, MID, 'CASE-A', '2026-10-16T01:00:00Z')
    assert store.request_resident_execution_stop(rid, EID, MID, 'sample_admin', '2026-10-16T01:00:01Z')
    # An overlapping ordinary progress writer may not erase Stop.
    resident_execution_work.write(record, old)
    current = resident_execution_work.read(record)
    assert current['stop_requested_by'] == 'CASE-A'
    assert current['stop_requested_at'] == '2026-10-16T01:00:00Z'
    current.update(execution_id='execution-' + 'b' * 24, user_message_id='msg_' + 'b' * 32)
    current.pop('stop_requested_at'); current.pop('stop_requested_by')
    resident_execution_work.write(record, current)
    with pytest.raises(ValueError, match='active Resident input'):
        store.request_resident_execution_stop(rid, EID, MID, 'CASE-A', '2026-10-16T01:00:02Z')
    assert not resident_execution_work.read(record).get('stop_requested_at')


def test_cancelled_projection_releases_command_lock_before_terminal_io(running, monkeypatch):
    """处理已取消的执行时先释放命令锁再写终态，期间其他命令可进入

    :importance: P1"""
    record, repo = running
    rid = record['resident_id']
    resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    entered, release = threading.Event(), threading.Event()
    original = repo.set_input_state
    def slow_terminal(*args, **kwargs):
        if args[2] == 'cancelled':
            assert not invariant_locks._depths().get('resident:' + rid, 0)
            entered.set()
            assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'set_input_state', slow_terminal)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: True)
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_terminal', mock.Mock())
    def command():
        with resident_record_domain.state_lock(rid):
            return resident_execution_work.read_control(rid)['state']['user_message_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        finalizer = pool.submit(resident_execution_domain.apply_cancelled_continuation, rid, 'continuation-stop')
        try:
            assert entered.wait(2)
            assert pool.submit(command).result(1) == MID
            assert repo.input_state(rid, MID)['state'] == 'running'
        finally:
            release.set()
        assert finalizer.result(3) is False
    assert repo.input_state(rid, MID)['state'] == 'cancelled'
    assert not resident_execution_work.read(record)
    assert repo.current_projection(rid)['state']['work_state'] == 'idle'


@pytest.mark.parametrize('replaced', ['execution', 'input', 'both'])
def test_stale_finalizer_does_not_clear_replacement(running, monkeypatch, replaced):
    """过期的取消收尾遇到执行或输入已被替换时，不清除新的执行记录"""
    record, repo = running
    rid = record['resident_id']
    append = resident_lifecycle_domain.append_conversation_message
    replacement = {
        'execution_id': 'execution-' + 'c' * 24 if replaced != 'input' else EID,
        'user_message_id': 'msg_' + 'c' * 32 if replaced != 'execution' else MID,
        'actor': 'CASE-A', 'submitted_at': '2026-10-16T01:00:00Z',
        'continuation_id': 'continuation-stop', 'queue': [],
    }
    def append_then_replace(*args, **kwargs):
        result = append(*args, **kwargs)
        resident_execution_work.write(record, replacement)
        return result
    monkeypatch.setattr(resident_lifecycle_domain, 'append_conversation_message', append_then_replace)
    assert not resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')
    for key in ('execution_id', 'user_message_id'):
        assert resident_execution_work.read(record)[key] == replacement[key]
    assert repo.input_state(rid, MID)['state'] == 'running'


def test_stop_retains_visibility_and_self_stop_guards(running, monkeypatch):
    """不可见的 Resident 不能被停止，Resident 也不能停止自己正在执行的输入"""
    record, _ = running
    rid = record['resident_id']
    with monkeypatch.context() as scope:
        scope.setattr(resident_record_domain, '_resident_visible_to_actor', lambda *a: False)
        with pytest.raises((PermissionError, KeyError)):
            resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    monkeypatch.setenv('PORTAL_RESIDENT_ID', rid)
    monkeypatch.setenv('PORTAL_RESIDENT_EXECUTION_ID', EID)
    with pytest.raises(RuntimeError, match='不能停止自己'):
        resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    assert not resident_execution_work.read(record).get('stop_requested_at')


def test_failed_stop_publication_rolls_back_intent_and_emits_no_control(running, monkeypatch):
    """停止状态发布失败时撤销停止意图，不发出取消或绑定通知"""
    record, repo = running
    rid = record['resident_id']
    def reject_publication(*args):
        raise RuntimeError('outbox write failed')
    monkeypatch.setattr(resident_delivery_storage, 'enqueue_state', reject_publication)
    with pytest.raises(RuntimeError, match='outbox write failed'):
        resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    assert not resident_execution_work.read(record).get('stop_requested_at')
    assert resident_delivery_work.pending_ids() == []
    assert repo.input_state(rid, MID)['state'] == 'running'
    lifecycle.notify_execution_cancellation.assert_not_called()
    lifecycle.notify_execution_binding.assert_not_called()


def test_terminal_publication_retry_keeps_one_notice_and_does_not_clear_early(running, monkeypatch):
    """取消终态发布失败后重试只留一条停止提示，确认前不清除执行记录"""
    record, repo = running
    rid = record['resident_id']
    resident_protocol.cancel_resident_input(rid, 'CASE-A', MID)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: True)
    monkeypatch.setattr(resident_execution_domain, '_notify_lineage_message_terminal', mock.Mock())
    original = repo.set_input_state
    def fail_terminal(*args, **kwargs):
        if args[2] == 'cancelled':
            raise RuntimeError('terminal publication unavailable')
        return original(*args, **kwargs)
    monkeypatch.setattr(repo, 'set_input_state', fail_terminal)
    with pytest.raises(RuntimeError, match='terminal publication unavailable'):
        resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')
    assert resident_execution_work.read(record)['execution_id'] == EID
    assert repo.input_state(rid, MID)['state'] == 'running'
    monkeypatch.setattr(repo, 'set_input_state', original)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: False)
    resident_execution_domain.apply_cancelled_continuation(rid, 'continuation-stop')
    pending_ack = resident_execution_work.read(record)
    assert pending_ack['execution_id'] == EID
    assert not pending_ack.get(resident_execution_work.CONVERSATION_PROJECTION_INTENTS)
    monkeypatch.setattr(resident_input_queue_domain, 'acknowledge_execution', lambda *a: True)
    resident_execution_domain._finalize_applied_execution(rid, 'continuation-stop', pending_ack)
    notices = [message for message in repo.read_messages(rid)
               if message.get('reply_to_message_id') == MID and message.get('role') == 'system']
    assert len(notices) == 1
    assert repo.input_state(rid, MID)['state'] == 'cancelled'
    assert not resident_execution_work.read(record)


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
