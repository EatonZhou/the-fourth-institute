"""Deletion replay confirms durable effects without changing retained identity.

:design: decision_d19b9909a674, decision_0f57656a2acd
"""
from types import SimpleNamespace

import pytest

from management_portal.society_service import (
    resident_catalog, resident_directory_domain, resident_workspace_domain,
)


@pytest.fixture
def deleted_identity(monkeypatch):
    record = {
        "resident_id": "resident_0123456789ab",
        "lineage_id": "lineage_0123456789ab",
        "deleted_at": "2026-10-08T00:00:00Z",
        "deleted_by": "original-owner",
    }
    confirmations = []
    monkeypatch.setattr(resident_catalog, "get_catalog", lambda: SimpleNamespace(
        get_historical=lambda resident_id: dict(record),
    ))
    monkeypatch.setattr(resident_workspace_domain, "confirm_resident_deletion_projection", confirmations.append)
    monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda record, actor: actor == "allowed")
    monkeypatch.setattr(resident_directory_domain, "_is_active_actor", lambda actor: actor == "allowed")
    return record, confirmations


def test_repeat_delete_keeps_first_facts_and_does_not_allocate_cleanup(deleted_identity, monkeypatch):
    """重复删除 Resident 保留首次删除的时间和操作者，只重新确认删除投影，不再规划清理

    :design-point:"""
    record, confirmations = deleted_identity

    def unexpected(*args, **kwargs):
        pytest.fail("A deletion replay must not prepare another cleanup plan")

    monkeypatch.setattr(resident_workspace_domain, "_prepare_resident_deletion", unexpected)
    repeated = resident_workspace_domain.retry_deleted_resident(record["resident_id"], "allowed")
    assert repeated["deleted_at"] == record["deleted_at"]
    assert repeated["deleted_by"] == record["deleted_by"]
    assert confirmations == [record["resident_id"]]


def test_repeat_delete_rechecks_permission_before_publication(deleted_identity):
    """重复删除前重新检查权限，无权调用者不触发删除投影确认

    :design-point:"""
    record, confirmations = deleted_identity
    with pytest.raises(PermissionError):
        resident_workspace_domain.retry_deleted_resident(record["resident_id"], "intruder")
    assert confirmations == []


def test_repeat_delete_reports_unfinished_resource_confirmation(deleted_identity, monkeypatch):
    """重复删除时资源墓碑尚未确认则报超时，删除记录保持不变

    :design-point:"""
    record, _ = deleted_identity

    def unavailable(resident_id):
        raise TimeoutError("Resource tombstone is pending")

    monkeypatch.setattr(resident_workspace_domain, "confirm_resident_deletion_projection", unavailable)
    with pytest.raises(TimeoutError, match="tombstone is pending"):
        resident_workspace_domain.retry_deleted_resident(record["resident_id"], "allowed")
    assert record["deleted_by"] == "original-owner"
