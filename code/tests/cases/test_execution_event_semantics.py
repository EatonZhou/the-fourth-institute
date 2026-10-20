from __future__ import annotations

import json
from pathlib import Path

from management_portal import portal_chat_workspace_projection as presentation
from management_portal.execution import event_projection as semantics


def completed_event(text: str) -> dict:
    return {
        "sequence": 7,
        "execution_id": "execution-" + "a" * 24,
        "type": "provider_event",
        "details": {
            "method": "item/completed",
            "params": {
                "turnId": "turn-current",
                "item": {
                    "id": "message-complete",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": text,
                },
            },
        },
    }


def test_completed_output_is_independent_of_display_budget_contracts(monkeypatch, subtests, isolated_portal_case):
    """执行输出和工具结果的语义内容保持完整，显示字数上限只在展示层截断"""
    with subtests.test(contract='test_completed_output_is_independent_of_display_budget'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            text = "完整内容\n" * 4000 + "END"
            event = completed_event(text)
            before = semantics.execution_outputs_from_events([event])
            monkeypatch.setattr(presentation, "RUNTIME_MESSAGE_CHARS", 16)
            after = semantics.execution_outputs_from_events([event])
            assert before == after
            assert after[0]["text"] == text
            shown = [presentation.runtime_detail(item) for item in semantics.runtime_details_from_objects([(7, event)], include_updates=True)]
            assert shown[0]["content"] != text
            assert "[content truncated for portal preview]" in shown[0]["content"]
    with subtests.test(contract='test_stream_semantics_keep_full_content_while_display_is_bounded'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            chunks = ["FIRST" * 20, "LAST" * 20]
            events = [
                (sequence, {
                    "method": "item/agentMessage/delta",
                    "params": {"itemId": "stream-message", "delta": chunk},
                })
                for sequence, chunk in enumerate(chunks, start=1)
            ]
            monkeypatch.setattr(presentation, "RUNTIME_MESSAGE_CHARS", 16)
            normalized = semantics.runtime_details_from_objects(events, include_updates=True)
            assert normalized[-1]["content"] == "".join(chunks)
            shown = [presentation.runtime_detail(item) for item in semantics.runtime_details_from_objects(events, include_updates=True)]
            assert shown[-1]["content"] == "".join(chunks)[-16:]
    with subtests.test(contract='test_chat_workspace_applies_display_limits_without_mutating_semantics'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            detail = {"kind": "update", "status": "completed", "content": "FULL" * 40}
            monkeypatch.setattr(presentation, "RUNTIME_MESSAGE_CHARS", 16)
            result = presentation.contract(
                workspace_key="test", surface="test", target={"kind": "resident", "id": "resident-test"},
                runtime_details=[detail],
            )
            assert detail["content"] == "FULL" * 40
            assert result["runtime"]["details"][0]["content"].startswith("FULL" * 4)
            assert "[content truncated for portal preview]" in result["runtime"]["details"][0]["content"]
    with subtests.test(contract='test_tool_output_semantics_preserve_body_and_failure_status'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            content = "Process exited with code 1\nOriginal token count: 400\n" + "EVIDENCE" * 100
            event = {"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": "tool-result", "output": content,
            }}
            monkeypatch.setattr(presentation, "RUNTIME_DETAIL_SUMMARY_CHARS", 24)
            detail = semantics.runtime_details_from_objects([(1, event)])[0]
            assert detail["status"] == "failed"
            assert detail["content"] == content
            assert len(presentation.runtime_detail(detail)["content"]) <= 27


def test_activity_round_trip_preserves_complete_semantic_output(tmp_path, monkeypatch):
    """执行活动写入文件再读回时保留完整消息，只在显示时截断，重复事件不再追加"""
    text = "完整执行消息\n" * 4000 + "LAST"
    event = completed_event(text)
    path = tmp_path / (event["execution_id"] + ".jsonl")
    monkeypatch.setattr(presentation, "RUNTIME_MESSAGE_CHARS", 16)
    assert semantics.append_execution_activity(path, [event]) == 1
    assert json.loads(path.read_text())["activity"]["content"] == text
    assert semantics.execution_projection_from_path(path)["outputs"][0]["text"] == text
    shown = [presentation.runtime_detail(item) for item in semantics.runtime_details_from_objects(enumerate([json.loads(line) for line in path.read_text().splitlines()], 1), include_updates=True)]
    assert shown[0]["content"] != text
    assert semantics.append_execution_activity(path, [event]) == 0


def test_technical_event_owners_do_not_depend_on_presentation_contracts(subtests, isolated_portal_case):
    """执行事件与 Lineage 代码不依赖展示层投影，工具参数和文件、搜索列表不按显示条数截断"""
    with subtests.test(contract='test_technical_event_owners_do_not_depend_on_presentation'), isolated_portal_case() as case_path:
        root = Path(__file__).resolve().parents[2] / "management_portal"
        for relative in (
            "execution/event_projection.py", "execution/service.py",
            "society_service/resident_domain.py", "society_service/server.py",
        ):
            source = (root / relative).read_text()
            assert "portal_chat_workspace_projection" not in source, relative
            assert "runtime_event_projection" not in source, relative
        assert "society_service" not in (root / "execution/event_projection.py").read_text()
    with subtests.test(contract='test_lineage_does_not_interpret_harness_recovery'), isolated_portal_case() as case_path:
        root = Path(__file__).resolve().parents[2] / "management_portal" / "society_service"
        for path in root.glob("*.py"):
            source = path.read_text()
            assert "harness_failure_continue" not in source, path
            assert "PROVIDER_ERROR_CONTINUE_FEATURE_ID" not in source, path
            assert "DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS" not in source, path
    with subtests.test(contract='test_tool_arguments_semantics_preserve_content_and_prompt'), isolated_portal_case() as case_path:
        arguments = {"cmd": "show", "content": "complete content", "prompt": "complete prompt"}
        event = {"type": "response_item", "payload": {
            "type": "function_call", "name": "exec_command", "arguments": json.dumps(arguments),
        }}
        detail = semantics.runtime_details_from_objects([(1, event)])[0]
        assert json.loads(detail["content"]) == arguments
    with subtests.test(contract='test_semantic_file_and_search_lists_have_no_display_count_limit'), isolated_portal_case() as case_path:
        changes = [{"kind": "update", "path": f"file-{index}.py"} for index in range(8)]
        queries = [f"query-{index}" for index in range(8)]
        events = [
            (1, {"type": "item.completed", "item": {"type": "fileChange", "changes": changes}}),
            (2, {"type": "item.completed", "item": {"type": "webSearch", "action": {"queries": queries}}}),
        ]
        details = semantics.runtime_details_from_objects(events)
        assert details[0]["content"].splitlines() == [f"update {change['path']}" for change in changes]
        assert details[1]["content"].splitlines() == queries
