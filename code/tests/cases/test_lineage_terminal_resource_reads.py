"""Terminal handling reads canonical Lineage state, never ResourceService."""
from __future__ import annotations

from types import SimpleNamespace

from management_portal.society_service import invariant_locks, lineage_domain, resource_client
from tests.cases.test_isa_lineage_lifecycle import lineage, future_creation_store, resident  # noqa: F401
from tests.cases.isa_test_support import ACTOR


def test_terminal_handling_does_not_read_resource_service(lineage, monkeypatch):
    """处理 Resident 消息终态时不向 ResourceService 读取"""
    worker = resident(lineage, 'Terminal')
    locked_reads = []
    def forbid(*args, **kwargs):
        locked_reads.append(invariant_locks.lock_held())
        raise AssertionError('terminal processing called ResourceService')
    monkeypatch.setattr(resource_client, 'get_resource_service', lambda: SimpleNamespace(get=forbid))

    lineage_domain.handle_resident_message_terminal(
        worker, ACTOR, "msg_terminal", lineage_id=lineage,
    )
    assert locked_reads == []
