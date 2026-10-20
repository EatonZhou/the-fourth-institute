""":design: decision_c524c775e0be"""
from __future__ import annotations

import json

import pytest

from management_portal.execution import event_projection as projection


EXECUTION_ID = "execution-" + "a" * 24


def _record(index: int, *, kind: str = "update", phase: str = "commentary") -> bytes:
    return (json.dumps({
        "type": "runtime_activity",
        "created_at": "2026-09-07T09:00:00Z",
        "activity": {
            "id": f"activity-{index}", "execution_id": EXECUTION_ID,
            "kind": kind, "title": kind, "status": "completed",
            "content": f"message-{index}", "message_phase": phase,
        },
    }, ensure_ascii=False) + "\n").encode()


@pytest.fixture
def activity_path(tmp_path, monkeypatch):
    path = tmp_path / f"{EXECUTION_ID}.jsonl"
    monkeypatch.setattr(projection, "execution_activity_path", lambda _id: path)
    return path


def test_incremental_projection_reads_only_appended_records(activity_path, monkeypatch):
    """增量投影确认后只解析新追加的活动记录

    :design-point:"""
    activity_path.write_bytes(b"".join(_record(i) for i in range(2000)))
    read_lines = []
    original = projection._parse_json_object

    def counted(value):
        if isinstance(value, str):
            read_lines.append(value)
        return original(value)

    monkeypatch.setattr(projection, "_parse_json_object", counted)
    reader = projection.ExecutionActivityProjection(EXECUTION_ID)
    first = reader.read()
    assert len(first["outputs"]) == 2000
    assert len(read_lines) == 2000
    reader.acknowledge()
    read_lines.clear()
    for _ in range(50):
        assert reader.read()["outputs"] == []
        reader.acknowledge()
    assert read_lines == []
    with activity_path.open("ab") as stream:
        stream.write(_record(2000))
    assert [m["text"] for m in reader.read()["outputs"]] == ["message-2000"]
    assert len(read_lines) == 1


def test_incremental_projection_retries_unacknowledged_batch(activity_path):
    """未确认的投影批次再次读取时原样返回，新读者从头重建全部消息

    :design-point:"""
    activity_path.write_bytes(_record(1))
    reader = projection.ExecutionActivityProjection(EXECUTION_ID)
    pending = reader.read()
    with activity_path.open("ab") as stream:
        stream.write(_record(2))
    assert reader.read() == pending
    reader.acknowledge()
    assert [m["text"] for m in reader.read()["outputs"]] == ["message-2"]
    reader.acknowledge()
    assert reader.read()["outputs"] == []
    # A new waiter reconstructs messages even if Scheduler events were lost.
    rebuilt = projection.ExecutionActivityProjection(EXECUTION_ID)
    assert [m["text"] for m in rebuilt.read()["outputs"]] == ["message-1", "message-2"]


def test_incremental_projection_waits_for_complete_lines(activity_path):
    """增量投影跳过未写完的行，等整行写完后再读出

    :design-point:"""
    record = _record(1)
    activity_path.write_bytes(record[:-4])
    reader = projection.ExecutionActivityProjection(EXECUTION_ID)
    assert reader.read()["outputs"] == []
    reader.acknowledge()
    with activity_path.open("ab") as stream:
        stream.write(record[-4:])
    assert len(reader.read()["outputs"]) == 1
    reader.acknowledge()
    assert reader.read()["outputs"] == []


@pytest.mark.parametrize("change", ["truncate", "replace"])
def test_incremental_projection_rebuilds_changed_files(activity_path, change):
    """活动文件被截断或替换时增量投影按新文件整体重建

    :design-point:"""
    activity_path.write_bytes(_record(1) + _record(2))
    reader = projection.ExecutionActivityProjection(EXECUTION_ID)
    reader.read()
    reader.acknowledge()
    if change == "truncate":
        activity_path.write_bytes(_record(3))
    else:
        replacement = activity_path.with_suffix(".replacement")
        replacement.write_bytes(_record(3) + _record(4))
        replacement.replace(activity_path)
    expected = projection.execution_projection_from_path(activity_path)
    assert reader.read() == expected


def test_incremental_projection_bounds_activity_and_preserves_message_identity(activity_path):
    """增量投影的运行活动条数受上限约束，分批读到的消息与整体投影一致

    :design-point:"""
    activity_path.write_bytes(_record(1) + _record(2, kind="tool"))
    reader = projection.ExecutionActivityProjection(EXECUTION_ID, runtime_limit=3)
    first = reader.read()
    reader.acknowledge()
    with activity_path.open("ab") as stream:
        for index in range(3, 10):
            stream.write(_record(index, kind="tool"))
        stream.write(_record(10, phase="final_answer"))
    following = reader.read()
    full = projection.execution_projection_from_path(activity_path, runtime_limit=3)
    assert first["outputs"] + following["outputs"] == full["outputs"]
    assert following["runtime_details"] == full["runtime_details"]
    assert len(following["runtime_details"]) == 3
    assert following["outputs"][0]["phase"] == "final_answer"


def test_incremental_projection_missing_file_can_appear_later(activity_path):
    """活动文件起初不存在时增量投影返回空，文件出现后读到新记录

    :design-point:"""
    reader = projection.ExecutionActivityProjection(EXECUTION_ID)
    assert reader.read()["outputs"] == []
    reader.acknowledge()
    activity_path.write_bytes(_record(1))
    assert len(reader.read()["outputs"]) == 1


def test_activity_append_reuses_sequence_state_and_recovers_it_after_restart(activity_path, monkeypatch):
    """追加执行活动时沿用内存中的序号去重而不重读文件，重启后从文件恢复序号

    :design-point:"""
    def event(sequence):
        return {'sequence': sequence, 'execution_id': EXECUTION_ID, 'type': 'service_completed',
                'created_at': f'2026-10-11T00:00:{sequence:02d}Z', 'details': {}}

    assert projection.append_execution_activity(activity_path, [event(1)]) == 1
    loads = projection.json.loads
    with monkeypatch.context() as patch:
        patch.setattr(projection.json, 'loads', lambda *args, **kwargs: pytest.fail('append reread prior activity'))
        assert projection.append_execution_activity(activity_path, [event(2)]) == 1
        assert projection.append_execution_activity(activity_path, [event(1), event(2)]) == 0
    projection._ACTIVITY_WRITES.pop(activity_path)
    assert projection.append_execution_activity(activity_path, [event(2), event(3)]) == 1
    assert [loads(line)['sequence'] for line in activity_path.read_text().splitlines()] == [1, 2, 3]


def test_activity_pruning_reaches_expired_files_beyond_old_batch_limit(activity_path):
    """清理执行活动时删除全部过期文件，不受旧的单批数量上限限制

    :design-point:"""
    import os

    activity_path.parent.mkdir(parents=True, exist_ok=True)
    for index in range(160):
        path = activity_path.parent / f'execution-{index:024x}.jsonl'
        path.write_text('{}\n')
        os.utime(path, (100, 100))
    assert projection.prune_execution_activity(older_than_epoch=150, time_budget_seconds=2) == 160
    assert not list(activity_path.parent.glob('execution-*.jsonl'))


def _subagent(method: str, status: str, steps: int, *, end_id: str = "live", at: int = 0) -> dict:
    from management_common.provider_subagent_runs import run_item

    run = {"tool_use_id": "toolu_a", "description": "Survey", "status": status, "end_id": end_id,
           "items": [{"kind": "tool", "tool_name": "Bash", "input": {"command": f"step {n}"}} for n in range(steps)]}
    return {"method": method, "execution_id": EXECUTION_ID, "timestamp": f"2026-10-09T15:00:{at:02d}Z",
            "params": {"turnId": "t", "item": run_item(run, turn_id="t", live=method == "subagent/progress")}}


def test_a_running_subagent_is_one_live_activity_until_its_card_is_a_message(activity_path):
    """运行中的子 agent 是一条实时活动，直到卡片成为消息

    subagent/progress 事件投影成每个子 agent 一条 kind=subagent 的运行中活动，并带上卡片；新进度把它移到末尾，较新的活动不会把仍在运行的卡片挤出显示窗口；结果卡片成为消息时，这条活动以 completed 结束且不再带卡片。

    :importance: P1
    :design: decision_b1ab45c4b3ec
    :design-point:
    """
    # The Execution stores each event's activity; the reader rebuilds it.
    projection.append_execution_activity(activity_path, [_subagent("subagent/progress", "running", 0, at=1)])
    projection.append_execution_activity(activity_path, [_subagent("subagent/progress", "running", 1, at=2)])
    reader = projection.ExecutionActivityProjection(EXECUTION_ID, runtime_limit=3)
    first = reader.read()
    live, = first["runtime_details"]
    assert (live["kind"], live["status"], live["content"]) == ("subagent", "running", "Survey")
    assert live["subagent"]["steps"] == [{"kind": "tool", "tool_name": "Bash", "text": "step 0", "error": False}]
    reader.acknowledge()
    # Newer activity does not push the running card out of view.
    with activity_path.open("ab") as stream:
        stream.write(b"".join(_record(i, kind="tool") for i in range(5)))
    projection.append_execution_activity(activity_path, [_subagent("subagent/progress", "running", 2, at=3)])
    with activity_path.open("ab") as stream:
        stream.write(b"".join(_record(i, kind="tool") for i in range(5, 10)))
    details = reader.read()["runtime_details"]
    assert [detail["kind"] for detail in details] == ["subagent", "tool", "tool", "tool"]
    assert len(details[0]["subagent"]["steps"]) == 2
    reader.acknowledge()
    # The finished card is a message; the run's activity ends with it.
    projection.append_execution_activity(activity_path, [_subagent("item/completed", "completed", 2, end_id="r1", at=4)])
    second = reader.read()
    assert [output["content_parts"][0]["type"] for output in second["outputs"]] == ["subagent_run"]
    ended = [detail for detail in second["runtime_details"] if detail["kind"] == "subagent"]
    assert [(detail["status"], "subagent" in detail) for detail in ended] == [("completed", False)]
    # A reader that starts over sees the same.
    fresh = projection.execution_projection_from_execution(EXECUTION_ID, runtime_limit=3)
    assert [(detail["kind"], detail["status"]) for detail in fresh["runtime_details"] if detail["kind"] == "subagent"] == [("subagent", "completed")]
