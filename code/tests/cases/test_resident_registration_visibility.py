"""
:importance: P1
:aspects: functional, behavior
"""
from __future__ import annotations
from management_portal.society_service import resident_catalog

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_common.projection_receipts import CommittedProjectionError
from management_portal.society_service import changes, resident_domain, resource_client
from management_portal.society_service.resident_resource_projection import ResidentResourceProjectionWorker
from tests.cases.test_resident_folder_creation_contract import _new_session_payload
from tests.cases.isa_test_support import isolated_instruction_store  # noqa: F401 - fixture
from tests.cases.test_resident_registration_resource_ack import mark_deleted, registration


def test_pending_registration_is_hidden_and_notification_follows_resource_ack(registration, monkeypatch):
    """Resident 就绪通知与目录可见性等待资源 ACK

    资源等待窗口内不展示半初始化身份，精确读取及启动返回已提交 503，ACK 后才就绪并通知。
    """
    _parent, resources = registration
    store = resident_catalog.get_catalog().state_store
    publish = resource_client.sync_resident_resource_bundles
    entered, release = threading.Event(), threading.Event()
    published_ids, notifications = [], []

    def delayed(records, **kwargs):
        published_ids.extend(record["resident_id"] for record in records)
        assert all(record["registration_state"] == "resource_pending" for record in records)
        entered.set()
        assert release.wait(5)
        return publish(records, **kwargs)

    def changed(resident_id, *, scope="runtime"):
        if scope == "identity":
            notifications.append(resident_id)
            assert store.resident_record(resident_id)["registration_state"] == "ready"
            assert resident_id in resources
            assert not store.resident_resource_projection_pending(resident_id)

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", delayed)
    monkeypatch.setattr(changes, "resident_changed", changed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        payload = _new_session_payload("Visibility boundary")
        future = pool.submit(resident_domain.register_resident, payload, "sample_admin")
        try:
            if not entered.wait(3):
                future.result(timeout=1)
                pytest.fail("registration never reached publication")
            resident_id, = published_ids
            assert store.resident_record(resident_id)["registration_state"] == "resource_pending"
            assert notifications == []
            engaged = resident_domain.list_resident_conversation_activity("sample_admin")
            assert resident_id not in {record["resident_id"] for record in engaged["residents"]}
            with pytest.raises(CommittedProjectionError) as pending:
                resident_domain.get_resident_workspace_roots(resident_id, "sample_admin")
            assert pending.value.details["projection_failures"][0]["resident_id"] == resident_id
            assert pending.value.details["domain_committed"]
        finally:
            release.set()
        created = future.result(timeout=3)
    assert created["registration_state"] == "ready"
    assert notifications == [created["resident_id"]]
    assert resident_domain.get_resident_workspace_roots(created["resident_id"], "sample_admin")["resident_id"] == created["resident_id"]


@pytest.mark.parametrize("recovery", ["worker", "confirm"])
def test_failed_publication_recovery_marks_ready_once_and_stale_runtime_cannot_regress(registration, monkeypatch, recovery):
    """资源重试只发布一次就绪且拒绝旧状态回退

    同步与后台确认复用同一 ACK，重复确认不重复发布就绪，旧 Runtime 写不回退 ready。
    """
    _parent, resources = registration
    publish = resource_client.sync_resident_resource_bundles
    notifications = []
    monkeypatch.setattr(changes, "resident_changed", lambda resident_id, **kwargs: notifications.append((resident_id, kwargs["scope"])))

    def unavailable(*args, **kwargs):
        raise OSError("Resource acknowledgement unavailable")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    with pytest.raises(CommittedProjectionError) as failure:
        resident_domain.register_resident(_new_session_payload("Recovery boundary"), "sample_admin")
    resident_id = failure.value.details["projection_failures"][0]["resident_id"]
    store = resident_catalog.get_catalog().state_store
    pending = store.resident_record(resident_id)
    assert pending["registration_state"] == "resource_pending"
    assert (resident_id, "identity") not in notifications
    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", publish)
    if recovery == "worker":
        # Explicitly make the existing failed outbox retry due in this fixture.
        store.enqueue_resident_resource_projection(resident_id, pending)
        assert ResidentResourceProjectionWorker(store, resource_client).drain_once() == 1
    else:
        resident_domain.confirm_resident_registration(resident_id, "sample_admin")
    assert store.resident_record(resident_id)["registration_state"] == "ready"
    assert resident_id in resources
    assert notifications.count((resident_id, "identity")) == 1
    resident_domain.confirm_resident_registration(resident_id, "sample_admin")
    assert notifications.count((resident_id, "identity")) == 1
    store.put_resident_record(resident_id, {**pending, "last_error": "concurrent runtime update"})
    current = store.resident_record(resident_id)
    assert current["registration_state"] == "ready"
    assert current["last_error"] == "concurrent runtime update"


def test_registration_state_is_always_explicit(registration):
    """Resident 注册状态始终显式保存

    部分保存保留已有注册状态，未知状态被拒绝，缺少状态的记录不算就绪，数据库拒绝空注册状态。

    :importance: P2
    :aspects: functional
    """
    created = resident_domain.register_resident(_new_session_payload("Explicit state"), "sample_admin")
    resident_id = created["resident_id"]
    store = resident_catalog.get_catalog().state_store
    partial = {key: value for key, value in store.resident_record(resident_id).items() if key != "registration_state"}
    store.put_resident_record(resident_id, {**partial, "last_error": "runtime update"})
    assert store.resident_record(resident_id)["registration_state"] == "ready"
    with pytest.raises(ValueError, match="registration_state"):
        store.put_resident_record(resident_id, {**partial, "registration_state": "pending"})
    assert not resident_catalog.registration_ready({"resident_id": resident_id})
    with pytest.raises(Exception, match="registration_state"):
        store.database.write(lambda connection: connection.execute(
            "UPDATE residents SET registration_state=NULL WHERE resident_id=%s", (resident_id,),
        ))


@pytest.mark.parametrize("race", ["newer", "deleted", "tombstone"])
def test_stale_or_deleted_resource_ack_cannot_publish_registration_ready(registration, monkeypatch, race):
    """旧 ACK 和删除竞态不能发布 Resident 就绪

    新投影修订、身份删除或墓碑状态出现后，旧资源确认不得把原身份发布为 ready。
    """
    _parent, _resources = registration

    def unavailable(*args, **kwargs):
        raise OSError("Resource unavailable")

    monkeypatch.setattr(resource_client, "sync_resident_resource_bundles", unavailable)
    with pytest.raises(CommittedProjectionError) as failure:
        resident_domain.register_resident(_new_session_payload("ACK fence"), "sample_admin")
    resident_id = failure.value.details["projection_failures"][0]["resident_id"]
    store = resident_catalog.get_catalog().state_store
    pending = store.resident_record(resident_id)
    stale = store.claim_resident_resource_projections(resident_id=resident_id)
    if race == "newer":
        store.enqueue_resident_resource_projection(resident_id, {**pending, "title": "new projection"})
    elif race == "tombstone":
        resident_catalog.get_catalog().remove([
            {**pending, "deleted_at": "2026-09-14T06:00:00Z", "deleted_by": "sample_admin"},
        ])
    else:
        mark_deleted(store, resident_id)
    receipt = store.complete_resident_resource_projections(stale)
    assert receipt["ready_resident_ids"] == []
    if race == "newer":
        assert receipt["completed"] == 0
        assert store.resident_record(resident_id)["registration_state"] == "resource_pending"
    else:
        with pytest.raises(KeyError):
            store.resident_record(resident_id)
        historical = store.resident_record(resident_id, include_deleted=True)
        assert historical["deleted_at"]
        assert historical["registration_state"] == "resource_pending"
