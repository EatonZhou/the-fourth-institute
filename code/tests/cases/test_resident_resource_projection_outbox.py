from __future__ import annotations

from pathlib import Path

import pytest

from management_portal.society_service import resource_client
from management_portal.society_service.resident_resource_projection import (
    ResidentResourceProjectionWorker,
)
from management_portal.society_service.state_store import LineageDataStore


def _record(title: str = "Canonical") -> dict:
    return {
        "resident_id": "resident_111111111111",
        "lineage_id": "lineage_222222222222",
        "title": title,
        "owned_workspace_id": "workspace_333333333333",
        "workspace_bindings": [],
    }


def test_outbox_write_rolls_back_with_owning_transaction_contracts(tmp_path, subtests, isolated_portal_case):
    """资源投影 outbox 随所在事务回滚，同一 Resident 的更新合并且旧确认不能删除最新记录"""
    with subtests.test(contract='test_outbox_write_rolls_back_with_owning_transaction'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = LineageDataStore()

        def fail() -> None:
            store.enqueue_resident_resource_projection('resident_111111111111', _record())
            raise RuntimeError('rollback')
        with pytest.raises(RuntimeError, match='rollback'):
            store.transaction(fail)
        assert store.resident_resource_projection_health()['pending_count'] == 0
    with subtests.test(contract='test_outbox_coalesces_updates_and_stale_ack_cannot_delete_latest'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = LineageDataStore()
        resident_id = 'resident_111111111111'
        assert store.enqueue_resident_resource_projection(resident_id, _record('v1')) == 1
        stale = store.claim_resident_resource_projections()
        assert store.enqueue_resident_resource_projection(resident_id, _record('v2')) == 2
        assert store.complete_resident_resource_projections(stale)["completed"] == 0
        latest = store.claim_resident_resource_projections()
        assert latest[0]['revision'] == 2
        assert latest[0]['payload']['title'] == 'v2'
        assert store.complete_resident_resource_projections(latest)["completed"] == 1


def test_projection_worker_retries_failure_and_completes_success(
    tmp_path: Path,
) -> None:
    """资源投影 worker 发布失败时保留待发记录并记下错误，之后重试成功"""
    store = LineageDataStore()
    store.enqueue_resident_resource_projection(
        "resident_111111111111",
        _record(),
    )

    class FailingProjector:
        @staticmethod
        def sync_resident_resource_bundles(*_args, **_kwargs):
            raise RuntimeError("resource unavailable")

    worker = ResidentResourceProjectionWorker(store, FailingProjector())
    assert worker.drain_once() == 0
    assert store.resident_resource_projection_health()["pending_count"] == 1
    assert "resource unavailable" in worker.health()["last_error"]

    store.enqueue_resident_resource_projection(
        "resident_111111111111",
        _record("retry"),
    )
    projected: list[dict] = []

    class Projector:
        @staticmethod
        def sync_resident_resource_bundles(records, **_kwargs):
            projected.extend(records)
            return len(records)

    worker = ResidentResourceProjectionWorker(store, Projector())
    assert worker.drain_once() == 1
    assert projected[0]["title"] == "retry"
    assert store.resident_resource_projection_health()["pending_count"] == 0

    class FlakyStore:
        attempts = 0

        @classmethod
        def claim_resident_resource_projections(cls, **_kwargs):
            cls.attempts += 1
            if cls.attempts == 1:
                raise RuntimeError("projection storage unavailable")
            return []

        @staticmethod
        def resident_resource_projection_health():
            return {"pending_count": 0, "last_error": ""}

    worker = ResidentResourceProjectionWorker(FlakyStore(), object())

    assert worker.drain_once() == 0
    assert "projection storage unavailable" in worker.health()["last_error"]
    assert worker.drain_once() == 0
    assert worker.health()["last_error"] == ""


def test_pending_publication_tracks_metadata_changes_and_fences_old_receipts():
    """待发布 Resident 合并元数据变更并拒绝旧确认

    身份、配置或 Session 更新与待发版本在同一事务提交；旧确认不能删除更新后的待发布记录。
    """
    from tests.cases.canonical_domain_fixture import canonical_projection
    from management_portal.society_service.state_store import get_lineage_data_store

    resident_id = 'resident_111111111111'
    canonical_projection(resident_id)
    store = get_lineage_data_store()
    record = store.resident_record(resident_id)
    store.enqueue_resident_resource_projection(resident_id, record)
    old = store.claim_resident_resource_projections(resident_id=resident_id)
    store.put_resident_record(resident_id, {**record, 'updated_at': '2026-10-11T00:00:00Z'})
    assert store.complete_resident_resource_projections(old)['completed'] == 0
    latest = store.claim_resident_resource_projections(resident_id=resident_id)
    assert latest[0]['revision'] > old[0]['revision']
    assert latest[0]['payload']['updated_at'] == '2026-10-11T00:00:00Z'
    assert store.complete_resident_resource_projections(latest)['completed'] == 1


def test_retry_rebuilds_an_older_pending_snapshot_from_current_canonical_facts():
    """Resident 发布重试从当前事实重建快照

    遗留待发快照按当前身份事实重新生成，发布当前标题和时间；不恢复旧内容。
    """
    from tests.cases.canonical_domain_fixture import canonical_projection
    from management_portal.society_service.state_store import get_lineage_data_store

    resident_id = 'resident_111111111111'
    canonical_projection(resident_id)
    store = get_lineage_data_store()
    old = store.resident_record(resident_id)
    store.put_resident_record(resident_id, {**old, 'title': 'Current', 'updated_at': '2026-10-11T00:00:00Z'})
    # An intent persisted before the publisher recovered must not retry its
    # obsolete snapshot forever or restore the obsolete title.
    store.enqueue_resident_resource_projection(resident_id, old)
    claims = store.claim_resident_resource_projections(resident_id=resident_id)
    assert claims[0]['payload']['title'] == 'Current'
    assert claims[0]['payload']['updated_at'] == '2026-10-11T00:00:00Z'
    resource_client.sync_resident(claims[0]['payload'])
    assert store.complete_resident_resource_projections(claims)['completed'] == 1


def test_tombstone_confirmation_publishes_only_the_selected_deletion() -> None:
    """确认删除墓碑只发布所选 Resident 的删除记录，其他待发记录保留"""
    store = LineageDataStore()
    resident_id = "resident_111111111111"
    unrelated_id = "resident_444444444444"
    deleted = {**_record(), "deleted_at": "2026-09-28T00:00:00Z"}
    store.enqueue_resident_resource_projection(resident_id, deleted)
    store.enqueue_resident_resource_projection(
        unrelated_id,
        {**_record("unrelated"), "resident_id": unrelated_id},
    )
    published: list[dict] = []

    class Projector:
        @staticmethod
        def sync_resident_resource_bundles(records, **_kwargs):
            published.extend(records)

    worker = ResidentResourceProjectionWorker(store, Projector())
    worker.confirm_tombstone(resident_id)

    assert published == [deleted]
    assert not store.resident_resource_projection_pending(resident_id)
    assert store.resident_resource_projection_pending(unrelated_id)


def test_tombstone_confirmation_rejects_an_active_projection() -> None:
    """待发记录不是删除墓碑时确认墓碑报错"""
    store = LineageDataStore()
    resident_id = "resident_111111111111"
    store.enqueue_resident_resource_projection(resident_id, _record())
    worker = ResidentResourceProjectionWorker(store, object())

    with pytest.raises(RuntimeError, match="non-tombstone"):
        worker.confirm_tombstone(resident_id)


def test_deleted_resident_bundle_only_projects_resident_tombstone(monkeypatch) -> None:
    """已删除 Resident 的资源同步只发布该 Resident 的删除状态"""
    payloads: list[list[dict]] = []

    class ResourceService:
        @staticmethod
        def sync_collaboration_resources(payload):
            payloads.append(payload)
            return {"resources": [item["descriptor"] for item in payload]}

    monkeypatch.setattr(
        resource_client,
        "get_resource_service",
        lambda: ResourceService(),
    )
    deleted = {
        **_record(),
        "deleted_at": "2026-09-01T00:00:00Z",
    }

    assert resource_client.sync_resident_resource_bundles([deleted]) == 1
    descriptors = [item["descriptor"] for item in payloads[0]]
    assert [item["resource_id"] for item in descriptors] == [deleted["resident_id"]]
    assert [item["status"] for item in descriptors] == ["deleted"]
    assert all(item["deleted_at"] == deleted["deleted_at"] for item in descriptors)
