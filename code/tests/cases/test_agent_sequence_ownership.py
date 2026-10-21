"""Sequences are ordered references; Agent records own purpose and ancestry.

:design: decision_b1ab45c4b3ec
"""
import pytest
from management_portal.execution.agent_data_store import AgentDataStore
from tests.cases.test_subagent_trace import _turn


def test_a_sequence_holds_references_and_its_agent_holds_purpose():
    """序列只保存引用，用途与归属记在 Agent 上

    Agent 的 trace 序列只保存有序的 Segment 引用，序列记录与 sequences 表都没有类型、所有者或父序列字段；类型记在 Agent 记录上。

    :design-point:
    """
    store = AgentDataStore()
    agent = store.ensure_agent('resident_123456789abc')
    segment = store.put_segment(segment_type='input', content='same reference', started_at='2026-10-08T00:00:00Z')
    store.append_agent_segments('resident_123456789abc', [segment], actor_ref='test')
    assert store.sequence_segment_ids(agent['trace_sequence_id']) == [segment]
    assert 'sequence_kind' not in store.read_sequence(agent['trace_sequence_id'])
    assert agent['sequence_kind'] == 'agent_trace'
    with store.database.read() as c:
        columns = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='sequences'").fetchall()}
    assert not {'owner_id','sequence_kind','parent_sequence_id'} & columns


def test_grandchild_registered_before_parent_content_and_replay_keeps_identity():
    """孙级子 agent 先于父级内容登记时父子关系正确，重放不重复，Fork 不复制子 agent trace

    :design-point:"""
    store, projector, request, _session = _turn()
    event = {'method':'subagent/event','params':{
        'traceKey':'C', 'ancestorTraceKeys':['B'], 'parentToolUseId':'spawn-C', 'agentId':'C',
        'event':{'method':'item/completed','params':{'turnId':'c-turn','item':{
            'type':'agentMessage','id':'answer','text':'child result'}}},
    }}
    def observe(value):
        projector.observe(provider='claude', backend_route_id='one', backend_session_id='session-1', execution_id=request.execution_id,event=value)
    observe(event); observe(event)
    b=store.agent(store.subagent_id(request.agent_id,'B'))
    c=store.agent(store.subagent_id(request.agent_id,'C'))
    assert b['parent_agent_id'] == request.agent_id
    assert c['parent_agent_id'] == b['agent_id']
    assert b['sequence_kind'] == c['sequence_kind'] == 'subagent_trace'
    assert len(store.sequence_segment_ids(c['trace_sequence_id'])) == 1
    assert store.sequence_segment_ids(b['trace_sequence_id']) == []
    store.fork_agent(source_agent_id=request.agent_id, target_agent_id='fork', actor_ref='test', segment_ids=store.sequence_segment_ids(store.trace_sequence_id(request.agent_id)))
    assert not set(store.sequence_segment_ids(c['trace_sequence_id'])) & set(store.sequence_segment_ids(store.trace_sequence_id('fork')))
    assert store.agent('fork')['parent_agent_id'] is None


def test_unknown_parent_can_be_proven_once_without_cycles():
    """父级未知的子 agent 可补记一次父级，成环或改为其他父级被拒绝

    :design-point:"""
    store = AgentDataStore()
    store.ensure_agent('root')
    store.ensure_agent('B', sequence_kind='subagent_trace')
    c = store.ensure_agent('C', sequence_kind='subagent_trace', parent_agent_id='B')
    with pytest.raises(ValueError, match='cycle'):
        store.ensure_agent('B', sequence_kind='subagent_trace', parent_agent_id='C')
    assert store.ensure_agent('B', sequence_kind='subagent_trace', parent_agent_id='root')['parent_agent_id']=='root'
    with pytest.raises(ValueError, match='different'):
        store.ensure_agent('C', sequence_kind='subagent_trace', parent_agent_id='root')
    assert store.agent('C')['trace_sequence_id']==c['trace_sequence_id']



def test_parent_edges_reject_self_reference_and_cycle():
    """子 agent 父级关系拒绝自引用和成环，Resident 自身的 Agent 不能有父级

    :module: execution.persistence
    :design-point:
    """
    store = AgentDataStore()
    with pytest.raises(ValueError):
        store.ensure_agent('child', sequence_kind='subagent_trace', parent_agent_id='child')
    store.ensure_agent('resident_111111111111')
    store.ensure_agent('child-a', sequence_kind='subagent_trace')
    store.ensure_agent('child-b', sequence_kind='subagent_trace', parent_agent_id='child-a')
    with pytest.raises(ValueError, match='cycle'):
        store.ensure_agent('child-a', sequence_kind='subagent_trace', parent_agent_id='child-b')
    # Only a subagent trace has a parent; a Resident's own Agent does not.
    with pytest.raises(ValueError):
        store.ensure_agent('resident_222222222222', parent_agent_id='resident_111111111111')

def test_kimi_reports_direct_parent_for_nested_child():
    """kimi 嵌套子 agent 的事件上报直接父级及派生它的工具调用

    :design-point:"""
    from management_portal.kimi_live_events import KimiLiveItems
    from tests.cases.test_live_provider_ingress import frame
    events=[]; live=KimiLiveItems('s', events.append)
    for ev in [frame('subagent.spawned',subagentId='B',parentToolCallId='spawn-B',parentAgentId='main'),
               frame('subagent.spawned',agent='B',subagentId='C',parentToolCallId='spawn-C',parentAgentId='B'),
               frame('turn.started',agent='C',prompt='child input')]:
        live.observe(ev)
    assert events[-1]['params']['ancestorTraceKeys']==['s:B']
    assert events[-1]['params']['parentToolUseId']=='spawn-C'


@pytest.mark.parametrize('provider', ['claude', 'kimi'])
def test_missing_parent_evidence_then_nested_edge_and_reconnect(provider):
    """缺少父级证据的子 agent 先独立记录，之后补上父级与祖先链，重连后仍写入同一 trace

    :design-point:"""
    store, projector, request, _session = _turn()
    def observe(item_id, **ancestry):
        projector.observe(provider=provider, backend_route_id='one', backend_session_id='session-1',
            execution_id=request.execution_id, event={'method':'subagent/event','params':{
                'traceKey':'C', 'parentToolUseId':'spawn-C', **ancestry,
                'event':{'method':'item/completed','params':{'turnId':'child-turn','item':{
                    'type':'agentMessage','id':item_id,'text':item_id}}}}})
    child_id = store.subagent_id(request.agent_id, 'C')
    parent_id = store.subagent_id(request.agent_id, 'B')
    observe('before-spawn')
    assert store.agent(child_id)['parent_agent_id'] is None
    sequence = store.agent(child_id)['trace_sequence_id']
    observe('direct-parent-known', parentTraceKey='B')
    assert store.agent(child_id)['parent_agent_id'] == parent_id
    assert store.agent(parent_id)['parent_agent_id'] is None
    observe('full-chain-known', ancestorTraceKeys=['B'])
    assert store.agent(parent_id)['parent_agent_id'] == request.agent_id
    observe('reconnect')
    assert store.agent(child_id)['parent_agent_id'] == parent_id
    assert store.agent(child_id)['trace_sequence_id'] == sequence
    assert len(store.sequence_segment_ids(sequence)) == 4


def test_kimi_does_not_invent_ancestry_when_spawn_was_not_observed():
    """未观察到派生事件时 kimi 不编造子 agent 的祖先链，看到派生后再补上

    :design-point:"""
    from management_portal.kimi_live_events import KimiLiveItems
    from tests.cases.test_live_provider_ingress import frame
    events=[]; live=KimiLiveItems('s', events.append)
    live.observe(frame('turn.started',agent='C',prompt='input'))
    assert events == []  # it waits for its spawn
    live.finish()
    assert 'ancestorTraceKeys' not in events[-1]['params']
    assert 'parentTraceKey' not in events[-1]['params']
    live.observe(frame('subagent.spawned',agent='B',subagentId='C',parentToolCallId='spawn-C'))
    live.observe(frame('turn.started',agent='C',prompt='input'))
    assert 'ancestorTraceKeys' not in events[-1]['params']
    assert events[-1]['params']['parentTraceKey'] == 's:B'
    live.observe(frame('subagent.spawned',subagentId='B',parentToolCallId='spawn-B'))
    live.observe(frame('turn.started',agent='C',prompt='input'))
    assert events[-1]['params']['ancestorTraceKeys'] == ['s:B']
