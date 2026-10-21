from __future__ import annotations
from management_portal.society_service import resident_catalog
from management_portal.society_service import resident_fork_domain

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_workspace_domain
from management_portal.society_service import changes
from management_portal.society_service import resident_lineage
from management_portal.society_service import resident_hatg_credentials
from management_portal.society_service import resource_client


class _ImmediateStateStore:
    def __init__(self) -> None:
        self.queued: list[tuple[str, dict]] = []

    @staticmethod
    def transaction(operation):
        return operation()

    def enqueue_resident_resource_projection(self, resident_id, record):
        self.queued.append((resident_id, dict(record)))


@pytest.mark.parametrize("external_resident_folder", [False, True])
def test_owned_workspace_alias_is_coalesced(tmp_path, external_resident_folder):
    """经符号链接别名指向自有 Workspace 的绑定被合并，不作为额外绑定"""
    resident_id = "resident_111111111111"
    lineage = tmp_path / "lineage"
    folder = lineage / "residents" / resident_id
    folder.parent.mkdir(parents=True)
    if external_resident_folder:
        original = tmp_path / "original" / "residents" / resident_id
        original.mkdir(parents=True)
        folder.symlink_to(original, target_is_directory=True)
    else:
        folder.mkdir()
    workspace = folder / "workspace"
    workspace.mkdir()
    alias = tmp_path / "lineage-alias"
    alias.symlink_to(lineage, target_is_directory=True)
    alias_path = alias / "residents" / resident_id / "workspace"
    record = {
        "resident_id": resident_id, "folder_schema": "lineage-folder-v3",
        "workspace_path": str(lineage), "owned_workspace_id": "workspace_111111111111",
        "owned_workspace": {"workspace_id": "workspace_111111111111", "path": str(workspace.resolve())},
        "workspace_bindings": [{"workspace_id": "workspace_111111111111", "path": str(alias_path), "purpose": ""}],
    }
    resident_record_domain._set_resident_owned_workspace_binding(record)
    assert record["workspace_bindings"] == []
    assert record["owned_workspace_id"] == "workspace_111111111111"
    assert resident_workspace_domain._resident_default_cwd_path(record) == str(workspace.resolve())
    assert alias_path.is_dir()


def test_move_preserves_owned_identity_and_explicit_extra_workspaces(tmp_path):
    """Resident 移到新 Lineage 后保留自有 Workspace ID 与额外绑定，路径更新到新位置"""
    resident_id = "resident_111111111111"
    lineage = tmp_path / "destination"
    owned_path = lineage / "residents" / resident_id / "workspace"
    previous_path = tmp_path / "source" / "residents" / resident_id / "workspace"
    extra = {"workspace_id": "workspace_222222222222", "path": str(tmp_path / "shared"), "purpose": "Task files"}
    record = {
        "resident_id": resident_id, "folder_schema": "lineage-folder-v3",
        "workspace_path": str(lineage), "owned_workspace_id": "workspace_111111111111",
        "owned_workspace": {"workspace_id": "workspace_111111111111", "path": str(previous_path)},
        "workspace_bindings": [extra],
    }
    resident_record_domain._set_resident_owned_workspace_binding(record)
    assert record["owned_workspace_id"] == "workspace_111111111111"
    assert record["owned_workspace"]["path"] == str(owned_path)
    assert record["workspace_bindings"] == [extra]
    normalized = json.dumps(record, sort_keys=True)
    resident_record_domain._set_resident_owned_workspace_binding(record)
    assert json.dumps(record, sort_keys=True) == normalized


def test_extra_workspace_never_selects_default_cwd(tmp_path):
    """额外绑定的 Workspace 不会成为默认工作目录，默认目录回到自有 Workspace"""
    resident_id = "resident_111111111111"
    lineage = tmp_path / "lineage"
    shared = {"workspace_id": "workspace_222222222222", "path": str(tmp_path / "shared"), "purpose": ""}
    record = {"resident_id": resident_id, "folder_schema": "lineage-folder-v3",
              "workspace_path": str(lineage), "default_cwd": shared["path"], "workspace_bindings": [shared]}
    resident_record_domain._set_resident_owned_workspace_binding(record)
    expected = lineage / "residents" / resident_id / "workspace"
    assert record["default_cwd"] == str(expected)
    assert record["owned_workspace"]["path"] == str(expected)
    assert record["workspace_bindings"] == [shared]


def test_peer_resident_rejects_conflicting_session_identity_before_write_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """同伴 Resident 带持久会话字段时在写入前被拒绝，只有身份变化才排队资源投影"""
    with subtests.test(contract='test_peer_resident_rejects_conflicting_session_identity_before_write'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            resident_id = 'resident_111111111111'
            created_resident_id = 'resident_222222222222'
            lineage_id = 'lineage_111111111111'
            record = {'resident_id': resident_id, 'lineage_id': lineage_id, 'lineage_membership': 'formal', 'folder_schema': 'lineage-folder-v3', 'workspace_path': str(tmp_path / 'lineage')}
            registry = {'residents': [{'resident_id': created_resident_id, 'lineage_id': lineage_id, 'lineage_membership': 'formal', 'backend': 'codex', 'codex_thread_id': '019f9f39-52a0-75d3-adb2-86c2b96a5352', 'backend_session_id': '019f9f39-52a0-75d3-adb2-86c2b96a5353'}]}
            monkeypatch.setattr(resident_lineage, 'snapshot', lambda _record: {'residents': []})
            with pytest.raises(ValueError, match='persisted Agent session fields'):
                resident_fork_domain._sync_resident_files(record, registry["residents"][0])
            with pytest.raises(KeyError):
                resident_catalog.get_catalog().get(created_resident_id)
    with subtests.test(contract='test_runtime_only_resident_save_does_not_sync_resource_identity'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            resident_id = 'resident_111111111111'
            previous = {'resident_id': resident_id, 'lineage_id': 'lineage_111111111111', 'title': 'Stable identity', 'updated_at': '2026-08-04T00:00:00Z', 'session_state': 'idle', 'principal_hierarchy': {}}
            current = {**previous, 'updated_at': '2026-08-04T00:01:00Z', 'session_state': 'running'}
            upserts: list[dict] = []
            state_store = _ImmediateStateStore()
            monkeypatch.setattr(resident_record_domain, '_validated_resident_record_payload', lambda record: dict(record))
            monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda _resident_id: dict(previous), upsert=lambda record, *, commit_configuration=False, commit_intake=False: upserts.append(record), state_store=state_store))
            monkeypatch.setattr(changes, 'resident_changed', lambda *_args, **_kwargs: None)
            resident_workspace_domain._save_resident_record(current)
            assert state_store.queued == []
            assert upserts == [current]
    with subtests.test(contract='test_identity_change_enqueues_resource_projection_once'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            resident_id = 'resident_111111111111'
            previous = {'resident_id': resident_id, 'lineage_id': 'lineage_111111111111', 'title': 'Before', 'principal_hierarchy': {}}
            current = {**previous, 'title': 'After'}
            state_store = _ImmediateStateStore()
            credential_calls: list[tuple] = []
            monkeypatch.setattr(resident_hatg_credentials, 'ensure_resident_credential', lambda *args, **kwargs: credential_calls.append((args, kwargs)))
            monkeypatch.setattr(resident_record_domain, '_validated_resident_record_payload', lambda record: dict(record))
            monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda _resident_id: dict(previous), upsert=lambda _record, *, commit_configuration=False, commit_intake=False: None, state_store=state_store))
            monkeypatch.setattr(changes, 'resident_changed', lambda *_args, **_kwargs: None)
            resident_workspace_domain._save_resident_record(current)
            assert state_store.queued == [(resident_id, current)]
            assert credential_calls == []


def test_save_and_sync_resident_identity_updates_both_stores_contracts(monkeypatch, subtests, isolated_portal_case):
    """保存 Resident 时先同步资源身份再写注册表，同一请求内查找不重复读取访问元数据

    :design: decision_0f57656a2acd
    :design-point:
    """
    with subtests.test(contract='test_save_and_sync_resident_identity_updates_both_stores'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            calls: list[tuple[str, str]] = []
            record = {'resident_id': 'resident_111111111111'}
            monkeypatch.setattr(resident_record_domain, '_apply_resident_resource_identity', lambda value: calls.append(('identity', value['resident_id'])))
            monkeypatch.setattr(resident_workspace_domain, '_save_resident_record', lambda value: calls.append(('registry', value['resident_id'])))
            resident_workspace_domain._save_resident_record_with_resource_identity(record)
            assert calls == [('identity', 'resident_111111111111'), ('registry', 'resident_111111111111')]
    with subtests.test(contract='test_find_resident_reuses_lineage_access_within_one_request'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            resident_id = 'resident_111111111111'
            record = {'resident_id': resident_id, 'lineage_id': 'lineage_222222222222'}
            calls: list[str] = []
            monkeypatch.setattr(resident_catalog, 'get_catalog', lambda: SimpleNamespace(get=lambda _resident_id: dict(record), root=Path('/tmp')))
            monkeypatch.setattr(resident_lineage.lineage_catalog, 'get_catalog', lambda _root: SimpleNamespace(get_access_metadata=lambda lineage_id: calls.append(lineage_id) or {'lineage_id': lineage_id}))
            with resident_lineage.access_snapshot_scope():
                first = resident_workspace_domain._find_resident(resident_id)
                second = resident_workspace_domain._find_resident(resident_id)
                assert 'lineage_owner_ids' not in first
                assert 'lineage_owner_ids' not in second
                assert 'owner_ids' not in first and 'owner_ids' not in second
                assert 'resource_capabilities' not in first
                assert 'resource_capabilities' not in second
            assert calls == []
