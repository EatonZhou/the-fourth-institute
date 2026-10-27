"""The Resident state scope covers Job writes, not the reads around them.

:design: decision_d918ebebc062
"""
from contextlib import contextmanager
from unittest import mock

import pytest

from management_portal.execution import event_projection as execution_events
from management_portal.society_service import (
    invariant_locks, resident_catalog, resident_execution_domain, resident_execution_work,
    resident_intake_domain,
)
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401

EXECUTION_ID = "execution-" + "d" * 24


@pytest.fixture
def bound_job(resident_record):
    resident_execution_work.write(resident_record, {
        "execution_id": EXECUTION_ID, "actor": "CASE-A", "user_message_id": "message-current",
        "submitted_at": "2026-10-16T00:00:00Z",
    })
    return resident_record


def held(resident_id):
    return invariant_locks._depths().get(f"resident:{resident_id}", 0) > 0


def test_execution_polling_takes_no_resident_state_scope(bound_job, monkeypatch):
    """执行轮询不进入 Resident 锁

    检查执行是否仍为当前、是否已请求停止、Send now 回执，读取和推进事件游标，以及没有变化的运行中快照，都不取得 Resident 锁。

    :design-point:
    """
    resident_id = bound_job["resident_id"]
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *_args, **_kwargs: {"outputs": [], "runtime_details": []})

    @contextmanager
    def forbidden(*_args, **_kwargs):
        pytest.fail("Execution polling waited for the Resident state scope")
        yield

    monkeypatch.setattr(invariant_locks, "resident_lock", forbidden)
    assert resident_execution_domain._resident_execution_still_current(resident_id, EXECUTION_ID)
    assert not resident_execution_domain._resident_execution_cancellation_requested(resident_id, EXECUTION_ID)
    assert resident_intake_domain._reconcile_queued_turn_inputs(resident_id, EXECUTION_ID)
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id, EXECUTION_ID, 7)
    resident_execution_domain._advance_resident_execution_event_cursor(resident_id, EXECUTION_ID, 3)
    assert resident_execution_domain._resident_execution_event_cursor(resident_id, EXECUTION_ID) == 7
    resident_execution_domain._project_resident_execution_snapshot(resident_id, EXECUTION_ID, {"status": "running"})


def test_a_terminal_snapshot_still_commits_under_the_state_scope(bound_job, monkeypatch):
    """终态快照仍在 Resident 锁内提交

    快照带来终态时，Job 写入在 Resident 锁内完成并记录终态。

    :design-point:
    """
    resident_id = bound_job["resident_id"]
    monkeypatch.setattr(execution_events, "execution_projection_from_execution",
                        lambda *_args, **_kwargs: {"outputs": [], "runtime_details": []})
    writes = []
    original = resident_execution_work.write
    monkeypatch.setattr(resident_execution_work, "write",
                        lambda record, job, **kwargs: (writes.append(held(resident_id)), original(record, job, **kwargs)))
    resident_execution_domain._project_resident_execution_snapshot(resident_id, EXECUTION_ID, {"status": "completed"})
    assert writes == [True]
    assert resident_execution_work.read(bound_job)["execution_terminal_status"] == "completed"


def test_display_projection_reads_no_resident_record_under_the_state_scope(bound_job, monkeypatch):
    """回复投影在 Resident 锁内不读取完整 Resident 记录

    写入一条执行输出时，授权与确认在锁内只读取执行控制态；写入完成后不留下投影意图。

    :design-point:
    """
    resident_id = bound_job["resident_id"]
    catalog = resident_catalog.get_catalog()
    reads = []
    original = catalog.get
    monkeypatch.setattr(catalog, "get", lambda identifier, **kwargs: (
        reads.append(held(resident_id)), original(identifier, **kwargs))[1])
    output = {"output_id": "msg_" + "e" * 32, "text": "intermediate", "phase": "commentary"}
    messages = resident_execution_domain._persist_execution_display_messages(resident_id, EXECUTION_ID, [output])
    assert [message["message_id"] for message in messages] == [output["output_id"]]
    assert True not in reads
    assert not resident_execution_work.read(bound_job).get(resident_execution_work.CONVERSATION_PROJECTION_INTENTS)


def test_resume_with_an_attached_waiter_takes_no_resident_state_scope(bound_job, monkeypatch):
    """已有完成等待者时恢复不进入 Resident 锁

    Resident 已有存活的完成等待者时，恢复调用在锁外判断无需挂接并直接返回。

    :design-point:
    """
    resident_execution_work.track_current_thread(bound_job)
    try:
        @contextmanager
        def forbidden(*_args, **_kwargs):
            pytest.fail("A resume with an attached waiter waited for the Resident state scope")
            yield

        monkeypatch.setattr(invariant_locks, "resident_lock", forbidden)
        started = resident_execution_domain.resume_resident_execution(bound_job["resident_id"])
    finally:
        resident_execution_work.finish_current_thread(bound_job)
    assert started["residents"] == 0


def test_steward_quota_lookup_runs_before_the_state_scope(resident_record, monkeypatch):
    """Steward 额度检查在进入 Resident 锁之前完成

    恢复执行时，可能访问 Execution 服务的 Steward 额度检查在锁外运行，锁内只重新核对并挂接等待者。

    :design-point:
    """
    from management_portal.society_service import family_quota
    resident_id = resident_record["resident_id"]
    resident_execution_work.write(resident_record, {"execution_id": EXECUTION_ID, "actor": "CASE-A", "queue": []})
    checks = []
    monkeypatch.setattr(family_quota, "ensure_role_reserve",
                        lambda record, *args: checks.append(held(resident_id)) or False)
    monkeypatch.setattr(resident_intake_domain, "_start_resident_continuation", mock.Mock())
    started = resident_execution_domain.resume_resident_execution(resident_id)
    assert started["residents"] == 1
    assert checks == [False]
