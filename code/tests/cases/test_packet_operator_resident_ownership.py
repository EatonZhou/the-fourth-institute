"""Resident decisions preserve the logical operator's ownership and batch.

:module: lineage.service
:design: decision_4adcc1ff2f2c
"""
import json

import pytest

from management_portal.society_service import lineage_domain, lineage_operator_runtime, trigger_paths
from tests.cases import society_service_test_support as support
from tests.cases.isa_test_support import ok, run
from tests.cases.test_isa_lineage_lifecycle import (  # noqa: F401
    future_creation_store, graph, lineage, resident,
)
from tests.cases.test_lineage_generic_primitives import (  # noqa: F401
    primitive_store, ACTOR, WORK_A, WORK_B, _worker,
)


def _operator(implementation='rules', *, executor=None):
    config = {'operator_implementation': implementation, 'prompt': 'Keep all'}
    if executor:
        config['resident_executor_id'] = executor
    return config


def _create_operator(lineage, title, implementation='rules'):
    result = ok('ApplyLineageChanges', {'lineage_id': lineage, 'operations': [{
        'op': 'create', 'kind': 'component', 'ref': 'operator', 'value': {
            'kind': 'filter', 'title': title, 'config': _operator(implementation),
        },
    }]})
    return result['refs']['operator']


def _switch(lineage, operator, *, executor=None):
    return {'lineage_id': lineage, 'operations': [{
        'op': 'update', 'kind': 'component', 'id': operator,
        'changes': {'config': _operator('resident', executor=executor)},
    }]}


def _delete_operator(lineage, operator):
    return ok('ApplyLineageChanges', {'lineage_id': lineage, 'operations': [
        {'op': 'delete', 'kind': 'component', 'id': operator},
    ]})


def _component(lineage_view, operator):
    return next(c for c in lineage_view['program']['components'] if c['id'] == operator)


def test_existing_member_can_execute_two_operators_without_losing_leadership(lineage):
    """既有成员绑定多个算子保持普通身份，删除其中一个不改变另一个或 Leader。"""
    leader = resident(lineage, 'Leader')
    resident(lineage, 'Other')
    ids = [_create_operator(lineage, title) for title in ('First', 'Second')]
    for cid in ids:
        ok('ApplyLineageChanges', _switch(lineage, cid, executor=leader))
    saved = graph(lineage)
    assert saved['leader_resident_id'] == leader
    assert not next(n for n in saved['nodes'] if n['id'] == leader).get('operator_executor')
    _delete_operator(lineage, ids[0])
    saved = graph(lineage)
    assert saved['leader_resident_id'] == leader
    assert _component(saved, ids[1])['config']['resident_executor_id'] == leader


@pytest.mark.parametrize('initial', ['resident', 'rules'])
def test_new_dedicated_executor_stays_exclusive_and_is_released_on_delete(lineage, initial):
    """新建及从规则切换产生的专用执行者仍拒绝共用，删除算子只解除身份。"""
    leader = resident(lineage, 'Leader')
    cid = _create_operator(lineage, 'First', initial)
    if initial == 'rules':
        before = graph(lineage)
        receipt = ok('ApplyLineageChanges', _switch(lineage, cid), key='switch-executor')
        replay = run('ApplyLineageChanges', _switch(lineage, cid), key='switch-executor')
        assert replay.status == 'committed' and replay.replayed
        assert replay.result['created_ids'] == receipt['created_ids']
        assert replay.result['revision'] == receipt['revision']
        assert receipt['revision'] == before['revision'] + 1
    saved = graph(lineage)
    worker = _component(saved, cid)['config']['resident_executor_id']
    assert saved['leader_resident_id'] == leader
    assert next(n for n in saved['nodes'] if n['id'] == worker)['operator_executor']['component_id'] == cid
    assert {n['id'] for n in saved['nodes']} == {leader, worker}
    if initial == 'rules':
        assert receipt['created_ids'] == [worker]
        ok('ApplyLineageChanges', _switch(lineage, cid), key='switch-executor-again')
        assert _component(graph(lineage), cid)['config']['resident_executor_id'] == worker
    other = _create_operator(lineage, 'Other')
    denied = run('ApplyLineageChanges', _switch(lineage, other, executor=worker))
    assert denied.status == 'failed'
    _delete_operator(lineage, cid)
    assert not next(n for n in graph(lineage)['nodes'] if n['id'] == worker).get('operator_executor')


def test_executor_preview_and_denial_do_not_stage_identity(lineage, monkeypatch):
    from management_portal import permission_system
    from management_portal.society_service import resident_registration_domain

    cid = _create_operator(lineage, 'Awaiting')
    before = graph(lineage)
    monkeypatch.setattr(resident_registration_domain, 'prepare_lineage_registration',
                        lambda *_a, **_k: pytest.fail('registration staged'))
    preview = ok('GetLineageChangesPreview', _switch(lineage, cid))
    assert preview['created_ids'] == []
    assert preview['updated_ids'] == [cid]
    assert graph(lineage) == before

    def deny(_actor, items):
        assert items == [('ApplyLineageChanges:update_component', lineage)]
        return False

    monkeypatch.setattr(permission_system, 'allows_elements', deny)
    result = run('ApplyLineageChanges', _switch(lineage, cid))
    assert result.status == 'failed' and result.error['code'] == 'instruction.forbidden'
    assert graph(lineage) == before


def test_executor_registration_and_graph_roll_back_together(lineage, monkeypatch):
    from management_portal.society_service import lineage_state, resident_catalog, resident_record_domain

    cid = _create_operator(lineage, 'Awaiting')
    before = graph(lineage)
    catalog = resident_catalog.get_catalog()
    old_ids = {r['resident_id'] for r in catalog.for_lineage(lineage)}
    persist = lineage_state._persist_lineage_state
    staged_folders = []

    def fail_after_persist(previous, updated, reason, **options):
        persist(previous, updated, reason, **options)
        assert reason == 'ApplyLineageChanges'
        worker = _component(updated, cid)['config']['resident_executor_id']
        record = catalog.get(worker)
        assert record['lineage_id'] == lineage
        folder = resident_record_domain.resident_folder_path(record)
        assert folder.is_dir()
        staged_folders.append(folder)
        raise ValueError('abort graph and executor')

    with monkeypatch.context() as m:
        m.setattr(lineage_state, '_persist_lineage_state', fail_after_persist)
        result = run('ApplyLineageChanges', _switch(lineage, cid), key='aborted-switch')
    assert result.status == 'failed' and 'abort graph and executor' in result.error['message']
    assert graph(lineage) == before
    assert {r['resident_id'] for r in catalog.for_lineage(lineage)} == old_ids
    assert staged_folders and not staged_folders[0].exists()
    ok('ApplyLineageChanges', _switch(lineage, cid), key='retried-switch')
    worker = _component(graph(lineage), cid)['config']['resident_executor_id']
    assert {r['resident_id'] for r in catalog.for_lineage(lineage)} == old_ids | {worker}


def test_executor_batch_commits_distinct_identities_in_one_revision(lineage):
    ids = [_create_operator(lineage, title) for title in ('First', 'Second')]
    before = graph(lineage)
    receipt = ok('ApplyLineageChanges', {
        'lineage_id': lineage,
        'operations': [_switch(lineage, cid)['operations'][0] for cid in ids],
    }, version=before['revision'])
    saved = graph(lineage)
    assert saved['revision'] == receipt['revision'] == before['revision'] + 1
    workers = {_component(saved, cid)['config']['resident_executor_id']: cid for cid in ids}
    assert len(workers) == 2
    assert set(receipt['created_ids']) == set(workers)
    assert {n['id']: n['operator_executor']['component_id'] for n in saved['nodes']} == workers
    assert not saved['leader_resident_id']


@pytest.mark.parametrize('parallel_rule_branch', [False, True])
def test_shared_executor_chain_joins_the_original_rule_branch_once(primitive_store, monkeypatch, parallel_rule_branch):
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
    if not parallel_rule_branch:
        components = [c for c in components if c['id'] not in {rules, join}]
        edges = [(source, first), (first, second), (second, WORK_B)]
    wires = [{'id': f'wire_{i+1:012x}', 'kind': 'route', 'source': a, 'source_port': 'output',
              'target': b, 'target_port': 'invoke'} for i, (a,b) in enumerate(edges)]
    saved = support.create_lineage_with_resident_members({'title': 'Async operator join',
        'nodes': [_worker(WORK_A), _worker(WORK_B)], 'program': {'components': components, 'wires': wires}}, ACTOR)
    lid = saved['lineage_id']
    lineage_domain.emit_program_manual_event(lid, {'event_source_id': source, 'prompt': 'chosen'}, ACTOR)
    assert [(rid, mid) for rid, mid, _ in sent] == [(WORK_A, 'job_1')]
    for cid, mid in [(first, 'job_1'), (second, 'job_2')]:
        pending = lineage_operator_runtime.read_operator_runtime(lid, cid)['pending_deliveries'][mid]
        # Native execution finishes and can be projected before the operator's
        # completion observer runs. Its pending decision must keep the path open.
        execution_id = support.record_terminal_execution(WORK_A, mid, ACTOR)
        trigger_paths.link_input(lid, WORK_A, mid, execution_id)
        path_id = pending['event']['payload']['trigger_path_id']
        trigger_paths.settle(path_id)
        assert trigger_paths.get(lid, path_id)['status'] == 'active'
        replies[mid] = '<filter-result>'+json.dumps({'selected_packet_ids':[p['id'] for p in pending['input_packets']]})+'</filter-result>'
        result = support.complete_resident_message(WORK_A, ACTOR, mid, lineage_id=lid)
        assert result['operator_results'][0]['status'] == 'filtered', result
    assert [(rid, mid) for rid, mid, _ in sent] == [(WORK_A, 'job_1'), (WORK_A, 'job_2'), (WORK_B, 'job_3')]
    assert sent[-1][2].count('chosen') >= (2 if parallel_rule_branch else 1)
    for cid in ([first, second, rules, join] if parallel_rule_branch else [first, second]):
        runtime = lineage_operator_runtime.read_operator_runtime(lid, cid)
        assert not runtime.get('inbox')
        assert not runtime.get('pending_deliveries')
    support.complete_resident_message(WORK_B, ACTOR, 'job_3', lineage_id=lid)
    assert trigger_paths.get(lid, path_id)['status'] == 'completed'
