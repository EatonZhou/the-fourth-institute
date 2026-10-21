"""A waiter projects Provider activity that changes neither events nor the row.

A TAU run asked a Resident to run ``sleep 30`` and saw "Command started" in
the Chat only when the command had finished.  The activity was in the shared
activity log 7 seconds after the request, but the Resident's waiter projected
activity only when a wait returned events or a changed snapshot; the Worker
announced the activity to another ExecutionService process, and a quiet tool
changes nothing else.  Growth of the activity log now counts as a change.

:module: resident.conversation
"""
from __future__ import annotations

from unittest import mock

from management_portal.execution import service as execution_service
from management_portal.execution.service import ExecutionService

EXECUTION_ID = "execution-" + "a" * 24


def _running():
    return {"execution_id": EXECUTION_ID, "status": "running",
            "scheduler": {"updated_at": "2026-10-02T10:45:46.566Z"}, "events": [], "result": {}}


def test_a_growing_activity_log_is_projected_without_a_row_change(tmp_path, monkeypatch):
    """Resident 等待器在执行行与事件都未变化时也投影活动日志中的新活动（安静工具的 Command started 及时可见）"""
    log = tmp_path / f"{EXECUTION_ID}.jsonl"
    log.write_text('{"type":"runtime_activity","activity":{"title":"开始执行"}}\n')
    monkeypatch.setattr(execution_service, "execution_activity_path", lambda execution_id: log)
    calls = []

    def request(method, payload, timeout=None):
        calls.append(method)
        if len(calls) == 3:  # the Provider starts a quiet command between two waits
            with log.open("a") as stream:
                stream.write('{"type":"runtime_activity","activity":{"title":"Command started"}}\n')
        if len(calls) == 5:
            return {**_running(), "status": "completed", "events": [{"sequence": 1, "type": "completed"}],
                    "result": {"execution_id": EXECUTION_ID, "status": "completed", "output": {"message": "done"}}}
        return _running()

    client = mock.Mock()
    client.request.side_effect = request
    projected = []

    ExecutionService(client=client).wait(
        EXECUTION_ID, actor="sample_admin",
        on_snapshot=lambda snapshot: projected.append((snapshot["status"], log.read_text().count("\n"))),
    )

    assert projected == [("running", 1), ("running", 2), ("completed", 2)]
