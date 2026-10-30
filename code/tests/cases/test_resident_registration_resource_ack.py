"""
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations
from management_portal.society_service import resident_catalog

import socket
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_common.projection_receipts import CommittedProjectionError
from management_portal.hatg import routing
from management_portal.society_service import (
    invariant_locks,
    lineage_reads,
    lineage_resident_nodes,
    resident_domain,
    resident_domain_common,
    resident_registration_domain,
    resource_client,
)
from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker
from management_portal.society_service import project_folders
from management_portal.resources.workspace_store import get_workspace_store
from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.isa_test_support import isolated_instruction_store, run  # noqa: F401 - fixture
from tests.cases.test_resident_folder_creation_contract import _new_session_payload
from management_portal.execution import codex_runtime_settings

@pytest.fixture
def registration(isolated_instruction_store, monkeypatch, tmp_path):
    isolated_instruction_store.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_PROCESS", "0")
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")
    monkeypatch.setattr(project_folders, "project_root_for_id", lambda _: isolated_instruction_store)
    monkeypatch.setattr(resident_registration_domain, "safe_absolute_path", lambda raw: Path(raw).resolve(strict=False))
    monkeypatch.setattr(
        codex_runtime_settings,
        "codex_runtime_catalog",
        lambda **_kwargs: {
            "models": ["test-model"],
            "default_model": "test-model",
            "efforts_by_model": {"test-model": ["high"]},
            "default_efforts_by_model": {"test-model": "high"},
            "service_tiers_by_model": {"test-model": []},
        },
    )

    def reject(*args, **kwargs):
        raise AssertionError("Registration ACK tests prohibit external effects")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    # Lineage reads also project quota state; this fixture owns that service
    # boundary while preserving the ban on sockets and subprocesses.
    from management_portal.execution.service import ExecutionService
    monkeypatch.setattr(ExecutionService, "binding_quota_states",
                        lambda _self, bindings: {key: {"state": "open", "reason": ""} for key in bindings})
    resources = install_resource_boundary(monkeypatch, synthetic_residents=False)
    parent = resident_domain.register_resident(_new_session_payload("ACK parent"), "sample_admin")
    return parent, resources


def _create_request(parent):
    graph = lineage_reads.get_lineage(parent["lineage_id"], "sample_admin")
    return {"arguments": {"lineage_id": parent["lineage_id"], "title": "ACK child", "provider": "codex"},
            "key": "registration-ack-create", "version": {parent["lineage_id"]: graph["revision"]}}


def create_resident(request):
    """CreateResident through the ISA; the result is returned, not raised."""

    return run("CreateResident", request["arguments"], key=request["key"], version=request["version"])


def execution_record(instruction_id):
    return routing.kernel_for("lineage", required=True).store.get(instruction_id)


def mark_deleted(store, resident_id):
    store.remove_residents([{
        "resident_id": resident_id,
        "deleted_at": "2026-10-08T00:00:00Z",
        "deleted_by": "sample_admin",
    }])


def test_replay_repairs_a_missing_outbox_only_after_a_real_ack(registration, monkeypatch):
    """CreateResident 重放时补回缺失的资源发布任务，收到真实确认后才把 Resident 置为 ready

    :importance: P2
    :aspects: functional
    """
    parent, resources = registration
    publish = resource_client.sync_resident_resource_bundles

    def unavailable(*args, **kwargs):
        raise OSError("Resource ACK unavailable")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    request = _create_request(parent)
    failure = create_resident(request)
    assert failure.error["code"] == "instruction.projection_pending"
    details = failure.error["details"]
    resident_id = next(item["resident_id"] for item in details["projection_failures"]
                       if item["projection"] == "resident.resource_bundle")
    store = resident_catalog.get_catalog().state_store
    pending = store.resident_record(resident_id)
    before_ids = {row["resident_id"] for row in store.all_residents()}
    before_revision = store.lineage_definition(parent["lineage_id"])["revision"]
    # Reproduce a committed pending identity whose publication work is absent.
    store.database.write(lambda connection: connection.execute(
        'DELETE FROM resident_resource_projection_outbox WHERE resident_id=%s', (resident_id,),
    ))
    monkeypatch.setattr(resident_domain_common, "new_resident_id", lambda *args: pytest.fail("created replacement identity"))
    retry = create_resident(request)
    assert retry.error["code"] == "instruction.projection_pending"
    assert store.resident_resource_projection_pending(resident_id)
    assert store.resident_record(resident_id)["registration_state"] == "resource_pending"
    assert resident_id not in resources

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", publish)
    confirmed = create_resident(request)
    assert confirmed.status == "committed" and confirmed.replayed
    record = store.resident_record(resident_id)
    assert record["registration_state"] == "ready"
    assert record["workspace_bindings"] == pending["workspace_bindings"]
    assert resident_id in resources
    assert not store.resident_resource_projection_pending(resident_id)
    assert {row["resident_id"] for row in store.all_residents()} == before_ids
    assert store.lineage_definition(parent["lineage_id"])["revision"] == before_revision
    assert execution_record(details["instruction_id"])["error"] is None


@pytest.mark.parametrize("state", ["ready", "leased", "deleted", "tombstone"])
def test_registration_projection_repair_preserves_ack_lease_and_deletion(registration, state):
    """注册发布恢复保持 ACK、租约与删除屏障

    已就绪身份不重复发布，既有租约与 revision 不被覆盖，已删除或 tombstone 身份返回 notfound。

    :module: lineage.service
    :importance: P2
    """
    parent, _resources = registration
    store = resident_catalog.get_catalog().state_store
    resident_id = parent["resident_id"]
    if state in {"deleted", "tombstone"}:
        if state == "deleted":
            mark_deleted(store, resident_id)
        else:
            resident_catalog.get_catalog().remove([
                {**parent, "deleted_at": "2026-09-15T00:00:00Z", "deleted_by": "sample_admin"},
            ])
            assert store.resident_deletion(resident_id) is not None
        with pytest.raises(KeyError):
            store.ensure_resident_registration_projection(resident_id)
        assert not store.resident_resource_projection_pending(resident_id)
        return
    if state == "leased":
        store.database.write(lambda connection: connection.execute(
            "UPDATE residents SET registration_state='resource_pending' WHERE resident_id=%s", (resident_id,),
        ))
        store.enqueue_resident_resource_projection(resident_id, parent)
        claims = store.claim_resident_resource_projections(resident_id=resident_id)
        assert len(claims) == 1
        store.ensure_resident_registration_projection(resident_id)
        assert store.claim_resident_resource_projections(resident_id=resident_id) == []
        assert store.complete_resident_resource_projections(claims)["completed"] == 1
    else:
        store.ensure_resident_registration_projection(resident_id)
    assert not store.resident_resource_projection_pending(resident_id)
    assert store.resident_record(resident_id)["registration_state"] == "ready"


def test_confirmation_cannot_repair_publication_without_current_owner_authority(registration, monkeypatch):
    """注册发布恢复必须先通过当前 owner 校验

    无当前管理权限时拒绝确认，不创建或重新排队任何资源发布工作。

    :module: lineage.service
    :importance: P2
    """
    parent, _resources = registration
    store = resident_catalog.get_catalog().state_store
    monkeypatch.setattr(resident_registration_domain, "_can_manage_resident", lambda *args: False)
    monkeypatch.setattr(store, "ensure_resident_registration_projection", lambda *args: pytest.fail("unauthorized repair"))
    with pytest.raises(PermissionError):
        resident_domain.confirm_resident_registration(parent["resident_id"], "sample_admin")


@pytest.mark.parametrize("entry", ["register", "hatg"])
def test_registration_waits_for_bundle_ack_without_blocking_other_residents(registration, monkeypatch, entry):
    """Resident 注册等待资源确认且不阻塞其他身份

    注册和 HATG 创建均在资源 bundle 确认后返回，随后可立即读取目录。
    """
    parent, resources = registration
    publish = resource_client.sync_resident_resource_bundles
    entered, release = threading.Event(), threading.Event()
    observed = []

    def delayed(records, **kwargs):
        assert not invariant_locks.lock_held()
        if records[0]["title"] == "ACK child":
            observed.append(records[0])
            entered.set()
            assert release.wait(5)
        return publish(records, **kwargs)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", delayed)
    request = _create_request(parent)

    def create():
        if entry == "hatg":
            return create_resident(request).raise_for_error()
        return resident_domain.register_resident(_new_session_payload("ACK child"), "sample_admin")

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(create)
        try:
            if not entered.wait(3):
                future.result(timeout=1)
                pytest.fail("creation did not reach Resource publication")
            assert not future.done()
            resident_id = observed[0]["resident_id"]
            assert resident_id not in resources
            independent = pool.submit(resident_domain.register_resident, _new_session_payload("Independent"), "sample_admin")
            assert independent.result(timeout=3)["resident_id"] != resident_id
        finally:
            release.set()
        created = future.result(timeout=3)
    assert created["resident_id"] in resources
    # Workspaces are registered in the Workspace store with the Resident row; the
    # Resource bundle ACK carries no Workspace projection.
    workspace_ids = [observed[0]["owned_workspace_id"],
                     *(binding["workspace_id"] for binding in observed[0]["workspace_bindings"])]
    assert get_workspace_store().rows(workspace_ids).keys() == set(workspace_ids)
    assert resident_domain.get_resident_workspace_roots(created["resident_id"], "sample_admin")["resident_id"] == created["resident_id"]


@pytest.mark.parametrize("entry", ["register", "hatg"])
def test_committed_registration_retries_only_publication_with_same_identity(registration, monkeypatch, entry):
    """已提交注册失败重试保留原身份与创建回执

    资源故障后的正式重试仅确认原身份投影，不重复创建、启动或更换 Workspace。

    :aspects: functional
    """
    parent, _resources = registration
    publish = resource_client.sync_resident_resource_bundles
    attempts = []

    def unavailable(records, **kwargs):
        attempts.append(records[0]["resident_id"])
        raise OSError("isolated Resource outage")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    request = _create_request(parent)
    if entry == "hatg":
        outcome = create_resident(request)
        assert outcome.error["code"] == "instruction.projection_pending"
        assert outcome.error["commit_state"] == "committed"
        assert outcome.error["details"]["retry"] == "same_instruction_same_idempotency_key"
        details = outcome.error["details"]
    else:
        with pytest.raises(CommittedProjectionError) as pending:
            resident_domain.register_resident(_new_session_payload("ACK child"), "sample_admin")
        details = pending.value.details
    assert details["domain_committed"] is True
    failure = next(item for item in details["projection_failures"] if item["projection"] == "resident.resource_bundle")
    resident_id = failure["resident_id"]
    store = resident_catalog.get_catalog().state_store
    committed = store.resident_record(resident_id)
    graph = lineage_reads.get_lineage(committed["lineage_id"], "sample_admin")
    assert failure["committed_revision"] == graph["revision"]
    before_ids = {record["resident_id"] for record in store.all_residents()}
    assert attempts == [resident_id]
    assert committed["registration_state"] == "resource_pending"
    assert store.resident_resource_projection_pending(resident_id)

    def observed(records, **kwargs):
        attempts.append(records[0]["resident_id"])
        return publish(records, **kwargs)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", observed)
    monkeypatch.setattr(resident_domain_common, "new_resident_id", lambda *args: pytest.fail("retry created a new identity"))
    if entry == "hatg":
        retried = create_resident(request)
        assert retried.status == "committed" and retried.replayed
        assert retried.result["resident_id"] == resident_id
        assert create_resident(request).result["resident_id"] == resident_id
    else:
        result = resident_domain.confirm_resident_registration(resident_id, "sample_admin")
        assert result["resident_id"] == resident_id
        assert result["workspace_bindings"] == committed["workspace_bindings"]
    assert {record["resident_id"] for record in store.all_residents()} == before_ids
    assert not store.resident_resource_projection_pending(resident_id)
    assert store.resident_record(resident_id)["registration_state"] == "ready"
    assert lineage_reads.get_lineage(committed["lineage_id"], "sample_admin")["revision"] == graph["revision"]
    assert attempts == [resident_id, resident_id]


def test_confirmation_rechecks_owner_and_deleted_identity(registration, monkeypatch):
    """确认 Resident 注册时重新检查调用者权限，已删除的 Resident 不能确认

    :importance: P2
    :aspects: functional
    """
    parent, _resources = registration
    record = lineage_resident_nodes.create_lineage_resident(parent["lineage_id"], _new_session_payload("To remove"), "sample_admin")["resident"]
    assert resident_domain.confirm_resident_registration(record["resident_id"], "sample_admin")["resident_id"] == record["resident_id"]
    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", lambda *args, **kwargs: pytest.fail("forbidden publication"))
    with pytest.raises(PermissionError):
        resident_domain.confirm_resident_registration(record["resident_id"], "")
    with monkeypatch.context() as denied:
        denied.setattr(resident_registration_domain, "_can_manage_resident", lambda *args, **kwargs: False)
        with pytest.raises(PermissionError):
            resident_domain.confirm_resident_registration(record["resident_id"], "sample_admin")
    store = resident_catalog.get_catalog().state_store
    mark_deleted(store, record["resident_id"])
    with pytest.raises(KeyError):
        resident_domain.confirm_resident_registration(record["resident_id"], "sample_admin")
    with pytest.raises(KeyError):
        store.resident_record(record["resident_id"])


@pytest.mark.parametrize("state", ["targeted", "leased", "newer", "deleted"])
def test_outbox_confirmation_obeys_exact_identity_and_current_publication(registration, state):
    """注册确认复用精确身份的现有发布回执

    确认不处理无关身份、不重复正在发布的 bundle，旧 ACK 不覆盖新状态，删除后不复活。
    """
    parent, _resources = registration
    store = resident_catalog.get_catalog().state_store
    record = {**store.resident_record(parent["resident_id"]), "title": "v1"}
    resident_id = record["resident_id"]
    store.put_resident_record(resident_id, record)
    store.enqueue_resident_resource_projection(resident_id, record)
    unrelated = {**record, "resident_id": "resident_333333333333"}
    store.enqueue_resident_resource_projection(unrelated["resident_id"], unrelated)
    published = []

    def publish(records, **kwargs):
        published.extend(records)
        if state == "newer" and len(published) == 1:
            changed = {**record, "title": "v2"}
            store.put_resident_record(resident_id, changed)
            store.enqueue_resident_resource_projection(resident_id, changed)
        return len(records)

    worker = ResidentResourceProjectionWorker(store, SimpleNamespace(sync_resident_resource_bundles=publish))
    if state == "deleted":
        mark_deleted(store, resident_id)
        with pytest.raises(KeyError):
            worker.confirm(resident_id)
        assert published == []
    elif state == "leased":
        claims = store.claim_resident_resource_projections(resident_id=resident_id)
        with pytest.raises(TimeoutError):
            worker.confirm(resident_id, timeout_seconds=0.02)
        assert published == []
        store.complete_resident_resource_projections(claims)
        assert worker.confirm(resident_id)["resident_id"] == resident_id
        assert published == []
    else:
        confirmed = worker.confirm(resident_id)
        assert confirmed["title"] == ("v2" if state == "newer" else "v1")
        assert [item["title"] for item in published] == (["v1", "v2"] if state == "newer" else ["v1"])
        assert not store.resident_resource_projection_pending(resident_id)
    assert store.resident_resource_projection_pending(unrelated["resident_id"])


@pytest.mark.parametrize("actor_kind", ["owner", "member", "outsider"])
def test_missing_resident_resource_confirmation_can_be_recovered(registration, monkeypatch, tmp_path, actor_kind):
    """资源投影未确认的 Resident 可确认原身份并补发资源

    开放模式下 Lineage 创建者、成员 Resident 与其他有效用户都可确认原身份。
    """
    parent, resources = registration
    child = lineage_resident_nodes.create_lineage_resident(parent["lineage_id"], _new_session_payload("Resource pending"), "sample_admin")["resident"]
    store = resident_catalog.get_catalog().state_store
    record = store.resident_record(child["resident_id"])
    store.enqueue_resident_resource_projection(record["resident_id"], record)
    resources.pop(record["resident_id"])
    actor = {"owner": "sample_admin", "member": parent["resident_id"], "outsider": "panda"}[actor_kind]
    confirmed = resident_domain.confirm_resident_registration(record["resident_id"], actor)
    assert confirmed["resident_id"] == record["resident_id"]
    assert record["resident_id"] in resources


def test_committed_registration_error_preserves_identity_over_rpc_and_http(monkeypatch):
    """注册投影失败跨 RPC 与 HTTP 保留恢复身份

    已提交但资源未确认时返回 503 与确切 Resident、Lineage、revision 和正式恢复入口。

    :aspects: functional
    """
    from management_portal import serve_management_portal
    from management_portal.society_service.server import _error_payload
    from management_portal.society_service.transport import SocietyServiceClient

    failure = {
        "projection": "resident.resource_bundle",
        "resident_id": "resident_111111111111",
        "lineage_id": "lineage_222222222222",
        "committed_revision": 7,
        "error": "Resource unavailable",
    }
    original = CommittedProjectionError([failure])
    with pytest.raises(CommittedProjectionError) as remote:
        SocietyServiceClient._raise_remote_error(_error_payload(original))
    assert remote.value.details == original.details
    replies = []
    monkeypatch.setattr(serve_management_portal, "_serve_json", lambda handler, status, payload: replies.append((status, payload)))
    serve_management_portal._serve_agent_workbench_error(object(), remote.value)
    status, payload = replies[0]
    assert status == 503
    assert payload["code"] == "projection.pending"
    assert payload["http_status"] == 503
    assert payload["commit_state"] == "committed"
    assert payload["retryable"] is False
    assert payload["details"]["domain_committed"] is True
    assert payload["details"]["projection_failures"] == [failure]


def test_pending_create_replay_after_deletion_is_notfound_without_recreation(registration, monkeypatch):
    """已删除创建身份的原键恢复返回 notfound

    HATG 原创建键的投影恢复在身份删除后返回 404，不补建身份、不重发资源。

    :aspects: functional
    """
    parent, _resources = registration
    request = _create_request(parent)

    def unavailable(*args, **kwargs):
        raise OSError("Resource unavailable")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    pending = create_resident(request)
    assert pending.error["code"] == "instruction.projection_pending"
    failure = next(item for item in pending.error["details"]["projection_failures"] if item["projection"] == "resident.resource_bundle")
    store = resident_catalog.get_catalog().state_store
    mark_deleted(store, failure["resident_id"])
    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", lambda *args, **kwargs: pytest.fail("deleted identity was published"))
    monkeypatch.setattr(resident_domain_common, "new_resident_id", lambda *args: pytest.fail("deleted identity was recreated"))
    deleted = create_resident(request)
    assert deleted.error["code"] == "instruction.not_found"
    with pytest.raises(KeyError):
        store.resident_record(failure["resident_id"])
