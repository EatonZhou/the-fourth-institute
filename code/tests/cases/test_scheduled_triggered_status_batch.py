"""Bounded scheduled and triggered monitor batches retain independent read authorization.

:design: decision_481dc4e6292c, decision_b820faf42218
"""
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import scheduled_triggered_monitor as monitor
from management_portal import permission_system
from management_portal.hatg.catalog import declaration
from management_portal.hatg.contracts import InstructionContext, InstructionError
from management_portal.society_service import lineage_domain as domain
from management_portal.society_service import lineage_aggregate
from tests.cases.test_scheduled_triggered_monitor import LINEAGE, BROKEN, Reads, lineage
from tests.cases.permission_test_support import permissions  # noqa: F401


def test_discovery_uses_one_authorization_batch_and_one_definition_rpc(monkeypatch):
    """定时与自动触发面板对 125 个 Lineage 只做一次批量授权和一次定义读取 RPC

    :design-point:"""
    ids = [f'lineage_{index:012x}' for index in range(125)]
    authorization = Mock(return_value=[None] * len(ids))
    calls = []
    def definitions(requested, actor):
        calls.append((requested, actor))
        return {'definitions': {rid: {'lineage_id': rid, 'program': {}, 'nodes': []} for rid in requested}}
    listing = SimpleNamespace(
        list_lineages=lambda actor: {'lineages': [{'lineage_id': rid} for rid in ids]},
        get_lineage_monitor_definitions=definitions,
    )
    monkeypatch.setattr(monitor, 'get_society_service', lambda: SimpleNamespace(lineage=listing))
    monkeypatch.setattr(monitor.permission_system, 'authorize_reads', authorization)
    reads = Reads()
    result = monitor.get_scheduled_triggered_status('caller', read=reads)
    assert result['items'] == [] and result['lineage_errors'] == [] and reads.calls == []
    authorization.assert_called_once()
    assert authorization.call_args.args[1] == [{'lineage_id': rid} for rid in ids]
    assert calls == [(ids, 'caller')]


def test_denied_and_missing_definitions_do_not_hide_authorized_neighbor(monkeypatch):
    """无权读取或已删除的 Lineage 只记为局部错误，不影响有权读取的 Lineage 显示

    :design-point:"""
    denied = 'lineage_bbbbbbbbbbbb'
    listing = SimpleNamespace(list_lineages=lambda actor: {'lineages': [
        {'lineage_id': rid} for rid in (LINEAGE, denied, BROKEN)]})
    calls = []
    def definitions(ids, actor):
        calls.append((ids, actor))
        return {'definitions': {LINEAGE: lineage()}, 'lineage_errors': {BROKEN: 'deleted during discovery'}}
    listing.get_lineage_monitor_definitions = definitions
    monkeypatch.setattr(monitor, 'get_society_service', lambda: SimpleNamespace(lineage=listing))
    monkeypatch.setattr(monitor.permission_system, 'authorize_reads', lambda *args: [
        None, InstructionError('instruction.not_found', 'not readable'), None])
    result = monitor.get_scheduled_triggered_status('caller', read=Reads())
    assert calls == [([LINEAGE, BROKEN], 'caller')]
    assert [row['resident_id'] for row in result['items']] == [lineage()['nodes'][0]['id']]
    assert set(result['items'][0]['allows']) >= {'UpdateComponent', 'UpdateResidentSettings'}
    assert {row['lineage_id']: row['error'] for row in result['lineage_errors']} == {
        denied: 'not readable', BROKEN: 'deleted during discovery'}


def test_batch_reuses_read_only_snapshot_but_checks_each_definition(monkeypatch):
    """批量读取监控定义共用一个只读快照，但逐个检查权限并去重

    :design-point:"""
    calls = []; active = []
    @contextmanager
    def snapshot():
        calls.append('snapshot')
        active.append(True)
        try:
            yield
        finally:
            active.pop()
    def definition(rid, actor):
        assert active == [True]
        calls.append((rid, actor))
        if rid == BROKEN:
            raise PermissionError('current read denied')
        return {'lineage_id': rid}
    monkeypatch.setattr(lineage_aggregate, 'lineage_catalog_store', lambda: SimpleNamespace(
        state_store=SimpleNamespace(database=SimpleNamespace(snapshot=snapshot))))
    monkeypatch.setattr(domain, 'get_lineage_monitor_definition', definition)
    result = domain.get_lineage_monitor_definitions([LINEAGE, BROKEN, LINEAGE], 'caller')
    assert calls == ['snapshot', (LINEAGE, 'caller'), (BROKEN, 'caller')]
    assert result == {'definitions': {LINEAGE: {'lineage_id': LINEAGE}},
                      'lineage_errors': {BROKEN: 'current read denied'}}
    assert domain.get_lineage_monitor_definitions([], 'caller') == {'definitions': {}, 'lineage_errors': {}}
    assert not active and len(calls) == 3


def test_permission_batch_reads_the_instance_once_and_isolates_denied_target(permissions, monkeypatch):
    """批量读授权一次读取权限实例、去重判定，被拒目标单独报错

    :design-point:"""
    caller = 'resident_0123456789ab'
    permissions.deny(caller, BROKEN)
    store = permissions.implementation.store
    reads = []
    original = store.snapshot
    def snapshot(principal, resources, **options):
        reads.append(sorted(resources))
        return original(principal, resources, **options)
    monkeypatch.setattr(store, 'snapshot', snapshot)
    args = [{'lineage_id': LINEAGE}, {'lineage_id': BROKEN}, {'lineage_id': LINEAGE}]
    result = permission_system.authorize_reads(declaration('GetLineage'), args, InstructionContext(caller=caller, entry='web'))
    assert result[0] is result[2] is None
    assert result[1].code == 'instruction.not_found'
    assert result[1].details['target_id'] == BROKEN and result[1].details['reason'] == 'denied'
    assert reads == [sorted([LINEAGE, BROKEN])]


def test_unreadable_instance_refuses_batch_without_fabricating_exemption(permissions, monkeypatch):
    """权限实例不可读时批量读授权直接失败，不放行

    :design-point:"""
    def unavailable(*_args, **_kwargs):
        raise RuntimeError('instance down')
    monkeypatch.setattr(permissions.implementation.store, 'snapshot', unavailable)
    with pytest.raises(RuntimeError, match='instance down'):
        permission_system.authorize_reads(declaration('GetLineage'), [{'lineage_id': LINEAGE}],
                                          InstructionContext(caller='caller', entry='web'))


def test_batch_read_authorization_refuses_a_mutation(permissions):
    """批量读授权拒绝用于写指令

    :design-point:"""
    with pytest.raises(ValueError, match='read'):
        permission_system.authorize_reads(declaration('DeleteLineage'), [], InstructionContext(caller='one', entry='web'))


@pytest.mark.parametrize('single', [False, True])
def test_component_views_read_shared_fire_runtime_once_and_keep_fields(monkeypatch, single):
    """组件运行视图只读取一次事件源触发状态，并给出暂停、监听、次数耗尽等字段

    :design-point:"""
    from management_portal.society_service import component_runtime, lineage_program_runtime
    sources = {f'event_source_{index:03}': {
        'kind': 'event_source', 'title': f'Timer {index}', 'event_type': 'time.reached',
        'schedule_mode': 'recurring', 'max_fires': 10,
    } for index in range(75)}
    states = {rid: {'fire_count': index, 'paused': index == 0,
                    'next_scheduled_at': '2026-10-09T14:00:00Z'}
              for index, rid in enumerate(sources)}
    read_runtime = Mock(return_value={'event_sources': states})
    monkeypatch.setattr(lineage_aggregate, 'read_lineage', lambda rid, **kwargs: {'lineage_id': rid})
    monkeypatch.setattr(component_runtime, '_compiled_program', lambda lineage: {'event_sources': sources, 'loops': {}})
    monkeypatch.setattr(lineage_program_runtime, 'read_runtime', read_runtime)
    selected = 'event_source_000' if single else ''
    result = component_runtime.views(LINEAGE, 'caller', component_id=selected)
    read_runtime.assert_called_once_with(LINEAGE)
    assert len(result) == (1 if single else 75)
    first = result[0]
    assert (first['component_id'], first['kind'], first['title'], first['event_type']) == (
        'event_source_000', 'event_source', 'Timer 0', 'time.reached')
    assert first['paused'] is True and first['state'] == 'paused'
    assert first['occurrence_count'] == 0 and first['remaining_fires'] == 10
    assert first['next_scheduled_at'] == '2026-10-09T14:00:00Z'
    if not single:
        assert result[11]['exhausted'] is True and result[11]['remaining_fires'] == 0
        assert [row['component_id'] for row in result] == sorted(sources)
        # A Lineage without a configured User still fires; its Residents act on their own authority.
        assert result[1]['state'] == 'listening' and 'error' not in result[1]
        assert result[1]['occurrence_count'] == 1 and result[1]['remaining_fires'] == 9
        assert result[1]['next_scheduled_at'] == '2026-10-09T14:00:00Z'
        assert result[11]['state'] == 'exhausted' and 'error' not in result[11]


def test_loop_only_component_view_does_not_read_event_fire_runtime(monkeypatch):
    """只有 Loop 组件的 Lineage 读取组件视图时不读取事件源触发状态

    :design-point:"""
    from management_portal.society_service import component_runtime, lineage_program_runtime
    monkeypatch.setattr(lineage_aggregate, 'read_lineage', lambda rid, **kwargs: {'lineage_id': rid})
    monkeypatch.setattr(component_runtime, '_compiled_program', lambda lineage: {
        'event_sources': {}, 'loops': {'loop_001': {'title': 'Loop'}}})
    fire_runtime = Mock(side_effect=AssertionError('no Event Source read needed'))
    monkeypatch.setattr(lineage_program_runtime, 'event_source_runtime_view', fire_runtime)
    monkeypatch.setattr(component_runtime.lineage_loop_runtime, 'view', lambda *args: {'state': 'running'})
    assert component_runtime.views(LINEAGE, 'caller') == [{'title': 'Loop', 'state': 'running'}]
    fire_runtime.assert_not_called()
