""":importance: P2"""
from __future__ import annotations

import base64
import io
import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal.execution.backend_contracts import BackendContext, BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.claude_backend import ClaudeBackend
from management_portal.execution.codex_backend import CodexBackend
from management_portal.execution.contracts import (
    ExecutionControlSignal,
    ExecutionRecoveryDeferred,
    ExecutionRequest,
    InputMedia,
    SessionOptions,
)
from management_portal.execution.harness import DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS, PORTAL_CONTROLLED_TOOL_RESULT_SOURCES, AgentLoopToolBatch, AgentLoopToolBatchTransform, AgentLoopToolBatchProcessor, AgentLoopToolResult, AgentHarness, ContextWindowObservation, HarnessFeatureOutcome, HarnessTool, InvocationTransformResult, default_agent_harness
from management_portal.execution.harness_capabilities import provider_capabilities
from management_portal.execution.harness_auto_policy import (
    AdaptiveHarnessController,
)
from management_portal.execution.harness_early_compaction import (
    EarlyContextCompactionFeature,
)
from management_portal.execution.harness_artifacts import HarnessArtifactStore
from management_portal.execution.harness_inline_media import InlineMediaHistoryFeature
from management_portal.execution.harness_native_items import approximate_inline_media_bytes
from management_portal.execution.harness_native_loop import NativeToolLoopGovernorFeature
from management_portal.execution.harness_provider_error_continue import (
    ProviderErrorContinueFeature,
)
from management_portal.execution.harness_observer import ContextSavingsObserver
from management_portal.execution.harness_read_dedup import HashAwareReadDedupFeature
from management_portal.execution.harness_tool_granularity import (
    TOOL_CALL_GRANULARITY_POLICY_MARKER,
    ToolCallGranularityFeature,
)
from management_portal.execution.harness_tool_output_offloading import READ_HARNESS_ARTIFACT_TOOL_NAME, ToolOutputOffloadingFeature
from management_portal.execution.session_manager import RuntimeSession

# A test double of an Adapter declares what it can do, as a real Adapter does
# in the Provider catalog.  No real Adapter rewrites history today.



@pytest.fixture(autouse=True)
def isolate_harness_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Default-construction tests must not append synthetic evidence to production.
    monkeypatch.setenv("PORTAL_EXECUTION_STATE_ROOT", str(tmp_path / "harness-state"))


class _RecordingProvider:
    provider_id = "codex"

    def __init__(self) -> None:
        self.invocations: list[BackendInvocation] = []

    def execute(
        self,
        invocation: BackendInvocation,
        _context: BackendContext,
    ) -> BackendResult:
        self.invocations.append(invocation)
        return BackendResult(
            output={"message": "done"},
            backend_session_id=invocation.session.backend_session_id,
            usage={
                "last": {"inputTokens": 10, "cachedInputTokens": 8},
                "ignored_text": "not telemetry",
            },
        )

    def interrupt(self, **_kwargs: object) -> bool:
        return False


class _NeverRunFeature:
    feature_id = "never_run"

    def apply(self, **_kwargs: object) -> HarnessFeatureOutcome:
        raise AssertionError("disabled AgentHarness feature was invoked")


def _invocation(harness: dict | None = None) -> BackendInvocation:
    harness_settings = (
        {"mode": "manual", **harness}
        if harness is not None
        else None
    )
    request = ExecutionRequest.new(
        actor="sample_admin",
        prompt="continue",
        service_type="agent_session",
        provider="codex",
        operation="turn",
        cwd="/tmp",
        runtime_options=(
            {"agent_harness": harness_settings}
            if harness_settings is not None
            else {}
        ),
        session=SessionOptions(
            mode="persistent",
            binding_id="resident:test",
            backend_route_id="zero",
        ),
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent",
            binding_id="resident:test",
            backend_session_id="019f0000-0000-7000-8000-000000000001",
            backend_route_id=request.session.backend_route_id,
            is_new_backend_session=False,
        ),
        user_prompt=request.prompt,
        system_context="",
        attach_system_context=False,
        is_new_backend_session=False,
    )


def _context(events: list[tuple[str, dict]]) -> BackendContext:
    return BackendContext(
        emit_event=lambda event_type, details: events.append(
            (event_type, details)
        ),
        cancellation_requested=lambda: False,
        set_interrupt_target=lambda *_args: None,
    )


def test_candidate_policy_requires_explicit_experiment_marker_contracts(tmp_path, subtests, isolated_portal_case):
    """候选 Harness 策略必须带显式实验标记

    未标明实验的请求不能启用候选策略，避免实验策略进入正常执行。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_policy_overrides_require_explicit_experiment_marker'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()

        with pytest.raises(ValueError, match="only accepted in a controlled experiment"):
            controller.resolve(
                _invocation({
                    "mode": "auto",
                    "policy_overrides": {"offload_trigger_bytes": 32 * 1024},
                    "features": {},
                }),
                {},
            )
        experiment_invocation = _invocation({
                "mode": "auto",
                "policy_overrides": {"offload_trigger_bytes": 32 * 1024},
                "features": {},
            })
        experiment_invocation = replace(
            experiment_invocation,
            request=replace(
                experiment_invocation.request,
                metadata={
                    "experiment_schema": "tfi.agent-harness-live-experiment.v1",
                    "experiment_arm": "candidate_policy",
                    "experiment_run_id": "run-1",
                    "experiment_pair_id": "pair-1",
                },
            ),
        )
        experiment = controller.resolve(
            experiment_invocation,
            {},
        )

        assert experiment.signals["policy_arm"] == "experiment"
        assert experiment.signals["policy_overrides"] == {
            "offload_trigger_bytes": 32 * 1024,
        }
        controller.effectiveness_store.close()
    with subtests.test(contract='test_promoted_champion_applies_only_to_its_cell'), isolated_portal_case() as case_path:
        tmp_path = case_path
        from management_portal.execution.harness_evolution import (
            HarnessEvolutionStore,
            PolicyCellCache,
        )

        store = HarnessEvolutionStore()
        store.start_challenger(
            "codex", "short_text",
            {"reasoning_downgrade_pressure_percent": 46.0},
            actor="unit-test",
        )
        store.promote("codex", "short_text", actor="unit-test")
        store.start_challenger(
            "claude", "short_text",
            {"reasoning_downgrade_pressure_percent": 58.0},
            actor="unit-test",
        )
        store.promote("claude", "short_text", actor="unit-test")
        PolicyCellCache.reset_for_tests()
        PolicyCellCache.shared().refresh()
        controller = AdaptiveHarnessController()

        decision = controller.resolve(
            _invocation({"mode": "auto", "features": {}}),
            {},
        )

        assert decision.signals["load_family"] == "short_text"
        assert decision.signals["policy_cell"] == "codex/short_text"
        assert decision.signals["policy_arm"] == "champion"
        assert decision.signals["policy_version"] == 2
        assert decision.signals["policy_overrides"] == {
            "reasoning_downgrade_pressure_percent": 46.0,
        }
        assert decision.signals["policy_selection_source"] == "policy_cell"
        controller.effectiveness_store.close()
    with subtests.test(contract='test_queue_wait_signal_preserves_observed_zero'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        without_queue = controller.resolve(
            _invocation({"mode": "auto", "features": {}}),
            {},
        )
        invocation = _invocation({"mode": "auto", "features": {}})
        invocation = replace(
            invocation,
            request=replace(
                invocation.request,
                metadata={"execution_queue_wait_ms": 0},
            ),
        )
        observed_zero = controller.resolve(invocation, {})

        assert without_queue.signals["queue_wait_observed"] is False
        assert observed_zero.signals["queue_wait_observed"] is True
        assert observed_zero.signals["queue_wait_ms"] == 0
        controller.effectiveness_store.close()
    with subtests.test(contract='test_after_tool_batch_offload_keeps_original_when_recall_schema_is_unknown'), isolated_portal_case() as case_path:
        tmp_path = case_path
        content = "large-existing-session-output-" + "E" * 8_000
        store = HarnessArtifactStore(tmp_path / "artifacts")
        feature = ToolOutputOffloadingFeature(store)
        invocation = feature.prepare_invocation(_invocation({
            "features": {
                "tool_output_offloading": {
                    "enabled": True,
                    "timing": "after_tool_batch",
                },
            },
        }))

        transformed = feature.after_tool_batch(
            batch=_plain_tool_batch(
                call_id="existing-large",
                content=content,
            ),
            provider=SimpleNamespace(provider_id="codex"),
            invocation=invocation,
            context=_context([]),
            config={"offload_trigger_bytes": 4 * 1024},
            state={},
        )

        assert transformed.applied is False
        assert transformed.reason == "artifact_recall_schema_unavailable"
        assert transformed.batch.items[0].result["contentItems"][0][
            "text"
        ] == content
        assert store.has_scope("v2:codex:resident:test") is False

        registered = feature.after_tool_batch(
            batch=_plain_tool_batch(
                call_id="registered-large",
                content=content,
            ),
            provider=SimpleNamespace(provider_id="codex"),
            invocation=replace(
                invocation,
                available_harness_tool_names=frozenset({
                    READ_HARNESS_ARTIFACT_TOOL_NAME,
                }),
            ),
            context=_context([]),
            config={"offload_trigger_bytes": 4 * 1024},
            state={},
        )
        assert registered.applied is True
        assert store.has_scope("v2:codex:resident:test") is True
    with subtests.test(contract='test_after_tool_batch_offload_never_reoffloads_recall_results'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessArtifactStore(tmp_path / "artifacts")
        feature = ToolOutputOffloadingFeature(store)
        invocation = feature.prepare_invocation(_invocation({
            "features": {
                "tool_output_offloading": {
                    "enabled": True,
                    "timing": "after_tool_batch",
                },
            },
        }))
        invocation = replace(
            invocation,
            available_harness_tool_names=frozenset({
                READ_HARNESS_ARTIFACT_TOOL_NAME,
            }),
        )
        content = "recalled-output-" + "R" * 20_000

        transformed = feature.after_tool_batch(
            batch=_plain_tool_batch(
                call_id="recall-output",
                content=content,
                tool_name=READ_HARNESS_ARTIFACT_TOOL_NAME,
                source="harness",
            ),
            provider=SimpleNamespace(provider_id="codex"),
            invocation=invocation,
            context=_context([]),
            config={"offload_trigger_bytes": 4 * 1024},
            state={},
        )

        assert transformed.applied is False
        assert transformed.reason == "no_current_turn_oversized_tool_outputs"
        assert transformed.batch.items[0].result["contentItems"][0][
            "text"
        ] == content
        assert store.has_scope("v2:codex:resident:test") is False
    with subtests.test(contract='test_default_agent_harness_auto_mode_observes_empty_short_turn_plan'), isolated_portal_case() as case_path:
        tmp_path = case_path
        provider = _RecordingProvider()
        harness = default_agent_harness(state_root=tmp_path)
        service = AgentSessionService(
            {"codex": provider},
            harness=harness,
        )
        events: list[tuple[str, dict]] = []

        service.execute(
            _invocation({"mode": "auto", "features": {}}),
            _context(events),
        )

        assert [event_type for event_type, _details in events] == [
            "agent_harness_prepared",
            "agent_harness_completed",
        ]
        reports = {
            report["feature_id"]: report
            for report in events[0][1]["features"]
        }
        assert reports["native_tool_loop_governor"]["status"] == "applied"
        assert reports["native_tool_loop_governor"]["action_applied"] is False
        assert provider.invocations[0].provider_event_processor is not None
        assert provider.invocations[0].provider_error_recovery is not None
        assert (
            provider.invocations[0]
            .provider_error_recovery.continuation_prompt
            == "continue"
        )
        assert (
            provider.invocations[0]
            .request.runtime_options["agent_harness"]["mode"]
            == "manual"
        )
        harness._adaptive_controller.effectiveness_store.flush()
        harness._adaptive_controller.effectiveness_store.close()


def _plain_tool_batch(
    *,
    call_id: str,
    content: str,
    tool_name: str = "read_file",
    arguments: dict | None = None,
    source: str = "harness",
) -> AgentLoopToolBatch:
    return AgentLoopToolBatch(items=(AgentLoopToolResult(
        call_id=call_id,
        tool_name=tool_name,
        arguments=dict(arguments or {"path": "same.py"}),
        result={
            "success": True,
            "contentItems": [{"type": "inputText", "text": content}],
        },
        source=source,
    ),))


def test_agent_harness_with_all_plugins_off_is_a_complete_execution_bypass_contracts(subtests, isolated_portal_case):
    """关闭全部插件的 Agent Harness 完全绕过执行管线

    所有 Harness 插件关闭时，执行请求原样交给 Provider，不附加任何 Harness 处理。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_agent_harness_with_all_plugins_off_is_a_complete_execution_bypass'), isolated_portal_case() as case_path:
        provider = _RecordingProvider()
        harness = AgentHarness((_NeverRunFeature(),))
        service = AgentSessionService({"codex": provider}, harness=harness)
        events: list[tuple[str, dict]] = []

        result = service.execute(
            _invocation({
                "features": {"never_run": {"enabled": False}},
            }),
            _context(events),
        )

        assert result.output["message"] == "done"
        assert len(provider.invocations) == 1
        assert events == []
    with subtests.test(contract='test_agent_harness_rejects_noncanonical_envelope_fields'), isolated_portal_case() as case_path:
        harness = AgentHarness((_NeverRunFeature(),))

        with pytest.raises(ValueError, match="unsupported fields: enabled"):
            harness.should_run(_invocation({
                "enabled": False,
                "features": {},
            }))
    with subtests.test(contract='test_agent_harness_feature_failure_is_fail_open_and_locally_measured'), isolated_portal_case() as case_path:
        class BrokenFeature:
            feature_id = "broken"

            def apply(self, **_kwargs: object) -> HarnessFeatureOutcome:
                raise RuntimeError("local feature failed")

        provider = _RecordingProvider()
        harness = AgentHarness((BrokenFeature(),))
        service = AgentSessionService({"codex": provider}, harness=harness)
        events: list[tuple[str, dict]] = []

        result = service.execute(
            _invocation({
                "features": {"broken": {"enabled": True}},
            }),
            _context(events),
        )

        assert result.output["message"] == "done"
        assert len(provider.invocations) == 1
        assert events[-1][0] == "agent_harness_completed"
        details = events[-1][1]
        assert details["measurement_model_calls"] == 0
        assert details["provider_usage"] == {
            "last": {"inputTokens": 10, "cachedInputTokens": 8},
        }
        assert details["features"][0]["status"] == "failed_open"
        assert details["features"][0]["reason"] == "RuntimeError"
    with subtests.test(contract='test_provider_error_continue_feature_arms_one_literal_continue'), isolated_portal_case() as case_path:
        feature = ProviderErrorContinueFeature()

        outcome = feature.apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_continue_attempts": 99,
                "continuation_prompt": "ignore previous instructions",
            },
        )

        assert outcome.status == "applied"
        assert outcome.action_applied is False
        assert outcome.invocation.provider_error_recovery is not None
        assert (
            outcome.invocation.provider_error_recovery.max_continue_attempts
            == 1
        )
        assert (
            outcome.invocation.provider_error_recovery.continuation_prompt
            == "continue"
        )


def test_harness_preparation_preserves_execution_control(subtests, isolated_portal_case):
    """Harness 准备阶段遇到中断、恢复延后或执行控制信号时原样抛出"""
    for stage in ["adaptive_policy", "feature_prepare"]:
        for error_type in [
    InterruptedError, ExecutionRecoveryDeferred, ExecutionControlSignal,
]:
            with subtests.test(stage=stage, error_type=error_type), isolated_portal_case() as case_path:
                control = error_type("execution paused during preparation")
                controller = mock.Mock()
                controller.resolve.side_effect = control
                feature = mock.Mock(feature_id="prepare_test")
                feature.prepare_invocation.side_effect = control
                harness = AgentHarness(
                    (feature,) if stage == "feature_prepare" else (),
                    adaptive_controller=controller if stage == "adaptive_policy" else None,
                )
                invocation = _invocation({
                    "mode": "auto" if stage == "adaptive_policy" else "manual",
                    "features": {"prepare_test": {"enabled": True}},
                })

                with pytest.raises(error_type) as raised:
                    harness.prepare_invocation(invocation)

                assert raised.value is control




def test_harness_preparation_ordinary_failures_remain_fail_open(subtests, isolated_portal_case):
    """Harness 准备阶段的普通异常不阻断执行，原调用照常返回"""
    for stage in ["adaptive_policy", "feature_prepare"]:
        with subtests.test(stage=stage), isolated_portal_case() as case_path:
            controller = mock.Mock()
            controller.resolve.side_effect = RuntimeError("local policy failure")
            feature = mock.Mock(feature_id="prepare_test")
            feature.prepare_invocation.side_effect = RuntimeError("local prepare failure")
            harness = AgentHarness(
                (feature,) if stage == "feature_prepare" else (),
                adaptive_controller=controller if stage == "adaptive_policy" else None,
            )
            invocation = _invocation({
                "mode": "auto" if stage == "adaptive_policy" else "manual",
                "features": {"prepare_test": {"enabled": True}},
            })

            assert harness.prepare_invocation(invocation) is invocation


def test_harness_turn_policy_hooks_preserve_execution_control(subtests, isolated_portal_case):
    """Harness 回合内的自适应策略钩子遇到中断、恢复延后或执行控制信号时原样抛出

    自适应策略开始回合、给出插件配置、观察工具批次和记录插件结果时抛出的执行控制信号属于 Worker，不能被当作可降级的优化失败吞掉；普通异常仍然降级，Provider 照常返回。
    """

    class CapturingFeature:
        feature_id = "tool_output_offloading"

        def after_tool_batch(self, *, batch: AgentLoopToolBatch, **_kwargs: object) -> AgentLoopToolBatchTransform:
            return AgentLoopToolBatchTransform(batch=batch, applied=False, reason="captured")

    class Provider(_RecordingProvider):
        def execute(self, invocation: BackendInvocation, context: BackendContext) -> BackendResult:
            del context
            invocation.tool_batch_processor.process(_plain_tool_batch(call_id="call-1", content="output"))
            return BackendResult(output={"message": "done"})

    for stage in ["begin_turn", "feature_config", "observe_tool_batch", "record_feature_result"]:
        for error_type in [InterruptedError, ExecutionRecoveryDeferred, ExecutionControlSignal, RuntimeError]:
            with subtests.test(stage=stage, error_type=error_type), isolated_portal_case():
                error = error_type(f"{stage} failed")
                runtime = mock.Mock()
                runtime.feature_config.side_effect = lambda _feature_id, config: config
                runtime.report.return_value = {}
                controller = mock.Mock()
                controller.resolve.return_value = SimpleNamespace(
                    features={"tool_output_offloading": {"enabled": True, "timing": "after_tool_batch"}},
                    report=lambda: {},
                )
                controller.begin_turn.return_value = runtime
                controller.observe.return_value = True
                if stage == "begin_turn":
                    controller.begin_turn.side_effect = error
                else:
                    getattr(runtime, stage).side_effect = error
                harness = AgentHarness((CapturingFeature(),), adaptive_controller=controller)
                invocation = replace(_invocation({"mode": "auto"}), harness_tools=(object(),))
                provider = Provider()

                def run() -> BackendResult:
                    return harness.execute(
                        provider=provider, invocation=invocation, context=_context([]),
                        execute=provider.execute,
                    )

                if error_type is RuntimeError:
                    assert run().output == {"message": "done"}
                else:
                    with pytest.raises(error_type) as raised:
                        run()
                    assert raised.value is error




def test_auto_policy_preserves_planned_batch_thresholds_before_model_reentry(
    tmp_path: Path,
) -> None:
    """auto 策略遇到大工具输出时转为 protective 档，工具批次卸载阈值降到 64 KiB"""
    configs: list[dict] = []

    class CapturingOffloadFeature:
        feature_id = "tool_output_offloading"

        def after_tool_batch(
            self,
            *,
            batch: AgentLoopToolBatch,
            config: dict,
            **_kwargs: object,
        ) -> AgentLoopToolBatchTransform:
            configs.append(dict(config))
            return AgentLoopToolBatchTransform(
                batch=batch,
                applied=False,
                reason="captured",
            )

    class Provider(_RecordingProvider):
        def execute(
            self,
            invocation: BackendInvocation,
            context: BackendContext,
        ) -> BackendResult:
            del context
            processor = invocation.tool_batch_processor
            assert processor is not None
            processor.process(_plain_tool_batch(
                call_id="large-current-turn-output",
                content="X" * (300 * 1024),
            ))
            return BackendResult(output={"message": "done"})

    events: list[tuple[str, dict]] = []
    service = AgentSessionService(
        {"codex": Provider()},
        harness=AgentHarness(
            (CapturingOffloadFeature(),),
            adaptive_controller=AdaptiveHarnessController(
                ),
        ),
    )
    invocation = replace(
        _invocation({
            "mode": "auto",
            "features": {
                "tool_output_offloading": {
                    "enabled": True,
                    "timing": "after_tool_batch",
                    "offload_trigger_bytes": 256 * 1024,
                },
            },
        }),
        harness_tools=(object(),),
    )

    service.execute(invocation, _context(events))

    assert configs[0]["offload_trigger_bytes"] == 64 * 1024
    runtime = events[-1][1]["adaptive_policy"]["turn_runtime"]
    assert runtime["effective_profile"] == "protective"
    assert runtime["portal_output_bytes_observed"] > 300 * 1024


@pytest.mark.parametrize(
    "source",
    sorted(PORTAL_CONTROLLED_TOOL_RESULT_SOURCES),
)
def test_current_turn_dedup_covers_every_portal_controlled_source(
    source: str,
) -> None:
    """本轮重复的只读工具输出在每一种 Portal 控制来源下都被去重"""
    feature = HashAwareReadDedupFeature()
    state: dict[str, object] = {}
    content = "shared-output-" + "D" * 2_000

    first = feature.after_tool_batch(
        batch=_plain_tool_batch(
            call_id=f"{source}-first",
            content=content,
            source=source,
        ),
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(),
        context=_context([]),
        config={"min_output_tokens": 32},
        state=state,
    )
    second = feature.after_tool_batch(
        batch=_plain_tool_batch(
            call_id=f"{source}-second",
            content=content,
            source=source,
        ),
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(),
        context=_context([]),
        config={"min_output_tokens": 32},
        state=state,
    )

    assert first.applied is False
    assert second.applied is True
    assert "duplicate current-turn read-only" in (
        second.batch.items[0].result["contentItems"][0]["text"]
    )




def test_after_tool_batch_offloads_exact_output_before_model_reentry(
    tmp_path: Path,
) -> None:
    """工具批次结束后超阈值的输出原样卸载为 artifact，回到模型前替换为可召回的占位"""
    content = "large-current-turn-output-" + "O" * 24_000
    store = HarnessArtifactStore(tmp_path / "artifacts")

    class Provider(_RecordingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.processed: AgentLoopToolBatch | None = None

        @staticmethod
        def observe_context_window(*, invocation):
            del invocation
            return ContextWindowObservation(tokens=4_000)

        def execute(
            self,
            invocation: BackendInvocation,
            context: BackendContext,
        ) -> BackendResult:
            del context
            self.invocations.append(invocation)
            processor = invocation.tool_batch_processor
            assert processor is not None
            assert processor.output_bytes_cap == 16 * 1024 * 1024
            self.processed = processor.process(_plain_tool_batch(
                call_id="large-output",
                content=content,
                tool_name="read_file",
            ))
            return BackendResult(output={"message": "done"})

    provider = Provider()
    feature = ToolOutputOffloadingFeature(store)
    service = AgentSessionService(
        {"codex": provider},
        harness=AgentHarness((feature,)),
    )
    events: list[tuple[str, dict]] = []

    invocation = _invocation({
        "features": {
            "tool_output_offloading": {
                "enabled": True,
                "timing": "after_tool_batch",
                "offload_trigger_bytes": 16 * 1024,
                "preview_chars": 256,
            },
        },
    })
    invocation = replace(
        invocation,
        session=replace(
            invocation.session,
            mode="persistent",
            backend_session_id="",
            backend_route_id="",
            is_new_backend_session=True,
        ),
        is_new_backend_session=True,
    )
    service.execute(
        invocation,
        _context(events),
    )

    assert provider.processed is not None
    placeholder = provider.processed.items[0].result["contentItems"][0][
        "text"
    ]
    handle_match = re.search(r"ha1_[0-9a-f]{64}", placeholder)
    assert handle_match is not None
    handle = handle_match.group(0)
    assert READ_HARNESS_ARTIFACT_TOOL_NAME in placeholder
    assert store.read(
        handle,
        scope="v2:codex:resident:test",
        limit=32_000,
    )["content"] == content
    assert any(
        tool.name == READ_HARNESS_ARTIFACT_TOOL_NAME
        for tool in provider.invocations[0].harness_tools
    )
    report = next(
        report
        for report in events[-1][1]["features"]
        if report["lifecycle"] == "after_tool_batch"
    )
    assert report["status"] == "applied"
    assert report["metrics"]["tool_output_count_offloaded"] == 1
    assert report["metrics"]["configured_offload_trigger_bytes"] == (
        16 * 1024
    )
    assert report["metrics"]["effective_offload_trigger_bytes"] == (
        16 * 1024
    )
    assert report["metrics"]["context_pressure_percent"] >= 75
    assert events[-1][1]["context_savings"][
        "current_turn_tool_tokens_estimated_removed"
    ] > 0




def test_failure_continue_switch_controls_provider_recovery_in_both_modes(tmp_path, subtests, isolated_portal_case):
    """auto 与 manual 模式下都由失败续跑开关决定是否启用一次 continue 恢复"""
    for mode in ["auto", "manual"]:
        for enabled in [False, True]:
            with subtests.test(mode=mode, enabled=enabled), isolated_portal_case() as case_path:
                tmp_path = case_path
                provider = _RecordingProvider()
                service = AgentSessionService(
                    {"codex": provider}, harness=default_agent_harness(state_root=tmp_path),
                )
                service.execute(
                    _invocation({
                        "mode": mode,
                        "features": {"provider_error_continue": {"enabled": enabled}},
                    }),
                    _context([]),
                )

                recovery = provider.invocations[0].provider_error_recovery
                assert (recovery is not None) is enabled
                if enabled:
                    assert recovery.max_continue_attempts == 1
                    assert recovery.continuation_prompt == "continue"


def test_default_agent_harness_auto_keeps_large_input_image_lossless_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """默认自动 Harness 不压缩大尺寸输入图片

    默认 auto 策略下，用户输入的大图片保持无损，不被预处理压缩。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_default_agent_harness_auto_keeps_large_input_image_lossless'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            from PIL import Image
            from management_portal.execution import harness_inline_media

            source = tmp_path / "large.bmp"
            Image.new("RGB", (3000, 1800), "white").save(source)
            invocation = _invocation({"mode": "auto", "features": {}})
            request = replace(
                invocation.request,
                input_media=(InputMedia(
                    media_type="image",
                    source_type="local_path",
                    source=str(source),
                ),),
            )
            provider = _RecordingProvider()
            events: list[tuple[str, dict]] = []
            service = AgentSessionService(
                {"codex": provider},
                harness=default_agent_harness(state_root=tmp_path / "state"),
            )

            service.execute(replace(invocation, request=request), _context(events))

            rewritten = Path(provider.invocations[0].request.input_media[0].source)
            assert rewritten == source
            prepared = next(details for kind, details in events if kind == "agent_harness_prepared")
            policy = prepared["adaptive_policy"]
            assert policy["signals"]["policy_arm"] == "champion"
            assert policy["signals"]["policy_overrides"] == {}
            assert policy["signals"]["large_input_image_signal"] is True
            report = next(
                item
                for item in prepared["features"]
                if item["feature_id"] == "inline_media_history"
            )
            assert report["metrics"].get("input_images_preprocessed", 0) == 0


def test_auto_mode_prunes_current_batch_images_before_model_reentry(
    tmp_path,
) -> None:
    """auto 模式回到模型前删去当前工具批次中较早的大图，只保留最近一张"""
    old_url = "data:image/png;base64," + "A" * 300_000
    recent_url = "data:image/png;base64," + "B" * 300_000

    class Provider(_RecordingProvider):
        def __init__(self) -> None:
            super().__init__()
            self.processed: AgentLoopToolBatch | None = None

        def execute(
            self,
            invocation: BackendInvocation,
            _context: BackendContext,
        ) -> BackendResult:
            self.invocations.append(invocation)
            processor = invocation.tool_batch_processor
            assert processor is not None
            self.processed = processor.process(AgentLoopToolBatch(items=(
                AgentLoopToolResult(
                    call_id="images-auto",
                    tool_name="render_images",
                    arguments={},
                    result={
                        "success": True,
                        "contentItems": [
                            {"type": "inputImage", "imageUrl": old_url},
                            {"type": "inputImage", "imageUrl": recent_url},
                        ],
                    },
                    source="harness",
                ),
            )))
            return BackendResult(output={"message": "done"})

    provider = Provider()
    service = AgentSessionService(
        {"codex": provider},
        harness=default_agent_harness(state_root=tmp_path),
    )
    events: list[tuple[str, dict]] = []

    invocation = _invocation({"mode": "auto", "features": {}})
    request = replace(
        invocation.request,
        runtime_options={
            **invocation.request.runtime_options,

        },
    )
    service.execute(replace(invocation, request=request, harness_tools=(HarnessTool(name="render_images", description="Render", input_schema={}, handler=lambda args: ""),)), _context(events))

    assert provider.processed is not None
    parts = provider.processed.items[0].result["contentItems"]
    assert parts[0]["type"] == "inputText"
    assert "AgentHarness omitted" in parts[0]["text"]
    assert parts[1]["imageUrl"] == recent_url
    reports = [
        report for report in events[-1][1]["features"]
        if report["feature_id"] == "inline_media_history"
        and report["lifecycle"] == "after_tool_batch"
    ]
    assert reports[0]["metrics"]["inline_image_count_removed"] == 1


def test_default_agent_harness_can_still_be_fully_bypassed_contracts(subtests, isolated_portal_case):
    """默认 Agent Harness 仍可整体绕过

    即使使用默认 auto Harness，执行请求也能显式选择完全绕过。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_default_agent_harness_can_still_be_fully_bypassed'), isolated_portal_case() as case_path:
        provider = _RecordingProvider()
        service = AgentSessionService(
            {"codex": provider},
            harness=default_agent_harness(),
        )
        events: list[tuple[str, dict]] = []

        service.execute(
            _invocation({
                "features": {
                    feature_id: {"enabled": False}
                    for feature_id in DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS
                },
            }),
            _context(events),
        )

        assert provider.invocations[0].user_prompt == "continue"
        assert events == []
    with subtests.test(contract='test_native_tool_loop_discards_pending_controls_after_final_answer'), isolated_portal_case() as case_path:
        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_checkpoints": 1,
                "large_output_bytes": 64 * 1024,
            },
        )
        processor = outcome.invocation.provider_event_processor
        assert processor is not None
        processor.process_event({
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "command": "pwd",
                    "status": "completed",
                    "aggregatedOutput": "x" * (70 * 1024),
                },
            },
        })
        processor.process_event({
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "answer-1",
                    "type": "agentMessage",
                    "phase": "final_answer",
                    "text": "finished",
                },
            },
        })

        assert processor.next_control() is None
        assert processor.report()["runtime_controls_suppressed"] == 1
    with subtests.test(contract='test_native_tool_loop_discards_pending_controls_after_turn_completed'), isolated_portal_case() as case_path:
        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_checkpoints": 1,
                "large_output_bytes": 64 * 1024,
            },
        )
        processor = outcome.invocation.provider_event_processor
        assert processor is not None
        processor.process_event({
            "method": "item/completed",
            "params": {
                "item": {
                    "id": "command-1",
                    "type": "commandExecution",
                    "command": "pwd",
                    "status": "completed",
                    "aggregatedOutput": "x" * (70 * 1024),
                },
            },
        })
        processor.process_event({
            "method": "turn/completed",
            "params": {"turn": {"status": "completed"}},
        })

        assert processor.next_control() is None
        assert processor.report()["runtime_controls_suppressed"] == 1
    with subtests.test(contract='test_post_tool_provider_latency_is_governed_during_the_active_turn'), isolated_portal_case() as case_path:
        now = [200.0]
        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_checkpoints": 5,
                "provider_latency_threshold_seconds": 5,
                "configured_reasoning_effort": "max",
                "initial_reasoning_effort": "max",
                "adaptive_reasoning_effort": True,
                "_monotonic": lambda: now[0],
            },
        )
        processor = outcome.invocation.provider_event_processor
        assert processor is not None
        processor.process_event({
            "method": "item/completed",
            "params": {"item": {
                "id": "command-1",
                "type": "commandExecution",
                "command": "pwd",
                "status": "completed",
                "exitCode": 0,
            }},
        })

        now[0] += 4.9
        assert processor.next_control() is None
        now[0] += 0.2
        control = processor.next_control()
        assert control is not None
        assert control["reason"] == "provider_latency_pressure"
        assert control.get("action", "steer") == "steer"
        assert "effort" not in control

        processor.record_control_result(control, True, "")
        report = processor.report()
        assert report["provider_latency_checks"] >= 2
        assert report["max_provider_silence_seconds"] >= 5



def test_native_image_observation_and_artifact_store_contracts(tmp_path, subtests, isolated_portal_case):
    """原生图片观察不打断进行中的回合，Harness 产物存储按内容与范围去重并精确分块读取

    Provider 已持有的大图片只计入原生内联媒体估算，不产生控制动作，也不计入工具内联媒体；同一范围内相同内容（包括重新打开存储后）得到同一句柄，不同范围得到不同句柄，分块读取返回准确的片段、下一偏移和结束标记。

    :module: execution.harness
    :importance: P1
    """
    with subtests.test(contract='test_native_input_image_observation_does_not_interrupt_active_turn'), isolated_portal_case() as case_path:
        tmp_path = case_path
        image_path = tmp_path / "already-provider-owned-large-image.png"
        image_path.write_bytes(b"A" * 80_000)
        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_checkpoints": 5,
                "media_pressure_bytes": 64 * 1024,
            },
        )
        processor = outcome.invocation.provider_event_processor
        assert processor is not None

        processor.process_event({
            "method": "item/completed",
            "params": {"item": {
                "id": "image-view-1",
                "type": "imageView",
                "status": "completed",
                "path": str(image_path),
            }},
        })

        assert processor.next_control() is None
        report = processor.report()
        assert report["native_inline_media_bytes_estimated"] >= 80_000
        assert report["native_tool_inline_media_bytes_estimated"] == 0

    with subtests.test(contract='test_harness_artifact_store_is_exact_chunked_and_scope_stable'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessArtifactStore(tmp_path / "artifacts")
        content = "prefix-" + "内容" * 2_000 + "-suffix"

        first = store.put(scope="resident:one", value=content)
        repeated = store.put(scope="resident:one", value=content)
        reopened = HarnessArtifactStore(tmp_path / "artifacts").put(
            scope="resident:one",
            value=content,
        )
        another_scope = store.put(scope="resident:two", value=content)
        chunk = store.read(first.handle, offset=7, limit=9)

        assert repeated.handle == first.handle
        assert reopened.handle == first.handle
        assert another_scope.handle != first.handle
        assert store.has_scope("resident:one") is True
        assert chunk["content"] == content[7:16]
        assert chunk["next_offset"] == 16
        assert chunk["eof"] is False
        assert store.read(first.handle, limit=32_000)["content"] == content
        assert store.read(
            first.handle,
            scope="resident:one",
            limit=32_000,
        )["content"] == content
        with pytest.raises(KeyError):
            store.read(first.handle, scope="resident:two")
        structured = [
            {"type": "input_text", "text": "exact"},
            {"nested": {"value": 7}},
        ]
        structured_ref = store.put(scope="resident:one", value=structured)
        assert json.loads(
            store.read(structured_ref.handle, limit=32_000)["content"]
        ) == structured


def test_native_tool_loop_governor_uses_semantic_ledger_and_soft_steer() -> None:
    """原生工具循环治理按语义键识别重复读取，并以中文软检查点引导模型"""
    events: list[tuple[str, dict]] = []
    outcome = NativeToolLoopGovernorFeature().apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(),
        context=_context(events),
        config={
            "checkpoint_interval": 80,
            "max_checkpoints": 1,
            "duplicate_read_threshold": 2,
            "large_output_bytes": 64 * 1024,
        },
    )
    processor = outcome.invocation.provider_event_processor
    assert processor is not None
    read_event = {
        "method": "item/completed",
        "params": {
            "item": {
                "id": "command-1",
                "type": "commandExecution",
                "command": "sed -n '1,80p' src/app.py",
                "cwd": "/repo",
                "status": "completed",
                "commandActions": [{
                    "type": "read",
                    "command": "sed",
                    "name": "src/app.py",
                    "path": "src/app.py",
                }],
                "aggregatedOutput": "source",
            },
        },
    }
    for _index in range(3):
        processor.process_event(read_event)

    controls = []
    while control := processor.next_control():
        controls.append(control)
    control = next(
        item for item in controls
        if item["reason"] == "duplicate_read_pressure"
    )
    control_text = control["input"][0]["text"]
    assert "<portal-milestone-handoff" not in control_text
    assert "原有目标与完成条件保持不变" in control_text
    assert "依据已确认的进度和证据完成尚未完成的工作" in control_text
    assert "当前任务的运行优化提示" in control_text
    assert "不要回复、确认或复述本检查点" in control_text
    assert "最终只向用户交付原任务结果" in control_text
    assert "This is a soft runtime checkpoint" not in control_text
    processor.record_control_result(control, True, "")
    report = processor.report()
    assert report["native_tool_calls_observed"] == 3
    assert report["semantic_read_keys_observed"] == 1
    assert report["duplicate_read_calls_observed"] == 2
    assert report["runtime_controls_applied"] == 1
    assert report["effectiveness_state"] == "control_applied"
    assert report["native_output_transform_available"] is False
    assert outcome.metrics["granularity_strategy"] == "auto"
    assert outcome.metrics["confirm_singleton_reads"] == 1
    assert events[-1][0] == "agent_harness_runtime_checkpoint"


def test_context_pressure_observes_native_compaction_without_interrupt() -> None:
    """上下文压力只发一次软引导而不中断轮次，观察到原生压缩后不再引导"""
    outcome = NativeToolLoopGovernorFeature().apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(),
        context=_context([]),
        config={
            "max_checkpoints": 1,

            "context_pressure_threshold_percent": 70,
        },
    )
    processor = outcome.invocation.provider_event_processor
    assert processor is not None

    usage_event = {
        "method": "thread/tokenUsage/updated",
        "params": {"tokenUsage": {
            "last": {"inputTokens": 80},
            "modelContextWindow": 100,
        }},
    }
    tool_event = {
        "method": "item/completed",
        "params": {"item": {
            "id": "command-1",
            "type": "commandExecution",
            "command": "pwd",
            "status": "completed",
        }},
    }
    processor.process_event(usage_event)
    processor.process_event(tool_event)
    first = processor.next_control()
    assert first is not None
    assert first["reason"] == "context_pressure"
    assert first.get("action", "steer") == "steer"
    assert first["metadata"]["kind"] == "safety"
    assert "<portal-milestone-handoff" not in first["input"][0]["text"]
    assert "原有目标与完成条件保持不变" in first["input"][0]["text"]
    processor.record_control_result(first, True, "")

    for index in range(2):
        processor.process_event({
            "method": "item/completed",
            "params": {"item": {
                "id": f"compact-{index}",
                "type": "contextCompaction",
                "status": "completed",
            }},
        })
    processor.process_event(usage_event)
    second = processor.next_control()
    assert second is None

    report = processor.report()
    assert report["context_compactions_observed"] == 2


    assert all(
        outcome["action"].endswith(":context_pressure")
        for outcome in report["runtime_action_outcomes"]
    )


def test_native_media_pressure_steers_without_compaction_or_effort_change() -> None:
    """内联图片压力只发一次软引导，不触发压缩也不调整推理强度"""
    now = [100.0]
    outcome = NativeToolLoopGovernorFeature().apply(
        provider=SimpleNamespace(provider_id="codex"),
        invocation=_invocation(),
        context=_context([]),
        config={
            "max_checkpoints": 5,
            "media_pressure_bytes": 64 * 1024,
            "configured_reasoning_effort": "max",
            "initial_reasoning_effort": "medium",
            "adaptive_reasoning_effort": True,

            "_monotonic": lambda: now[0],
        },
    )
    processor = outcome.invocation.provider_event_processor
    assert processor is not None

    def image_event(call_id: str, marker: str) -> dict:
        return {
            "method": "item/completed",
            "params": {"item": {
                "id": call_id,
                "type": "dynamicToolCall",
                "status": "completed",
                "contentItems": [{
                    "type": "inputImage",
                    "imageUrl": (
                        "data:image/png;base64," + marker * 80_000
                    ),
                }],
            }},
        }

    processor.process_event(image_event("image-1", "A"))
    first = processor.next_control()
    assert first is not None
    assert first["reason"] == "native_inline_media_pressure"
    assert first.get("action", "steer") == "steer"
    assert "effort" not in first
    processor.record_control_result(first, True, "")

    now[0] += 1
    processor.process_event(image_event("image-2", "B"))
    second = processor.next_control()
    assert second is None

    report = processor.report()
    assert report["native_inline_media_items_observed"] == 2
    assert report["ungoverned_inline_media_bytes"] > 0




def test_provider_latency_is_governed_before_the_first_provider_event_contracts(subtests, isolated_portal_case):
    """Provider 首个事件之前的延迟也受治理

    Provider 迟迟没有产生第一个事件时，Harness 同样按延迟规则处理，而不是等到首个事件后才计时。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_provider_latency_is_governed_before_the_first_provider_event'), isolated_portal_case() as case_path:
        now = [300.0]
        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation(),
            context=_context([]),
            config={
                "max_checkpoints": 5,
                "provider_latency_threshold_seconds": 5,

                "configured_reasoning_effort": "max",
                "initial_reasoning_effort": "max",
                "adaptive_reasoning_effort": True,
                "_monotonic": lambda: now[0],
            },
        )
        processor = outcome.invocation.provider_event_processor
        assert processor is not None

        now[0] += 5.1
        first = processor.next_control()
        assert first is not None
        assert first["reason"] == "provider_latency_pressure"
        assert first.get("action", "steer") == "steer"
        assert "effort" not in first
        processor.record_control_result(first, True, "")

        now[0] += 4.9
        assert processor.next_control() is None
        now[0] += 0.2
        second = processor.next_control()
        assert second is None

        now[0] += 10
        assert processor.next_control() is None
        report = processor.report()

        assert report["provider_latency_checks"] >= 4
    with subtests.test(contract='test_native_tool_loop_governor_observes_recovered_turn'), isolated_portal_case() as case_path:
        invocation = replace(
            _invocation(),
            recovery_checkpoint={"turn_id": "turn-in-progress"},
        )

        outcome = NativeToolLoopGovernorFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=invocation,
            context=_context([]),
            config={},
        )

        assert outcome.status == "applied"
        assert outcome.invocation.provider_event_processor is not None
    with subtests.test(contract='test_tool_granularity_feature_attaches_a_safe_batching_policy'), isolated_portal_case() as case_path:
        invocation = _invocation({"enabled": True})

        outcome = ToolCallGranularityFeature().apply(
            provider=SimpleNamespace(harness_capabilities=frozenset()),
            invocation=invocation,
            context=_context([]),
            config={"enabled": True},
        )

        assert outcome.status == "applied"
        assert TOOL_CALL_GRANULARITY_POLICY_MARKER in outcome.invocation.user_prompt
        assert outcome.invocation.user_prompt.endswith("continue")
        assert "Keep writes, destructive actions" in outcome.invocation.user_prompt
        assert "use a resized preview" in outcome.invocation.user_prompt
        assert "available tools' output limits" in outcome.invocation.user_prompt
        assert outcome.metrics["policy_version"] == 2
    with subtests.test(contract='test_inline_media_feature_bounds_input_image_detail'), isolated_portal_case() as case_path:
        base = _invocation({"enabled": True})
        request = replace(
            base.request,
            input_media=(InputMedia(
                media_type="image",
                source_type="local_path",
                source="/tmp/large.png",
            ),),
        )

        outcome = InlineMediaHistoryFeature().apply(
            provider=SimpleNamespace(provider_id="codex"),
            invocation=replace(base, request=request),
            context=_context([]),
            config={"enabled": True},
        )

        assert outcome.status == "applied"
        assert outcome.reason == "input_image_detail_bounded"
        assert outcome.invocation.request.runtime_options[
            "input_image_detail"
        ] == "high"
        assert outcome.metrics["input_image_count"] == 1
    with subtests.test(contract='test_early_compaction_feature_is_skipped_without_auto_compact_threshold'), isolated_portal_case() as case_path:
        outcome = EarlyContextCompactionFeature().apply(
            provider=SimpleNamespace(harness_capabilities=frozenset()),
            invocation=_invocation({"enabled": True}),
            context=_context([]),
            config={"enabled": True, "threshold_percent": 80},
        )

        assert outcome.status == "skipped"
        assert outcome.reason == "requires_auto_compact_threshold"
















def test_offloaded_artifact_recall_survives_disabling_the_plugin_contracts(tmp_path, subtests, isolated_portal_case):
    """关闭卸载插件后仍能召回已卸载的产物

    插件关闭后，此前卸载到产物中的工具输出仍可通过召回工具读取。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_offloaded_artifact_recall_survives_disabling_the_plugin'), isolated_portal_case() as case_path:
        tmp_path = case_path
        store = HarnessArtifactStore(tmp_path / "artifacts")
        store.put(
            scope="v2:codex:resident:test",
            value="retained output",
        )
        feature = ToolOutputOffloadingFeature(store)
        provider = _RecordingProvider()
        service = AgentSessionService(
            {"codex": provider},
            harness=AgentHarness((feature,)),
        )
        events: list[tuple[str, dict]] = []

        service.execute(
            _invocation({
                "features": {
                    "tool_output_offloading": {"enabled": False},
                },
            }),
            _context(events),
        )

        assert events == []
        assert [tool.name for tool in provider.invocations[0].harness_tools] == [
            READ_HARNESS_ARTIFACT_TOOL_NAME
        ]
    with subtests.test(contract='test_native_image_view_estimates_base64_pressure_from_source_path'), isolated_portal_case() as case_path:
        tmp_path = case_path
        image_path = tmp_path / "large.png"
        image_path.write_bytes(b"x" * 600_001)

        estimated = approximate_inline_media_bytes({
            "type": "imageView",
            "path": str(image_path),
        })

        assert estimated == 4 * ((600_001 + 2) // 3)


def test_after_tool_batch_media_config_runs_input_setup_before_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """内联图片功能设为工具批次后时机时，仍在调用 Provider 前把输入图片 detail 设为 high"""
    from PIL import Image
    from management_portal.execution import harness_inline_media

    source = tmp_path / "large.png"
    Image.new("RGB", (3000, 1800), "white").save(source)
    base = _invocation({
        "features": {
            "inline_media_history": {
                "enabled": True,
                "timing": "after_tool_batch",
                                            },
        },
    })
    request = replace(
        base.request,
        input_media=(InputMedia(
            media_type="image",
            source_type="local_path",
            source=str(source),
        ),),
    )
    provider = _RecordingProvider()
    events: list[tuple[str, dict]] = []
    service = AgentSessionService(
        {"codex": provider},
        harness=AgentHarness((InlineMediaHistoryFeature(),)),
    )

    service.execute(replace(base, request=request), _context(events))

    received = provider.invocations[0]
    rewritten = Path(received.request.input_media[0].source)
    assert rewritten == source
    assert rewritten.is_file()
    assert received.request.runtime_options["input_image_detail"] == "high"
    assert [event_type for event_type, _details in events] == [
        "agent_harness_prepared",
        "agent_harness_completed",
    ]
    prepared = events[0][1]
    report = next(
        item
        for item in prepared["features"]
        if item["feature_id"] == "inline_media_history"
    )
    assert report["status"] == "applied"
    assert report["metrics"].get("input_images_preprocessed", 0) == 0


def test_early_compaction_after_tool_timing_is_armed_before_provider_loop_contracts(subtests, isolated_portal_case):
    """工具调用后的提前压缩在 Provider 循环开始前布置

    提前压缩的触发时机在 Provider 事件循环开始前就已登记，不会错过第一轮工具结果。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_early_compaction_after_tool_timing_is_armed_before_provider_loop'), isolated_portal_case() as case_path:
        invocation = _invocation({
            "features": {
                "early_context_compaction": {
                    "enabled": True,
                    "threshold_percent": 80,
                    "timing": "after_tool_batch",
                },
            },
        })
        provider = _RecordingProvider()
        provider.configure_auto_compact_threshold = mock.Mock(
            return_value=InvocationTransformResult(
                invocation,
                True,
                "native_auto_compact_threshold_configured",
            )
        )
        service = AgentSessionService(
            {"codex": provider},
            harness=AgentHarness((EarlyContextCompactionFeature(),)),
        )

        service.execute(invocation, _context([]))

        provider.configure_auto_compact_threshold.assert_called_once_with(
            invocation=invocation,
            threshold_percent=70,
        )
        assert len(provider.invocations) == 1
    with subtests.test(contract='test_early_compaction_arms_new_session_creation'), isolated_portal_case() as case_path:
        invocation = _invocation({
            "features": {
                "early_context_compaction": {
                    "enabled": True,
                    "timing": "after_tool_batch",
                },
            },
        })
        request = replace(
            invocation.request,
            operation="create",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:create",
                backend_route_id="zero",
            ),
        )
        create_invocation = replace(
            invocation,
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="resident:create",
                backend_session_id="",
                backend_route_id="zero",
                is_new_backend_session=True,
            ),
            is_new_backend_session=True,
        )
        provider = _RecordingProvider()
        provider.configure_auto_compact_threshold = mock.Mock(
            return_value=InvocationTransformResult(
                create_invocation,
                True,
                "native_auto_compact_threshold_configured",
            )
        )
        service = AgentSessionService(
            {"codex": provider},
            harness=AgentHarness((EarlyContextCompactionFeature(),)),
        )

        service.execute(create_invocation, _context([]))

        provider.configure_auto_compact_threshold.assert_called_once_with(
            invocation=create_invocation,
            threshold_percent=70,
        )
        assert provider.invocations == [create_invocation]
    with subtests.test(contract='test_claude_backend_sends_harness_transformed_prompt'), isolated_portal_case() as case_path:
        client = mock.Mock()
        client.run_turn.return_value = SimpleNamespace(
            response="done",
            turn_id="turn-1",
            session_id="claude-session",
            usage={},
        )
        request = ExecutionRequest.new(
            actor="sample_admin",
            prompt="original",
            service_type="agent_session",
            provider="claude",
            operation="turn",
            cwd="/tmp",
            session=SessionOptions(
                mode="persistent",
                binding_id="resident:test",
            ),
        )
        invocation = BackendInvocation(
            request=request,
            session=RuntimeSession(
                mode="persistent",
                binding_id="resident:test",
                backend_session_id="claude-session",
                is_new_backend_session=False,
            ),
            user_prompt="harness policy\n\noriginal",
            system_context="",
            attach_system_context=False,
            is_new_backend_session=False,
        )

        with mock.patch(
            "management_portal.execution.claude_backend.shared_claude_client",
            return_value=client,
        ):
            ClaudeBackend().execute(invocation, _context([]))

        assert client.run_turn.call_args.kwargs["prompt"] == (
            "harness policy\n\noriginal"
        )


def test_early_compaction_timings_only_configure_native_threshold(subtests, isolated_portal_case):
    """提前压缩在各种时机下都只配置 Provider 原生自动压缩阈值，不主动压缩会话"""
    for timing in ["before_turn", "after_tool_batch", "both"]:
        with subtests.test(timing=timing), isolated_portal_case() as case_path:
            invocation = _invocation({"enabled": True})
            provider = SimpleNamespace(
                provider_id="codex",
                compact_active_session_before_turn=mock.Mock(),
                configure_auto_compact_threshold=mock.Mock(
                    return_value=InvocationTransformResult(
                        invocation,
                        True,
                        "native_auto_compact_threshold_configured",
                    )
                ),
            )

            outcome = EarlyContextCompactionFeature().apply(
                provider=provider,
                invocation=invocation,
                context=_context([]),
                config={
                    "enabled": True,
                    "threshold_percent": 80,
                    "timing": timing,
                },
            )

            assert outcome.status == "applied"
            # Moving the Provider's compaction point is the feature's action.
            assert outcome.action_applied is True
            provider.compact_active_session_before_turn.assert_not_called()
            provider.configure_auto_compact_threshold.assert_called_once_with(
                invocation=invocation,
                threshold_percent=70,
            )


@pytest.mark.parametrize("operation", ["create", "turn"])
def test_codex_early_compaction_uses_80_percent_of_effective_window(
    tmp_path: Path,
    operation: str,
) -> None:
    """codex 提前压缩阈值按有效上下文窗口的 80% 计算，并保留其他配置覆盖项"""
    (tmp_path / "models_cache.json").write_text(
        json.dumps({
            "models": [{
                "slug": "gpt-test",
                "context_window": 272_000,
                "max_context_window": 272_000,
                "effective_context_window_percent": 95,
            }],
        }),
        encoding="utf-8",
    )
    registry = mock.Mock()
    registry.routes.return_value = (
        SimpleNamespace(account_id="zero", codex_home=tmp_path),
    )
    backend = CodexBackend(account_registry=registry)
    invocation = _invocation({"enabled": True})
    request = replace(
        invocation.request,
        operation=operation,
        runtime_options={
            **invocation.request.runtime_options,
            "model": "gpt-test",
            "config_overrides": {"unrelated": "preserved"},
        },
    )

    transformed = backend.configure_auto_compact_threshold(
        invocation=replace(invocation, request=request),
        threshold_percent=80,
    )

    assert transformed.applied is True
    overrides = transformed.invocation.request.runtime_options[
        "config_overrides"
    ]
    assert overrides["model_auto_compact_token_limit"] == 206_720
    assert overrides["model_auto_compact_token_limit_scope"] == "total"
    assert overrides["unrelated"] == "preserved"
    assert transformed.metrics["effective_context_window"] == 258_400
    assert transformed.metrics["effective_threshold_percent"] == 80.0


def test_native_compaction_setup_does_not_requeue_execution(tmp_path, subtests, isolated_portal_case):
    """配置原生压缩阈值的执行一次完成，不重新排队、不手动压缩也不中断轮次"""
    for timing in ["before_turn", "after_tool_batch", "both"]:
        with subtests.test(timing=timing), isolated_portal_case() as case_path:
            tmp_path = case_path
            from management_portal.execution.runtime import ExecutionRuntime
            from management_portal.execution.scheduler import Scheduler, SchedulerClient
            from management_portal.execution.service import ExecutionServiceServer
            from management_portal.execution.worker import ExecutionWorker

            invocation = _invocation({"features": {"early_context_compaction": {
                "enabled": True, "timing": timing,
            }}})
            provider_client = mock.Mock()
            provider_client.compact_thread.side_effect = AssertionError("Manual compaction forbidden")
            backend = CodexBackend(account_registry=mock.Mock(), client_factory=lambda _socket: provider_client)
            backend.configure_auto_compact_threshold = mock.Mock(return_value=InvocationTransformResult(
                invocation, True, "native_auto_compact_threshold_configured",
            ))
            backend.execute = mock.Mock(return_value=BackendResult(
                output={"message": "done"},
                backend_session_id=invocation.session.backend_session_id,
                backend_route_id="zero",
            ))
            sessions = mock.Mock()
            sessions.prepare.return_value = invocation.session
            scheduler = Scheduler(dispatch_interval_min_seconds=0,
                                  dispatch_interval_max_seconds=0)
            with ExecutionServiceServer(tmp_path / "s", scheduler) as server:
                transport = mock.Mock()
                transport.request.side_effect = lambda method, params, **_kwargs: server.dispatch(method, params)
                scheduler_client = SchedulerClient(
                    worker_id="dev:native-compaction-test", build_id="test",
                    capabilities=["agent_session:codex"], service_client=transport,
                )
                scheduler_client.register()
                scheduler.submit(invocation.request)
                claim = scheduler.claim(worker_id="dev:native-compaction-test",
                                        capabilities=["agent_session:codex"])
                assert claim["claimed"] is True
                runtime = ExecutionRuntime(
                    agent_providers={"codex": backend},
                    agent_harness=AgentHarness((EarlyContextCompactionFeature(),)),
                    session_manager=sessions, agent_data_projector=mock.Mock(),
                    state_root=tmp_path / "runtime",
                )
                outcome = ExecutionWorker(
                    build_id="test", worker_id="dev:native-compaction-test",
                    scheduler=scheduler_client, runtime=runtime,
                ).execute_claim(claim)
                assert outcome["status"] == "completed", outcome
                assert scheduler.snapshot(invocation.request.execution_id)["status"] == "completed"
                backend.configure_auto_compact_threshold.assert_called_once()
                backend.execute.assert_called_once()
                provider_client.compact_thread.assert_not_called()
                provider_client.interrupt_turn.assert_not_called()


def test_tool_output_pruning_is_bypassed_below_projected_context_trigger_contracts(subtests, isolated_portal_case):
    """预计上下文未到触发线时不裁剪工具输出

    工具输出裁剪只在预计上下文用量超过触发线时启用，低于时整体绕过。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_inline_media_policy_can_govern_current_tool_batch_images'), isolated_portal_case() as case_path:
        old_url = "data:image/png;base64," + "A" * 70_000
        recent_url = "data:image/png;base64," + "B" * 70_000
        batch = AgentLoopToolBatch(items=(AgentLoopToolResult(
            call_id="images-1",
            tool_name="render_images",
            arguments={},
            result={
                "success": True,
                "contentItems": [
                    {"type": "inputImage", "imageUrl": old_url},
                    {"type": "inputImage", "imageUrl": recent_url},
                ],
            },
            source="harness",
        ),))

        transformed = InlineMediaHistoryFeature().after_tool_batch(
            batch=batch,
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation({"enabled": True}),
            context=_context([]),
            config={
                "max_inline_media_bytes": 64 * 1024,
                "trigger_inline_media_bytes": 64 * 1024,
                "keep_recent_images": 1,
            },
            state={},
        )

        assert transformed.applied is True
        parts = transformed.batch.items[0].result["contentItems"]
        assert parts[0]["type"] == "inputText"
        assert "AgentHarness omitted" in parts[0]["text"]
        assert parts[1]["imageUrl"] == recent_url
        assert transformed.metrics["inline_image_count_removed"] == 1




def test_inline_media_policy_compresses_single_large_tool_image_before_return_contracts(subtests, isolated_portal_case):
    """内联媒体策略在返回前压缩单个大工具图片

    工具结果中单张过大的图片在交回模型前被压缩到允许尺寸。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_inline_media_policy_compresses_single_large_tool_image_before_return'), isolated_portal_case() as case_path:
        from PIL import Image

        source = Image.effect_noise((1024, 1024), 100).convert("RGB")
        encoded = io.BytesIO()
        source.save(encoded, format="PNG")
        image_url = (
            "data:image/png;base64,"
            + base64.b64encode(encoded.getvalue()).decode("ascii")
        )
        assert len(image_url) > 128 * 1024
        batch = AgentLoopToolBatch(items=(AgentLoopToolResult(
            call_id="image-1",
            tool_name="render_image",
            arguments={},
            result={
                "success": True,
                "contentItems": [{
                    "type": "inputImage",
                    "imageUrl": image_url,
                }],
            },
            source="harness",
        ),))

        transformed = InlineMediaHistoryFeature().after_tool_batch(
            batch=batch,
            provider=SimpleNamespace(provider_id="codex"),
            invocation=_invocation({"enabled": True}),
            context=_context([]),
            config={
                "max_inline_media_bytes": 128 * 1024,
                "trigger_inline_media_bytes": 128 * 1024,
                "max_tool_image_edge": 512,
                "tool_image_jpeg_quality": 76,
            },
            state={},
        )

        result_url = transformed.batch.items[0].result[
            "contentItems"
        ][0]["imageUrl"]
        assert transformed.applied is True
        assert transformed.reason == "current_tool_batch_inline_media_compressed"
        assert result_url.startswith("data:image/jpeg;base64,")
        assert len(result_url.encode("utf-8")) <= 128 * 1024
        assert transformed.metrics["inline_images_compressed"] == 1
        assert transformed.metrics["inline_media_compression_bytes_avoided"] > 0
    with subtests.test(contract='test_inline_media_feature_is_skipped_without_any_of_its_capabilities'), isolated_portal_case() as case_path:
        invocation = _invocation({"enabled": True})
        outcome = InlineMediaHistoryFeature().apply(
            provider=SimpleNamespace(harness_capabilities=frozenset()),
            invocation=invocation,
            context=_context([]),
            config={"enabled": True},
        )

        assert outcome.status == "skipped"
        assert outcome.reason == "input_image_detail_unavailable"


def test_native_governor_resumes_observation_in_the_next_provider_turn():
    """原生治理在 Provider 轮次结束后暂停延迟计时，下一轮开始后恢复观测"""
    now = [100.0]
    outcome = NativeToolLoopGovernorFeature().apply(
        provider=SimpleNamespace(provider_id="codex"), invocation=_invocation(), context=_context([]),
        config={"provider_latency_threshold_seconds": 5, "_monotonic": lambda: now[0]},
    )
    processor = outcome.invocation.provider_event_processor
    processor.process_event({"method": "turn/started", "params": {"turn": {"id": "first"}}})
    processor.process_event({"method": "turn/completed", "params": {"turn": {"id": "first"}}})
    now[0] += 6
    assert processor.next_control() is None
    processor.process_event({"method": "turn/started", "params": {"turn": {"id": "second"}}})
    now[0] += 6
    assert processor.next_control()["reason"] == "provider_latency_pressure"


def test_recovered_turn_keeps_native_compaction_configuration():
    """恢复的轮次仍按策略阈值配置原生自动压缩"""
    invoked = []
    invocation = replace(_invocation(), recovery_checkpoint={"turn_id": "existing"})

    def configure(**kwargs):
        invoked.append(kwargs)
        return InvocationTransformResult(invocation=kwargs["invocation"], applied=True, reason="configured")

    provider = SimpleNamespace(provider_id="codex", configure_auto_compact_threshold=configure)
    outcome = EarlyContextCompactionFeature().apply(
        provider=provider, invocation=invocation, context=_context([]), config={"policy_threshold_percent": 65},
    )
    assert outcome.status == "applied"
    assert invoked[0]["threshold_percent"] == 65


def test_codex_harness_tool_result_passes_through_tool_batch_lifecycle():
    """codex 的 Harness 工具结果经工具批次处理后返回，未注册的工具调用失败"""
    seen = []
    class Processor:
        def process(self, batch):
            seen.append(batch)
            return AgentLoopToolBatch(items=(replace(batch.items[0], result={"success": True, "contentItems": [{"type": "inputText", "text": "governed"}]}),))
    tool = HarnessTool(name="read_fixture", description="Read fixture", input_schema={}, handler=lambda args: "original")
    dispatch = CodexBackend._harness_tool_dispatcher((tool,), Processor())
    result = dispatch({"callId": "call-1", "tool": tool.name, "arguments": {"path": "fixture"}})
    assert seen[0].items[0].source == "harness"
    assert seen[0].items[0].call_id == "call-1"
    assert seen[0].items[0].arguments == {"path": "fixture"}
    assert result["contentItems"][0]["text"] == "governed"
    assert dispatch({"tool": "unregistered_user_tool", "arguments": {}})["success"] is False


def test_resident_isa_is_always_attached_without_a_feature_switch():
    """Resident 执行总是挂载 hatg_instruction 工具，无需功能开关"""
    invocation = _invocation({"mode": "manual", "features": {}})
    invocation = replace(invocation, request=replace(invocation.request, metadata={**invocation.request.metadata, "resident_id": "resident_aaaaaaaaaaaa"}))
    prepared = AgentHarness(()).prepare_invocation(invocation)
    assert "hatg_instruction" in {tool.name for tool in prepared.harness_tools}
