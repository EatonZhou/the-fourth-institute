from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock


from management_portal import resident_workspace_adapter as portal_chat_workspace_backend
from management_portal.execution import event_projection
from management_portal.execution.backends import BackendContext


def test_native_dynamic_tool_activity_reaches_the_runtime_projection(tmp_path):
    """原生动态工具的开始、完成和失败事件进入运行时投影并写入活动记录"""
    from management_portal.execution.native_tool_events import tool_started, tool_completed

    started = tool_started("wait-1", "clock.sleep", {"duration_ms": 45000})
    completed = tool_completed("wait-1", "clock.sleep", {}, "done")
    failed = tool_completed("wait-2", "clock.sleep", {}, "cancelled", failed=True)
    events = [
        {"type": "provider_event", "details": event, "sequence": index}
        for index, event in enumerate([started, completed, failed], 1)
    ]
    details = event_projection.runtime_details_from_objects(enumerate(events, 1))
    assert [(row["kind"], row["title"], row["status"]) for row in details] == [
        ("tool_call", "Tool: clock.sleep", "running"),
        ("tool_output", "Tool finished: clock.sleep", "completed"),
        ("tool_output", "Tool finished: clock.sleep", "failed"),
    ]
    path = tmp_path / "activity.jsonl"
    assert event_projection.append_execution_activity(path, events) == 3
    stored = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["activity"]["status"] for row in stored] == ["running", "completed", "failed"]


def test_a_failed_claude_tool_result_is_projected_as_failed() -> None:
    """Claude 非 Bash 工具的失败结果在执行活动里显示为失败

    Claude Code Adapter 把 Bash 以外的工具调用和结果记为 function_call 与 function_call_output，
    失败的结果带 is_error。经活动记录压缩与投影后，失败的结果显示失败，成功的显示完成。
    """
    activities = []
    context = BackendContext(
        emit_event=lambda _kind, activity: activities.append(activity),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
    )
    for call_id, output, failed in [("read-1", "File does not exist.", True), ("read-2", "1\thello", False)]:
        context.observe_provider_event({"type": "response_item", "payload": {
            "type": "function_call", "name": "Read", "call_id": call_id, "arguments": {"file_path": "/tmp/x"}}})
        context.observe_provider_event({"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": call_id, "output": output, "is_error": failed}})
    events = [{"type": "provider_event", "details": activity} for activity in activities]
    details = event_projection.runtime_details_from_objects(enumerate(events, 1))
    assert [(row["kind"], row["status"]) for row in details] == [
        ("tool_call", "running"), ("tool_output", "failed"), ("tool_call", "running"), ("tool_output", "completed"),
    ]


def test_backend_start_reason_is_consumed_only_by_a_new_turn() -> None:
    """轮次开始原因只由新 turn 消耗，同一 turn 重复上报沿用原因，之后默认为继续执行"""
    activities = []
    persisted_reasons = []
    context = BackendContext(
        emit_event=lambda _kind, activity: activities.append(activity),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
        turn_start_reason="backend_service_update",
        turn_start_reason_changed=persisted_reasons.append,
    )

    def started(turn_id):
        return {
            "method": "turn/started",
            "params": {"threadId": "thread-existing", "turn": {"id": turn_id}},
        }

    original = started("turn-restored")
    context.set_turn_start_reason("provider_recovery")
    context.observe_provider_event(original)
    context.set_turn_start_reason("context_compaction")
    context.observe_provider_event(original)
    context.observe_provider_event(started("turn-compacted"))
    context.observe_provider_event(started("turn-following"))

    assert [activity["turn_start_reason"] for activity in activities] == [
        "backend_service_update", "backend_service_update",
        "context_compaction", "continuation",
    ]
    assert persisted_reasons == [
        "continuation", "context_compaction", "continuation",
    ]
    assert "turn_start_reason" not in original


def test_provider_turn_start_reason_survives_activity_storage(tmp_path, subtests, isolated_portal_case):
    """各类轮次开始原因及其标题在活动记录存取后保留"""
    for reason, title in [
    ("initial", "开始执行"),
    ("context_compaction", "压缩后继续"),
    ("backend_service_update", "后端服务更新后恢复运行"),
    ("provider_recovery", "恢复运行"),
    ("continuation", "继续执行"),
]:
        with subtests.test(reason=reason, title=title), isolated_portal_case() as case_path:
            tmp_path = case_path
            event = {
                "type": "provider_event",
                "details": {
                    "method": "turn/started",
                    "turn_start_reason": reason,
                    "params": {
                        "threadId": "thread-existing",
                        "turn": {"id": "turn-next", "status": "inProgress"},
                    },
                },
            }
            details = event_projection.runtime_details_from_objects([(1, event)])
            assert details[0]["title"] == title
            assert details[0]["status"] == "running"
            assert details[0]["turn_id"] == "turn-next"
            assert details[0]["turn_start_reason"] == reason
            path = tmp_path / "activity.jsonl"
            event_projection.append_execution_activity(path, [event])
            stored = event_projection.runtime_details_from_objects(enumerate([json.loads(line) for line in path.read_text().splitlines()], 1))
            assert stored[0]["title"] == title
            assert stored[0]["turn_start_reason"] == reason


def test_provider_terminal_activity_preserves_actual_outcome(subtests, isolated_portal_case):
    """轮次结束活动按 Provider 实际状态显示完成、中断、失败或取消，未知状态显示已结束"""
    for provider_status, status, title in [
    ("completed", "completed", "Completed"),
    ("interrupted", "interrupted", "已中断"),
    ("failed", "failed", "执行失败"),
    ("cancelled", "cancelled", "已取消"),
    ("", "unknown", "执行轮已结束"),
    ("future_status", "unknown", "执行轮已结束"),
]:
        with subtests.test(provider_status=provider_status, status=status, title=title), isolated_portal_case() as case_path:
            events = []
            context = BackendContext(
                emit_event=lambda kind, details: events.append(
                    {"type": kind, "details": details}
                ),
                cancellation_requested=lambda: False,
                set_interrupt_target=lambda *_args: None,
            )
            event = {
                "method": "turn/completed",
                "params": {
                    "threadId": "thread-existing",
                    "turn": {
                        "id": "turn-terminal",
                        "status": provider_status,
                        "error": {"message": "provider unavailable"}
                        if provider_status == "failed" else None,
                    },
                },
            }
            context.observe_provider_event(event)
            for payload in ({"type": "provider_event", "details": event}, events[-1]):
                details = event_projection.runtime_details_from_objects([(1, payload)])
                assert details[0]["title"] == title
                assert details[0]["status"] == status
                assert details[0]["turn_id"] == "turn-terminal"
                if provider_status == "failed":
                    assert "provider unavailable" in details[0]["content"]


def test_interruption_reason_survives_activity_storage(tmp_path, subtests, isolated_portal_case):
    """Worker 换代的中断原因在活动记录存取后保留，且只在中断状态下显示"""
    for reason, provider_status, title in [
    ("worker_handover", "interrupted", "Worker 换代旧 turn 中断"),
    ("", "interrupted", "已中断"),
    ("user_cancel", "interrupted", "已中断"),
    ("worker_handover", "cancelled", "已取消"),
    ("worker_handover", "failed", "执行失败"),
    ("worker_handover", "completed", "Completed"),
]:
        with subtests.test(reason=reason, provider_status=provider_status, title=title), isolated_portal_case() as case_path:
            tmp_path = case_path
            event = {
                "type": "provider_event",
                "details": {
                    "method": "turn/completed",
                    "interruption_reason": reason,
                    "params": {
                        "threadId": "thread-existing",
                        "turn": {"id": "turn-before-update", "status": provider_status},
                    },
                },
            }
            details = event_projection.runtime_details_from_objects([(1, event)])
            assert details[0]["title"] == title
            assert details[0]["status"] == provider_status
            path = tmp_path / "activity.jsonl"
            event_projection.append_execution_activity(path, [event])
            stored = event_projection.runtime_details_from_objects(enumerate([json.loads(line) for line in path.read_text().splitlines()], 1))
            assert stored[0]["title"] == title
            if provider_status == "interrupted" and reason:
                assert stored[0]["interruption_reason"] == reason
            else:
                assert "interruption_reason" not in stored[0]


def _provider_command_event() -> dict:
    return {
        "created_at": "2026-07-29T03:00:00Z",
        "type": "provider_event",
        "details": {
            "method": "item/started",
            "params": {
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "command": "pytest -q",
                    "status": "inProgress",
                }
            },
        },
    }


def test_known_execution_provider_event_projects_activity_contracts(subtests, isolated_portal_case):
    """已知 Provider 事件投影为活动卡片，未知事件不生成兜底卡片"""
    with subtests.test(contract='test_known_execution_provider_event_projects_activity'), isolated_portal_case() as case_path:
        details = event_projection.runtime_details_from_objects([(1, _provider_command_event())], include_updates=True)
        assert len(details) == 1
        assert details[0]['title'] == 'Command started'
        assert details[0]['status'] == 'running'
        assert 'pytest -q' in details[0]['content']
    with subtests.test(contract='test_unknown_provider_event_has_no_runtime_fallback'), isolated_portal_case() as case_path:
        details = event_projection.runtime_details_from_objects([(1, {'created_at': '2026-07-29T03:00:00Z', 'type': 'provider_event', 'details': {'method': 'provider/futureEvent', 'params': {'message': 'unknown'}}})], include_updates=True)
        assert details == []
    with subtests.test(contract='test_unknown_runtime_event_type_has_no_runtime_fallback'), isolated_portal_case() as case_path:
        details = event_projection.runtime_details_from_objects([(1, {'created_at': '2026-07-29T03:00:00Z', 'type': 'provider_future_envelope', 'details': {'message': 'unknown'}})], include_updates=True)
        assert details == []
    with subtests.test(contract='test_agent_harness_card_does_not_inherit_provider_failure_status'), isolated_portal_case() as case_path:
        details = event_projection.runtime_details_from_objects([(1, {'created_at': '2026-09-02T02:23:21Z', 'type': 'agent_harness_completed', 'details': {'status': 'failed', 'features': [{'feature_id': 'native_tool_loop_governor', 'status': 'applied', 'reason': 'native_tool_loop_governor_attached'}]}})], include_updates=True)
        assert len(details) == 1
        assert details[0]['title'] == 'Agent Harness'
        assert details[0]['status'] == 'completed'
    with subtests.test(contract='test_agent_harness_card_still_reports_a_feature_failure'), isolated_portal_case() as case_path:
        details = event_projection.runtime_details_from_objects([(1, {'created_at': '2026-09-02T02:23:21Z', 'type': 'agent_harness_completed', 'details': {'status': 'completed', 'features': [{'feature_id': 'native_tool_loop_governor', 'status': 'failed', 'reason': 'RuntimeError'}]}})], include_updates=True)
        assert len(details) == 1
        assert details[0]['status'] == 'failed'
    with subtests.test(contract='test_agent_message_completion_replaces_partial_execution_update'), isolated_portal_case() as case_path:
        execution_id = 'execution-' + '1' * 24
        item_id = 'msg_shared'
        details = event_projection.runtime_details_from_objects([(1, {'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/agentMessage/delta', 'params': {'itemId': item_id, 'delta': '本地工作区为空，页面应由已连接的网站会'}}}), (2, {'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/completed', 'params': {'item': {'id': item_id, 'type': 'agentMessage', 'text': '本地工作区为空，页面应由已连接的网站会话托管。我现在会检查现有 Sites 项目。'}}}})], include_updates=True)
        assert len(details) == 1
        assert details[0]['id'] == f'runtime-update:{execution_id}:{item_id}'
        assert details[0]['status'] == 'completed'
        assert details[0]['content'].endswith('我现在会检查现有 Sites 项目。')


def test_command_activity_replay_uses_native_turn_and_item_identity(tmp_path, subtests, isolated_portal_case):
    """命令活动按原生 turn 和 item ID 去重，命令文本相同的不同调用分别保留"""
    for method in ["item/started", "item/progress", "item/completed"]:
        with subtests.test(method=method), isolated_portal_case() as case_path:
            tmp_path = case_path
            path = tmp_path / f"execution-{'d' * 24}.jsonl"
            event = _provider_command_event()
            event["execution_id"] = "execution-" + "d" * 24
            event["sequence"] = 10
            event["details"]["method"] = method
            event["details"]["params"]["turnId"] = "turn-1"
            if method == "item/completed":
                event["details"]["params"]["item"].update(status="completed", exitCode=0)
            assert event_projection.append_execution_activity(path, [event]) == 1

            replay = json.loads(json.dumps(event))
            replay.update(sequence=100, created_at="2026-07-29T03:01:00Z")
            assert event_projection.append_execution_activity(path, [replay]) == 0

            other_item = json.loads(json.dumps(replay))
            other_item["sequence"] = 101
            other_item["details"]["params"]["item"]["id"] = "command-2"
            assert event_projection.append_execution_activity(path, [other_item]) == 1
            other_turn = json.loads(json.dumps(replay))
            other_turn["sequence"] = 102
            other_turn["details"]["params"]["turnId"] = "turn-2"
            assert event_projection.append_execution_activity(path, [other_turn]) == 1

            records = [json.loads(line) for line in path.read_text().splitlines()]
            assert len(records) == 3, "Equal command text must not collapse distinct native calls"
            assert len({row["activity"]["id"] for row in records}) == 3
            assert records[0]["created_at"] == event["created_at"]
            assert records[0]["activity"]["provider_item_id"] == "command-1"
            assert records[0]["activity"]["turn_id"] == "turn-1"


def test_native_command_activity_keeps_distinct_terminal_observations_contracts(tmp_path, subtests, isolated_portal_case):
    """同一命令退出码不同的结束事件分别记录，重放的执行事件不重复写入"""
    with subtests.test(contract='test_native_command_activity_keeps_distinct_terminal_observations'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / 'activity.jsonl'
        event = _provider_command_event()
        event['details']['method'] = 'item/completed'
        event['details']['params']['turnId'] = 'turn-1'
        event['details']['params']['item'].update(status='completed', exitCode=0)
        assert event_projection.append_execution_activity(path, [event]) == 1
        changed = json.loads(json.dumps(event))
        changed['details']['params']['item']['exitCode'] = 1
        assert event_projection.append_execution_activity(path, [changed]) == 1
        records = [json.loads(line) for line in path.read_text().splitlines()]
        assert [row['activity']['status'] for row in records] == ['completed', 'failed']
    with subtests.test(contract='test_activity_trace_does_not_append_replayed_execution_events'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / f'execution-{'a' * 24}.jsonl'
        event = {'sequence': 42, 'created_at': '2026-08-10T08:29:34.533Z', 'type': 'provider_event', 'details': {'method': 'error', 'params': {'willRetry': True, 'error': {'message': 'Reconnecting... 2/5'}}}}
        first_written = event_projection.append_execution_activity(path, [event])
        replay_written = event_projection.append_execution_activity(path, [event])
        payloads = [json.loads(line) for line in path.read_text().splitlines()]
        assert first_written == 1
        assert replay_written == 0
        assert len(payloads) == 1
        assert payloads[0]['activity']['id'] == 'runtime:42:error'
        assert payloads[0]['activity']['phase'] == 'reconnecting'
    with subtests.test(contract='test_activity_trace_keeps_same_local_id_from_different_timestamps'), isolated_portal_case() as case_path:
        tmp_path = case_path
        path = tmp_path / f'execution-{'b' * 24}.jsonl'
        for created_at in ('2026-08-10T08:00:00Z', '2026-08-10T09:00:00Z'):
            assert event_projection.append_execution_activity(path, [{'created_at': created_at, 'type': 'service_completed', 'details': {}}]) == 1
        payloads = [json.loads(line) for line in path.read_text().splitlines()]
        assert [item['activity']['id'] for item in payloads] == ['runtime:1:completed', 'runtime:1:completed']
        assert len({item['created_at'] for item in payloads}) == 2
    with subtests.test(contract='test_execution_projection_deduplicates_replayed_activity_rows'), isolated_portal_case() as case_path:
        tmp_path = case_path
        execution_id = 'execution-' + 'c' * 24
        path = tmp_path / f'{execution_id}.jsonl'
        record = {'type': 'runtime_activity', 'created_at': '2026-08-10T08:29:34.533Z', 'activity': {'id': 'runtime:42:error', 'kind': 'error', 'title': 'Reconnecting', 'content': 'Reconnecting... 2/5', 'status': 'running', 'phase': 'reconnecting'}}
        path.write_text(''.join((json.dumps(record) + '\n' for _ in range(3))), encoding='utf-8')
        projection = event_projection.execution_projection_from_path(path, runtime_limit=14)
        assert [item['id'] for item in projection['runtime_details']] == ['runtime:42:error']
        assert projection['runtime_details'][0]['phase'] == 'reconnecting'
    with subtests.test(contract='test_execution_projection_gives_idless_provider_messages_stable_line_ids'), isolated_portal_case() as case_path:
        tmp_path = case_path
        execution_id = 'execution-' + '4' * 24
        path = tmp_path / f'{execution_id}.jsonl'
        path.write_text('\n'.join([json.dumps({'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Claude update one'}}}}), json.dumps({'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/completed', 'params': {'item': {'type': 'agentMessage', 'text': 'Claude update two'}}}})]) + '\n', encoding='utf-8')
        projection = event_projection.execution_projection_from_path(path, runtime_limit=14)
        output_ids = [item['output_id'] for item in projection['outputs']]
        assert len(set(output_ids)) == 2
        assert all((output_id.startswith('msg_') for output_id in output_ids))
        assert output_ids == [item['output_id'] for item in event_projection.execution_projection_from_path(path, runtime_limit=14)['outputs']]
        assert projection['runtime_details'] == []


def test_command_activity_without_complete_native_identity_keeps_legacy_fallback(tmp_path, subtests, isolated_portal_case):
    """缺少 item 或 turn ID 的命令活动按旧方式以时间区分，重放时再写一条"""
    for missing in ["item", "turn"]:
        with subtests.test(missing=missing), isolated_portal_case() as case_path:
            tmp_path = case_path
            path = tmp_path / "activity.jsonl"
            event = _provider_command_event()
            if missing == "item":
                event["details"]["params"]["item"].pop("id")
                event["details"]["params"]["turnId"] = "turn-1"
            assert event_projection.append_execution_activity(path, [event]) == 1
            replay = json.loads(json.dumps(event))
            replay["created_at"] = "2026-07-29T03:01:00Z"
            assert event_projection.append_execution_activity(path, [replay]) == 1


def test_agent_harness_applied_metrics_are_visible_without_model_measurement() -> None:
    """没有测量模型调用时 Agent Harness 卡片仍显示各项上下文节省指标"""
    details = event_projection.runtime_details_from_objects(
        [(
            1,
            {
                "created_at": "2026-08-10T12:00:00Z",
                "type": "agent_harness_completed",
                "details": {
                    "status": "completed",
                    "provider_wall_ms": 83400,
                    "measurement_model_calls": 0,
                    "context_savings": {
                        "measurement_model_calls": 0,
                        "net_visible_tokens_estimated_avoided": 74000,
                    },
                    "provider_usage": {
                        "last": {
                            "inputTokens": 138000,
                            "cachedInputTokens": 120000,
                        }
                    },
                    "features": [
                        {
                            "feature_id": "inline_media_history",
                            "status": "applied",
                            "reason": "historical_inline_media_replaced",
                            "metrics": {
                                "inline_image_count_removed": 6,
                                "inline_media_bytes_removed": 578236,
                            },
                        },
                        {
                            "feature_id": "tool_output_offloading",
                            "status": "applied",
                            "reason": "large_tool_outputs_offloaded",
                            "metrics": {
                                "tool_output_count_offloaded": 2,
                                "artifact_bytes_retained": 131072,
                                "estimated_tokens_reclaimed": 31000,
                            },
                        },
                        {
                            "feature_id": "hash_aware_read_dedup",
                            "status": "applied",
                            "reason": "duplicate_current_turn_read_results_removed",
                            "metrics": {
                                "duplicate_output_count_removed": 12,
                                "estimated_tokens_reclaimed": 42150,
                            },
                        },
                        {
                            "feature_id": "hash_aware_read_dedup",
                            "status": "applied",
                            "reason": "duplicate_read_outputs_removed",
                            "metrics": {
                                "duplicate_output_count_removed": 4,
                                "estimated_tokens_reclaimed": 850,
                            },
                        },
                        {
                            "feature_id": "artifact_search_and_slice",
                            "status": "applied",
                            "reason": "artifact_recall_tools_attached",
                            "metrics": {
                                "tool_schema_tokens_avoided": 1300,
                            },
                        },
                        {
                            "feature_id": "tool_call_granularity",
                            "status": "applied",
                            "reason": "tool_granularity_policy_added",
                            "metrics": {"prompt_tokens_added": 700},
                        },
                    ],
                },
            },
        )],
        include_updates=True,
    )

    assert len(details) == 1
    assert details[0]["title"] == "Agent Harness"
    assert "历史图片减少 6 张 / 565 KiB" in details[0]["content"]
    assert "大型工具输出转存 2 条 / 128 KiB / 约 31000 tokens" in details[0]["content"]
    assert "重复只读结果去重 12 条 / 约 42150 tokens" in details[0]["content"]
    assert "重复只读结果去重 4 条 / 约 850 tokens" in details[0]["content"]
    assert "artifact_search_and_slice: applied" in details[0]["content"]
    assert "tool_call_granularity: applied" in details[0]["content"]
    assert "本轮可见上下文估算净减少 74000 tokens" in details[0]["content"]
    assert "Provider 83.4s" in details[0]["content"]
    assert "cached 120000" in details[0]["content"]


def test_activity_trace_drops_raw_token_snapshots(
    tmp_path: Path,
) -> None:
    """活动记录不保存原始 token 用量快照"""
    path = tmp_path / f"execution-{'9' * 24}.jsonl"
    written = event_projection.append_execution_activity(
        path,
        [
            {
                "created_at": "2026-07-31T08:00:00Z",
                "type": "provider_event",
                "details": {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 181_000,
                                "cachedInputTokens": 160_000,
                                "outputTokens": 400,
                                "reasoningOutputTokens": 120,
                                "totalTokens": 181_400,
                            },
                            "total": {"totalTokens": 500_000},
                            "modelContextWindow": 258_400,
                        },
                    },
                },
            },
            {
                "created_at": "2026-07-31T08:01:00Z",
                "type": "provider_event",
                "details": {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "tokenUsage": {
                            "last": {
                                "inputTokens": 221_000,
                                "cachedInputTokens": 198_000,
                                "outputTokens": 600,
                                "reasoningOutputTokens": 180,
                                "totalTokens": 221_600,
                            },
                            "total": {"totalTokens": 721_600},
                            "modelContextWindow": 258_400,
                        },
                    },
                },
            },
            {
                "created_at": "2026-07-31T08:02:00Z",
                "type": "service_completed",
                "details": {},
            },
        ],
    )

    payloads = [json.loads(line) for line in path.read_text().splitlines()]

    assert written == 1
    assert [item["type"] for item in payloads] == ["runtime_activity"]
    assert payloads[0]["activity"]["kind"] == "completed"
    assert "tokenUsage" not in path.read_text(encoding="utf-8")


def test_activity_trace_has_a_hard_size_limit_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """活动记录文件不超过大小上限，过期记录可按时间预算分批清理"""
    with subtests.test(contract='test_activity_trace_has_a_hard_size_limit'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            path = tmp_path / f'execution-{'8' * 24}.jsonl'
            monkeypatch.setattr(event_projection, 'MAX_ACTIVITY_TRACE_BYTES', 400)
            event_projection.append_execution_activity(path, [{'created_at': f'2026-07-31T08:00:{index:02d}Z', 'type': 'service_completed', 'details': {}} for index in range(10)])
            assert path.stat().st_size <= 400
    with subtests.test(contract='test_expired_activity_traces_are_pruned'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(event_projection, 'execution_state_root', lambda: tmp_path)
            old = event_projection.execution_activity_path('execution-' + '7' * 24)
            current = event_projection.execution_activity_path('execution-' + '6' * 24)
            assert old is not None and current is not None
            old.parent.mkdir(parents=True)
            old.write_text('old\n', encoding='utf-8')
            current.write_text('current\n', encoding='utf-8')
            os.utime(old, (100, 100))
            os.utime(current, (200, 200))
            removed = event_projection.prune_execution_activity(older_than_epoch=150)
            assert removed == 1
            assert not old.exists()
            assert current.exists()
    with subtests.test(contract='test_activity_trace_pruning_can_be_bounded'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(event_projection, 'execution_state_root', lambda: tmp_path)
            paths = [
                event_projection.execution_activity_path(f'execution-{index:024x}')
                for index in range(4)
            ]
            assert all(path is not None for path in paths)
            paths[0].parent.mkdir(parents=True)
            for path in paths:
                path.write_text('old\n', encoding='utf-8')
                os.utime(path, (100, 100))
            removed = sum(
                event_projection.prune_execution_activity(
                    older_than_epoch=150,
                    time_budget_seconds=0.01,
                )
                for _ in range(2)
            )
            assert removed == 4
            assert not any(path.exists() for path in paths)


def test_execution_events_produce_completed_agent_outputs_contracts(subtests, isolated_portal_case):
    """完成的 agent 消息事件生成输出，不同执行中同 ID 的消息不合并"""
    with subtests.test(contract='test_execution_events_produce_completed_agent_outputs'), isolated_portal_case() as case_path:
        execution_id = 'execution-' + '2' * 24
        outputs = event_projection.execution_outputs_from_events([{'sequence': 7, 'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/completed', 'params': {'turnId': 'turn_1', 'item': {'id': 'msg_complete', 'type': 'agentMessage', 'phase': 'final_answer', 'text': '后端生成的完整展示消息'}}}}])
        assert outputs == [{'output_id': outputs[0]['output_id'], 'created_at': outputs[0]['created_at'], 'phase': 'final_answer', 'text': '后端生成的完整展示消息', 'execution_id': execution_id}]
        assert outputs[0]['output_id'].startswith('msg_')
    with subtests.test(contract='test_same_item_id_from_different_executions_does_not_collapse'), isolated_portal_case() as case_path:
        item_id = 'msg_reused'
        objects = []
        for line_number, suffix in enumerate(('1', '2'), start=1):
            execution_id = 'execution-' + suffix * 24
            objects.append((line_number, {'execution_id': execution_id, 'type': 'provider_event', 'details': {'method': 'item/completed', 'params': {'item': {'id': item_id, 'type': 'agentMessage', 'text': f'execution {suffix}'}}}}))
        details = event_projection.runtime_details_from_objects(objects, include_updates=True)
        assert [item['content'] for item in details] == ['execution 1', 'execution 2']
        assert len({item['id'] for item in details}) == 2
    with subtests.test(contract='test_portal_chat_workspace_does_not_invent_missing_activity'), isolated_portal_case() as case_path:
        with mock.patch.object(portal_chat_workspace_backend, '_ensure', return_value={'resident_id': 'resident_workspace'}), mock.patch.object(portal_chat_workspace_backend, '_context_for', return_value={'title': 'Project Chat', 'project_id': 'Demo'}):
            workspace = portal_chat_workspace_backend.get_workspace('alice', 'project', 'project:Demo', {'title': 'Project Chat', 'project_id': 'Demo'})
        assert workspace['chat_workspace']['runtime'] == {'live': False, 'details': []}


def test_execution_projection_keeps_messages_outside_bounded_activity_window(
    tmp_path: Path,
) -> None:
    """执行投影的活动窗口截断后仍保留全部 agent 消息"""
    execution_id = "execution-" + "3" * 24
    path = tmp_path / f"{execution_id}.jsonl"
    events: list[dict] = []
    for index in range(18):
        events.append({
            "execution_id": execution_id,
            "created_at": f"2026-07-29T03:{index:02d}:00Z",
            "type": "provider_event",
            "details": {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": f"message-{index}",
                        "type": "agentMessage",
                        "phase": "commentary",
                        "text": f"visible update {index}",
                    },
                },
            },
        })
    for index in range(30):
        events.append({
            "execution_id": execution_id,
            "created_at": f"2026-07-29T04:{index:02d}:00Z",
            "type": "provider_event",
            "details": {
                "method": "item/completed",
                "params": {
                    "item": {
                        "id": f"command-{index}",
                        "type": "commandExecution",
                        "command": f"check-{index}",
                        "exitCode": 0,
                    },
                },
            },
        })
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    projection = event_projection.execution_projection_from_path(
        path,
        runtime_limit=14,
        message_limit=120,
    )

    assert [item["text"] for item in projection["outputs"]] == [
        f"visible update {index}"
        for index in range(18)
    ]
    assert all(
        item["phase"] == "commentary"
        and item["output_id"].startswith("msg_")
        for item in projection["outputs"]
    )
    assert len(projection["runtime_details"]) == 14
    assert all(
        item["kind"] == "command"
        for item in projection["runtime_details"]
    )


def test_provider_event_with_a_malformed_item_has_no_card() -> None:
    """Provider 事件的 item 不是对象时不生成卡片，也不中断其余事件的投影"""
    events = [
        (1, {"type": "provider_event", "details": {"method": "item/completed", "params": {"item": "malformed"}}}),
        (2, {"type": "provider_event", "details": {"method": "subagent/progress", "params": {"item": ["malformed"]}}}),
        (3, {"type": "service_completed", "details": {}}),
    ]
    details = event_projection.runtime_details_from_objects(events, include_updates=True)
    assert [detail["kind"] for detail in details] == ["completed"]
