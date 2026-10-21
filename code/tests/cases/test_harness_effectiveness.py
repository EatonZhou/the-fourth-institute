""":importance: P2"""
import asyncio
import os
from management_common.json_columns import json_value
from tests.cases.database_test_support import connect
from psycopg.errors import LockNotAvailable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.execution.harness_effectiveness import (
    HarnessEffectivenessStore,
    classify_failure,
    load_family,
)


def _observation(store):
    store.flush()
    with connect(store.database) as connection:
        cursor = connection.execute("SELECT * FROM harness_observations ORDER BY execution_id")
        return dict(zip((column.name for column in cursor.description), cursor.fetchone()))


def _decision(execution_id: str) -> SimpleNamespace:
    features = {
        "inline_media_history": {"enabled": True},
        "native_tool_loop_governor": {"enabled": False},
    }
    return SimpleNamespace(
        execution_id=execution_id,
        scope="v1:dev:codex:binding",
        provider="codex",
        signals={
            "policy_variant": "champion",
            "current_media_count": 1,
            "current_media_large_image_count": 1,
        },
        features=features,
        report=lambda: {
            "enabled_feature_ids": ["inline_media_history"],
        },
    )


def test_effectiveness_uses_turn_consumption_not_context_or_session_total(tmp_path, subtests, isolated_portal_case):
    """效果用量采用当前轮消耗且不取上下文或会话总量"""
    for usage, expected in [
        ({"context": {"inputTokens": 41373, "outputTokens": 165},
          "last": {"inputTokens": 160930, "outputTokens": 2480},
          "total": {"inputTokens": 387057, "outputTokens": 9934}}, (160930, 2480)),
        ({"last": {"inputTokens": 10, "outputTokens": 0},
          "total": {"inputTokens": 1000, "outputTokens": 500}}, (10, 0)),
        ({"input_tokens": 0, "output_tokens": 0,
          "last": {"inputTokens": 100, "outputTokens": 20}}, (0, 0)),
        ({"context": {"inputTokens": 50, "outputTokens": 5}}, (0, 0)),
        ({"total": {"inputTokens": 1000, "outputTokens": 500}}, (0, 0)),
    ]:
        with subtests.test(usage=usage, expected=expected), isolated_portal_case() as case_path:
            tmp_path = case_path
            store = HarnessEffectivenessStore()
            try:
                store.record_turn(decision=_decision("usage-test"), details={
                    "status": "completed", "provider_usage": usage,
                }, result=None)
                store.flush()
                with connect(store.database) as connection:
                    row = connection.execute(
                        "SELECT input_tokens,output_tokens FROM harness_strategy_observations"
                    ).fetchone()
                assert row == expected
            finally:
                store.close()


def test_retried_execution_keeps_plan_and_signals_aligned_with_latest_outcome_contracts(tmp_path, subtests, isolated_portal_case):
    """重试执行的计划和信号与最新结果保持一致"""
    with subtests.test(contract='test_retried_execution_keeps_plan_and_signals_aligned_with_latest_outcome'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessEffectivenessStore()
        first = _decision("retried")
        latest = _decision("retried")
        latest.signals = {"policy_variant": "challenger", "repo_task_signal": True,
                          "queue_wait_ms": 320, "queue_wait_observed": True}
        latest.features = {"tool_call_granularity": {"enabled": True}}
        latest.report = lambda: {"enabled_feature_ids": ["tool_call_granularity"]}
        try:
            store.record_turn(decision=first, details={"status": "failed"}, result=None)
            store.record_turn(decision=latest, details={"status": "completed"}, result=None)
            store.flush()
            with connect(store.database) as connection:
                row = connection.execute("SELECT production_variant,production_plan_json,signals_json,"
                                         "queue_wait_ms,status FROM harness_strategy_observations").fetchone()
            assert row[0] == "challenger"
            assert json_value(row[1]) == ["tool_call_granularity"]
            assert json_value(row[2])["queue_wait_ms"] == row[3] == 320
            assert row[4] == "completed"
        finally:
            store.close()
    with subtests.test(contract='test_reasoning_quality_signal_requires_recurrent_explicit_failures'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessEffectivenessStore()
        for index, passed in enumerate((False, True, False, True), start=1):
            execution_id = f"execution-quality-{index}"
            store.record_turn(
                decision=_decision(execution_id),
                details={"status": "completed", "total_wall_ms": 10},
                result={"output": "ok"},
            )
            store.record_quality(
                execution_id,
                passed=passed,
                source="test_oracle",
                workload_family="single_media_large",
            )
        store.flush()

        signal = store.reasoning_quality_signal(
            scope="v1:dev:codex:binding",
            provider="codex",
            workload_family="single_media_large",
        )

        assert signal["sample_count"] == 4
        assert signal["failure_count"] == 2
        assert signal["failure_rate"] == 0.5
        assert signal["guard"] is True
        assert store.reasoning_quality_signal(
            scope="v1:dev:codex:binding",
            provider="codex",
            workload_family="unknown-family",
        )["guard"] is False
        store.close()
    with subtests.test(contract='test_quality_label_can_arrive_before_async_turn_observation'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessEffectivenessStore()
        store.record_quality(
            "execution-early-label",
            passed=False,
            source="experiment",
            workload_family="failure_recovery",
        )
        store.record_turn(
            decision=_decision("execution-early-label"),
            details={"status": "failed", "total_wall_ms": 9},
            result={"error": "timeout"},
        )

        row = _observation(store)
        assert row["load_family"] == "failure_recovery"
        assert row["status"] == "failed"
        assert row["quality_result"] == "fail"
        store.close()


def test_failure_classification_identifies_provider_bad_requests(subtests, isolated_portal_case):
    """失败分类区分 Provider 请求错误、取消与上下文溢出"""
    for error, expected in (
        ('Codex turn failed: {"detail":"Bad Request"}', "provider_bad_request"),
        (RuntimeError("HTTP 400 invalid request"), "provider_bad_request"),
        (
            RuntimeError("execution worker lease expired after backend execution started"),
            "infrastructure_worker_lease_expired",
        ),
        (RuntimeError("ordinary execution error"), "execution_failure"),
        (asyncio.CancelledError(), "cancelled"),
        (RuntimeError("Claude Code turn interrupted"), "cancelled"),
        (RuntimeError("DSH operation job_1 did not complete: aborted."), "cancelled"),
        (RuntimeError("Claude Code turn failed: API Error: 529 Overloaded."), "provider_unavailable"),
        (
            RuntimeError("Claude Code turn failed: Prompt is too long · the request is ~1415461 tokens"),
            "context_overflow",
        ),
        ('{"error": {"type": "invalid_request_error", "code": "context_length_exceeded"}}', "context_overflow"),
    ):
        with subtests.test(error=error, expected=expected), isolated_portal_case() as case_path:
            assert classify_failure("failed", error) == expected


def test_effectiveness_records_plan_action_cost_failure_and_explicit_quality(
    tmp_path: Path,
) -> None:
    """效果记录保留计划、实际动作、成本及明确质量反馈"""
    store = HarnessEffectivenessStore()
    store.record_turn(
        decision=_decision("execution-1"),
        details={
            "status": "completed",
            "total_wall_ms": 125.5,
            "provider_wall_ms": 100.0,
            "provider_usage": {"input_tokens": 90, "output_tokens": 12},
            "features": [
                {"feature_id": "inline_media_history", "status": "applied"},
                {
                    "feature_id": "native_tool_loop_governor",
                    "status": "applied",
                    "action_applied": False,
                    "reason": "native_tool_loop_governor_attached",
                },
                {
                    "feature_id": "tool_call_granularity",
                    "status": "failed_open",
                    "action_applied": False,
                    "reason": "RuntimeError",
                },
            ],
            "provider_event_loop": {
                "runtime_action_outcomes": [
                    {
                        "action": (
                            "native_runtime_control:steer:"
                            "context_pressure"
                        ),
                        "status": "applied",
                        "reason": "",
                    },
                    {
                        "action": "native_runtime_control:steer:large_output_pressure",
                        "status": "failed",
                        "reason": "TimeoutError",
                    },
                ]
            },
        },
        result={"output": "ok"},
    )
    store.record_quality(
        "execution-1",
        passed=True,
        source="test_oracle",
        workload_family="single_media_large",
    )

    store.flush()
    row = _observation(store)
    assert row["input_tokens"] == 90
    assert row["output_tokens"] == 12
    assert row["quality_result"] == "pass"
    with connect(store.database) as connection:
        actions_json, outcomes_json, measurement_version = connection.execute(
            'SELECT actual_actions_json, action_outcomes_json, measurement_version FROM harness_strategy_observations WHERE execution_id = %s',
            ("execution-1",),
        ).fetchone()
    assert json_value(actions_json) == [
        "inline_media_history",
        "native_runtime_control:steer:context_pressure",
    ]
    assert json_value(outcomes_json) == [
        {"action": "inline_media_history", "reason": "", "status": "applied"},
        {
            # Attached but nothing to steer: kept, not counted as an action.
            "action": "native_tool_loop_governor",
            "reason": "native_tool_loop_governor_attached",
            "status": "no_action",
        },
        {
            "action": "tool_call_granularity",
            "reason": "RuntimeError",
            "status": "failed_open",
        },
        {
            "action": "native_runtime_control:steer:context_pressure",
            "reason": "",
            "status": "applied",
        },
        {
            "action": "native_runtime_control:steer:large_output_pressure",
            "reason": "TimeoutError",
            "status": "failed",
        },
    ]
    assert measurement_version == 7
    store.close()


def test_namespace_store_reuses_one_writer_and_close_releases_it(isolated_portal_case):
    """同命名空间复用写入器且关闭后重新创建"""
    with isolated_portal_case():
        first = HarnessEffectivenessStore.shared()
        second = HarnessEffectivenessStore.shared()
        assert first is second
        first.close()
        with pytest.raises(RuntimeError, match="closed"):
            first.record_quality("late", passed=True, source="test")
        replacement = HarnessEffectivenessStore.shared()
        assert replacement is not first
        replacement.close()


def test_async_writer_recovers_after_one_failed_write(tmp_path: Path) -> None:
    """异步写入器在一次写入失败后继续处理后续记录"""
    store = HarnessEffectivenessStore()
    original_write = store._write
    calls = 0

    def fail_once(kind, payload):
        nonlocal calls
        calls += 1
        if calls <= 4:
            raise LockNotAvailable("row lock unavailable")
        return original_write(kind, payload)

    store._write = fail_once
    store.record_quality("first", passed=True, source="test")
    store.flush()
    assert store.health()["write_failure_count"] == 1
    assert store.health()["writer_alive"] is True

    store.record_quality("second", passed=True, source="test")
    store.flush()
    assert store.health()["write_failure_count"] == 1
    store.close()


def test_evidence_writes_retry_transient_row_lock(tmp_path: Path) -> None:
    """证据写入遇短暂行锁冲突时重试"""
    store = HarnessEffectivenessStore()
    original_write = store._write
    attempts = 0

    def lock_once(kind, payload):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise LockNotAvailable("row lock unavailable")
        return original_write(kind, payload)

    store._write = lock_once
    store.record_quality("retried-label", result="pass", source="test")
    store.flush()

    assert attempts == 2
    assert store.health()["write_failure_count"] == 0
    store.close()


def test_load_family_prefers_specific_stress_shape() -> None:
    """负载分类优先识别多媒体、工具风暴和严重上下文压力"""
    assert load_family({"current_media_count": 3}) == "multi_media"
    assert load_family({"previous_tool_calls": 90}) == "tool_storm"
    assert load_family({"context_pressure_percent": 80}) == (
        "context_pressure_severe"
    )


def test_quality_feedback_supports_score_reason_and_last_label_wins_contracts(tmp_path, subtests, isolated_portal_case, monkeypatch):
    """质量反馈支持分数原因且后写标签覆盖旧标签"""
    with subtests.test(contract='test_quality_feedback_supports_score_reason_and_last_label_wins'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessEffectivenessStore()
        store.record_turn(
            decision=_decision("quality-update"),
            details={"status": "completed", "total_wall_ms": 10},
            result={"output": "ok"},
        )
        store.record_quality(
            "quality-update",
            result="fail",
            source="user_feedback",
            score=0.2,
            reason="answer missed required section",
        )
        store.record_quality(
            "quality-update",
            result="pass",
            source="task_oracle",
            score=0.9,
            reason="corrected result passed",
        )
        row = _observation(store)
        assert row["quality_result"] == "pass"
        assert row["quality_score"] == 0.9
        with pytest.raises(ValueError, match="between 0 and 1"):
            store.record_quality(
                "bad-score", result="pass", source="test", score=1.1
            )
        store.close()
    with subtests.test(contract='test_outdated_schema_is_rejected_without_runtime_migration'), isolated_portal_case() as case_path:
        tmp_path = case_path
        with monkeypatch.context() as isolated:
            isolated.setenv(
                "PORTAL_DATABASE_NAMESPACE",
                f"{os.environ['PORTAL_DATABASE_NAMESPACE'][:32]}_hc",
            )
            first = HarnessEffectivenessStore()
            with first._connect():
                pass
            with connect(first.database) as connection:
                connection.execute("DROP VIEW harness_observations")
                connection.execute(
                    "ALTER TABLE harness_strategy_observations "
                    "DROP COLUMN provider_latency_observed"
                )
                connection.execute(
                    "ALTER TABLE harness_strategy_observations "
                    "DROP COLUMN queue_wait_observed"
                )
                connection.execute(
                    "ALTER TABLE harness_strategy_observations "
                    "DROP COLUMN action_outcomes_json"
                )
            first.close()

            outdated = HarnessEffectivenessStore()
            with pytest.raises(RuntimeError, match="does not match its schema"):
                with outdated._connect():
                    pass
            outdated.close()
    with subtests.test(contract='test_async_evidence_writer_is_lossless_under_concurrent_labels'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessEffectivenessStore()

        def record(index: int) -> None:
            execution_id = f"concurrent-{index}"
            store.record_turn(
                decision=_decision(execution_id),
                details={
                    "status": "completed",
                    "total_wall_ms": index + 1,
                    "provider_wall_ms": index + 0.5,
                    "queue_wait_ms": 0,
                },
                result={"output": "ok"},
            )
            store.record_quality(
                execution_id,
                passed=True,
                source="concurrency_oracle",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(record, range(64)))

        store.flush()
        with connect(store.database) as connection:
            counts = connection.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE quality_result='pass'), COUNT(*) FILTER (WHERE status<>'completed') FROM harness_observations").fetchone()
        assert counts == (64, 64, 0)
        assert store.health()["write_failure_count"] == 0
        store.close()
