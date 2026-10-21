"""Terminal handling reads canonical Lineage state, never ResourceService."""
from __future__ import annotations

from management_portal.society_service import invariant_locks, lineage_domain
from tests.cases.test_projection_recovery_fence import ACTOR, fence_fixture  # noqa: F401


def test_terminal_handling_does_not_read_resource_service(fence_fixture, monkeypatch):
    """处理 Resident 消息终态时不向 ResourceService 读取"""
    before, _request, service, *_ = fence_fixture
    locked_reads = []
    def forbid(*args, **kwargs):
        locked_reads.append(invariant_locks.lock_held())
        raise AssertionError('terminal processing called ResourceService')
    monkeypatch.setattr(service, 'get', forbid)

    lineage_domain.handle_resident_message_terminal(
        "resident_0123456789ab", ACTOR, "msg_terminal", lineage_id=before["lineage_id"],
    )
    assert locked_reads == []
