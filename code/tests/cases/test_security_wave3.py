from __future__ import annotations

import re
from pathlib import Path

import pytest

from management_portal.society_service import (
    lineage_access_policy,
    resident_domain_host,
    resident_workspace_domain,
    workspace_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from management_portal.resources.workspace_store import get_workspace_store


def _resident_owning(workspace: Path) -> None:
    """A live Resident whose owned Workspace is the given directory."""
    def seed(connection):
        workspace_id = get_workspace_store().ensure('workspace_5ec000000001', str(workspace), connection=connection)
        connection.execute('INSERT INTO residents(resident_id, owned_workspace_id) VALUES (%s, %s)',
                           ('resident_5ec000000001', workspace_id))
    get_lineage_data_store().database.write(seed)


def test_workspace_link_requires_actor_bound_workspace_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """Workspace 文件链接按调用者对路径所属 Workspace 的 ReadResource / UpdateWorkspaceFile 判定可读或可写，拒绝符号链接"""
    with subtests.test(contract='test_workspace_link_requires_actor_bound_workspace'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            workspace = tmp_path / 'workspace'
            workspace.mkdir()
            target = workspace / 'note.txt'
            target.write_text('private', encoding='utf-8')
            _resident_owning(workspace)
            # Instructions passing on the Workspace holding the file: the read one reads, the write one also writes.
            passes = {'owner': {'ReadResource', 'UpdateWorkspaceFile'}, 'viewer': {'ReadResource'}}
            monkeypatch.setattr('management_portal.permission_system.permitted', lambda actor, instruction, parameter, ids, **_options: [
                (item, instruction in passes.get(actor, ()) and item.startswith('workspace_')) for item in ids])
            with pytest.raises(PermissionError):
                workspace_domain.get_workspace_link_file(str(target), 'stranger')
            preview = workspace_domain.get_workspace_link_file(str(target), 'viewer')
            assert preview['content'] == 'private'
            assert preview['file_address'] == '' and preview['allows'] == {'UpdateWorkspaceFile': False}
            preview = workspace_domain.get_workspace_link_file(str(target), 'owner')
            assert preview['content'] == 'private'
            assert preview['file_address'] == 'workspace_5ec000000001/file/note.txt'
            # The save button asks UpdateWorkspaceFile, the instruction it runs.
            assert preview['allows'] == {'UpdateWorkspaceFile': True}
    with subtests.test(contract='test_shared_collaboration_file_is_open_to_authenticated_actors_without_resident_scan'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            shared_root = tmp_path / 'shared-skills'
            shared_root.mkdir()
            target = shared_root / 'rules.md'
            target.write_text('before', encoding='utf-8')
            monkeypatch.setattr(workspace_domain, 'SHARED_COLLABORATION_ROOTS', (shared_root,))
            monkeypatch.setattr(resident_domain_host.resident_lineage, 'all', lambda: (_ for _ in ()).throw(AssertionError('Resident scan is forbidden')))
            with pytest.raises(PermissionError, match='authenticated actor'):
                workspace_domain.get_workspace_link_file(str(target), '')
            preview = workspace_domain.get_workspace_link_file(str(target), 'portal-user')
            assert preview['content'] == 'before'
            # Saving needs a Workspace address; this root is in no registered Workspace.
            assert preview['file_address'] == '' and preview['allows'] == {'UpdateWorkspaceFile': False}
    with subtests.test(contract='test_workspace_link_rejects_symlink_identity_before_resolution'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            workspace = tmp_path / 'workspace'
            workspace.mkdir()
            target = workspace / 'target.txt'
            target.write_text('target', encoding='utf-8')
            link = workspace / 'link.txt'
            link.symlink_to(target)
            _resident_owning(workspace)
            monkeypatch.setattr(resident_workspace_domain, 'resolve_resident_identity', lambda resident_id, _actor: {'resident_id': resident_id})
            with pytest.raises(ValueError, match='symlink'):
                workspace_domain.resolve_workspace_link_file_path(str(link), actor='owner')


def test_an_absolute_path_belongs_to_the_deepest_workspace_or_shared_folder_holding_it(isolated_portal_case, monkeypatch):
    """绝对路径归属于包含它的最深一层已登记 Workspace；不在任何 Workspace 内时归属共享协作目录，个人目录除外

    权限按这个对象判定（decision_b820faf42218 §5.5）：Workspace 嵌套时取最深的一层，已删除的 Workspace 不算；
    个人 AMA 的目录本身也是一个对象，归属其所有者；个人根下既不在 Workspace 也不在 AMA 目录里的路径没有归属。
    """
    with isolated_portal_case() as case_path:
        def seed(connection):
            store = get_workspace_store()
            store.ensure('workspace_5ec000000002', str(case_path / 'outer'), connection=connection)
            store.ensure('workspace_5ec000000003', str(case_path / 'outer' / 'inner'), connection=connection)
            store.ensure('workspace_5ec000000004', str(case_path / 'outer' / 'inner' / 'gone'), connection=connection)
            connection.execute(f"UPDATE {store.table} SET deleted_at = now() WHERE workspace_id = 'workspace_5ec000000004'")
        get_lineage_data_store().database.write(seed)
        ama = case_path / 'personal' / 'lineage_5ec000000009'
        monkeypatch.setattr(lineage_access_policy, 'personal_folders', lambda: [(str(ama), 'lineage_5ec000000009', 'alice')])
        place = lineage_access_policy.path_resource
        assert place(str(case_path / 'outer' / 'a.md')) == (str(case_path / 'outer'), 'workspace_5ec000000002', '')
        assert place(str(case_path / 'outer' / 'inner' / 'gone' / 'b.md'))[1] == 'workspace_5ec000000003'
        assert place(str(ama / 'resident.md')) == (str(ama), 'lineage_5ec000000009', 'alice')
        shared, private = (case_path / 'skills', case_path / 'skills' / 'personal')
        assert place(str(shared / 'x' / 'SKILL.md'), shared_roots=(shared,), private_roots=(private,)) == (str(shared), str(shared), '')
        assert place(str(private / 'y.md'), shared_roots=(shared,), private_roots=(private,)) is None
        assert place(str(case_path / 'elsewhere.md')) is None


def test_portal_workflow_actions_are_immutable_shas() -> None:
    """GitHub 工作流引用的 action 都固定到完整的提交 SHA"""
    workflows = Path(".github/workflows")
    refs = re.findall(r"uses:\s*[^\s@]+@([^\s#]+)", "\n".join(
        path.read_text(encoding="utf-8") for path in workflows.glob("*.yml")
    ))
    assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)
