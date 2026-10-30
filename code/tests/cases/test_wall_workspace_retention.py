"""WALL source ownership across real Resident/Workspace retirement.

:design: decision_06defc47e960, decision_8838269736d3
"""
from pathlib import Path

import pytest

from management_portal import isa_client, wall_residents
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service import (
    lineage_graph_edits,
    lineage_reads,
    lineage_resident_nodes,
    resident_catalog,
    resident_domain,
)
from management_portal.society_service import resident_protocol, resident_workspace_domain
from management_portal.workbench_projections import service
from tests.cases.test_resident_folder_creation_contract import _new_session_payload, future_creation_store
from tests.cases.test_workbench_projections import APP, resources, store


@pytest.mark.parametrize('lost_reply', ['none', 'before_unbind', 'after_unbind', 'after_delete', 'ancestor_binding'])
def test_wall_release_preserves_source_after_last_real_resident_deletion(
    store, resources, future_creation_store, tmp_path, monkeypatch, lost_reply,
):
    """WALL 释放真实成员并完成持久清理后，源码及嵌套文件保留；丢失回执可恢复。"""
    actor = 'sample_admin'
    first = resident_domain.register_resident(_new_session_payload('First worker'), actor)
    lid = first['lineage_id']
    second = lineage_resident_nodes.create_lineage_resident(lid, _new_session_payload('Second worker'), actor)['resident']
    survivor = lineage_resident_nodes.create_lineage_resident(lid, _new_session_payload('Other project'), actor)['resident']
    root = tmp_path / 'engineering'
    child = root / 'module'
    child.mkdir(parents=True)
    (root / 'SPEC.md').write_text('# Keep engineering source')
    (child / 'hello.py').write_bytes(b'print("hello")\n')
    for resident in (first, second):
        resident_domain.add_resident_reference_workspace(resident['resident_id'], str(root), actor)
    if lost_reply == 'ancestor_binding':
        # A broader extra binding must not let retirement collect the source
        # again through its parent after the exact project binding is removed.
        resident_domain.add_resident_reference_workspace(first['resident_id'], str(tmp_path), actor)
    resident_domain.add_resident_reference_workspace(first['resident_id'], str(child), actor)
    roles = {'wall-work:sample:first': first['resident_id'],
             'wall-discussion:sample:second': second['resident_id'],
             'wall-work:other:keep': survivor['resident_id']}
    app = store.get(APP, 'alice')
    store.update(APP, {'bindings': {'main': {'lineage_id': lid, 'residents': roles}}}, 'alice', app['revision'])
    runtime = service.ProjectionService(store)
    monkeypatch.setattr(wall_residents, 'get_service', lambda: runtime)
    monkeypatch.setattr(store, 'builtin', lambda *_: store.get(APP, 'alice'))
    monkeypatch.setattr(service, 'lineage_graph', lambda _actor, team: lineage_reads.get_lineage(team, actor))
    calls, interrupted = [], []

    def invoke(caller, name, arguments, **kwargs):
        assert caller == actor
        calls.append((name, dict(arguments), kwargs.get('key')))
        rid = arguments.get('resident_id')
        if name == 'GetResident':
            try:
                return {'resident': resident_protocol.get_resident(rid, actor)}
            except KeyError:
                raise InstructionError('instruction.not_found', 'Resident deleted')
        if name == 'GetLineage':
            return {'lineage': lineage_reads.get_lineage(arguments['lineage_id'], actor)}
        if name == 'ListResidentStates':
            return {'states': [{'state': {'work_state': 'idle'}}]}
        if name == 'UnbindWorkspace':
            if lost_reply == 'before_unbind' and not interrupted:
                interrupted.append(True)
                raise RuntimeError('lost reply')
            result = resident_workspace_domain.remove_resident_reference_workspace(
                rid, actor, workspace_id=arguments['workspace_id'])
            if lost_reply == 'after_unbind' and not interrupted:
                interrupted.append(True)
                raise RuntimeError('lost reply')
            return result
        if name == 'ApplyLineageChanges':
            assert all(op['kind'] == 'resident' for op in arguments['operations'])
            assert kwargs['version'] == lineage_reads.get_lineage(lid, actor)['revision']
            result = lineage_graph_edits.apply_lineage_changes(lid, {
                'resident_ids': [op['id'] for op in arguments['operations']]}, actor)
            if lost_reply == 'after_delete' and not interrupted:
                interrupted.append(True)
                raise RuntimeError('lost reply')
            return result
        if name == 'DeleteResident':
            assert resident_catalog.get_catalog().get_historical(rid)['deleted_at']
            for operation in resident_domain.ready_resident_lifecycle_operations():
                resident_domain.resume_resident_lifecycle_operation(operation)
            return {}
        pytest.fail(name)

    monkeypatch.setattr(service, 'isa', invoke)
    monkeypatch.setattr(isa_client, 'call', invoke)
    record = {'id': 'sample', 'root': str(root)}
    if lost_reply not in {'none', 'ancestor_binding'}:
        with pytest.raises(RuntimeError, match='lost reply'):
            wall_residents.release(record, actor, {}, blocks=None)
        assert store.get(APP, actor)['bindings']['main']['residents'] == roles
        assert (root / 'SPEC.md').read_text() == '# Keep engineering source'
    assert wall_residents.release(record, actor, {}, blocks=None) == {first['resident_id'], second['resident_id']}
    for operation in resident_domain.ready_resident_lifecycle_operations():
        resident_domain.resume_resident_lifecycle_operation(operation)
    assert (root / 'SPEC.md').read_text() == '# Keep engineering source'
    assert (child / 'hello.py').read_bytes() == b'print("hello")\n'
    for resident in (first, second):
        assert resident_catalog.get_catalog().get_historical(resident['resident_id'])['deleted_at']
        assert not Path(resident['owned_workspace']['path']).exists()
    assert resident_catalog.get_catalog().get(survivor['resident_id'])
    assert store.get(APP, actor)['bindings']['main']['residents'] == {'wall-work:other:keep': survivor['resident_id']}
    if lost_reply == 'before_unbind':
        attempts = [key for name, _, key in calls if name == 'UnbindWorkspace']
        assert attempts[0] == attempts[1]


def test_source_inside_owned_system_folder_rejects_release_before_any_mutation(monkeypatch, tmp_path):
    """工程在待删除的 Resident 系统目录内时明确拒绝，不能承诺解绑即可保留。"""
    from management_portal.lineage_workflows import WorkflowBindings
    rid, lid = 'resident_111111111111', 'lineage_111111111111'
    folder = tmp_path / 'residents' / rid
    def invoke(actor, name, arguments, **kwargs):
        assert name == 'GetResident', 'must not mutate a source inside the system folder'
        return {'resident': {'resident_id': rid, 'lineage_id': lid, 'workspaces': [
            {'workspace_id': 'workspace_111111111111', 'path': str(folder / 'workspace'), 'is_owned': True}]}}
    monkeypatch.setattr(isa_client, 'call', invoke)
    with pytest.raises(ValueError, match='overlaps'):
        WorkflowBindings().delete_resident(actor='sample_admin', resident_id=rid, lineage_id=lid,
                                           preserve_paths=(str(folder / 'source'),))
