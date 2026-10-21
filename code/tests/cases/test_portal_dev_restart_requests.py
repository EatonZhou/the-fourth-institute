from __future__ import annotations

from pathlib import Path

from scripts import portal_dev_restart_requests


def test_restart_requests_merge_under_one_operation_and_only_latest_revision_can_acknowledge(
    tmp_path: Path,
) -> None:
    """多个重启请求合并为同一操作，只有最新修订号能确认完成"""
    request_path = tmp_path / "restart-request.json"
    lock_path = tmp_path / "restart-request.lock"
    first = portal_dev_restart_requests.submit_restart_request(
        ["web"],
        path=request_path,
        lock_path=lock_path,
    )
    second = portal_dev_restart_requests.submit_restart_request(
        ["lineage"],
        path=request_path,
        lock_path=lock_path,
    )

    assert second["request_id"] == first["request_id"]
    assert first["revision"] == 1
    assert second["revision"] == 2
    assert second["components"] == ["lineage", "web"]
    assert portal_dev_restart_requests.acknowledge_restart_request(
        first["request_id"],
        first["revision"],
        path=request_path,
        lock_path=lock_path,
    ) is False
    assert portal_dev_restart_requests.acknowledge_restart_request(
        second["request_id"],
        second["revision"],
        path=request_path,
        lock_path=lock_path,
    ) is True
    assert portal_dev_restart_requests.read_restart_request(request_path) == {}


def test_restart_batch_has_quiet_window_and_cannot_be_starved():
    """重启合并窗口有上限"""
    from datetime import datetime, timezone

    stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
    request = {"first_requested_at": stamp(100), "requested_at": stamp(102)}
    assert portal_dev_restart_requests.restart_request_delay(request, now=102) == 5
    request["requested_at"] = stamp(129)
    assert portal_dev_restart_requests.restart_request_delay(request, now=129) == 1
    assert portal_dev_restart_requests.restart_request_delay(request, now=130) == 0
