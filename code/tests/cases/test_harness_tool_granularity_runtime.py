from __future__ import annotations

from typing import Any

from management_portal.execution import harness_native_items
from management_portal.execution.harness_native_loop import (
    NativeToolLoopRuntime,
)
from management_portal.execution.harness_auto_policy import (
    AdaptiveHarnessController,
)


def _runtime(
    strategy: str,
    *,
    fragmented_threshold: int = 3,
    discovery_threshold: int = 3,
    confirm_singleton_reads: int = 1,
    max_granularity_steers: int = 1,
    duplicate_read_threshold: int = 100,
    max_checkpoints: int = 5,
    adaptive_turn_runtime: Any = None,
    planned_limits: dict | None = None,
) -> NativeToolLoopRuntime:
    return NativeToolLoopRuntime(
        checkpoint_interval=500,
        max_checkpoints=max_checkpoints,
        duplicate_read_threshold=duplicate_read_threshold,
        large_output_bytes=16 * 1024 * 1024,
        granularity_strategy=strategy,
        fragmented_read_batch_threshold=fragmented_threshold,
        discovery_target_threshold=discovery_threshold,
        confirm_singleton_reads=confirm_singleton_reads,
        max_granularity_steers=max_granularity_steers,
        adaptive_turn_runtime=adaptive_turn_runtime,
        planned_limits=planned_limits,

        emit=lambda _details: None,
    )


def test_context_pressure_steers_without_interrupting_or_reducing_effort_contracts(subtests, isolated_portal_case):
    """context 压力只发 steer 提醒，不中断也不降 effort；各粒度策略按读取批次形态触发"""
    with subtests.test(contract='test_context_pressure_steers_without_interrupting_or_reducing_effort'), isolated_portal_case() as case_path:
        runtime = _runtime(
            "auto",

        )
        runtime.context_pressure_threshold_percent = 70

        runtime.process_event({
            "method": "thread/tokenUsage/updated",
            "params": {
                "tokenUsage": {
                    "last": {"inputTokens": 72_000},
                    "modelContextWindow": 100_000,
                }
            },
        })
        tool_item = {
            "id": "read-after-pressure",
            "type": "commandExecution",
            "command": "rg target",
            "status": "completed",
            "exitCode": 0,
        }
        runtime.process_event({
            "method": "item/started",
            "params": {"item": tool_item},
        })
        runtime.process_event({
            "method": "item/completed",
            "params": {"item": tool_item},
        })

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "context_pressure"
        assert "action" not in control
        assert "effort" not in control
        assert control["metadata"]["kind"] == "safety"
        assert control["input"][0]["type"] == "text"

        runtime.record_control_result(control, True, "")
        report = runtime.report()




        assert report["runtime_action_outcomes"] == [
            {
                "action": "native_runtime_control:steer:context_pressure",
                "status": "applied",
                "reason": "",
            },
        ]
    with subtests.test(contract='test_fragmentation_strategy_distinguishes_parallel_and_serial_reads'), isolated_portal_case() as case_path:
        runtime = _runtime("fragmented", fragmented_threshold=3)
        _complete_batch(
            runtime,
            _read_item("parallel-a", "src/a.py"),
            _read_item("parallel-b", "src/b.py"),
            _read_item("parallel-c", "src/c.py"),
        )

        assert runtime.next_control() is None
        report = runtime.report()
        assert report["read_batches_observed"] == 1
        assert report["batched_read_batches_observed"] == 1
        assert report["singleton_read_batches_observed"] == 0
        assert report["max_read_batch_width"] == 3

        for index in range(3):
            _complete_batch(
                runtime,
                _read_item(f"serial-{index}", f"src/serial_{index}.py"),
            )

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "fragmented_read_sequence"
        assert "连续执行 3 个单项只读批次" in control["input"][0]["text"]
        report = runtime.report()
        assert report["singleton_read_batches_observed"] == 3
        assert report["granularity_steers_enqueued"] == 1
    with subtests.test(contract='test_discovery_strategy_steers_immediately_when_search_fans_out'), isolated_portal_case() as case_path:
        runtime = _runtime("discovery", discovery_threshold=3)
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output=(
                    "src/alpha.py:10:needle\n"
                    "src/beta.py:20:needle\n"
                    "tests/test_gamma.py:30:needle\n"
                ),
            ),
        )

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "read_discovery_opportunity"
        assert "至少 3 个候选目标" in control["input"][0]["text"]
        report = runtime.report()
        assert report["discovery_opportunities_observed"] == 1
        assert report["max_discovery_targets_observed"] == 3
    with subtests.test(contract='test_count_strategy_exposes_why_raw_call_count_is_a_weak_proxy'), isolated_portal_case() as case_path:
        runtime = _runtime("count", fragmented_threshold=3)
        _complete_batch(
            runtime,
            _read_item("parallel-a", "src/a.py"),
            _read_item("parallel-b", "src/b.py"),
            _read_item("parallel-c", "src/c.py"),
        )

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "read_call_count"
        assert runtime.report()["batched_read_batches_observed"] == 1
    with subtests.test(contract='test_granularity_strategies_do_not_treat_writes_as_safe_reads'), isolated_portal_case() as case_path:
        runtime = _runtime("adaptive", fragmented_threshold=2)
        for index in range(3):
            _complete_batch(
                runtime,
                _read_item(
                    f"write-{index}",
                    f"src/write_{index}.py",
                    action_type="write",
                ),
            )

        assert runtime.next_control() is None
        report = runtime.report()
        assert report["read_batches_observed"] == 0
        assert report["granularity_steers_enqueued"] == 0


def _policy_invocation() -> Any:
    return type("Invocation", (), {
        "request": type("Request", (), {
            "provider": "codex",
            "operation": "turn",
            "cwd": "",
            "input_media": (),
            "runtime_options": {},
        })(),
        "session": type("Session", (), {
            "binding_id": "native-runtime",
            "is_new_backend_session": False,
        })(),
        "user_prompt": "continue",
        "is_new_backend_session": False,
        "harness_tools": (),
    })()


def _read_item(
    item_id: str,
    path: str,
    *,
    action_type: str = "read",
    output: str = "source",
) -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "commandExecution",
        "command": f"read {path}",
        "cwd": "/repo",
        "commandActions": [{
            "type": action_type,
            "path": path,
            "query": "needle" if action_type == "search" else "",
        }],
        "aggregatedOutput": output,
    }


def _event(method: str, item: dict[str, Any]) -> dict[str, Any]:
    return {"method": method, "params": {"item": item}}


def _complete_batch(
    runtime: NativeToolLoopRuntime,
    *items: dict[str, Any],
) -> None:
    for item in items:
        runtime.process_event(_event("item/started", item))
    for item in items:
        runtime.process_event(_event("item/completed", item))


def test_turn_policy_triggers_cumulative_native_output_pressure_contracts(tmp_path, subtests, isolated_portal_case):
    """turn 策略按累计原生输出触发大输出提醒，同一 turn 内保持碎片读取阈值"""
    with subtests.test(contract='test_turn_policy_triggers_cumulative_native_output_pressure'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(_policy_invocation(), {})
        turn_policy = controller.begin_turn(decision)
        runtime = _runtime(
            "count",
            adaptive_turn_runtime=turn_policy,
            planned_limits={"cumulative_output_bytes": decision.features["native_tool_loop_governor"]["cumulative_output_bytes"]},
        )

        for index in range(4):
            item = {
                "id": f"command-{index}",
                "type": "commandExecution",
                "command": "printf payload",
                "commandActions": [{"type": "execute", "command": "printf"}],
                "aggregatedOutput": "X" * (70 * 1024),
            }
            _complete_batch(runtime, item)

        controls = []
        while control := runtime.next_control():
            controls.append(control)
        report = runtime.report()
        assert "large_output_pressure" in {
            control["reason"] for control in controls
        }
        assert report["large_native_outputs_observed"] == 0
        assert report["adaptive_turn_policy_attached"] is True
        assert report["planned_turn_limits"][
            "cumulative_output_bytes"
        ] == 256 * 1024
    with subtests.test(contract='test_turn_policy_preserves_fragmentation_threshold_during_the_same_native_turn'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(_policy_invocation(), {})
        turn_policy = controller.begin_turn(decision)
        runtime = _runtime(
            "auto",
            fragmented_threshold=5,
            confirm_singleton_reads=3,
            adaptive_turn_runtime=turn_policy,
            planned_limits={"cumulative_output_bytes": decision.features["native_tool_loop_governor"]["cumulative_output_bytes"]},
        )

        output = {
            "id": "large-discovery",
            "type": "commandExecution",
            "command": "rg needle",
            "commandActions": [{"type": "search", "path": "."}],
            "aggregatedOutput": "X" * (200 * 1024),
        }
        _complete_batch(runtime, output)

        report = runtime.report()
        assert runtime.granularity.fragmented_read_batch_threshold == 5
        assert runtime.granularity.confirm_singleton_reads == 3

        _complete_batch(runtime, _read_item("serial-a", "src/a.py"))
        _complete_batch(runtime, _read_item("serial-b", "src/b.py"))
        controls = []
        while control := runtime.next_control():
            controls.append(control)
        assert "auto_serial_fragmentation" not in {
            control["reason"] for control in controls
        }


def test_adaptive_strategy_emits_at_most_one_granularity_steer_contracts(subtests, isolated_portal_case):
    """adaptive 策略最多发一次粒度 steer，auto 策略等搜索后出现单项串行读取才提醒"""
    with subtests.test(contract='test_adaptive_strategy_emits_at_most_one_granularity_steer'), isolated_portal_case() as case_path:
        runtime = _runtime("adaptive", fragmented_threshold=2)
        for index in range(6):
            _complete_batch(
                runtime,
                _read_item(f"serial-{index}", f"src/serial_{index}.py"),
            )

        controls = []
        while control := runtime.next_control():
            controls.append(control)
        assert len(controls) == 1
        assert runtime.report()["granularity_steers_enqueued"] == 1
    with subtests.test(contract='test_auto_strategy_waits_for_fragmented_followup_before_steering'), isolated_portal_case() as case_path:
        runtime = _runtime("auto", discovery_threshold=3)
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output=(
                    "src/alpha.py:10:needle\n"
                    "src/beta.py:20:needle\n"
                    "tests/test_gamma.py:30:needle\n"
                ),
            ),
        )

        assert runtime.next_control() is None
        assert runtime.report()["auto_candidate_pending"] is True

        _complete_batch(runtime, _read_item("single", "src/alpha.py"))

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "auto_confirmed_fragmentation"
        assert "不要回复、确认或复述本检查点" in control["input"][0]["text"]
        assert "最终只向用户交付原任务结果" in control["input"][0]["text"]
        report = runtime.report()
        assert report["auto_discovery_candidates_armed"] == 1
        assert report["auto_singleton_followups_observed"] == 1
        assert report["auto_confirmed_steers"] == 1
        assert report["auto_candidate_pending"] is False
    with subtests.test(contract='test_auto_strategy_stays_silent_for_batched_followup'), isolated_portal_case() as case_path:
        runtime = _runtime("auto", discovery_threshold=3)
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output="src/a.py\nsrc/b.py\nsrc/c.py\n",
            ),
        )
        _complete_batch(
            runtime,
            _read_item("parallel-a", "src/a.py"),
            _read_item("parallel-b", "src/b.py"),
        )

        assert runtime.next_control() is None
        report = runtime.report()
        assert report["auto_batched_followups_observed"] == 1
        assert report["auto_candidate_pending"] is False
        assert report["granularity_steers_enqueued"] == 0
    with subtests.test(contract='test_auto_strategy_requires_configured_singleton_confirmation'), isolated_portal_case() as case_path:
        runtime = _runtime(
            "auto",
            discovery_threshold=3,
            confirm_singleton_reads=2,
        )
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output="src/a.py\nsrc/b.py\nsrc/c.py\n",
            ),
        )
        _complete_batch(runtime, _read_item("single-a", "src/a.py"))
        assert runtime.next_control() is None

        _complete_batch(runtime, _read_item("single-b", "src/b.py"))
        assert runtime.next_control() is not None
    with subtests.test(contract='test_auto_strategy_keeps_candidate_across_interleaved_write'), isolated_portal_case() as case_path:
        runtime = _runtime("auto", discovery_threshold=3)
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output="src/a.py\nsrc/b.py\nsrc/c.py\n",
            ),
        )
        _complete_batch(
            runtime,
            _read_item("write", "src/a.py", action_type="write"),
        )

        assert runtime.next_control() is None
        report = runtime.report()
        assert report["auto_candidates_cancelled"] == 0
        assert report["auto_candidate_pending"] is True

        _complete_batch(runtime, _read_item("single", "src/b.py"))
        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "auto_confirmed_fragmentation"
    with subtests.test(contract='test_auto_strategy_measures_post_steer_window'), isolated_portal_case() as case_path:
        runtime = _runtime(
            "auto",
            discovery_threshold=3,
            max_granularity_steers=2,
        )
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output="src/a.py\nsrc/b.py\nsrc/c.py\n",
            ),
        )
        _complete_batch(runtime, _read_item("single", "src/a.py"))
        control = runtime.next_control()
        assert control is not None
        runtime.record_control_result(control, True, "")

        for index in range(4):
            _complete_batch(
                runtime,
                _read_item(f"parallel-{index}-a", f"src/{index}_a.py"),
                _read_item(f"parallel-{index}-b", f"src/{index}_b.py"),
            )

        report = runtime.report()
        assert report["auto_post_steer_read_batches_observed"] == 4
        assert report["auto_post_steer_batched_read_batches"] == 4
        assert report["auto_post_steer_max_read_batch_width"] == 2
        assert report["auto_post_steer_observation_pending"] is False
        assert runtime.next_control() is None


def test_auto_strategy_detects_serial_reads_without_discovery_contracts(subtests, isolated_portal_case):
    """auto 策略无搜索时也识别串行单项读取，运行时对重复读取、失败循环和 context 压力发出提醒"""
    with subtests.test(contract='test_auto_strategy_detects_serial_reads_without_discovery'), isolated_portal_case() as case_path:
        runtime = _runtime("auto", fragmented_threshold=3)

        for index in range(3):
            _complete_batch(
                runtime,
                _read_item(f"serial-{index}", f"src/{index}.py"),
            )

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "auto_serial_fragmentation"
    with subtests.test(contract='test_auto_strategy_reinforces_persistent_fragmentation_once'), isolated_portal_case() as case_path:
        runtime = _runtime(
            "auto",
            discovery_threshold=3,
            max_granularity_steers=2,
        )
        _complete_batch(
            runtime,
            _read_item(
                "search",
                ".",
                action_type="search",
                output="src/a.py\nsrc/b.py\nsrc/c.py\n",
            ),
        )
        _complete_batch(runtime, _read_item("first-single", "src/a.py"))
        first = runtime.next_control()
        assert first is not None
        runtime.record_control_result(first, True, "")

        for index in range(4):
            _complete_batch(
                runtime,
                _read_item(f"continued-{index}", f"src/continued_{index}.py"),
            )

        second = runtime.next_control()
        assert second is not None
        assert second["reason"] == "auto_persistent_fragmentation"
        report = runtime.report()
        assert report["granularity_steers_enqueued"] == 2
        assert report["auto_persistent_fragmentation_steers"] == 1
    with subtests.test(contract='test_runtime_deduplicates_same_reason_controls'), isolated_portal_case() as case_path:
        runtime = _runtime(
            "off",
            duplicate_read_threshold=2,
        )
        for index in range(8):
            _complete_batch(
                runtime,
                _read_item(f"repeat-{index}", "src/repeated.py"),
            )

        controls = []
        while control := runtime.next_control():
            controls.append(control)
        duplicate_controls = [
            control for control in controls
            if control["reason"] == "duplicate_read_pressure"
        ]
        assert len(duplicate_controls) == 1
        assert runtime.report()["duplicate_read_calls_observed"] == 7
    with subtests.test(contract='test_runtime_reacts_to_repeated_failed_tools'), isolated_portal_case() as case_path:
        runtime = _runtime("off")
        for index in range(3):
            item = _read_item(f"failed-{index}", f"src/{index}.py")
            item["status"] = "failed"
            item["exitCode"] = 1
            _complete_batch(runtime, item)

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "failure_loop_pressure"
        assert runtime.report()["failed_tool_calls_observed"] == 3
    with subtests.test(contract='test_runtime_does_not_exit_after_repeated_same_turn_compaction'), isolated_portal_case() as case_path:
        runtime = _runtime("off")
        for index in range(2):
            runtime.process_event(_event(
                "item/completed",
                {"id": f"compact-{index}", "type": "contextCompaction"},
            ))

        assert runtime.next_control() is None
        assert runtime.report()["context_compactions_observed"] == 2
    with subtests.test(contract='test_runtime_reacts_to_live_context_pressure'), isolated_portal_case() as case_path:
        runtime = _runtime("off")
        runtime.process_event({
            "method": "thread/tokenUsage/updated",
            "params": {
                "tokenUsage": {
                    "last": {"inputTokens": 85_000},
                    "modelContextWindow": 100_000,
                },
            },
        })
        assert runtime.next_control() is None
        item = _read_item("pressure-read", "pressure.py")
        runtime.process_event({
            "method": "item/started",
            "params": {"item": item},
        })
        runtime.process_event({
            "method": "item/completed",
            "params": {"item": item},
        })

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "context_pressure"
        assert runtime.report()["max_context_pressure_percent"] == 85.0


def test_runtime_reserves_long_turn_control_capacity_for_critical_pressure_contracts(subtests, isolated_portal_case):
    """长 turn 的常规提醒名额用尽后仍为失败循环、重复读取等关键压力保留容量"""
    with subtests.test(contract='test_runtime_reserves_long_turn_control_capacity_for_critical_pressure'), isolated_portal_case() as case_path:
        runtime = _runtime("off", max_checkpoints=5)
        for reason in (
            "failure_loop_pressure",
            "duplicate_read_pressure",
            "large_output_pressure",
            "native_inline_media_pressure",
            "tool_call_checkpoint",
            "context_pressure",
            "compaction_churn",
        ):
            runtime.controls.queue_safety(reason)

        controls = []
        while control := runtime.next_control():
            controls.append(control["reason"])

        assert controls == [
            "failure_loop_pressure",
            "duplicate_read_pressure",
            "native_inline_media_pressure",
            "context_pressure",
        ]
        report = runtime.report()
        assert report["regular_safety_controls_enqueued"] == 2
        assert report["critical_safety_controls_enqueued"] == 2
        assert report["runtime_controls_suppressed"] == 3
    with subtests.test(contract='test_runtime_detects_native_inline_media_but_reports_protocol_limit'), isolated_portal_case() as case_path:
        runtime = _runtime("off")
        _complete_batch(runtime, {
            "id": "image-result",
            "type": "dynamicToolCall",
            "tool": "view_image",
            "contentItems": [{
                "type": "image",
                "imageUrl": "data:image/png;base64," + "A" * (600 * 1024),
            }],
        })

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "native_inline_media_pressure"
        report = runtime.report()
        assert report["native_inline_media_bytes_estimated"] > 512 * 1024
        assert report["native_output_transform_available"] is False
    with subtests.test(contract='test_real_app_server_snake_case_command_shape_is_observed'), isolated_portal_case() as case_path:
        runtime = _runtime("discovery", discovery_threshold=3)
        item = {
            "id": "exec-real",
            "type": "commandExecution",
            "command": [
                "/bin/bash",
                "-lc",
                "pwd && rg --files && git status --short",
            ],
            "cwd": "file:///repo",
            "parsed_cmd": [{
                "type": "unknown",
                "cmd": "pwd && rg --files && git status --short",
            }],
            "aggregated_output": (
                "src/alpha.py\nsrc/beta.py\ntests/test_gamma.py\n"
            ),
        }

        _complete_batch(runtime, item)

        control = runtime.next_control()
        assert control is not None
        assert control["reason"] == "read_discovery_opportunity"
        report = runtime.report()
        assert report["native_output_bytes_estimated"] == len(
            item["aggregated_output"]
        )
        assert report["read_operations_observed"] == 3
        assert report["max_discovery_targets_observed"] == 3
    with subtests.test(contract='test_shell_fallback_rejects_mutations_and_tests'), isolated_portal_case() as case_path:
        runtime = _runtime("adaptive", fragmented_threshold=2)
        for index, command in enumerate((
            "cat src/a.py > src/b.py",
            "sed -i 's/a/b/' src/a.py",
            "python3 -m pytest -q",
        )):
            _complete_batch(
                runtime,
                {
                    "id": f"unsafe-{index}",
                    "type": "commandExecution",
                    "command": ["/bin/bash", "-lc", command],
                    "parsed_cmd": [{"type": "unknown", "cmd": command}],
                    "aggregated_output": "output",
                },
            )

        assert runtime.next_control() is None
        assert runtime.report()["read_batches_observed"] == 0


def test_off_strategy_keeps_discovery_scan_off_shared_reader(
    monkeypatch: Any,
) -> None:
    """粒度策略为 off 时不扫描命令输出统计候选目标"""
    def unexpected_scan(_item: dict[str, Any]) -> int:
        raise AssertionError("off strategy must not scan discovery output")

    monkeypatch.setattr(
        harness_native_items,
        "discovery_target_count",
        unexpected_scan,
    )
    runtime = _runtime("off")
    _complete_batch(
        runtime,
        {
            "id": "large-search",
            "type": "commandExecution",
            "command": ["/bin/bash", "-lc", "rg needle src"],
            "parsed_cmd": [{"type": "unknown", "cmd": "rg needle src"}],
            "aggregated_output": "src/module.py:1:value\n" * 4_000,
        },
    )

    assert runtime.report()["max_discovery_targets_observed"] == 0


def test_fixed_cumulative_output_reminder_waits_for_planned_boundary():
    """累计输出提醒在达到计划的字节上限时才触发，且只触发一次"""
    runtime = _runtime("count", planned_limits={"cumulative_output_bytes": 256 * 1024})
    runtime.native_output_bytes_observed = 256 * 1024 - 1
    runtime._apply_adaptive_controls()
    assert runtime.next_control() is None
    runtime.native_output_bytes_observed += 1
    runtime._apply_adaptive_controls()
    assert runtime.next_control()["reason"] == "large_output_pressure"
    runtime._apply_adaptive_controls()
    assert runtime.next_control() is None
