"""What a subagent card does: open the run's whole trace (GetSubagentRun) and stop that one run (CancelSubagent).

:module: resident.conversation
:importance: P1
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from management_portal.society_service import execution_client, resident_access, resident_conversation_stream, resident_protocol
from management_portal.society_service import resident_conversation_records
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401
from tests.cases.test_subagent_trace import _turn


def _child_event(projector, request, method: str, item: dict, at: str) -> None:
    projector.observe(
        provider="claude", backend_route_id="one", backend_session_id="session-1", execution_id=request.execution_id,
        event={"method": "subagent/event", "timestamp": at, "params": {
            "parentToolUseId": "toolu_agent", "traceKey": "toolu_agent",
            "event": {"method": method, "params": {"turnId": "turn-1", "item": item}},
        }},
    )


def test_the_whole_run_comes_from_its_own_trace_and_reads_on_from_any_step(resident_record) -> None:
    """GetSubagentRun 从子 agent 自己的轨迹读取全部步骤并可从任一步续读

    读取子 agent 轨迹中的工具调用输入输出、失败标记、改动文件和完整消息；每步带 index，按 offset 续读返回该步及之后的步骤；未知 trace_ref 报不存在。
    """
    resident_id = resident_record["resident_id"]
    _store, projector, request, _session = _turn(resident_id)
    for method, item, at in [
        ("item/started", {"id": "toolu_ls", "type": "toolCall", "name": "Bash", "input": {"command": "ls tests"}}, "2026-10-11T00:00:01.000Z"),
        ("item/completed", {"id": "toolu_ls", "type": "toolCall", "output": "a.py b.py", "status": "completed", "error": False}, "2026-10-11T00:00:02.000Z"),
        ("item/started", {"id": "toolu_ed", "type": "toolCall", "name": "Edit", "input": {"file_path": "/repo/a.py", "old_string": "x", "new_string": "y"}}, "2026-10-11T00:00:03.000Z"),
        ("item/completed", {"id": "toolu_ed", "type": "toolCall", "output": "no match", "status": "failed", "error": True}, "2026-10-11T00:00:04.000Z"),
        ("item/completed", {"id": "m1", "type": "agentMessage", "phase": "commentary", "text": "found 2 files " * 600}, "2026-10-11T00:00:05.000Z"),
    ]:
        _child_event(projector, request, method, item, at)

    run = resident_protocol.get_subagent_run(resident_id, "CASE-A", "toolu_agent")
    tools = [step for step in run["steps"] if step["kind"] == "tool"]
    assert [(step["tool_name"], step["error"]) for step in tools] == [("Bash", False), ("Edit", True)]
    assert tools[0]["input"] == {"command": "ls tests"} and tools[0]["output"] == "a.py b.py"
    assert tools[1]["files"] == ["/repo/a.py"]
    # A bare failure flag says only that it failed; what went wrong reads in its output.
    assert tools[1]["output"] == "no match" and "error_text" not in tools[1]
    said = [step for step in run["steps"] if step["kind"] == "message"]
    # What it said reads whole, well past a tool output's length.
    assert said[-1]["text"] == ("found 2 files " * 600).strip() and not said[-1]["text_truncated"]
    indexes = [step["index"] for step in run["steps"]]
    assert indexes == sorted(indexes) and run["total"] > indexes[-1]
    # A reader following the run reads on from a step and gets that step and the ones after.
    later = resident_protocol.get_subagent_run(resident_id, "CASE-A", "toolu_agent", offset=tools[1]["index"])
    assert [step["index"] for step in later["steps"]] == [index for index in indexes if index >= tools[1]["index"]]
    with pytest.raises(KeyError):
        resident_protocol.get_subagent_run(resident_id, "CASE-A", "toolu_unknown")


def test_one_subagent_stops_through_its_execution_for_whoever_may_stop_the_turn(resident_record, monkeypatch) -> None:
    """CancelSubagent 只对能停止本轮的用户生效并经 Execution 输入控制送达

    无管理权限拒绝；Execution 不属于该 Resident 或不存在时报不存在；送达的控制为单个 subagent_stop，每次请求使用新的 control_id，Provider 失败原因原样返回。
    """
    resident_id = resident_record["resident_id"]
    service = Mock()
    service.append_input.return_value = {"status": "delivered"}
    monkeypatch.setattr(execution_client, "service", lambda: service)
    monkeypatch.setattr(resident_access, "can_manage_resident", lambda _record, actor, **_kwargs: actor == "CASE-A")
    store = resident_conversation_stream.get_repository().agent_data
    owners = {"execution-own": resident_id, "execution-other": "resident_bbbbbbbbbbbb"}
    monkeypatch.setattr(store, "execution", lambda execution_id: {"agent_id": owners[execution_id]} if execution_id in owners else None)

    with pytest.raises(KeyError, match="resident not found"):
        resident_protocol.stop_subagent(resident_id, "guest", "execution-own", "toolu_agent")
    for execution_id in ("execution-other", "execution-missing"):
        with pytest.raises(KeyError):
            resident_protocol.stop_subagent(resident_id, "CASE-A", execution_id, "toolu_agent")
    service.append_input.assert_not_called()

    first = resident_protocol.stop_subagent(resident_id, "CASE-A", "execution-own", "toolu_agent")
    assert first["status"] == "delivered"
    call = service.append_input.call_args
    assert call.args == ("execution-own",)
    assert call.kwargs["input"] == [{"type": "subagent_stop", "tool_use_id": "toolu_agent"}]
    assert call.kwargs["control_id"] == first["control_id"]
    # A stop that came before the Provider knew the run may be asked again: each is its own control.
    service.append_input.return_value = {"status": "failed", "error": "unknown_subagent"}
    again = resident_protocol.stop_subagent(resident_id, "CASE-A", "execution-own", "toolu_agent")
    assert again == {"control_id": again["control_id"], "status": "failed", "error": "unknown_subagent"}
    assert again["control_id"] != first["control_id"]


def test_a_running_subagents_activity_names_its_execution() -> None:
    """运行中子 agent 的 Activity 带所属 Execution

    子 agent 运行中卡片的 protocol Activity 带 execution_id，供 Chat 停止该子 agent；其他 Activity 不带该字段。

    :importance: P2
    """
    activity = resident_conversation_records.protocol_activity({
        "activity_id": "runtime-update:execution-live:subagent:toolu_agent", "resident_id": "resident_aaaaaaaaaaaa",
        "kind": "subagent", "status": "running", "execution_id": "execution-live",
        "subagent": {"tool_use_id": "toolu_agent", "description": "Survey"},
    })
    assert activity["execution_id"] == "execution-live" and activity["subagent"]["tool_use_id"] == "toolu_agent"
    other = resident_conversation_records.protocol_activity({
        "activity_id": "a1", "resident_id": "resident_aaaaaaaaaaaa", "kind": "tool", "status": "running", "execution_id": "execution-live",
    })
    assert "execution_id" not in other


def test_a_codex_subagents_commands_read_with_their_output_and_its_file_changes_name_their_files(resident_record) -> None:
    """Codex 子 agent 的命令步骤带输出、文件改动步骤带改动的文件

    Codex 把命令输出放在 aggregatedOutput、把文件改动的路径放在 changes[].path；子 agent 详情窗口读取的轨迹步骤据此给出命令输出和改动的文件。
    """
    resident_id = resident_record["resident_id"]
    _store, projector, request, _session = _turn(resident_id)
    for item, at in [
        ({"id": "exec-1", "type": "commandExecution", "command": "/bin/bash -lc 'python3 --version'", "cwd": "/w",
          "status": "completed", "exitCode": 0, "aggregatedOutput": "Python 3.12.3\n"}, "2026-10-11T00:00:01.000Z"),
        ({"id": "patch-1", "type": "fileChange", "status": "completed",
          "changes": [{"path": "/w/notes/trial.md", "kind": {"type": "add"}, "diff": "hello\n"}]}, "2026-10-11T00:00:02.000Z"),
    ]:
        _child_event(projector, request, "item/completed", item, at)

    tools = [step for step in resident_protocol.get_subagent_run(resident_id, "CASE-A", "toolu_agent")["steps"] if step["kind"] == "tool"]
    # Codex keeps a command's output in ``aggregatedOutput`` and a file change's files under ``changes``.
    assert tools[0]["output"].strip() == "Python 3.12.3" and tools[0]["files"] == []
    assert tools[1]["files"] == ["/w/notes/trial.md"]
