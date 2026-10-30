""":importance: P2"""
from __future__ import annotations
from management_portal.society_service import resident_backend
from management_portal.society_service import resident_catalog

from tests.cases.society_service_test_support import install_resource_boundary

from pathlib import Path

import pytest

from management_portal.society_service import (
    changes,
    invariant_locks,
    lineage_graph_edits,
    lineage_lifecycle,
    lineage_reads,
    lineage_state,
    resident_conversation_stream,
    resident_domain,
    resident_domain_common,
    resident_lifecycle_steps_domain,
    resident_lineage,
    resident_record_domain,
)
from management_portal.society_service import resident_workspace_domain


LINEAGE_ID = "lineage_111111111111"
ROOT_ID = "resident_111111111111"
PEER_ID = "resident_222222222222"


from management_portal.society_service import resident_lifecycle_domain, resident_runtime_domain, resident_session_backend_domain
from management_portal.society_service import lineage_aggregate

@pytest.fixture()
def deletion_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, dict[str, dict]]:
    data_root = tmp_path / "portal_lineages"
    monkeypatch.setattr(lineage_aggregate, "DATA_ROOT", data_root)
    monkeypatch.setattr(lineage_state, "LINEAGE_FOLDER_ROOT", data_root / "lineages")
    monkeypatch.setenv("PORTAL_LINEAGE_FOLDER_ROOT", str(data_root / "lineages"))
    resource_records = install_resource_boundary(monkeypatch)
    from management_portal.society_service import resident_workspace_domain
    from management_portal.society_service.domain_ports import execution_port
    # Deleted Residents' quota overrides are forgotten in the Execution store.
    monkeypatch.setattr(execution_port, "forget_binding_quota_overrides", lambda binding_ids: {"removed": 0})
    monkeypatch.setattr(resident_domain_common, "STAGING_ROOT", tmp_path)
    monkeypatch.setattr(
        resident_workspace_domain,
        "project_info_for_workspace",
        lambda _record, _workspace: {},
    )
    return data_root, resource_records


def _resident_record(resident_id: str, folder: Path) -> dict:
    workspace_id = f"workspace_{resident_id.removeprefix('resident_')}"
    workspace_path = folder / "residents" / resident_id / "workspace"
    return {
        "resident_id": resident_id,
        "backend": "codex",
        "registration_state": "ready",
        "lineage_id": LINEAGE_ID,
        "folder_schema": "lineage-folder-v3",
        "workspace_path": str(folder),
        "owned_workspace_id": workspace_id,
        "owned_workspace": {"workspace_id": workspace_id, "path": str(workspace_path)},
        "workspace_bindings": [],
        "title": resident_id,
    }


def _write_resident_facts(records: list[dict]) -> None:
    for record in records:
        resident_record_domain.save_resident_record(record)
    lineage_graph_edits.update_lineage(
        LINEAGE_ID,
        {
            **lineage_aggregate.read_lineage(LINEAGE_ID, actor="sample_admin"),
            "leader_resident_id": ROOT_ID,
        },
        "sample_admin",
    )


def _write_lineage(folder: Path) -> None:
    assert folder == lineage_state._lineage_dir(LINEAGE_ID)
    with lineage_aggregate.lineage_scope(LINEAGE_ID):
        lineage_lifecycle._create_lineage_in_scope(
            {
                "title": "统一删除",
                # Membership is published by _write_resident_facts; graph attributes
                # must not introduce a second, temporarily foreign member list.
                "nodes": [],
                "edges": [],
            },
            "sample_admin",
            LINEAGE_ID,
        )


def test_resident_backend_release_is_recovered_outside_state_locks(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 后端会话释放在状态锁之外执行，并删除会话绑定"""
    record = {
        "resident_id": PEER_ID,
        "lineage_id": LINEAGE_ID,
        "kind": "other",
        "provider": "codex",
    }
    submitted: list[dict] = []
    monkeypatch.setattr(
        resident_backend,
        "execution_provider",
        lambda _record: "codex",
    )
    monkeypatch.setattr(
        resident_backend,
        "execution_binding_id",
        lambda _record: f"resident:{PEER_ID}",
    )
    binding = {"backend_route_id": "zero", "provider_session_ref": "release-fixture"}

    def session_record(_record, *, expected_binding=None):
        assert expected_binding is None or expected_binding == binding
        return dict(binding)

    monkeypatch.setattr(resident_backend, "session_record", session_record)
    monkeypatch.setattr(
        resident_runtime_domain,
        "resident_working_directory",
        lambda _record: Path("/tmp/resident-release-regression"),
    )
    def release_session(**kwargs):
        assert not invariant_locks.lock_held()
        submitted.append(dict(kwargs))
        return {}

    monkeypatch.setattr(resident_session_backend_domain, "run_backend_session_operation", release_session)
    deleted = []
    monkeypatch.setattr(
        resident_backend,
        "delete_session_identity",
        lambda record, *, expected_provider_session_ref: deleted.append((record["resident_id"], expected_provider_session_ref)),
    )

    with invariant_locks.lineage_scope(LINEAGE_ID):
        operation_id = resident_record_domain.lifecycle_operations().enqueue(
            PEER_ID, [resident_lifecycle_domain.resident_release_step(record, "sample_admin")],
        )
        assert submitted == []

    assert resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert len(submitted) == 1
    assert submitted[0]["operation"] == "release"
    assert submitted[0]["operation_id"] == f"{operation_id}:0"
    assert submitted[0]["runtime_options"]["delete_binding_on_release"] is True
    assert submitted[0]["backend_route_id"] == binding["backend_route_id"]
    assert deleted == [(PEER_ID, binding["provider_session_ref"])]


def test_lineage_page_delete_uses_resident_aggregate_delete(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除 Lineage 时逐个删除其 Resident 并保留历史记录，Workspace 一并标记删除"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    records = [
        _resident_record(ROOT_ID, folder),
        _resident_record(PEER_ID, folder),
    ]
    _write_resident_facts(records)

    from management_portal.execution.agent_data_store import get_agent_data_store
    agents = {record["resident_id"]: get_agent_data_store().agent(record["resident_id"]) for record in records}

    def reject_global_scan() -> list[dict]:
        pytest.fail("Deleting one Lineage must not enumerate other Residents")

    with monkeypatch.context() as scoped_patch:
        scoped_patch.setattr(resident_lineage, "all", reject_global_scan)
        with changes.collect_changes() as recorded_changes:
            result = lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")

    assert resident_lineage.all() == []
    assert result["deleted_resident_ids"] == [ROOT_ID, PEER_ID]
    assert folder.exists(), "Filesystem cleanup follows the identity commit"
    for operation_id in resident_domain.ready_resident_lifecycle_operations():
        assert resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert not folder.exists()
    assert "recoverable_path" not in result
    for resident_id, agent in agents.items():
        historical = resident_catalog.get_catalog().get_historical(resident_id)
        assert historical["deleted_at"] and historical["deleted_by"] == "sample_admin"
        assert historical["lineage_id"] == LINEAGE_ID
        assert "workspace_path" not in historical
        assert get_agent_data_store().agent(resident_id) == agent
        with pytest.raises(KeyError):
            resident_catalog.get_catalog().get(resident_id)
        assert resident_workspace_domain.retry_deleted_resident(resident_id, "sample_admin")["deleted_at"] == historical["deleted_at"]
    assert lineage_reads.list_lineages("sample_admin")["lineages"] == []
    assert resource_records[LINEAGE_ID]["status"] == "deleted"
    state_store = resident_catalog.get_catalog().state_store
    assert not state_store.resident_resource_projection_pending(ROOT_ID)
    assert not state_store.resident_resource_projection_pending(PEER_ID)
    # Workspace lifecycle lives in the Workspace store.
    from management_portal.resources.workspace_store import get_workspace_store
    workspaces = get_workspace_store().descriptors(
        ["workspace_111111111111", "workspace_222222222222"],
    )
    assert {workspace_id: item["status"] for workspace_id, item in workspaces.items()} == {
        "workspace_111111111111": "deleted",
        "workspace_222222222222": "deleted",
    }
    assert {
        (domain, resource_id, scope)
        for domain, resource_id, scope, _payload in recorded_changes
    } == {
        ("resident", ROOT_ID, "identity"),
        ("resident", PEER_ID, "identity"),
        ("lineage", LINEAGE_ID, ""),
    }


def test_lineage_deletion_projection_precedes_blocked_provider_release(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provider 释放受阻时 Lineage 仍先标记为已删除，文件夹绑定保留到释放完成"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    _write_resident_facts([_resident_record(ROOT_ID, folder)])
    apply_step = resident_lifecycle_steps_domain.apply_resident_lifecycle_step
    applied = []
    from management_portal.society_service.resident_lineage_host import ResidentLineageHost
    unbound = []
    original_unbind = ResidentLineageHost.unbind_deleted_lineage_folder

    def record_unbind(step: dict) -> None:
        unbound.append(step["lineage_id"])
        original_unbind(step)

    monkeypatch.setattr(ResidentLineageHost, "unbind_deleted_lineage_folder", record_unbind)

    def blocked_release(operation_id: str, step: dict) -> None:
        applied.append(step["kind"])
        if step["kind"] == "release":
            raise TimeoutError("Provider release is unavailable")
        apply_step(operation_id, step)

    monkeypatch.setattr(resident_lifecycle_steps_domain, "apply_resident_lifecycle_step", blocked_release)
    lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")
    for operation_id in resident_domain.ready_resident_lifecycle_operations():
        assert not resident_domain.resume_resident_lifecycle_operation(operation_id)

    assert resource_records[LINEAGE_ID]["status"] == "deleted"
    assert unbound == [], "Provider release must retain its original folder binding"
    assert applied[:2] == ["lineage_tombstone", "release"]
    assert folder.exists()
    assert lineage_reads.list_lineages("sample_admin")["lineages"] == []


@pytest.mark.parametrize("actor", ["sample_admin", ROOT_ID, PEER_ID])
def test_nonleader_resident_delete_removes_its_lineage_projection(
    deletion_store: tuple[Path, dict[str, dict]],
    actor: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除非 Leader 的 Resident 时从 Lineage 图移除节点并删除其文件夹，Lineage 保持有效"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    records = [
        _resident_record(ROOT_ID, folder),
        _resident_record(PEER_ID, folder),
    ]
    _write_resident_facts(records)
    resident_conversation_stream.get_repository().set_state(
        PEER_ID,
        {
            "work_state": "idle",
            "active_message_id": "",
            "queued_count": 0,
            "intake_state": "open",
        },
    )

    before = lineage_aggregate.read_lineage(LINEAGE_ID, actor="sample_admin")
    lineage_lifecycle.update_lineage_metadata(LINEAGE_ID, {
        "expected_revision": before["revision"],
    }, "sample_admin")
    result = _delete_member(PEER_ID, actor)

    lineage = lineage_aggregate.read_lineage(LINEAGE_ID, actor="sample_admin")
    assert [node["id"] for node in lineage["nodes"]] == [ROOT_ID]
    assert {
        item["resident_id"]
        for item in resident_lineage.all()
    } == {ROOT_ID}
    assert result["deleted_resident_ids"] == [PEER_ID]
    assert result["leader_changed"] is False
    historical = resident_catalog.get_catalog().get_historical(PEER_ID)
    assert historical["deleted_at"] and historical["deleted_by"] == actor
    # The automatic-run identity passes to a remaining Resident (decision_d19b9909a674).
    assert "runtime_principal_id" not in lineage
    state_database = (
        resident_catalog.get_catalog().state_store.database
    )
    with state_database.read() as connection:
        assert connection.execute(
            'SELECT COUNT(*) FROM conversation_states WHERE resident_id = %s',
            (PEER_ID,),
        ).fetchone()[0] == 1
    # The Resident's folder is removed, not archived (decision_d19b9909a674).
    assert not (folder / "residents" / PEER_ID).exists()
    assert resource_records[LINEAGE_ID]["status"] == "active"
    assert resource_records[PEER_ID]["status"] == "deleted"
    assert not resident_catalog.get_catalog().state_store.resident_resource_projection_pending(
        PEER_ID
    )
    with pytest.raises(PermissionError, match="不属于目标 Lineage"):
        lineage_state.authorize_lineage_resident_principal(
            LINEAGE_ID,
            PEER_ID,
        )


def test_lineage_delete_is_rejected_before_moving_data_when_a_member_is_running(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有成员 Resident 仍在工作时删除 Lineage 被拒绝，数据保持不动"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    records = [
        _resident_record(ROOT_ID, folder),
        _resident_record(PEER_ID, folder),
    ]
    _write_resident_facts(records)
    monkeypatch.setattr(
        resident_lifecycle_domain,
        "resident_has_pending_work",
        lambda resident_id: resident_id == PEER_ID,
    )

    with pytest.raises(ValueError, match=PEER_ID):
        lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")

    assert {
        item["resident_id"]
        for item in resident_lineage.all()
    } == {ROOT_ID, PEER_ID}
    assert folder.is_dir()
    assert resource_records[LINEAGE_ID]["status"] == "active"


def test_lineage_cleanup_failure_preserves_deleted_identity_and_retryable_files(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删除 Lineage 后文件清理失败时身份仍为已删除，文件留待重试清理"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    _write_resident_facts([_resident_record(ROOT_ID, folder)])
    result = lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")
    operation_id, = resident_domain.ready_resident_lifecycle_operations()
    with monkeypatch.context() as failure:
        from management_portal.society_service import resident_workspace_retirement
        failure.setattr(resident_workspace_retirement, "remove_tree",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")))
        assert not resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert folder.is_dir()
    assert resident_lineage.all() == []
    assert resource_records[LINEAGE_ID]["status"] == "deleted"
    operations = resident_record_domain.lifecycle_operations()
    assert "remove failed" in operations.health()["last_error"]
    operations.store.database.write(lambda connection: connection.execute(
        'UPDATE resident_lifecycle_operations SET available_at=to_timestamp(0) WHERE operation_id=%s', (operation_id,),
    ))
    assert resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert not folder.exists()


def test_lineage_cleanup_removes_results_an_agent_sealed_read_only(
    deletion_store: tuple[Path, dict[str, dict]],
) -> None:
    """删除 Lineage 时移除 Agent 设为只读的结果目录

    Agent 用 chmod -R a-w 封存的结果目录仍归 Portal 用户所有；删除 Lineage 时恢复属主权限后移除整个文件夹，删除操作不会因 Permission denied 一直重试。

    :importance: P1
    :design: decision_d19b9909a674
    """
    data_root, _resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    _write_resident_facts([_resident_record(ROOT_ID, folder)])
    # An Agent sealed its results with `chmod -R a-w`; the Portal's user still owns them.
    sealed = folder / "residents" / ROOT_ID / "workspace" / "results"
    sealed.mkdir(parents=True)
    (sealed / "result.json").write_text("{}", encoding="utf-8")
    for path in (sealed / "result.json", sealed):
        path.chmod(0o555)
    lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")
    operation_id, = resident_domain.ready_resident_lifecycle_operations()
    assert resident_domain.resume_resident_lifecycle_operation(operation_id)
    assert not folder.exists()
    assert resident_record_domain.lifecycle_operations().health() == {"pending": 0, "failed": 0, "last_error": ""}


def test_lineage_delete_rolls_back_every_authority_when_catalog_commit_fails(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lineage 目录提交失败时删除整体回滚，成员、文件和资源状态不变"""
    data_root, resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    _write_resident_facts([
        _resident_record(ROOT_ID, folder),
        _resident_record(PEER_ID, folder),
    ])
    catalog = lineage_aggregate.lineage_catalog_store()
    monkeypatch.setattr(
        catalog,
        "remove",
        lambda _lineage_id, **kwargs: (_ for _ in ()).throw(
            OSError("catalog commit failed")
        ),
    )

    with pytest.raises(OSError, match="catalog commit failed"):
        lineage_lifecycle.delete_lineage(LINEAGE_ID, "sample_admin")

    assert folder.is_dir()
    assert resource_records[LINEAGE_ID]["status"] == "active"
    assert {
        item["resident_id"]
        for item in resident_lineage.all()
    } == {ROOT_ID, PEER_ID}
    assert lineage_aggregate.read_lineage(
        LINEAGE_ID,
        actor="sample_admin",
    )["lineage_id"] == LINEAGE_ID


def _delete_member(resident_id: str, actor: str) -> dict:
    """Delete one member the way DeleteResident does."""
    return lineage_graph_edits.delete_lineage_graph_objects(
        LINEAGE_ID, {"resident_ids": [resident_id]}, actor, include_lineage=False,
    )


def test_resident_delete_does_not_publish_a_rolled_back_graph(
    deletion_store: tuple[Path, dict[str, dict]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resident 删除提交失败回滚时，不发布 Lineage 图的变更"""
    data_root, _resource_records = deletion_store
    folder = data_root / "lineages" / LINEAGE_ID
    _write_lineage(folder)
    _write_resident_facts([
        _resident_record(ROOT_ID, folder),
        _resident_record(PEER_ID, folder),
    ])
    peer_folder = folder / "residents" / PEER_ID
    peer_folder.mkdir(parents=True, exist_ok=True)
    catalog = resident_catalog.get_catalog()
    monkeypatch.setattr(
        catalog,
        "remove",
        lambda _resident_ids: (_ for _ in ()).throw(
            OSError("resident commit failed")
        ),
    )
    published: list[str] = []
    monkeypatch.setattr(
        lineage_state,
        "_sync_lineage_resource_identity",
        lambda lineage: published.append(str(lineage.get("lineage_id") or "")),
    )

    with pytest.raises(OSError, match="resident commit failed"):
        _delete_member(PEER_ID, "sample_admin")

    assert published == []
    assert peer_folder.is_dir()
    assert {
        item["resident_id"]
        for item in resident_lineage.all()
    } == {ROOT_ID, PEER_ID}
    assert {
        node["id"]
        for node in lineage_aggregate.read_lineage(
            LINEAGE_ID,
            actor="sample_admin",
        )["nodes"]
    } == {ROOT_ID, PEER_ID}
