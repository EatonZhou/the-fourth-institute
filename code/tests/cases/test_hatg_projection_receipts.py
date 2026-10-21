from __future__ import annotations

import copy
import socket
import subprocess

import pytest

from management_portal.hatg import routing
from management_portal.society_service import changes, invariant_locks, lineage_domain
from tests.cases.society_service_test_support import install_resource_boundary
from tests.cases.isa_test_support import isolated_instruction_store, run  # noqa: F401 - fixture


@pytest.fixture
def projection_fixture(isolated_instruction_store, isolate_portal_databases, tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_SOCIETY_SERVICE_PROCESS", "0")
    monkeypatch.setattr(invariant_locks, "_ROOT_PROVIDER", lambda: tmp_path / "locks")

    def reject(*args, **kwargs):
        raise AssertionError("projection regression prohibits external effects")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(subprocess, "Popen", reject)
    records = install_resource_boundary(monkeypatch)
    created = lineage_domain.create_lineage({"title": "projection fixture", "nodes": [], "edges": []}, "sample_admin")
    before = lineage_domain._read_lineage_state(created["lineage_id"])
    request = {
        "lineage_id": created["lineage_id"],
        "idempotency_key": "projection-fixture",
        "description": "committed fixture",
    }
    return before, request, records


def submit(request, actor="sample_admin"):
    """UpdateLineage through the ISA; the result is returned, not raised."""

    return run("UpdateLineage", {"lineage_id": request["lineage_id"], "changes": {"description": request["description"]}},
               actor=actor, key=request["idempotency_key"])


def execution_record(instruction_id):
    return routing.kernel_for("lineage", required=True).store.get(instruction_id)


def test_uncommitted_failures_do_not_offer_projection_recovery(projection_fixture, monkeypatch):
    """Lineage 写入事务回滚的失败不标记为已提交，也不提供投影恢复"""
    before, request, _records = projection_fixture
    attempts = []
    store_type = type(lineage_domain._lineage_catalog_store())
    original_upsert = store_type.upsert

    def interrupted(store, value):
        attempts.append(value["revision"])
        original_upsert(store, value)
        raise OSError("transaction must roll back")

    monkeypatch.setattr(store_type, "upsert", interrupted)
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", lambda *_: pytest.fail("uncommitted projection"))
    for attempt in range(2):
        request["idempotency_key"] = f"projection-fixture-{attempt}"
        failure = submit(request)
        assert failure.error["code"] == "instruction.failed"
        assert not failure.error["details"].get("domain_committed")
    assert lineage_domain._read_lineage_state(before["lineage_id"]) == before
    assert len(attempts) == 2


def test_projection_replay_reauthorizes_and_preserves_pending_receipt(projection_fixture, monkeypatch):
    """投影待完成的指令重放时重新授权，保留待完成回执并拒绝改参数的同键调用"""
    before, request, records = projection_fixture
    attempts = []

    def unavailable(projection):
        attempts.append(projection["revision"])
        raise OSError("isolated unavailable resource")

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    initial = submit(request)
    assert initial.error["code"] == "instruction.projection_pending"
    assert initial.error["commit_state"] == "committed"
    instruction_id = initial.error["details"]["instruction_id"]
    pending = execution_record(instruction_id)
    assert pending["status"] == "committed" and pending["error"]["code"] == "instruction.projection_pending"
    assert submit(request).error["code"] == "instruction.projection_pending"
    changed_request = copy.deepcopy(request)
    changed_request["description"] = "must not overwrite"
    assert submit(changed_request).error["code"] == "instruction.idempotency_conflict"
    assert attempts == [before["revision"] + 1] * 2
    assert execution_record(instruction_id) == pending


def test_projection_recovery_uses_current_state_without_repeating_commit_or_notification(projection_fixture, monkeypatch):
    """投影恢复按当前最新状态投影，不重复提交也不重复发送变更通知"""
    before, request, _records = projection_fixture
    original_projection = lineage_domain._sync_lineage_resource_identity
    original_changed = changes.lineage_changed
    notifications = []
    projected = []

    def changed(*args, **kwargs):
        notifications.append((args, kwargs))
        return original_changed(*args, **kwargs)

    def unavailable(_projection):
        raise OSError("temporary projection failure")

    monkeypatch.setattr(changes, "lineage_changed", changed)
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    initial = submit(request)
    assert initial.error["code"] == "instruction.projection_pending"

    def observed(projection):
        assert not invariant_locks.lock_held()
        projected.append((projection["revision"], projection["description"]))
        return original_projection(projection)

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", observed)
    newer_request = {**request, "idempotency_key": "projection-newer-commit",
                     "description": "newer authoritative state"}
    assert submit(newer_request).status == "committed"
    newer = lineage_domain._read_lineage_state(before["lineage_id"])
    assert newer["revision"] == before["revision"] + 2
    notification_count = len(notifications)
    assert notification_count == 2
    # The newer commit's publication already covers the pending call's commit,
    # so replaying the pending call completes it without publishing again.
    result = submit(request)
    assert result.status == "committed" and result.replayed, result
    assert projected == [(newer["revision"], newer["description"])]
    assert len(notifications) == notification_count
    assert lineage_domain._read_lineage_state(before["lineage_id"]) == newer
    assert execution_record(initial.error["details"]["instruction_id"])["error"] is None


def test_later_non_projection_failure_is_not_reported_as_success(projection_fixture, monkeypatch):
    """提交后投影之外的步骤失败时指令报告失败并标明已提交，不报告为成功"""
    before, request, _records = projection_fixture
    attempts = []

    def unavailable(_projection):
        attempts.append("projection")
        raise OSError("resource projection failed")

    def unrelated_failure(*args, **kwargs):
        attempts.append("notification")
        raise ValueError("separate committed-phase failure")

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    monkeypatch.setattr(changes, "lineage_changed", unrelated_failure)
    for _attempt in range(2):
        failure = submit(request)
        assert failure.status == "failed"
        assert failure.error["code"] != "instruction.projection_pending"
        assert failure.error["details"]["domain_committed"] is True
        assert failure.error["commit_state"] == "committed"
    assert attempts == ["projection", "notification"]
    assert lineage_domain._read_lineage_state(before["lineage_id"])["revision"] == before["revision"] + 1


def test_pending_projection_is_not_evicted_from_the_execution_records(projection_fixture, monkeypatch):
    """投影待完成的执行记录不被执行记录清理删除"""
    from management_portal.hatg import store as execution_store

    before, request, _records = projection_fixture

    def unavailable(_projection):
        raise OSError("pending projection must retain its record")

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    initial = submit(request)
    assert initial.error["code"] == "instruction.projection_pending"
    instruction_id = initial.error["details"]["instruction_id"]
    pending = execution_record(instruction_id)
    instruction_store = routing.kernel_for("lineage", required=True).store
    instruction_store.prune(now="2099-01-01T00:00:00.000Z")
    assert execution_record(instruction_id) == pending
    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", lambda projection: None)
    for index in range(2):
        later = submit({**request, "idempotency_key": f"later-{index}", "description": f"later {index}"})
        assert later.status == "committed", later
    assert execution_record(instruction_id) == pending


def test_domain_projection_error_preserves_commit_facts_through_rpc(projection_fixture, monkeypatch):
    """domain 投影失败经 RPC 传回时保留已提交版本和仅需补投影的恢复信息"""
    from management_common.projection_receipts import CommittedProjectionError
    from management_portal.society_service.server import _error_payload
    from management_portal.society_service.transport import SocietyServiceClient

    before, request, _records = projection_fixture

    def unavailable(_projection):
        raise OSError("isolated domain projection failure")

    monkeypatch.setattr(lineage_domain, "_sync_lineage_resource_identity", unavailable)
    with pytest.raises(CommittedProjectionError) as domain_failure:
        lineage_domain.apply_lineage_graph_patch(
            before["lineage_id"],
            {"patch": {"description": "committed domain RPC"}},
            "sample_admin",
        )
    committed = lineage_domain._read_lineage_state(before["lineage_id"])
    assert committed["revision"] == before["revision"] + 1
    assert committed["description"] == "committed domain RPC"
    with pytest.raises(CommittedProjectionError) as transported:
        SocietyServiceClient._raise_remote_error(_error_payload(domain_failure.value))
    assert transported.value.details == domain_failure.value.details
    assert transported.value.details["domain_committed"] is True
    assert transported.value.details["committed_revision"] == committed["revision"]
    assert transported.value.details["recovery"] == "projection_only"
