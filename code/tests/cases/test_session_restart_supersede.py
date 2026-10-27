"""RestartSession: a new restart replaces an unfinished one (decision_d19b9909a674).

:module: resident.session
:importance: P1
:aspects: functional, behavior
:design: decision_d19b9909a674
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from management_portal.society_service import resident_domain, resident_record_domain, resident_restart_domain
from management_portal.society_service.domain_ports import execution_port
from tests.cases.test_session_restart import ExecutionStub, _restart_domain


def test_a_new_restart_replaces_an_unfinished_one(monkeypatch, isolated_portal_case) -> None:
    """新的 RestartSession 直接取代尚未完成的重启

    Resident 已有未完成的重启时，新的重启取消它（记为 superseded）、等待其生命周期操作结束后按新参数重新开始；不再以 session_restart_in_progress 拒绝。
    """
    with isolated_portal_case():
        record, operations, saved = _restart_domain(monkeypatch)
        first = resident_domain.restart_resident_session(
            record["resident_id"], "alice",
            {"target_provider": "codex", "target_account": "one", "context_strategy": "compact"},
        )
        assert first["status"] == "accepted"
        first_step = operations[-1]["step"]

        # The first restart's lifecycle operation stays pending until the Lineage coordinator finishes it.
        queued = {"first": True}

        def enqueue(_resident_id, steps):
            operations[-1]["step"] = steps[0]
            return "operation_second"

        monkeypatch.setattr(
            resident_record_domain, "lifecycle_operations",
            lambda: SimpleNamespace(pending=lambda *_: queued["first"], enqueue=enqueue),
        )
        cancelled: list[str] = []
        monkeypatch.setattr(execution_port, "service", lambda: ExecutionStub(
            cancel=lambda execution_id, **_kwargs: cancelled.append(execution_id) or {
                "status": "cancelled", "result": {"status": "cancelled"}},
            resume=lambda *_args, **_kwargs: {"status": "cancelled", "result": {"status": "cancelled"}},
        ))

        def coordinator_tick(_seconds):
            resident_restart_domain.resume_resident_session_restart(first_step)
            queued["first"] = False

        monkeypatch.setattr("time.sleep", coordinator_tick)

        second = resident_domain.restart_resident_session(
            record["resident_id"], "alice",
            {"target_provider": "codex", "target_account": "one", "context_strategy": "fresh"},
        )

        assert second["status"] == "accepted"
        assert second["restart_id"] != first["restart_id"]
        assert second["intent"]["context_strategy"] == "fresh"
        assert cancelled == [first["execution_id"]]
        ended = [value["session_restart"] for value in saved
                 if (value.get("session_restart") or {}).get("restart_id") == first["restart_id"]]
        assert ended[-1]["status"] == "cancelled"
        assert ended[-1]["cancelled_reason"] == "superseded"
        assert record["session_restart"]["restart_id"] == second["restart_id"]
        assert operations[-1]["step"]["state"]["restart_id"] == second["restart_id"]


def test_the_same_restart_repeated_returns_the_unfinished_one(monkeypatch, isolated_portal_case) -> None:
    """参数相同的 RestartSession 返回尚未完成的同一次重启

    未完成的重启与新调用参数相同时返回原重启，不新建操作。

    :importance: P2
    """
    with isolated_portal_case():
        record, operations, _saved = _restart_domain(monkeypatch)
        body = {"target_provider": "codex", "target_account": "one", "context_strategy": "compact"}
        first = resident_domain.restart_resident_session(record["resident_id"], "alice", dict(body))
        monkeypatch.setattr(
            resident_record_domain, "lifecycle_operations",
            lambda: SimpleNamespace(pending=lambda *_: True, enqueue=lambda *_: pytest.fail("no new operation")),
        )
        again = resident_domain.restart_resident_session(record["resident_id"], "alice", dict(body))
        assert again["restart_id"] == first["restart_id"]
        assert len(operations) == 1
