"""本轮修改 for Claude, Kimi and DSH, rebuilt from their own Edit/Write tool calls.

:module: spec.design-governance
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest import mock

from management_common.provider_turn_artifacts import (
    TOOL_CALL_DIFF_NOTE, ToolEditArtifacts, content_part, review_target, tool_edit_diff,
)
from management_portal.execution import backends
from management_portal.execution.native_tool_events import tool_completed
from management_portal.society_service import resident_conversation_stream, resident_execution_domain
from tests.cases.test_resident_execution_protocol import resident_record  # noqa: F401 - fixture


def _edits(*calls: tuple[str, dict, bool]) -> ToolEditArtifacts:
    artifacts = ToolEditArtifacts("/work")
    for tool, arguments, failed in calls:
        artifacts.observe(tool_completed("call", tool, arguments, "ok", failed=failed))
    return artifacts


def test_a_turns_file_tool_calls_become_one_diff_per_file():
    """本轮文件工具调用按文件合并为 diff，跳过失败调用和 Shell 写入"""
    artifacts = _edits(
        ("Edit", {"file_path": "/work/a.py", "old_string": "x = 1\ny = 2", "new_string": "x = 1\ny = 3"}, False),
        ("write", {"file_path": "notes/新.md", "content": "hello\nworld"}, False),
        ("Write", {"path": "log.txt", "content": "more\n", "mode": "append"}, False),
        ("Edit", {"file_path": "/work/a.py", "old_string": "q", "new_string": "r", "replace_all": True}, False),
        ("Edit", {"file_path": "/work/failed.py", "old_string": "q", "new_string": "r"}, True),
        ("Bash", {"command": "echo hi > shell.txt"}, False),
    )
    event = artifacts.finish(thread_id="session", turn_id="turn-1")
    artifact = event["item"]["artifacts"]
    assert event["item"]["type"] == "turnArtifacts" and artifact["diff_source"] == "tool_calls"
    diff = artifact["diff"]
    # Edits to one file share its block, in call order; the failed call and the shell write are absent.
    assert diff.count("diff --git") == 3 and "failed.py" not in diff and "shell.txt" not in diff
    assert diff.startswith("diff --git a//work/a.py b//work/a.py\n--- a//work/a.py\n+++ b//work/a.py\n"
                           "@@ -1,2 +1,2 @@ Edit（行号从被替换片段起算）\n x = 1\n-y = 2\n+y = 3\n"
                           "@@ -1 +1 @@ Edit（行号从被替换片段起算，替换全部匹配处）\n-q\n+r\n")
    # Relative paths resolve against the turn's directory; non-ASCII paths are quoted as git quotes them.
    assert '+++ "b//work/notes/\\346\\226\\260.md"\n@@ -0,0 +1,2 @@ Write 写入全文（写入前的内容未记录）\n+hello\n+world\n' \
        "\\ No newline at end of file\n" in diff
    assert diff.endswith("+++ b//work/log.txt\n@@ -0,0 +1,1 @@ Write 追加到文件末尾\n+more\n")
    assert artifacts.finish(thread_id="session", turn_id="turn-2") is None


def test_a_diff_over_the_display_limit_is_marked_truncated():
    """超过显示上限的本轮修改 diff 被截断并标记"""
    diff, truncated = tool_edit_diff([{"path": "/big", "kind": "write", "new": "x\n" * 200000}])
    assert truncated and len(diff) == 262144


def test_the_saved_card_tells_a_review_how_the_diff_was_built():
    """保存的修改卡片注明 diff 来自工具调用，审查说明附带该注释和 diff"""
    event = _edits(("Edit", {"file_path": "/work/a.py", "old_string": "a", "new_string": "b"}, False)).finish(
        thread_id="session", turn_id="turn-1")
    part = content_part(event["item"], "execution-" + "a" * 24)
    assert part["type"] == "native_turn" and part["diff_source"] == "tool_calls" and "-a\n+b\n" in part["diff"]
    instructions = review_target({"created_by_agent_id": "resident_aaaaaaaaaaaa", "content": part},
                                 "resident_aaaaaaaaaaaa")["instructions"]
    assert TOOL_CALL_DIFF_NOTE in instructions and instructions.endswith(part["diff"])


EDIT_ARGUMENTS = {"file_path": "/work/a.py", "old_string": "old", "new_string": "new"}


def _native_socket_turn(backend_class, events: list[dict]) -> list[dict]:
    observed: list[dict] = []
    context = mock.Mock()
    context.observe_provider_event.side_effect = lambda event, **_kwargs: observed.append(event)

    def run_turn(**kwargs):
        for event in events:
            kwargs["event_callback"](event)
        return SimpleNamespace(response="done", session_id="session", turn_id="turn-1", usage={})

    invocation = SimpleNamespace(
        request=SimpleNamespace(runtime_options={"provider_profile": "deepseek_primary"}, operation="turn",
                                input_media=(), execution_id="execution-native", cwd="/work",
                                session=SimpleNamespace(binding_id="resident:resident_native"), permission_profile=""),
        session=SimpleNamespace(backend_route_id="deepseek_primary", backend_session_id="session"),
        user_prompt="edit", system_context="", attach_system_context=False,
    )
    client = SimpleNamespace(run_turn=run_turn, session_status=lambda *a: {}, close_session=lambda *a: {})
    backend = backend_class()
    backend.client_factory = lambda: client  # type: ignore[method-assign]
    with mock.patch("management_portal.execution.native_harness_tools.prepare_tools", return_value=([], None)), \
            mock.patch.object(backend_class, "record_provider_compaction"):
        backend.execute(invocation, context)
    return observed


def _turn_end(observed: list[dict]) -> list[str]:
    return [event["params"]["item"]["type"] if event.get("method") == "item/completed" else event.get("method")
            for event in observed if event.get("method") in {"item/completed", "turn/completed"}]


def test_claude_and_dsh_turns_record_their_edits_before_the_turn_completes():
    """Claude 和 DSH 轮次在轮次完成事件之前记录本轮文件修改"""
    completed = {"method": "turn/completed", "params": {"threadId": "session", "turn": {"id": "turn-1", "status": "completed"}}}
    claude = _native_socket_turn(backends.ClaudeBackend, [
        {"method": "item/started", "params": {"item": {"id": "t1", "type": "toolCall", "name": "Edit", "input": EDIT_ARGUMENTS}}},
        {"method": "item/completed", "params": {"item": {"id": "t1", "type": "toolCall", "output": "ok", "status": "completed"}}},
        completed,
    ])
    dsh = _native_socket_turn(backends.DshBackend, [
        {"method": "item/started", "params": {"item": {"id": "t1", "type": "dynamicToolCall", "tool": "edit",
                                                        "arguments": '{"file_path": "a.py", "old_string": "old", "new_string": "new"}'}}},
        {"method": "item/completed", "params": {"item": {"id": "t1", "type": "dynamicToolCall", "tool": "edit",
                                                          "arguments": "{}", "status": "completed"}}},
        completed,
    ])
    for observed in (claude, dsh):
        assert _turn_end(observed)[-2:] == ["turnArtifacts", "turn/completed"]
        artifact = next(event for event in observed if event.get("method") == "item/completed"
                        and event["params"]["item"]["type"] == "turnArtifacts")["params"]["item"]["artifacts"]
        assert artifact["turn_id"] == "turn-1" and "+++ b//work/a.py\n" in artifact["diff"] and "-old\n+new\n" in artifact["diff"]


def test_a_turn_without_file_edits_has_no_card():
    """没有文件修改的轮次不生成本轮修改卡片"""
    observed = _native_socket_turn(backends.ClaudeBackend, [
        {"method": "item/started", "params": {"item": {"id": "t1", "type": "toolCall", "name": "Bash", "input": {"command": "ls"}}}},
        {"method": "item/completed", "params": {"item": {"id": "t1", "type": "toolCall", "output": "a.py", "status": "completed"}}},
        {"method": "turn/completed", "params": {"threadId": "session", "turn": {"id": "turn-1", "status": "completed"}}},
    ])
    assert "turnArtifacts" not in _turn_end(observed)


def test_a_kimi_turn_records_its_edits_before_the_turn_completes():
    """Kimi 轮次在轮次完成事件之前记录本轮文件修改"""
    observed: list[dict] = []
    context = mock.Mock()
    context.is_cancelled.return_value = False
    context.observe_provider_event.side_effect = lambda event, **_kwargs: observed.append(event)

    def run_turn(**kwargs):
        for event in (
            {"method": "turn/started", "params": {"turnId": "p1"}},
            {"method": "item/started", "params": {"turnId": "p1", "item": {"id": "t1", "type": "toolCall", "name": "Write",
                                                                           "input": {"path": "out.txt", "content": "x\n"}}}},
            {"method": "item/completed", "params": {"turnId": "p1", "item": {"id": "t1", "type": "toolCall", "output": "ok",
                                                                             "status": "completed", "error": False}}},
            {"method": "turn/completed", "params": {"turnId": "p1", "turn": {"id": "p1", "status": "completed"}}},
        ):
            kwargs["event_callback"](event)
        return SimpleNamespace(response="done", turn_id="p1", session_id="session_kimi", usage={})

    invocation = SimpleNamespace(
        request=SimpleNamespace(session=SimpleNamespace(binding_id="resident:test"), runtime_options={}, permission_profile="", cwd="/work", operation="turn",
                                execution_id="execution-kimi"),
        session=SimpleNamespace(backend_route_id="kimi_primary", backend_session_id="session_kimi"), recovery_checkpoint={},
        system_context="", user_prompt="write", attach_system_context=False, is_new_backend_session=False,
    )
    with mock.patch.object(backends, "shared_kimi_client", return_value=SimpleNamespace(run_turn=run_turn)), \
            mock.patch.object(backends.KimiBackend, "record_provider_compaction"):
        backends.KimiBackend().execute(invocation, context)  # type: ignore[arg-type]
    assert _turn_end(observed)[-2:] == ["turnArtifacts", "turn/completed"]
    artifact = observed[-2]["params"]["item"]["artifacts"]
    assert artifact["turn_id"] == "p1" and "+++ b//work/out.txt\n@@ -0,0 +1,1 @@ Write 写入全文" in artifact["diff"]


def test_a_provider_without_native_review_reviews_the_snapshot_in_an_ordinary_turn(resident_record, monkeypatch):  # noqa: F811
    """不支持原生审查的 Provider 在普通轮次中按提示审查修改快照"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    event = _edits(("Edit", EDIT_ARGUMENTS, False)).finish(thread_id="session", turn_id="turn-1")
    part = content_part(event["item"], "execution-" + "b" * 24)
    repository.agent_data.put_operation_record(segment_id=part["segment_id"], segment_type="model", content=part,
                                               created_by_agent_id=resident_id, content_type="application/json",
                                               execution_id=part["execution_id"], created_by_actor_ref=resident_id,
                                               performed_by_actor_ref=resident_id)
    repository.append_message(resident_id, {"message_id": "msg_tool_review", "role": "user", "content_parts": [
        {"type": "turn_review", "segment_id": part["segment_id"]},
    ]})
    provider = resident_execution_domain._resident_execution_request_backend(resident_record)
    providers = resident_execution_domain.AGENT_PROVIDERS
    monkeypatch.setitem(providers, provider, dataclasses.replace(providers[provider], native_review=False))
    job = {"actor": "CASE-A", "job_id": "job_tool_review", "user_message_id": "msg_tool_review", "message": "审查指定轮次的修改快照"}
    request = resident_execution_domain._build_resident_execution_request(resident_record, job)
    assert "review_target" not in request.runtime_options
    target = review_target(repository.agent_data.operation_record(part["segment_id"]), resident_id)
    assert request.prompt.startswith("审查指定轮次的修改快照") and request.prompt.endswith(target["instructions"])
