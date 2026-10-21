"""Deleting identities collects unused Workspaces and preserves surviving users.

:module: resident.workspace-memory
:design: decision_8838269736d3
"""
from pathlib import Path

import pytest

from management_portal.resources.workspace_store import get_workspace_store
from management_portal.society_service import lineage_domain, resident_catalog
from management_portal.society_service import resident_workspace_domain as domain
from management_portal.society_service.resident_workspace_retirement import remove_retired_folder
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload, future_creation_store, resident_domain,
)


def drain():
    for operation in resident_domain.ready_resident_lifecycle_operations():
        resident_domain.resume_resident_lifecycle_operation(operation)


@pytest.mark.parametrize('whole_lineage', [False, True])
@pytest.mark.parametrize('location', ['owned', 'legacy', 'external'])
def test_shared_workspace_survives_member_and_lineage_deletion(
    future_creation_store, tmp_path, whole_lineage, location,
):
    """删除节点或整个 Lineage 均保留其他 Resident 使用的目录及绑定"""
    owner = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    reader = resident_domain.register_resident(_new_session_payload('Reader'), 'sample_admin')
    root = Path(owner['workspace_path'])
    if location == 'owned':
        folder = Path(owner['owned_workspace']['path'])
        wid = owner['owned_workspace_id']
    else:
        folder = root / 'node-workspaces/shared' if location == 'legacy' else tmp_path / 'external'
        folder.mkdir(parents=True)
        wid = 'workspace_eeeeeeeeeeee'
        get_workspace_store().ensure(wid, str(folder))
        domain.add_resident_reference_workspace(owner['resident_id'], str(folder), 'sample_admin', workspace_id=wid)
    evidence = folder / 'evidence.txt'
    evidence.write_text('shared evidence')
    domain.add_resident_reference_workspace(reader['resident_id'], str(folder), 'sample_admin', workspace_id=wid)
    own_folder = Path(owner['owned_workspace']['path']).parent
    junk = own_folder / 'obsolete.txt'
    junk.write_text('remove')

    if whole_lineage:
        lineage_domain.delete_lineage(owner['lineage_id'], 'sample_admin')
    else:
        lineage_domain.delete_lineage_graph_objects(
            owner['lineage_id'], {'resident_ids': [owner['resident_id']]}, 'sample_admin', include_lineage=False,
        )
    drain()
    assert resident_catalog.get_catalog().get_historical(owner['resident_id'])['deleted_at']
    assert get_workspace_store().rows([wid])[wid]['path'] == str(folder)
    assert evidence.read_text() == 'shared evidence'
    assert wid in {b['workspace_id'] for b in resident_catalog.get_catalog().get(reader['resident_id'])['workspace_bindings']}
    assert not junk.exists()
    if not whole_lineage:
        # The empty-Lineage path must preserve exactly the same shared files.
        lineage_domain.delete_lineage(owner['lineage_id'], 'sample_admin')
        assert evidence.read_text() == 'shared evidence'


def test_last_reference_collects_external_workspace_and_nested_shared_child_survives(
    future_creation_store, tmp_path,
):
    """外部目录的最后使用者删除后，清理独占内容并保留共享子工作区"""
    owner = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    reader = resident_domain.register_resident(_new_session_payload('Reader'), 'sample_admin')
    parent = tmp_path / 'external'
    shared = parent / 'nested/shared'
    shared.mkdir(parents=True)
    (parent / 'discard.txt').write_text('discard')
    (shared / 'keep.txt').write_text('keep')
    wid, child = 'workspace_cccccccccccc', 'workspace_dddddddddddd'
    get_workspace_store().ensure(wid, str(parent))
    get_workspace_store().ensure(child, str(shared))
    domain.add_resident_reference_workspace(owner['resident_id'], str(parent), 'sample_admin', workspace_id=wid)
    domain.add_resident_reference_workspace(reader['resident_id'], str(shared), 'sample_admin', workspace_id=child)
    lineage_domain.delete_lineage(owner['lineage_id'], 'sample_admin')
    drain()
    assert get_workspace_store().rows([wid]) == {}
    assert get_workspace_store().rows([child])
    assert not (parent / 'discard.txt').exists()
    assert (shared / 'keep.txt').read_text() == 'keep'
    lineage_domain.delete_lineage(reader['lineage_id'], 'sample_admin')
    drain()
    assert get_workspace_store().rows([child]) == {}
    assert not shared.exists()


def test_cleanup_rechecks_new_registration_and_does_not_follow_symlinks(tmp_path):
    """延迟清理保留新登记的目录，符号链接不会导致删除外部内容"""
    root = tmp_path / 'deleted'
    keep = root / 'nested/new'
    keep.mkdir(parents=True)
    (keep / 'keep.txt').write_text('keep')
    (root / 'discard.txt').write_text('discard')
    external = tmp_path / 'outside'
    external.mkdir()
    (external / 'evidence.txt').write_text('outside')
    (root / 'alias').symlink_to(external, target_is_directory=True)
    get_workspace_store().ensure('workspace_aaaaaaaaaaaa', str(keep))
    remove_retired_folder(root)
    remove_retired_folder(root)
    assert (keep / 'keep.txt').read_text() == 'keep'
    assert not (root / 'discard.txt').exists()
    assert not (root / 'alias').is_symlink()
    assert (external / 'evidence.txt').read_text() == 'outside'


def test_batch_deletion_ignores_references_from_other_deleted_members(future_creation_store):
    """整组删除按整批存活引用判断，待删除成员互相引用不会留下工作区"""
    first = resident_domain.register_resident(_new_session_payload('First'), 'sample_admin')
    second = lineage_domain.create_lineage_resident(
        first['lineage_id'], _new_session_payload('Second'), 'sample_admin',
    )['resident']
    for reader, owner in [(first, second), (second, first)]:
        domain.add_resident_reference_workspace(
            reader['resident_id'], owner['owned_workspace']['path'], 'sample_admin',
            workspace_id=owner['owned_workspace_id'],
        )
    lineage_domain.delete_lineage(first['lineage_id'], 'sample_admin')
    drain()
    ids = [r['owned_workspace_id'] for r in (first, second)]
    assert get_workspace_store().rows(ids) == {}
    assert not Path(first['workspace_path']).exists()


def test_external_workspace_cleanup_keeps_surviving_resident_system_files(future_creation_store):
    """外部工作区包含其他存活节点时，该节点的身份文件和运行目录一起保留"""
    owner = resident_domain.register_resident(_new_session_payload('Owner'), 'sample_admin')
    reader = resident_domain.register_resident(_new_session_payload('Reader'), 'sample_admin')
    project_workspace = reader['workspace_bindings'][0]
    domain.remove_resident_reference_workspace(
        reader['resident_id'], 'sample_admin', workspace_id=project_workspace['workspace_id'],
    )
    folder = Path(reader['owned_workspace']['path']).parent
    identity = folder / 'resident.md'
    identity.write_text('surviving identity')
    runtime = folder / 'runtime/keep.txt'
    runtime.parent.mkdir(exist_ok=True)
    runtime.write_text('surviving runtime')
    lineage_domain.delete_lineage(owner['lineage_id'], 'sample_admin')
    drain()
    assert identity.read_text() == 'surviving identity'
    assert runtime.read_text() == 'surviving runtime'
    assert resident_catalog.get_catalog().get(reader['resident_id'])
