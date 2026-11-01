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
    from tests.cases.canonical_domain_fixture import canonical_projection
    canonical_projection(resident_id, deleted_at=deleted["deleted_at"])
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

    assert len(published) == 1
    assert published[0]["resident_id"] == resident_id
    assert published[0]["deleted_at"] == deleted["deleted_at"]
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


@pytest.mark.parametrize("failure", ["none", "token_store", "unlink"])
def test_committed_tombstones_recover_credentials_without_after_commit(
    tmp_path, monkeypatch, failure,
):
    """提交后丢失回调仍可恢复；单项撤销失败保留持久重试且不阻断其他 claim。"""
    import time
    from types import SimpleNamespace
    from management_portal import agent_token_auth
    from management_portal.society_service import (
        lineage_data_projection_outbox, resident_hatg_credentials, resident_workspace_domain,
    )
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.canonical_domain_fixture import canonical_projection
    from tests.cases.test_resident_hatg_credentials import _deleted_record, LINEAGE_ID

    monkeypatch.setattr(resident_hatg_credentials, "TOKEN_ROOT", tmp_path / "tokens")
    ids = ["resident_111111111111", "resident_444444444444", "resident_555555555555"]
    live_id = "resident_666666666666"
    paths = {rid: resident_hatg_credentials.ensure_resident_credential(rid, LINEAGE_ID) for rid in [*ids, live_id]}
    tokens = {rid: path.read_text().strip() for rid, path in paths.items()}
    store = get_lineage_data_store()
    dropped_callbacks = []
    # The real transaction commits, but none of its post-commit effects run:
    # the same durable state left by process exit immediately after commit.
    with monkeypatch.context() as crash:
        crash.setattr(store.database, "after_commit", dropped_callbacks.append)
        with store.database.transaction():
            deletions = [_deleted_record(store, rid) for rid in ids]
            resident_workspace_domain._confirm_deleted_resident_resources(
                [record for record, _ in deletions],
                {record["resident_id"]: revision for record, revision in deletions},
            )
    assert dropped_callbacks
    assert all(agent_token_auth.authenticate_bearer_token(token) for token in tokens.values())
    canonical_projection(live_id, lineage_id=LINEAGE_ID)
    # A stale tombstone payload for a live identity must be rebuilt as live.
    store.enqueue_resident_resource_projection(
        live_id, {**store.resident_record(live_id), "deleted_at": "2026-11-01T00:00:00Z"},
    )
    original_put = agent_token_auth.AgentTokenStore.put
    original_unlink = Path.unlink

    def put(self, rid, record):
        if rid == ids[0] and record.get("enabled") is False:
            raise RuntimeError("injected token store failure")
        return original_put(self, rid, record)

    def unlink(path, *args, **kwargs):
        if path == paths[ids[0]]:
            raise PermissionError("injected unlink failure")
        return original_unlink(path, *args, **kwargs)

    published = []

    class Projector:
        @staticmethod
        def sync_resident_resource_bundles(records, **kwargs):
            published.extend(record["resident_id"] for record in records)
            return resource_client.sync_resident_resource_bundles(records, **kwargs)

    with monkeypatch.context() as fault:
        if failure == "token_store":
            fault.setattr(agent_token_auth.AgentTokenStore, "put", put)
        elif failure == "unlink":
            fault.setattr(Path, "unlink", unlink)
        worker = ResidentResourceProjectionWorker(LineageDataStore(), Projector())
        completed = worker.drain_once()
        assert all(agent_token_auth.authenticate_bearer_token(tokens[rid]) is None for rid in ids[1:])
        assert agent_token_auth.authenticate_bearer_token(tokens[live_id]) is not None
        assert completed == (4 if failure == "none" else 3)
        if failure != "none":
            assert ids[0] not in published
            assert "injected" in worker.health()["last_error"]
            assert store.resident_resource_projection_pending(ids[0])
            assert (agent_token_auth.authenticate_bearer_token(tokens[ids[0]]) is not None) == (failure == "token_store")
            with store.database.read() as connection:
                row = connection.execute(
                    "SELECT attempt_count, last_error, lease_token FROM resident_resource_projection_outbox WHERE resident_id=%s",
                    (ids[0],),
                ).fetchone()
            assert row["attempt_count"] == 1 and "injected" in row["last_error"]
            assert row["lease_token"] == ""
        assert all(not store.resident_resource_projection_pending(rid) for rid in [*ids[1:], live_id])

    # Rebuild both stores and worker; advance only the outbox clock past backoff.
    recovered_tokens = agent_token_auth.AgentTokenStore()
    monkeypatch.setattr(agent_token_auth, "_STORE", recovered_tokens)
    recovered_store = LineageDataStore()
    monkeypatch.setattr(lineage_data_projection_outbox, "time", SimpleNamespace(time=lambda: time.time() + 120))
    recovered = ResidentResourceProjectionWorker(recovered_store, resource_client)
    assert recovered.drain_once() == (0 if failure == "none" else 1)
    assert recovered_store.resident_resource_projection_health()["pending_count"] == 0
    for rid in ids:
        assert agent_token_auth.authenticate_bearer_token(tokens[rid]) is None
        assert recovered_tokens.get(rid)["enabled"] is False
        assert not paths[rid].exists()
        assert resident_hatg_credentials.ensure_resident_credential(rid, LINEAGE_ID) is None
        assert recovered_store.resident_record(rid, include_deleted=True)["deleted_at"]
        with pytest.raises(KeyError):
            recovered_store.resident_record(rid)
    assert agent_token_auth.authenticate_bearer_token(tokens[live_id]) is not None
    assert recovered.drain_once() == 0


def test_tombstone_confirmation_retains_claim_when_revocation_fails(tmp_path, monkeypatch):
    """显式确认撤销失败不能 ACK，重建 worker 后可以确认同一持久 claim。"""
    from management_portal import agent_token_auth
    from management_portal.society_service import resident_hatg_credentials
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.test_resident_hatg_credentials import _deleted_record, LINEAGE_ID, RESIDENT_ID

    monkeypatch.setattr(resident_hatg_credentials, "TOKEN_ROOT", tmp_path / "tokens")
    _deleted_record(get_lineage_data_store())
    path = resident_hatg_credentials.ensure_resident_credential(RESIDENT_ID, LINEAGE_ID)
    token = path.read_text().strip()
    worker = ResidentResourceProjectionWorker(LineageDataStore(), resource_client)

    def unavailable(*args):
        raise OSError("credential store unavailable")

    with monkeypatch.context() as fault:
        fault.setattr(resident_hatg_credentials, "revoke_resident_credential", unavailable)
        with pytest.raises(OSError, match="credential store unavailable"):
            worker.confirm_tombstone(RESIDENT_ID)
    assert worker.health()["pending_count"] == 1
    assert agent_token_auth.authenticate_bearer_token(token) is not None
    ResidentResourceProjectionWorker(LineageDataStore(), resource_client).confirm_tombstone(RESIDENT_ID)
    assert agent_token_auth.authenticate_bearer_token(token) is None
    assert worker.health()["pending_count"] == 0
