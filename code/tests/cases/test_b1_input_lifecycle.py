"""Resident input lifecycle regressions for deletion, controls and media retention."""
from types import SimpleNamespace

import pytest

from management_common.json_columns import json_value
from management_portal.society_service import (
    invariant_locks, resident_catalog, resident_conversation_stream, resident_intake_domain,
    resident_execution_work, resident_lifecycle_domain, resident_mailbox, resident_input_recovery,
    resident_media_domain, resident_record_domain,
)
from management_portal.society_service.domain_ports import execution_port
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.society_service.state_store import LineageDataStore
from tests.cases.test_lineage_catalogs import _lineage_definition, _record
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401


@pytest.mark.parametrize('fail_delete', [False, True])
def test_catalog_deletion_archives_turn_and_queues_ack_atomically(tmp_path, monkeypatch, fail_delete):
    """目录删除原子归档执行并排队确认操作"""
    store = LineageDataStore()
    lineage = _lineage_definition()
    store.put_lineage_definition(lineage['lineage_id'], lineage, {})
    record = _record('resident_aaaaaaaaaaaa')
    rid = record['resident_id']
    store.put_resident_record(rid, record)
    get_agent_data_store().ensure_agent(rid, actor_ref='CASE-A')
    store.put_resident_execution_record(rid, {
         'user_message_id': 'msg_finished',
        'execution_id': 'execution-finished', 'actor': 'CASE-A', 'outcome': 'failed',
     "submitted_at": "2026-10-14T00:00:00Z"})
    get_agent_data_store().finish_execution("execution-finished", status="failed", finished_at="2026-10-14T00:00:00Z")
    catalog = resident_catalog.ResidentCatalog(tmp_path, store)
    monkeypatch.setattr(resident_lifecycle_domain, 'ensure_resident_agent', lambda *a, **kw: None)
    monkeypatch.setattr(resident_mailbox, 'close', lambda *a: None)
    tombstone = {**record, 'deleted_at': '2026-10-11T00:00:00Z', 'deleted_by': 'CASE-A'}
    if fail_delete:
        def fail(*a):
            raise RuntimeError('delete failure')
        monkeypatch.setattr(store, 'remove_residents', fail)
        with pytest.raises(RuntimeError, match='delete failure'):
            catalog.remove([tombstone])
        assert store.resident_execution_record(rid)['execution_id'] == 'execution-finished'
        with store.database.read() as c:
            assert not c.execute('SELECT 1 FROM resident_lifecycle_operations').fetchall()
        return
    catalog.remove([tombstone])
    with store.database.read() as c:
        assert not c.execute('SELECT 1 FROM resident_execution_work WHERE resident_id=%s', (rid,)).fetchone()
        pending = c.execute('SELECT payload_json FROM resident_lifecycle_operations WHERE resident_id=%s', (rid,)).fetchone()
    assert json_value(pending['payload_json'])['steps'] == [
        {'kind': 'execution_ack', 'execution_id': 'execution-finished', 'actor': 'CASE-A'},
    ]
    assert get_agent_data_store().execution('execution-finished')['status'] == 'failed'
    catalog.remove([tombstone])
    assert get_agent_data_store().execution('execution-finished')['status'] == 'failed'


def test_control_does_not_follow_a_replaced_turn(resident_record, monkeypatch):
    """轮次被替换后控制输入不发送到新执行"""
    resident_execution_work.write(resident_record, {
         'execution_id': 'execution-new', 'actor': 'CASE-A',
     "submitted_at": "2026-10-14T00:00:00Z"})
    monkeypatch.setattr(execution_port, 'service', lambda: SimpleNamespace(
        append_input=lambda *a, **kw: pytest.fail('sent the old control to a new turn'),
    ))
    result = resident_intake_domain._submit_active_turn_input(
        resident_record, 'CASE-A', control_key='msg_control', turn_input=[{'type': 'text', 'text': 'hello'}],
        source='queued_message', expected_execution_id='execution-old',
    )
    assert result['status'] == 'inactive'


def test_image_cleanup_preserves_active_unknown_and_recent_rounds(tmp_path, monkeypatch):
    """图片清理保留活跃状态未知及恢复点引用的输入"""
    record = {'resident_id': 'resident_aaaaaaaaaaaa'}
    names = ['msg_' + str(i) * 32 for i in range(1, 6)]
    for name in names:
        (tmp_path / 'input_media' / name).mkdir(parents=True)
    monkeypatch.setattr(resident_record_domain, '_sessions_path', lambda r: tmp_path)
    monkeypatch.setattr(resident_input_recovery, 'checkpoints', lambda r: [{'message_id': names[0]}])
    states = {names[0]: 'failed', names[1]: 'completed', names[2]: 'cancelled', names[3]: 'running'}
    def state(rid, mid):
        assert invariant_locks.lock_held()
        return {'state': states[mid]}
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(input_state=state))
    removed = resident_media_domain.cleanup_expired_input_images(record)
    assert set(removed) == {names[1], names[2]}
    assert {p.name for p in (tmp_path / 'input_media').iterdir()} == {names[0], names[3], names[4]}


def test_context_reads_all_pages_and_counts_omitted_messages(monkeypatch):
    """上下文读取遍历所有分页并统计省略消息"""
    from management_portal.society_service import conversation_projection as p, resident_protocol
    messages = [{'message_id': f'msg_{i}', 'role': 'user', 'content_parts': [{'type': 'text', 'text': str(i)}]} for i in range(610)]
    monkeypatch.setattr(p.resource_client, 'get', lambda *a, **kw: {})
    monkeypatch.setattr(resident_protocol, 'get_resident_conversation_snapshot', lambda *a, **kw: {
        'messages': messages[-200:], 'next_page_token': '410', 'state': {},
    })
    calls = []
    def page(rid, actor, token, *, limit):
        end = int(token); start = max(0, end-limit); calls.append(token)
        return {'messages': messages[start:end], 'next_page_token': str(start) if start else ''}
    monkeypatch.setattr(resident_protocol, 'get_conversation_messages', page)
    result = p.read_context('resident_aaaaaaaaaaaa', actor='CASE-A', max_messages=500)
    assert result['source_message_count'] == 610
    assert result['returned_message_count'] == 500
    assert result['truncated_message_count'] == 110
    assert result['messages'][0]['content'] == '110'
    assert calls


def test_image_cleanup_keeps_group_members_after_round_item_is_trimmed(tmp_path, monkeypatch):
    """轮次条目裁剪后图片清理仍保留分组成员"""
    record = {'resident_id': 'resident_aaaaaaaaaaaa'}
    message_id = 'msg_' + 'a' * 32
    info_id = 'msg_' + 'b' * 32
    (tmp_path / 'input_media' / info_id).mkdir(parents=True)
    (tmp_path / 'input_media' / message_id).mkdir(parents=True)
    monkeypatch.setattr(resident_record_domain, '_sessions_path', lambda r: tmp_path)
    monkeypatch.setattr(resident_input_recovery, 'checkpoints', lambda r: [{'message_id': 'group_1', 'kind': 'group', 'infos': [{'message_id': info_id}]}])
    monkeypatch.setattr(resident_mailbox, 'members_of', lambda r, mid: [message_id])
    monkeypatch.setattr(resident_conversation_stream, 'get_repository', lambda: SimpleNamespace(
        input_state=lambda *a: {'state': 'completed'},
    ))
    assert resident_media_domain.cleanup_expired_input_images(record) == []
    assert (tmp_path / 'input_media' / message_id).is_dir()
    assert (tmp_path / 'input_media' / info_id).is_dir()
