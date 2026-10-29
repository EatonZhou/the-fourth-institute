"""验证无 Leader Lineage 使用正式整组删除路径，保留授权、并发锁、待运行任务、转移和目录一致性检查。

:module: lineage.hatg
:importance: P1
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_domain_host as host
from management_portal.society_service import resident_lineage_host as graph_host
from management_portal.society_service import resident_lifecycle_domain as lifecycle
from management_portal.society_service import resident_record_domain as record
from management_portal.society_service import resident_workspace_domain as workspace
from management_portal.society_service import resident_access
from management_portal.society_service import lineage_aggregate


@pytest.fixture
def members(tmp_path, monkeypatch):
    lineage = 'lineage_012345abcdef'
    folder = tmp_path / lineage
    folder.mkdir()
    rows = [{
        'resident_id': f'resident_00000000000{i}', 'lineage_id': lineage,
        'folder_schema': 'lineage-folder-v3',
        'lineage_storage': 'portal_lineage_store', 'workspace_path': str(folder),
        'owned_workspace_id': f'workspace_00000000000{i}',
        'owned_workspace': {
            'workspace_id': f'workspace_00000000000{i}',
            'path': str(folder / 'residents' / f'resident_00000000000{i}' / 'workspace'),
        },
        'workspace_bindings': [],
    } for i in range(1, 4)]
    for row in rows:
        (folder / 'residents' / row['resident_id']).mkdir(parents=True)
    state = SimpleNamespace(rows=rows, folder=folder, lineage=lineage, plans=[], locks=[],
                            confirmed=[],
                            pending=set(), transfer=False, authorized=True, graph_authorized=True)
    monkeypatch.setattr(host.resident_lineage, 'for_lineage', lambda _: rows)
    monkeypatch.setattr(host.resident_lineage, 'is_lineage_leader', lambda _: False)
    monkeypatch.setattr(record, 'find_resident', lambda rid: next((r for r in rows if r['resident_id'] == rid), None))
    monkeypatch.setattr(resident_access, 'can_manage', lambda *a: state.authorized)
    monkeypatch.setattr(resident_access, 'is_active_actor', lambda *a: state.authorized)
    monkeypatch.setattr(workspace, 'lineage_registry_records_locked', lambda _: rows)
    monkeypatch.setattr(lifecycle, 'resident_has_pending_work', lambda rid: rid in state.pending)
    monkeypatch.setattr(workspace, '_safe_lineage_workspace_for_delete', lambda _: folder)
    monkeypatch.setattr(record, 'resident_workspace_path', lambda row: row.get('mismatched_folder', folder))
    monkeypatch.setattr(record, 'resident_folder_path', lambda row: folder / 'residents' / row['resident_id'])
    monkeypatch.setattr(workspace, 'project_info_for_workspace', lambda *a: {})
    monkeypatch.setattr(lifecycle, 'resident_release_step', lambda row, actor: {'kind': 'release', 'resident_id': row['resident_id']})
    monkeypatch.setattr(workspace.resident_lineage_host_port, 'canonical_lineage_folder', lambda _: folder)

    def ensure_manager(definition, actor):
        assert definition['lineage_id'] == lineage and actor == 'sample_admin'
        if not state.graph_authorized:
            raise PermissionError('Lineage management denied')

    monkeypatch.setattr(lineage_aggregate, 'read_lineage', lambda _, **_kwargs: {'lineage_id': lineage})
    monkeypatch.setattr(lineage_aggregate, 'ensure_manager', ensure_manager)
    monkeypatch.setattr(graph_host.lineage_domain, '_lineage_dir', lambda _: folder)
    monkeypatch.setattr(workspace.resident_lineage_host_port, 'prepare_lineage_deletion', graph_host.ResidentLineageHost.prepare_lineage_deletion)

    def settled(_):
        if state.transfer:
            raise ValueError('transfer pending')

    monkeypatch.setattr(record, 'lifecycle_operations', lambda: SimpleNamespace(require_lineage_transfer_settled=settled))

    @contextmanager
    def lock(ids, **kwargs):
        state.locks.append(ids)
        yield

    monkeypatch.setattr(workspace.invariant_locks, 'lineage_scope', lock)
    monkeypatch.setattr(workspace.invariant_locks, 'resident_locks', lock)

    def commit(plans, **kwargs):
        state.plans.extend(plans)
        records = [record for plan in plans for record in plan['records']]
        return (
            [plan['result'] for plan in plans],
            records,
            {record['resident_id']: index for index, record in enumerate(records, start=1)},
        )

    monkeypatch.setattr(workspace, '_commit_resident_deletions', commit)
    monkeypatch.setattr(
        workspace,
        '_confirm_deleted_resident_resources',
        lambda records, revisions: state.confirmed.extend(record['resident_id'] for record in records),
    )
    return state


def test_leaderless_lineage_delete_includes_all_members_and_durable_effects(members):
    """无 Leader 团队整组删除及持久清理"""
    result = host.ResidentDomainHost.delete_registered_lineage(members.lineage, '', 'sample_admin')
    ids = [row['resident_id'] for row in members.rows]
    assert result['deleted_resident_ids'] == ids
    assert ids in members.locks
    assert result['workspace_original_path'] == str(members.folder)
    assert [step['kind'] for step in members.plans[0]['steps']] == [
        'lineage_tombstone', 'release', 'release', 'release', 'forget_deleted', 'lineage_folder_unbind', 'remove']
    assert [step['resident_id'] for step in members.plans[0]['steps'] if step['kind'] == 'release'] == ids
    assert members.plans[0]['steps'][-1]['workspace_ids'] == [row['owned_workspace_id'] for row in members.rows]
    assert members.confirmed == ids


@pytest.mark.parametrize('blocker', ['owner', 'lineage_owner', 'pending_member', 'transfer', 'folder'])
def test_leaderless_delete_checks_whole_aggregate_before_commit(members, blocker):
    """无 Leader 团队删除的整组授权与安全检查"""
    if blocker == 'owner':
        members.authorized = False
    elif blocker == 'lineage_owner':
        members.graph_authorized = False
    elif blocker == 'pending_member':
        members.pending.add(members.rows[-1]['resident_id'])
    elif blocker == 'transfer':
        members.transfer = True
    elif blocker == 'folder':
        members.rows[-1]['mismatched_folder'] = members.folder / 'wrong'
    with pytest.raises((ValueError, PermissionError)):
        host.ResidentDomainHost.delete_registered_lineage(members.lineage, '', 'sample_admin')
    assert members.plans == []
