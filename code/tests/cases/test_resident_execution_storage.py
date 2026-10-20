"""
:module: spec.design-governance
:design: decision_71aa039e3211, decision_edc470e09f5a
"""
from copy import deepcopy

import pytest

from management_portal.society_service import resident_execution_storage
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_conversation_projection_authority import RESIDENT, _repository


def test_completed_execution_is_removed_only_after_ack_and_keeps_queue(tmp_path):
    """已结束的执行记录只在按自身 execution_id 确认后删除，排队输入保留

    :design-point:"""
    store = _repository(tmp_path).state_store
    job = {'execution_id':'execution-one','actor':'sample_admin',
           'submitted_at':'2026-10-11T00:00:00Z','user_message_id':'message-one','outcome':'failed','finished_at':'2026-10-11T00:00:00Z',
           'queue':[{'user_message_id':'message-next'}]}
    store.put_resident_execution_record(RESIDENT,job)
    assert store.resident_execution_record(RESIDENT)['execution_id'] == 'execution-one'
    store.acknowledge_resident_execution(RESIDENT,'other-execution')
    assert store.resident_execution_record(RESIDENT)['execution_id'] == 'execution-one'
    store.acknowledge_resident_execution(RESIDENT,'execution-one')
    assert store.resident_execution_record(RESIDENT) == {'queue':job['queue']}
    with store.database.read() as c:
        assert c.execute('SELECT count(*) FROM resident_execution_work').fetchone()[0] == 0



def test_cursor_update_is_monotonic_fenced_and_does_not_read_preparation(tmp_path, monkeypatch):
    """执行事件游标只前进、只接受当前 execution_id，更新时不读取 preparation

    :design-point:"""
    store = _repository(tmp_path).state_store
    job = {'execution_id':'execution-one','actor':'sample_admin','message':'large ' * 200_000}
    store.put_resident_execution_record(RESIDENT,job)
    with store.database.read() as c:
        before = c.execute('SELECT preparation_json FROM resident_execution_work').fetchone()[0]
    monkeypatch.setattr(resident_execution_storage,'read',lambda *a,**kw: pytest.fail('cursor loaded the turn'))
    store.advance_resident_execution_cursor(RESIDENT,'execution-one',12)
    store.advance_resident_execution_cursor(RESIDENT,'execution-one',4)
    store.advance_resident_execution_cursor(RESIDENT,'other',19)
    with store.database.read() as c:
        row = c.execute('SELECT event_cursor,preparation_json FROM resident_execution_work').fetchone()
        assert row['event_cursor'] == 12 and row['preparation_json'] == before


def test_execution_revision_is_not_reused_after_cleanup(tmp_path):
    """删除执行记录后新执行的 revision 仍然递增

    :design-point:"""
    store = _repository(tmp_path).state_store
    first = store.put_resident_execution_record(RESIDENT,{'execution_id':'one','actor':'sample_admin'})
    store.delete_resident_execution_record(RESIDENT)
    second = store.put_resident_execution_record(RESIDENT,{'execution_id':'two','actor':'sample_admin'})
    assert second > first


def test_deferred_prompt_keeps_runtime_context_until_execution(tmp_path):
    """延后构建的 prompt 在执行前保留 runtime_context，尚无实际发送内容

    :module: resident.identity
    :design-point:
    """
    store = _repository(tmp_path).state_store
    store.put_resident_execution_record(RESIDENT, {
        'execution_id': 'execution-deferred', 'actor': 'sample_admin',
        'message': 'question',
        'effective_message': 'question',
        'runtime_context': 'current task context',
    })
    saved = store.resident_execution_record(RESIDENT)
    assert saved['runtime_context'] == 'current task context'
    assert not saved.get('prompt_builder_version')
    assert 'actual_sent_prompt' not in saved


def test_states_use_owners_once_and_ignore_the_notification_cache(tmp_path,monkeypatch):
    """批量读取状态时每个执行只向执行服务查询一次，关闭 intake 不影响已接纳的执行

    :design-point:"""
    repository = _repository(tmp_path)
    store = repository.state_store
    repository.set_state(RESIDENT,{'work_state':'idle','intake_state':'open'})
    store.database.write(lambda c:c.execute('UPDATE residents SET intake_open=false WHERE resident_id=%s',(RESIDENT,)))
    store.put_resident_execution_record(RESIDENT,{'execution_id':'execution-one','actor':'sample_admin',
        'submitted_at':'2026-10-11T00:00:00Z','user_message_id':'message-one','queue':[{'user_message_id':'message-next'}]})
    calls=[]
    class Service:
        def states(self,executions):
            calls.append(executions)
            return {'execution-one':{'status':'running'}}
    monkeypatch.setattr(execution_port,'service',lambda:Service())
    result=repository.states([RESIDENT,RESIDENT])[RESIDENT]
    assert result['intake_state'] == 'closed'
    # Closing intake leaves already admitted work running.
    assert result['work_state'] == 'running' and result['queued_count'] == 1
    assert calls == [{'execution-one':'sample_admin'}]


def test_stale_execution_write_cannot_undo_stop_cursor_or_ack(tmp_path):
    """过期的执行记录写入不能撤销停止请求、事件游标、确认和接纳时间

    :design-point:"""
    store=_repository(tmp_path).state_store
    job={'execution_id':'exec','actor':'sample_admin'}
    stale=deepcopy(job)
    store.put_resident_execution_record(RESIDENT,job)
    store.put_resident_execution_record(RESIDENT,{**job,'stop_requested_at':'now','execution_event_cursor':20,
                                                'intake_admitted_at':'before-close'})
    store.acknowledge_resident_execution(RESIDENT,'exec')
    store.put_resident_execution_record(RESIDENT,stale)
    saved=store.resident_execution_record(RESIDENT)
    assert saved['stop_requested_at']=='now' and saved['execution_event_cursor']==20
    assert saved['execution_acknowledged'] is True
    assert saved['intake_admitted_at'] == 'before-close'
