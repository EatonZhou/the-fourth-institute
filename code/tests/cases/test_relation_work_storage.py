"""
:module: spec.design-governance
:design: decision_71aa039e3211
"""
import pytest

from management_portal.society_service import relation_work_storage as work
from management_portal.execution.agent_data_store import get_agent_data_store
from management_portal.execution import execution_inputs
from management_portal.society_service.state_store import RuntimeCASConflict
from tests.cases.test_lineage_object_state import LINEAGE_ID, _store, object_resources


def test_relation_kinds_roundtrip_and_unchanged_work_is_not_rewritten(tmp_path):
    """边上的交接与吸收工作原样读回，未变化的边不被重写

    :design-point:"""
    store=_store(tmp_path)
    before={'edge-one':{'handoff':{'cursor':1},'absorption':{'status':'pending'}},
            'edge-two':{'absorption':{'status':'pending'}}}
    with store.database.transaction() as c:
        work.put(c,LINEAGE_ID,{},before)
        assert work.read(c,LINEAGE_ID)==before
        version=c.execute("SELECT xmin FROM context_absorption_work WHERE edge_id='edge-two'").fetchone()[0]
    with store.database.transaction() as c:
        after={**before,'edge-one':{'handoff':{'cursor':2},'absorption':{'status':'pending'}}}
        work.put(c,LINEAGE_ID,before,after)
    with store.database.read() as c:
        assert work.read(c,LINEAGE_ID)==after
        assert c.execute("SELECT xmin FROM context_absorption_work WHERE edge_id='edge-two'").fetchone()[0]==version


def test_relation_cas_merges_different_edges_and_rejects_same_edge(tmp_path):
    """Lineage 运行态 CAS 合并对不同边的并发修改，拒绝对同一条边的冲突修改

    :design-point:"""
    store=_store(tmp_path)
    initial=store.lineage_snapshot(LINEAGE_ID)[1]
    first={**initial,'edges':{'absorption-a':{'absorption':{'status':'pending'}}}}
    second={**initial,'edges':{'absorption-b':{'absorption':{'status':'pending'}}}}
    store.put_lineage_runtime_cas(LINEAGE_ID,initial,first)
    result=store.put_lineage_runtime_cas(LINEAGE_ID,initial,second)
    assert set(result['edges'])=={'absorption-a','absorption-b'}
    with pytest.raises(RuntimeCASConflict):
        store.put_lineage_runtime_cas(LINEAGE_ID,initial,first)



def test_latest_execution_is_not_hidden_by_an_older_completion(tmp_path):
    """较早的执行完成后，Lineage 节点仍显示最新排队的执行

    :design-point:"""
    store = _store(tmp_path)
    resident = "resident_111111111111"
    agent = get_agent_data_store()
    agent.ensure_agent(resident, actor_ref="CASE-A")
    for identity, at in [("old", "2026-10-11T00:00:00Z"), ("new", "2026-10-11T00:01:00Z")]:
        agent.reserve_execution(execution_id=identity, agent_id=resident, provider="codex", actor_ref="CASE-A", created_at=at)
        execution_inputs.attach(agent, identity, [{"message_id": identity, "role": "primary", "accepted_at": at}])
        agent.queue_execution(identity)
    agent.finish_execution("old", status="completed", finished_at="2026-10-11T00:02:00Z")
    current = store.lineage_snapshot(LINEAGE_ID)[1]["nodes"][resident]
    assert current["status"] == "queued" and current["last_execution"]["execution_id"] == "new"
    agent.finish_execution("new", status="completed", finished_at="2026-10-11T00:03:00Z")
    assert store.lineage_snapshot(LINEAGE_ID)[1]["nodes"][resident]["status"] == "idle"


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'completed', 'queued'])
def test_latest_execution_failure_details_are_scoped_to_its_input(tmp_path, status):
    """最近执行按原输入投影失败详情，取消或新执行不能继承旧错误"""
    import json
    from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository

    store = _store(tmp_path)
    resident = 'resident_111111111111'
    agent = get_agent_data_store()
    agent.ensure_agent(resident, actor_ref='CASE-A')
    agent.reserve_execution(execution_id='current', agent_id=resident, provider='codex',
                            actor_ref='CASE-A', created_at='2026-10-22T00:00:00Z')
    execution_inputs.attach(agent, 'current', [{'message_id': 'input-current', 'role': 'primary',
                                               'accepted_at': '2026-10-22T00:00:00Z'}])
    agent.queue_execution('current')
    if status != 'queued':
        agent.finish_execution('current', status=status, finished_at='2026-10-22T00:00:02Z')
    with store.database.transaction() as connection:
        ResidentConversationRepository(store, agent)._ensure_conversation(connection, resident)
        for message, error in [('unrelated-input', 'wrong error'), ('input-current', 'The read operation timed out')]:
            connection.execute(
                'INSERT INTO conversation_input_states(resident_id,message_id,state,error_json,updated_at) '
                "VALUES(%s,%s,'failed',%s,'2026-10-22T00:00:02Z')",
                (resident, message, json.dumps({'code': 'timeout', 'message': error})),
            )
    result = store.lineage_snapshot(LINEAGE_ID)[1]['nodes'][resident]['last_execution']
    assert result['execution_id'] == 'current' and result['message_id'] == 'input-current'
    assert result['status'] == status
    assert result['error'] == ('The read operation timed out' if status == 'failed' else '')
    assert result['error_code'] == ('timeout' if status == 'failed' else '')
    if status == 'failed':
        # Reusing an input on a new execution must not attribute its previous
        # failed attempt's error before the new input-state projection arrives.
        agent.reserve_execution(execution_id='next', agent_id=resident, provider='codex',
                                actor_ref='CASE-A', created_at='2026-10-22T00:01:00Z')
        execution_inputs.attach(agent, 'next', [{'message_id': 'input-current', 'role': 'primary',
                                                'accepted_at': '2026-10-22T00:01:00Z'}])
        agent.finish_execution('next', status='failed', finished_at='2026-10-22T00:01:02Z')
        latest = store.lineage_snapshot(LINEAGE_ID)[1]['nodes'][resident]['last_execution']
        assert latest['execution_id'] == 'next' and latest['status'] == 'failed'
        assert latest['error'] == '' and latest['error_code'] == ''


def test_current_resident_observations_do_not_scan_completed_history(tmp_path):
    """读取 Resident 当前执行状态只检查少量行，不扫描已完成的历史执行

    :design-point:"""
    store = _store(tmp_path)
    resident = 'resident_111111111111'

    agent = get_agent_data_store()
    agent.ensure_agent(resident, actor_ref="CASE-A")
    with agent.database.transaction() as connection:
        connection.execute("""
            INSERT INTO executions(execution_id,agent_id,actor_ref,provider,provider_turn_id,created_at,
                                   started_at,finished_at,status,usage_json)
            SELECT 'history-' || i,%s,'CASE-A','codex','','2026-10-10T00:00:00Z',
                   '2026-10-10T00:00:00Z','2026-10-10T00:01:00Z','completed','{}'
            FROM generate_series(1,8000) i
        """, (resident,))
        connection.execute("ANALYZE executions")
    agent.reserve_execution(execution_id="current", agent_id=resident, provider="codex",
                            actor_ref="CASE-A", created_at="2026-10-11T00:00:00Z")
    execution_inputs.attach(agent, "current", [{"message_id": "current", "role": "primary",
                                                "accepted_at": "2026-10-11T00:00:00Z"}])

    with store.database.read() as connection:
        class MeasuredConnection:
            plan = None

            def execute(self, sql, args):
                self.plan = connection.execute(
                    'EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, args,
                ).fetchone()[0][0]['Plan']
                return connection.execute(sql, args)

        measured = MeasuredConnection()
        observations = work.resident_observations(measured, LINEAGE_ID)
        assert observations[resident]['last_execution']['message_id'] == 'current'
        assert 'resident_222222222222' not in observations

    def records_examined(plan):
        own = 0
        if plan.get('Relation Name') == 'executions':
            own = (plan['Actual Rows'] + plan.get('Rows Removed by Filter', 0)) * plan['Actual Loops']
        return own + sum(records_examined(child) for child in plan.get('Plans', []))

    assert records_examined(measured.plan) < 20, measured.plan
