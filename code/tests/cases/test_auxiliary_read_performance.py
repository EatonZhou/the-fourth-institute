"""Bounded auxiliary reads must keep authorization and send-time context.

:module: workflow.paper
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal import paper_backend, resident_workspace_adapter
from management_portal.society_service import lineage_read_cache


def test_paper_lookup_checks_only_selected_binding_and_live_membership(monkeypatch):
    """Paper 查找只读取选中绑定并校验当前成员

    忽略无关团队，重复绑定拒绝，移动后的成员与目标绑定必须实时验证。

    :design: decision_528bf9985705, decision_e37f240e186d
    :design-point:
    """
    from management_portal.workbench_projections import native, service
    from management_portal.workbench_projections.store import ProjectionError

    scope = 'surface-chat:CASE-A:paper:library'
    rid = 'resident_111111111111'
    app = {'bindings': {
        'main': {'lineage_id': 'lineage_111111111111', 'residents': {scope: rid}},
        'unrelated': {'lineage_id': 'lineage_222222222222', 'residents': {'reviewer': 'resident_222222222222'}},
    }}
    reads = []
    available = [True]
    def graph(actor, lid):
        reads.append((actor, lid))
        assert actor == 'CASE-A'
        if lid == 'lineage_222222222222':
            raise KeyError('Unrelated deleted team')
        return {'nodes': [{'id': rid}] if available[0] else []}
    def builtin(name, actor):
        assert (name, actor) == ('paper', 'CASE-A')
        return deepcopy(app)
    projection = service.ProjectionService(store=SimpleNamespace(builtin=builtin))
    monkeypatch.setattr(native, 'get_service', lambda: projection)
    monkeypatch.setattr(service, 'lineage_graph', graph)

    for _ in range(2):
        reads.clear()
        result = native.lookup('paper', actor='CASE-A', resident_scope=scope)
        assert result == {'resident_id': rid, 'lineage_id': 'lineage_111111111111'}
        assert reads == [('CASE-A', 'lineage_111111111111')]
    reads.clear()
    app['bindings']['main']['residents'].pop(scope)
    assert native.lookup('paper', actor='CASE-A', resident_scope=scope) == {}
    assert reads == []
    app['bindings']['main']['residents'][scope] = rid
    app['bindings']['unrelated']['residents'][scope] = rid
    with pytest.raises(ProjectionError) as error:
        native.lookup('paper', actor='CASE-A', resident_scope=scope)
    assert error.value.code == 'projection.role_ambiguous'
    assert reads == []
    app['bindings']['unrelated']['residents'].pop(scope)
    available[0] = False  # Deletion or a move must invalidate the next read.
    with pytest.raises(ProjectionError) as error:
        native.lookup('paper', actor='CASE-A', resident_scope=scope)
    assert error.value.code == 'projection.resident_unbound'
    # Settings still diagnose all bindings, including unrelated broken ones.
    detail = projection.initialize('paper', 'CASE-A')
    assert {issue['binding'] for issue in detail['binding_issues']} == {'main', 'unrelated'}


@pytest.mark.parametrize('failure', ['forbidden', 'deleted', 'outage'])
def test_paper_lookup_propagates_current_target_access_errors(monkeypatch, failure):
    """Paper 查找保留当前目标的权限和读取错误

    目标团队不可读或读取失败时传播真实错误，不误报为空身份。

    :design: decision_528bf9985705, decision_e37f240e186d
    :design-point:
    """
    from management_portal.hatg.contracts import InstructionError
    from management_portal.workbench_projections import native, service
    from management_portal.workbench_projections.store import ProjectionError

    app = {'bindings': {'main': {'lineage_id': 'lineage_111111111111',
                                'residents': {'chat': 'resident_111111111111'}}}}
    allowed = [True]
    def graph(actor, lid):
        assert (actor, lid) == ('CASE-A', 'lineage_111111111111')
        if allowed[0]:
            return {'nodes': [{'id': 'resident_111111111111'}]}
        if failure == 'forbidden':
            raise PermissionError('revoked')
        if failure == 'deleted':
            raise KeyError('deleted')
        raise InstructionError('instruction.unavailable', 'outage', http_status=503)
    projection = service.ProjectionService(store=SimpleNamespace(builtin=lambda *args: deepcopy(app)))
    monkeypatch.setattr(native, 'get_service', lambda: projection)
    monkeypatch.setattr(service, 'lineage_graph', graph)
    assert native.lookup('paper', actor='CASE-A', resident_scope='chat')['resident_id']
    allowed[0] = False
    if failure == 'outage':
        with pytest.raises(InstructionError) as error:
            native.lookup('paper', actor='CASE-A', resident_scope='chat')
        assert error.value.http_status == 503
    else:
        with pytest.raises(ProjectionError) as error:
            native.lookup('paper', actor='CASE-A', resident_scope='chat')
        assert error.value.code == ('projection.lineage_forbidden' if failure == 'forbidden' else 'projection.lineage_unbound')


def test_paper_lookup_preserves_published_entry_authorization_and_unbound_errors(monkeypatch):
    """Paper 发布入口保留授权及未绑定错误

    已发布工作台入口守住授权；缺失绑定按真实错误处理。

    :design: decision_528bf9985705, decision_e37f240e186d
    :design-point:
    """
    from management_portal.workbench_projections import native, service
    from management_portal.workbench_projections.store import ProjectionError

    allowed = [True]
    published = [{'bindings': {'main': {'lineage_id': 'lineage_111111111111', 'residents': {}}}}]
    calls = []
    def builtin(name, actor):
        calls.append((name, actor))
        if not allowed[0]:
            raise PermissionError('Workbench access revoked')
        return deepcopy(published[0])
    projection = service.ProjectionService(store=SimpleNamespace(builtin=builtin))
    monkeypatch.setattr(native, 'get_service', lambda: projection)
    assert native.lookup('paper', actor='CASE-A', resident_scope='chat') == {}
    allowed[0] = False
    with pytest.raises(PermissionError, match='revoked'):
        native.lookup('paper', actor='CASE-A', resident_scope='chat')
    assert calls == [('paper', 'CASE-A'), ('paper', 'CASE-A')]
    allowed[0] = True
    published[0] = None
    with pytest.raises(ProjectionError) as error:
        native.lookup('paper', actor='CASE-A', resident_scope='chat')
    assert error.value.code == 'projection.business_unbound'
    published[0] = {'bindings': {}}
    with pytest.raises(ProjectionError) as error:
        native.lookup('paper', actor='CASE-A', resident_scope='chat')
    assert error.value.code == 'projection.lineage_unbound'


def test_paper_session_read_skips_library_context_but_prepare_keeps_it(monkeypatch):
    """Paper 会话读取保持轻量，发送准备保留完整上下文

    会话读取只解析显示信息且不创建身份；发送准备仍生成真实论文上下文并确保身份。

    :design: decision_528bf9985705, decision_e37f240e186d
    :design-point:
    """
    calls = []
    monkeypatch.setattr(paper_backend, 'chat_workspace_display_context',
                        lambda **kw: calls.append(('display', kw)) or {
                            'title': 'Research Chat', 'subtitle': 'Selected paper',
                            'prompt_context': '',
                        })
    monkeypatch.setattr(paper_backend, 'chat_workspace_prompt_context',
                        lambda **kw: calls.append(('prompt', kw)) or {
                            'title': 'Research Chat', 'subtitle': 'Selected paper',
                            'prompt_context': 'Actual paper context for send',
                        })
    monkeypatch.setattr(resident_workspace_adapter.discussion_residents, 'resolve',
                        lambda **kw: calls.append(('identity', kw)) or {})
    read = resident_workspace_adapter.get_workspace(
        'CASE-A', 'paper', 'paper:4105', {'paper_id': '4105', 'query': 'Compare'},
    )
    assert [kind for kind, _ in calls] == ['display', 'identity']
    assert calls[-1][1]['create'] is False
    assert read['chat_workspace']['subtitle'] == 'Selected paper'
    assert read['binding_state'] == 'unbound'
    assert 'Actual paper context' not in read['initial_context']['context_text']
    calls.clear()
    prepared = resident_workspace_adapter.prepare_workspace('CASE-A', {
        'surface': 'paper', 'workspace_key': 'paper:4105',
        'context': {'paper_id': '4105', 'query': 'Compare'}, 'message': 'Compare',
    })
    assert [kind for kind, _ in calls] == ['prompt', 'identity']
    assert calls[0][1]['paper_id'] == '4105'
    assert calls[0][1]['query'] == 'Compare'
    assert calls[-1][1]['create'] is True
    assert 'Actual paper context for send' in prepared['initial_context']['context_text']


def test_paper_display_read_never_hydrates_resource_catalog(monkeypatch):
    """Paper 显示读取只查当前论文，不扫描全库

    读取指定论文或Library Chat显示信息时不加载全部论文目录。

    :design: decision_528bf9985705, decision_e37f240e186d
    :design-point:
    """
    def forbidden(*args, **kwargs):
        pytest.fail('Session GET must not load every paper')
    monkeypatch.setattr(paper_backend, 'resource_index_papers', forbidden)
    monkeypatch.setattr(paper_backend, 'PAPER_STORE', SimpleNamespace(
        paper_by_id=lambda paper_id: {'id': paper_id, 'title': 'Specific paper'},
    ))
    monkeypatch.setattr(resident_workspace_adapter.discussion_residents, 'resolve', lambda **kw: {})
    result = resident_workspace_adapter.get_workspace('CASE-A', 'paper', 'paper:3380', {'paper_id': '3380'})
    assert result['chat_workspace']['subtitle'] == 'Specific paper'
    result = resident_workspace_adapter.get_workspace('CASE-A', 'paper', 'library', {})
    assert result['chat_workspace']['subtitle'] == 'Library Chat'


def test_lineage_read_checks_one_fingerprint_and_invalidates_live_facts(monkeypatch):
    """Lineage 读取合并指纹检查并随状态和权限变化失效

    每次读取只加载一次规范Resident和状态快照；缓存随游标、成员事实、定义修订和权限变化失效，调用者修改不污染缓存。

    :module: lineage.service
    :design: decision_d918ebebc062
    :design-point:
    """
    resident_ids = [f'resident_{index:012x}' for index in range(108)]
    lineage = {'lineage_id': 'lineage_4d9182d0fa61', 'revision': 1,
               'nodes': [{'id': rid, 'title': 'Authored'} for rid in resident_ids],
               'program': {}}
    records = [{'resident_id': rid, 'family': {}} for rid in resident_ids]
    cursor = [1]
    counts = {'records': 0, 'states': 0, 'snapshot': 0, 'view': 0}
    allowed = [True]

    def get_many(ids):
        counts['records'] += 1
        assert list(ids) == resident_ids
        return deepcopy(records)

    def states(ids):
        counts['states'] += 1
        assert list(ids) == resident_ids
        return {rid: {'cursor': cursor[0]} for rid in ids}

    monkeypatch.setattr(lineage_read_cache.resident_catalog, 'get_catalog',
                        lambda: SimpleNamespace(get_many=get_many))
    monkeypatch.setattr(lineage_read_cache.resident_protocol, 'read_resident_state_snapshots', states)

    def snapshot(lineage_id, actor):
        counts['snapshot'] += 1
        if not allowed[0]:
            raise PermissionError('revoked')
        return deepcopy(lineage)

    def fingerprint(value):
        return lineage_read_cache.definition_fingerprint(
            value, seed_folder=lambda _: Path('/unused'),
            node_resident_id=lambda node: node['id'],
        )

    def verified(value, actor):
        counts['view'] += 1
        return {**deepcopy(value), 'observed_cursor': cursor[0],
                'actor': actor}

    ports = lineage_read_cache.ReadProjectionPorts(
        safe_actor=lambda actor: actor, snapshot_for_read=snapshot,
        cache_key=lambda lid, actor: ('isolated', lid, actor),
        fingerprint=fingerprint,
        cache=lineage_read_cache.LineageReadCache(), clone_json=deepcopy,
        project_titles=lambda value: value['nodes'][0].update(title='Canonical'),
        verified_view=verified,
    )
    def read(actor='CASE-A'):
        before = dict(counts)
        value = lineage_read_cache.current_lineage_view_for_read(lineage['lineage_id'], actor, ports=ports)[1]
        assert counts['records'] - before['records'] == 0
        assert counts['states'] - before['states'] == 1
        assert counts['snapshot'] - before['snapshot'] == 1
        return value

    first = read()
    assert first['nodes'][0]['title'] == 'Canonical'
    first['nodes'][0]['title'] = 'Caller mutation'
    assert read()['nodes'][0]['title'] == 'Canonical'
    assert counts['view'] == 1
    cursor[0] = 2
    assert read()['observed_cursor'] == 2
    lineage['revision'] += 1
    assert read()['revision'] == 2
    lineage['nodes'][0]['last_execution'] = {'status': 'failed'}
    assert read()['nodes'][0]['last_execution'] == {'status': 'failed'}
    assert read('sample_admin')['actor'] == 'sample_admin'
    assert counts['view'] == 5
    allowed[0] = False
    with pytest.raises(PermissionError, match='revoked'):
        read()
    assert counts['view'] == 5
