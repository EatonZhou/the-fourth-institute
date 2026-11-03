"""Resident decisions preserve the logical operator's ownership and batch.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""
import json

import pytest

from management_portal.society_service import lineage_domain, lineage_operator_runtime
from tests.cases import society_service_test_support as support
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401
    future_creation_store, graph, lineage, resident,
)
from tests.cases.test_lineage_generic_primitives import (  # noqa: F401
    primitive_store, ACTOR, WORK_A, WORK_B, _worker,
)


def test_existing_member_can_execute_two_operators_without_losing_leadership(lineage):
    """既有成员绑定多个算子保持普通身份，删除其中一个不改变另一个或 Leader。"""
    leader = resident(lineage, 'Leader')
    resident(lineage, 'Other')
    ids = []
    for title in ['First', 'Second']:
        cid = ok('CreateComponent', {'lineage_id': lineage, 'kind': 'filter', 'title': title,
                                    'config': {'operator_implementation': 'rules'}})['component_id']
        ok('UpdateComponent', {'lineage_id': lineage, 'component_id': cid,
            'changes': {'config': {'operator_implementation': 'resident',
                                   'resident_executor_id': leader, 'prompt': 'Keep all'}}})
        ids.append(cid)
    saved = graph(lineage)
    assert saved['leader_resident_id'] == leader
    assert not next(n for n in saved['nodes'] if n['id'] == leader).get('operator_executor')
    ok('DeleteComponent', {'lineage_id': lineage, 'component_id': ids[0]})
    saved = graph(lineage)
    assert saved['leader_resident_id'] == leader
    remaining = next(c for c in saved['program']['components'] if c['id'] == ids[1])
    assert remaining['config']['resident_executor_id'] == leader


@pytest.mark.parametrize('initial', ['resident', 'rules'])
def test_new_dedicated_executor_stays_exclusive_and_is_released_on_delete(lineage, initial):
    """新建及从规则切换产生的专用执行者仍拒绝共用，删除算子只解除身份。"""
    resident(lineage, 'Leader')
    cid = ok('CreateComponent', {'lineage_id': lineage, 'kind': 'filter',
        'config': {'operator_implementation': initial, 'prompt': 'Keep'}})['component_id']
    if initial == 'rules':
        ok('UpdateComponent', {'lineage_id': lineage, 'component_id': cid,
                              'changes': {'config': {'operator_implementation': 'resident', 'prompt': 'Keep'}}})
    saved = graph(lineage)
    worker = next(c for c in saved['program']['components'] if c['id'] == cid)['config']['resident_executor_id']
    assert next(n for n in saved['nodes'] if n['id'] == worker)['operator_executor']['component_id'] == cid
    other = ok('CreateComponent', {'lineage_id': lineage, 'kind': 'filter',
                                  'config': {'operator_implementation': 'rules'}})['component_id']
    denied = run('UpdateComponent', {'lineage_id': lineage, 'component_id': other,
        'changes': {'config': {'operator_implementation': 'resident', 'resident_executor_id': worker, 'prompt': 'Keep'}}})
    assert denied.status == 'failed'
    ok('DeleteComponent', {'lineage_id': lineage, 'component_id': cid})
    assert not next(n for n in graph(lineage)['nodes'] if n['id'] == worker).get('operator_executor')


def test_shared_executor_chain_joins_the_original_rule_branch_once(primitive_store, monkeypatch):
    """同一普通执行者串行处理两个算子，与同步规则分支在原批次只汇合一次。"""
    support.install_registered_lineage_members(monkeypatch)
    sent = []
    replies = {}
    def send(node, prompt, actor):
        message = f'job_{len(sent)+1}'
        sent.append((node['id'], message, prompt))
        return {'message_id': message}
    monkeypatch.setattr(support, 'NODE_MESSAGE_SENDER', send)
    monkeypatch.setattr(support, 'RESIDENT_MESSAGE_EVIDENCE_RESOLVER',
                        lambda rid, mid, actor: {'job_id': mid, 'reply': replies.get(mid, 'done')})
    first, second, rules, join = ('filter_111111111111', 'filter_222222222222',
                                  'filter_333333333333', 'reducer_444444444444')
    source = 'event_source_555555555555'
    components = [{'id': source, 'kind': 'event_source', 'config': {'event_type': 'manual.triggered'}}]
    components += [{'id': cid, 'kind': 'filter', 'config': {'operator_implementation': 'resident',
                    'resident_executor_id': WORK_A, 'prompt': 'Keep all'}} for cid in [first, second]]
    components += [{'id': rules, 'kind': 'filter', 'config': {'operator_implementation': 'rules'}},
                   {'id': join, 'kind': 'reducer', 'config': {'operator_implementation': 'rules',
                     'reduce_strategy': 'join', 'reduce_separator': ' | ', 'output_key': 'joined'}}]
    edges = [(source, first), (source, rules), (first, second), (second, join), (rules, join), (join, WORK_B)]
    wires = [{'id': f'wire_{i+1:012x}', 'kind': 'route', 'source': a, 'source_port': 'output',
              'target': b, 'target_port': 'invoke'} for i, (a,b) in enumerate(edges)]
    saved = support.create_lineage_with_resident_members({'title': 'Async operator join',
        'nodes': [_worker(WORK_A), _worker(WORK_B)], 'program': {'components': components, 'wires': wires}}, ACTOR)
    lid = saved['lineage_id']
    lineage_domain.emit_program_manual_event(lid, {'event_source_id': source, 'prompt': 'chosen'}, ACTOR)
    assert [(rid, mid) for rid, mid, _ in sent] == [(WORK_A, 'job_1')]
    for cid, mid in [(first, 'job_1'), (second, 'job_2')]:
        pending = lineage_operator_runtime.read_operator_runtime(lid, cid)['pending_deliveries'][mid]
        replies[mid] = '<filter-result>'+json.dumps({'selected_packet_ids':[p['id'] for p in pending['input_packets']]})+'</filter-result>'
        result = support.complete_resident_message(WORK_A, ACTOR, mid, lineage_id=lid)
        assert result['operator_results'][0]['status'] == 'filtered', result
    assert [(rid, mid) for rid, mid, _ in sent] == [(WORK_A, 'job_1'), (WORK_A, 'job_2'), (WORK_B, 'job_3')]
    assert sent[-1][2].count('chosen') >= 2
    for cid in [first, second, rules, join]:
        runtime = lineage_operator_runtime.read_operator_runtime(lid, cid)
        assert not runtime.get('inbox')
        assert not runtime.get('pending_deliveries')
