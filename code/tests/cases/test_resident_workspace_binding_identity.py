from management_portal.society_service.resident_lifecycle import ResidentLifecycleOperations
from pathlib import Path

import pytest

from management_portal.society_service import resident_domain
from management_portal.society_service import resident_lifecycle_domain
from management_portal.society_service import resident_record_domain
from tests.cases.test_resident_folder_creation_contract import (
    _new_session_payload,
    future_creation_store as resident_creation_store,
)


from management_portal.society_service import resident_workspace_domain


@pytest.fixture
def future_creation_store(resident_creation_store, monkeypatch):
    # Storage and the Lineage host resolve the same canonical layout in v30.
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(resident_creation_store / "lineages"))
    return resident_creation_store

def test_update_workspace_inputs_persists_replacement_and_removal(future_creation_store, tmp_path):
    """更新 workspace_paths 会替换或清空引用 Workspace，保留的绑定和自有 Workspace 不变"""
    paths = [tmp_path / name for name in ("one", "two", "three")]
    for path in paths:
        path.mkdir()
    payload = _new_session_payload("Workspace update")
    payload["workspace_paths"] = list(map(str, paths[:2]))
    record = resident_domain.register_resident(payload, "sample_admin")
    rid = record["resident_id"]
    owned = record["owned_workspace"]
    retained = next(b for b in record["workspace_bindings"] if b["path"] == str(paths[1]))
    resident_workspace_domain._update_resident_record(rid, {"workspace_paths": list(map(str, paths[1:]))}, "sample_admin")
    current = resident_workspace_domain._find_resident(rid)
    assert current["owned_workspace"] == owned
    assert set(current["workspace_paths"]) == set(map(str, paths[1:]))
    assert next(b for b in current["workspace_bindings"] if b["path"] == str(paths[1])) == retained
    resident_workspace_domain._update_resident_record(rid, {"workspace_paths": []}, "sample_admin")
    current = resident_workspace_domain._find_resident(rid)
    assert current["workspace_bindings"] == []
    assert current["owned_workspace"] == owned


def test_catalog_rejects_symlinked_default_workspace_path(
    future_creation_store: Path,
    tmp_path: Path,
) -> None:
    """自有 Workspace 路径经符号链接指向时被视为无效，保存被拒绝"""
    record = resident_domain.register_resident(
        _new_session_payload("Canonical owned workspace validation"), "sample_admin"
    )
    resident_id = record["resident_id"]
    lineage_root = Path(record["workspace_path"])
    alias_root = tmp_path / "lineage-alias"
    alias_root.symlink_to(lineage_root, target_is_directory=True)
    alias_workspace = alias_root / "residents" / resident_id / "workspace"
    damaged = resident_workspace_domain._find_resident(resident_id)
    damaged["owned_workspace"]["path"] = str(alias_workspace)
    damaged["default_cwd"] = str(alias_workspace)
    assert resident_workspace_domain._resident_default_cwd_path(damaged) == ""
    with pytest.raises(ValueError, match="Workspace"):
        resident_workspace_domain._save_resident_record(damaged)


def test_reference_workspace_ids_remain_unique_after_removal(
    future_creation_store: Path,
    tmp_path: Path,
) -> None:
    """移除引用 Workspace 后再添加，新绑定的 ID 不与旧 ID 重复，其余绑定的 ID 不变"""
    record = resident_domain.register_resident(
        _new_session_payload("Workspace root identity"), "sample_admin"
    )
    resident_id = record["resident_id"]
    directories = [tmp_path / name for name in ("first", "second", "third")]
    for directory in directories:
        directory.mkdir()
    for directory in directories[:2]:
        resident_domain.add_resident_reference_workspace(
            resident_id, str(directory), "sample_admin"
        )
    before = resident_workspace_domain._find_resident(resident_id)
    first = next(
        binding for binding in before["workspace_bindings"]
        if binding["path"] == str(directories[0])
    )
    second = next(
        binding for binding in before["workspace_bindings"]
        if binding["path"] == str(directories[1])
    )
    resident_domain.remove_resident_reference_workspace(
        resident_id, "sample_admin", workspace_path=str(directories[0])
    )
    resident_domain.add_resident_reference_workspace(
        resident_id, str(directories[2]), "sample_admin"
    )
    after = resident_workspace_domain._find_resident(resident_id)
    bindings = after["workspace_bindings"]
    root_ids = [binding["workspace_id"] for binding in bindings]
    assert len(root_ids) == len(set(root_ids))
    third = next(binding for binding in bindings if binding["path"] == str(directories[2]))
    assert third["workspace_id"] != first["workspace_id"]
    retained_second = next(binding for binding in bindings if binding["path"] == str(directories[1]))
    assert retained_second["workspace_id"] == second["workspace_id"]
    assert second["workspace_id"] != third["workspace_id"]
    assert next(root for root in resident_workspace_domain._resident_workspace_roots(after)
                if root["workspace_id"] == third["workspace_id"])["path"] == str(directories[2])
    removed = resident_domain.remove_resident_reference_workspace(
        resident_id, "sample_admin", workspace_id=third["workspace_id"]
    )
    assert removed["path"] == str(directories[2])
    final = resident_workspace_domain._find_resident(resident_id)
    assert any(binding["workspace_id"] == second["workspace_id"] and binding["path"] == str(directories[1]) for binding in final["workspace_bindings"])
    assert final["default_cwd"] == record["default_cwd"]
    assert all(directory.is_dir() for directory in directories)


@pytest.fixture()
def reference_workspace_mutation(future_creation_store: Path, tmp_path: Path, request):
    record = resident_domain.register_resident(
        _new_session_payload("Workspace document commit"), "sample_admin"
    )
    resident_id = record["resident_id"]
    directory = tmp_path / "reference"
    directory.mkdir()
    if request.param == "remove":
        resident_domain.add_resident_reference_workspace(
            resident_id, str(directory), "sample_admin"
        )

    def mutate():
        if request.param == "add":
            return resident_domain.add_resident_reference_workspace(
                resident_id, str(directory), "sample_admin"
            )
        return resident_domain.remove_resident_reference_workspace(
            resident_id, "sample_admin", workspace_path=str(directory)
        )

    document = Path(resident_record_domain._resident_folder_path(record)) / "workspaces.md"
    return resident_id, document, mutate


@pytest.mark.parametrize("reference_workspace_mutation", ["add", "remove"], indirect=True)
def test_workspace_document_does_not_change_when_canonical_save_fails(
    reference_workspace_mutation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """添加或移除引用 Workspace 时记录保存失败，workspaces.md 与记录都不变"""
    resident_id, document, mutate = reference_workspace_mutation
    before_record = resident_workspace_domain._find_resident(resident_id)
    before_document = document.read_bytes()

    def fail_save(record):
        raise RuntimeError("injected canonical save failure")

    monkeypatch.setattr(resident_workspace_domain, "_save_resident_record", fail_save)
    with pytest.raises(RuntimeError, match="injected canonical save failure"):
        mutate()
    assert document.read_bytes() == before_document
    assert resident_workspace_domain._find_resident(resident_id) == before_record
    assert not resident_lifecycle_domain._lifecycle_operations().pending(resident_id)


@pytest.mark.parametrize("reference_workspace_mutation", ["add", "remove"], indirect=True)
def test_workspace_document_failure_recovers_from_committed_bindings(
    reference_workspace_mutation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """workspaces.md 写入失败时绑定照常提交，之后由生命周期操作按最新绑定补写"""
    resident_id, document, mutate = reference_workspace_mutation
    before_record = resident_workspace_domain._find_resident(resident_id)
    before_document = document.read_bytes()
    refresh = resident_workspace_domain._refresh_resident_workspace_document

    def fail_projection(record):
        assert resident_workspace_domain._find_resident(resident_id)["workspace_bindings"] == record["workspace_bindings"]
        raise OSError("injected document failure")

    with monkeypatch.context() as patch:
        patch.setattr(resident_workspace_domain, "_refresh_resident_workspace_document", fail_projection)
        result = mutate()
    assert result["workspace_document_pending"] is True
    assert document.read_bytes() == before_document
    current = resident_workspace_domain._find_resident(resident_id)
    assert current["workspace_bindings"] != before_record["workspace_bindings"]
    operations = resident_lifecycle_domain._lifecycle_operations()
    assert operations.pending(resident_id)
    current["workspace_bindings"][0]["purpose"] = "Recovery reads the latest binding"
    resident_workspace_domain._save_resident_record(current)
    operations.store.database.write(lambda connection: connection.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s',
        (result["operation_id"],),
    ))
    refreshed = []

    def observe_projection(record):
        refreshed.append(record["workspace_bindings"])
        refresh(record)

    monkeypatch.setattr(resident_workspace_domain, "_refresh_resident_workspace_document", observe_projection)
    assert operations.run(result["operation_id"], resident_lifecycle_domain._apply_resident_lifecycle_step) == resident_id
    assert refreshed == [current["workspace_bindings"]]
    assert not operations.pending(resident_id)
    assert document.read_bytes() != before_document


@pytest.mark.parametrize("reference_workspace_mutation", ["add", "remove"], indirect=True)
def test_workspace_binding_and_projection_request_commit_together(
    reference_workspace_mutation, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace 绑定变更与文档刷新任务一起提交，任务登记失败时绑定也不变"""
    resident_id, document, mutate = reference_workspace_mutation
    before_record = resident_workspace_domain._find_resident(resident_id)
    before_document = document.read_bytes()

    def fail_enqueue(self, target_id, steps):
        raise RuntimeError("injected projection enqueue failure")

    monkeypatch.setattr(ResidentLifecycleOperations, "enqueue", fail_enqueue)
    with pytest.raises(RuntimeError, match="injected projection enqueue failure"):
        mutate()
    assert resident_workspace_domain._find_resident(resident_id) == before_record
    assert document.read_bytes() == before_document


def test_deletion_preserves_workspace_still_bound_by_another_resident(future_creation_store, tmp_path):
    """删除 Resident 后其他 Resident 仍绑定的 Workspace 保留，最后一个使用者删除后回收"""
    from management_portal.resources.workspace_store import get_workspace_store
    from management_portal.society_service import resident_catalog
    shared=tmp_path/'shared-deletion-case'
    shared.mkdir()
    (shared/'keep.txt').write_text('retain')
    a=resident_domain.register_resident(_new_session_payload('Owner A'), 'sample_admin')
    b=resident_domain.register_resident(_new_session_payload('User B'), 'sample_admin')
    for record in (a,b):
        resident_domain.add_resident_reference_workspace(record['resident_id'],str(shared),'sample_admin')
    resident_domain.add_resident_reference_workspace(b['resident_id'],a['default_cwd'],'sample_admin',workspace_id=a['owned_workspace_id'])
    resident_domain.delete_resident_lineage(a['resident_id'],'sample_admin')
    store=get_workspace_store()
    assert not store.rows([a['owned_workspace_id']],include_deleted=True)[a['owned_workspace_id']]['deleted_at']
    current=resident_workspace_domain._find_resident(b['resident_id'])
    assert a['owned_workspace_id'] in {v['workspace_id'] for v in current['workspace_bindings']}
    assert Path(a['default_cwd']).is_dir()
    assert str(shared) in current['workspace_paths']
    assert (shared/'keep.txt').read_text()=='retain'
    assert resident_catalog.get_catalog().get(a['resident_id'],include_deleted=True)['owned_workspace_id']==a['owned_workspace_id']

