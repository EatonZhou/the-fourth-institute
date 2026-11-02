"""交接副本通过正式 ISA 归位，权限、版本和提交结果遵循同一 owner。

:module: resident.fork-handoff
:design: decision_7ef392b5f60d, decision_4aa13bc21e8e
"""
from pathlib import Path
import pytest

from tests.cases.test_lineage_backend import lineage_store
from tests.cases.test_handoff_location_owner import existing
from tests.cases.isa_test_support import run, reset_kernels
from tests.cases.permission_test_support import enable, Fact
from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import lineage_handoffs, lineage_resident_workspaces


@pytest.fixture(autouse=True)
def kernels():
    reset_kernels()
    yield
    reset_kernels()


def arguments(lid, handoff, item, snapshot):
    return dict(lineage_id=lid, handoff_id=handoff['id'], item_id=item['item_id'],
                workspace_id=snapshot['workspace_id'], expected_workspace_version=snapshot['expected_version'])


@pytest.mark.parametrize('method', ['filesystem_copy', 'git_worktree'])
def test_formal_relocation_preserves_identity_content_binding_history_and_replays(lineage_store, tmp_path, method):
    lid, h, item, resident, source, snapshot = existing(lineage_store, tmp_path, method)
    old = Path(snapshot['expected_path']); (old/'user-edit').write_text('retained')
    call = arguments(lid, h, item, snapshot)
    result = run('RelocateWorkspaceHandoff', call, key='relocate')
    row = result.raise_for_error()['workspace']
    assert result.result['status'] == 'completed'
    assert row['workspace_id'] == row['owner_id'] == snapshot['workspace_id']
    assert not old.exists() and (Path(row['path'])/'user-edit').read_text() == 'retained'
    current = lineage_handoffs.get_handoff(lid, h['id'], 'sample_admin')['handoff']['items'][0]
    assert current['attempts'] == item['attempts']
    assert current['target']['path'] == current['result']['target_path'] == row['path']
    bindings = lineage_resident_workspaces.get_node_workspaces(lid, resident, 'sample_admin')['workspaces']
    assert any(b['workspace_id'] == row['workspace_id'] and b['path'] == row['path'] for b in bindings)
    again = run('RelocateWorkspaceHandoff', call, key='relocate')
    # HATG keeps stable result identities, not stale mutable Workspace fields.
    assert again.replayed and again.instruction_id == result.instruction_id
    assert again.result['operation_id'] == result.result['operation_id']
    assert again.result['workspace']['workspace_id'] == row['workspace_id']
    assert get_workspace_store().rows([row['workspace_id']])[row['workspace_id']]['updated_at'] == row['updated_at']


@pytest.mark.parametrize('grants', [('lineage',), ('workspace',), ('lineage', 'workspace')])
def test_both_contributor_grants_required_by_actual_permission_scheme(lineage_store, tmp_path, monkeypatch, grants):
    lid, h, item, resident, source, snapshot = existing(lineage_store, tmp_path, 'filesystem_copy')
    world = enable(monkeypatch)
    world.users('borrower', category='intern')
    wid = snapshot['workspace_id']
    world.place(Fact('lineage', lid), Fact('workspace', wid))
    for kind in grants:
        world.grant('borrower', lid if kind == 'lineage' else wid, '@contributor')
    result = run('RelocateWorkspaceHandoff', arguments(lid, h, item, snapshot), actor='borrower', key='authorized-move')
    if len(grants) == 2:
        assert result.raise_for_error()['workspace']['owner_id'] == wid
    else:
        assert result.status == 'failed' and result.error['code'] == 'instruction.forbidden'
        assert Path(snapshot['expected_path']).is_dir()
        assert get_workspace_store().rows([wid])[wid]['owner_id'] == lid


def test_stale_workspace_version_is_conflict_without_filesystem_effects(lineage_store, tmp_path):
    lid, h, item, resident, source, snapshot = existing(lineage_store, tmp_path, 'filesystem_copy')
    args = arguments(lid, h, item, snapshot); args['expected_workspace_version'] = 'stale'
    result = run('RelocateWorkspaceHandoff', args, key='stale')
    assert result.status == 'failed' and result.error['code'] == 'instruction.version_conflict'
    assert result.error['details']['current_version'] == snapshot['expected_version']
    assert Path(snapshot['expected_path']).exists()


def test_committed_dependency_failure_has_committed_pending_receipt(lineage_store, tmp_path, monkeypatch):
    from management_portal import directory_move_owner
    from management_portal.society_service import directory_relocation
    lid, h, item, resident, source, snapshot = existing(lineage_store, tmp_path, 'filesystem_copy')
    original = directory_move_owner.reconcile_dependencies
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', lambda *_: (_ for _ in ()).throw(OSError('retry Git dependency')))
    result = run('RelocateWorkspaceHandoff', arguments(lid, h, item, snapshot), key='pending')
    receipt = result.raise_for_error()
    assert receipt['status'] == 'pending' and receipt['operation_id'] == result.instruction_id
    assert 'retry Git dependency' in receipt['error']
    assert Path(receipt['workspace']['path']).is_dir() and not Path(snapshot['expected_path']).exists()
    monkeypatch.setattr(directory_move_owner, 'reconcile_dependencies', original)
    assert any(r['operation_id'] == receipt['operation_id'] and r['phase'] == 'completed'
               for r in directory_relocation.recover_pending_moves())
