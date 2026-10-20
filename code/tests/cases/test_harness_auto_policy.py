from __future__ import annotations
from management_portal.execution.harness_artifact_recall import artifact_scope_for_invocation

from pathlib import Path
from types import SimpleNamespace

import pytest

from management_portal.execution.harness import AgentHarness
from management_portal.execution.harness_auto_policy import (
    AdaptiveHarnessController,
)


def _invocation(
    *,
    binding_id: str = "binding-auto-policy",
    prompt: str = "continue",
    cwd: str = "",
    media_count: int = 0,
    media_sources: tuple[Path, ...] = (),
    tool_count: int = 0,
    is_new: bool = False,
    provider: str = "codex",
    policy_overrides: dict[str, object] | None = None,
    reasoning_effort: str = "",
    capabilities: frozenset[str] | None = None,
) -> SimpleNamespace:
    request = SimpleNamespace(
        provider=provider,
        operation="turn",
        cwd=cwd,
        input_media=(
            tuple(SimpleNamespace(
                media_type="image",
                source_type="local_path",
                source=str(source),
            ) for source in media_sources)
            or tuple(object() for _ in range(media_count))
        ),
        runtime_options={
            **({"effort": reasoning_effort} if reasoning_effort else {}),
            "agent_harness": {
                "mode": "auto",
                **(
                    {"policy_overrides": policy_overrides}
                    if policy_overrides is not None
                    else {}
                ),
                "features": {},
            }
        },
        metadata=(
            {
                "experiment_schema": "tfi.agent-harness-live-experiment.v1",
                "experiment_arm": "trial",
                "experiment_run_id": "unit-test-run",
                "experiment_pair_id": "unit-test-pair",
            }
            if policy_overrides is not None
            else {}
        ),
    )
    return SimpleNamespace(
        request=request,
        session=SimpleNamespace(
            binding_id=binding_id,
            is_new_backend_session=is_new,
        ),
        user_prompt=prompt,
        is_new_backend_session=is_new,
        harness_tools=tuple(object() for _ in range(tool_count)),
        # None: the Provider catalog speaks for ``provider``.
        harness_capabilities=capabilities,
    )


def _details(
    *,
    input_tokens: int = 0,
    context_window: int = 100_000,
    tool_calls: int = 0,
    output_bytes: int = 0,
    duplicate_reads: int = 0,
    discovery_opportunities: int = 0,
    singleton_streak: int = 0,
    image_results: int = 0,
) -> dict:
    return {
        "provider_usage": {"input_tokens": input_tokens},
        "context_savings": {
            "context_window_tokens": context_window,
            "context_pressure_percent": 0,
        },
        "provider_event_loop": {
            "native_tool_calls_observed": tool_calls,
            "native_output_bytes_estimated": output_bytes,
            "duplicate_read_calls_observed": duplicate_reads,
            "discovery_opportunities_observed": discovery_opportunities,
            "max_consecutive_singleton_read_batches": singleton_streak,
            "large_native_outputs_observed": int(
                output_bytes >= 128 * 1024
            ),
        },
        "after_tool_batch": {
            "tool_result_count": tool_calls,
            "image_or_multimodal_result_count": image_results,
        },
    }


def test_production_auto_short_turn_attaches_only_runtime_observer_contracts(tmp_path, subtests, isolated_portal_case):
    """短轮次的自动策略只挂载运行观察器

    预计很短的轮次只附加运行时观察器，不启用其他 Harness 组件。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_production_auto_short_turn_attaches_only_runtime_observer'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(
            _invocation(
                is_new=True,
                prompt="只回答 HARNESS_EXACT_OK",
            ),
            {},
        )

        enabled = {
            feature_id
            for feature_id, config in decision.features.items()
            if config.get("enabled") is True
        }
        assert enabled == {
            "native_tool_loop_governor",
            "provider_error_continue",
        }
        # Without a policy cell every turn runs the Backend's parameters.
        assert decision.signals["policy_arm"] == "champion"
        assert decision.signals["policy_version"] == 0
        assert decision.signals["policy_overrides"] == {}
        assert decision.signals["policy_cell"] == (
            f"codex/{decision.signals['load_family']}"
        )
        governor = decision.features["native_tool_loop_governor"]
        assert governor["provider_latency_threshold_seconds"] == 600

        recovery = decision.features["provider_error_continue"]
        assert "max_continue_attempts" not in recovery
    with subtests.test(contract='test_max_effort_uses_safe_runtime_latency_guard'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()

        decision = controller.resolve(
            _invocation(reasoning_effort="max"),
            {},
        )

        governor = decision.features["native_tool_loop_governor"]
        assert governor["provider_latency_threshold_seconds"] == 600
    with subtests.test(contract='test_production_auto_runtime_observer_enters_harness_lifecycle'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        harness = AgentHarness(adaptive_controller=controller)
        invocation = _invocation(
            is_new=True,
            prompt="只回答 HARNESS_EXACT_OK",
        )
        invocation.request.execution_id = "execution-selective-bypass"

        assert harness.should_run(invocation)
    with subtests.test(contract='test_policy_overrides_need_a_controlled_experiment'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        invocation = _invocation(
            binding_id="override-outside-experiment",
            policy_overrides={"offload_trigger_bytes": 32 * 1024},
        )
        invocation.request.metadata = {}
        with pytest.raises(ValueError, match="only accepted in a controlled experiment"):
            AgentHarness(adaptive_controller=controller)._effective_config(invocation)
    with subtests.test(contract='test_unknown_or_unsafe_policy_override_is_rejected'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        for overrides in (
            {"policy_variant": "candidate_v4"},
            {"offload_trigger_bytes": 4 * 1024 * 1024},
        ):
            with pytest.raises(ValueError):
                AgentHarness(adaptive_controller=controller)._effective_config(
                    _invocation(binding_id="unsafe-override", policy_overrides=overrides)
                )
    with subtests.test(contract='test_production_auto_keeps_large_single_image_lossless'), isolated_portal_case() as case_path:
        tmp_path = case_path
        large = tmp_path / "large.png"
        large.write_bytes(b"x" * (1024 * 1024 + 1))
        controller = AdaptiveHarnessController()

        decision = controller.resolve(
            _invocation(
                media_sources=(large,),
            ),
            {},
        )

        media = decision.features["inline_media_history"]
        assert media["enabled"] is True
        assert "preprocess_input_images" not in media
        assert decision.features["early_context_compaction"]["enabled"] is False
        assert decision.signals["current_media_source_bytes_max"] == (
            1024 * 1024 + 1
        )
        assert decision.signals["current_media_large_image_count"] == 1
        assert decision.signals["large_input_image_signal"] is True
    with subtests.test(contract='test_production_auto_keeps_multiple_large_images_lossless'), isolated_portal_case() as case_path:
        tmp_path = case_path
        first = tmp_path / "first.png"
        second = tmp_path / "second.png"
        first.write_bytes(b"x" * (1024 * 1024 + 1))
        second.write_bytes(b"x" * (1024 * 1024 + 1))
        controller = AdaptiveHarnessController()

        decision = controller.resolve(
            _invocation(
                media_sources=(first, second),
            ),
            {},
        )

        assert "preprocess_input_images" not in decision.features["inline_media_history"]
        assert decision.signals["current_media_large_image_count"] == 2
        assert decision.signals["large_input_image_signal"] is True




def test_production_auto_does_not_preprocess_small_image_contracts(tmp_path, subtests, isolated_portal_case):
    """自动策略不预处理小图片

    生产 auto 策略只处理超出阈值的图片，小图片原样发送。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_production_auto_does_not_preprocess_small_image'), isolated_portal_case() as case_path:
        tmp_path = case_path
        small = tmp_path / "small.png"
        small.write_bytes(b"x" * (1024 * 1024 - 1))
        controller = AdaptiveHarnessController()

        decision = controller.resolve(
            _invocation(media_sources=(small,)),
            {},
        )

        assert "preprocess_input_images" not in decision.features["inline_media_history"]
        assert decision.signals["current_media_large_image_count"] == 0
        assert decision.signals["large_input_image_signal"] is False
    with subtests.test(contract='test_portal_tools_arm_only_after_batch_lossless_interceptors'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(_invocation(tool_count=4), {})

        assert decision.features["tool_output_offloading"]["enabled"]
        assert decision.features["tool_output_offloading"]["timing"] == (
            "after_tool_batch"
        )
        assert decision.features["hash_aware_read_dedup"]["enabled"]
        assert decision.features["hash_aware_read_dedup"]["timing"] == (
            "after_tool_batch"
        )
    with subtests.test(contract='test_media_feedback_keeps_current_turn_interceptor_across_turns'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        first = controller.resolve(_invocation(media_count=1), {})
        assert first.features["inline_media_history"]["enabled"]
        assert first.features["inline_media_history"]["timing"] == (
            "after_tool_batch"
        )

        assert controller.observe(
            decision=first,
            details=_details(input_tokens=8_000),
            result={},
        )
        second = controller.resolve(_invocation(), {})

        assert second.features["inline_media_history"]["enabled"]
        assert second.features["inline_media_history"]["timing"] == (
            "after_tool_batch"
        )
        assert second.signals["current_media_count"] == 0


def test_context_pressure_adapts_initial_reasoning_and_arms_active_compaction_contracts(tmp_path, subtests, isolated_portal_case):
    """上下文压力调整初始推理强度并布置主动压缩

    上下文接近上限时，自动策略降低本轮初始推理强度并提前布置压缩。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_complex_task_only_downgrades_at_critical_pressure'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        controller = AdaptiveHarnessController()
        invocation = _invocation(
            cwd=str(repo),
            prompt="修复 repository module 中的 bug",
            reasoning_effort="max",
        )
        scope = controller.resolve(invocation, {}).scope
        controller.state_store.save(scope, {
            "turn_count": 2,
            "last_context_pressure_percent": 84,
        })

        decision = controller.resolve(invocation, {})
        governor = decision.features["native_tool_loop_governor"]

        assert decision.signals["reasoning_task_complexity"] is True
        assert governor["initial_reasoning_effort"] == "high"
    with subtests.test(contract='test_repo_task_without_turn_steering_uses_prompt_granularity_policy'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        controller = AdaptiveHarnessController()

        # The Adapter reports native tool calls but takes no input during a
        # turn: the governor observes, and the standing prompt policy asks
        # for larger read batches in its place.
        decision = controller.resolve(
            _invocation(
                binding_id="observing-backend",
                cwd=str(repo),
                prompt="refactor the backend module and tests",
                capabilities=frozenset({"native_tool_events"}),
            ),
            {},
        )

        assert decision.features["tool_call_granularity"]["enabled"]
        assert decision.features["native_tool_loop_governor"]["enabled"]
        assert decision.signals["turn_steering_available"] is False

        steering = controller.resolve(
            _invocation(
                binding_id="steering-backend",
                cwd=str(repo),
                prompt="refactor the backend module and tests",
                capabilities=frozenset({"native_tool_events", "active_turn_input"}),
            ),
            {},
        )

        assert not steering.features["tool_call_granularity"]["enabled"]
        assert steering.features["native_tool_loop_governor"]["enabled"]
    with subtests.test(contract='test_turn_runtime_accounts_without_changing_planned_budgets'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(_invocation(tool_count=1), {})
        runtime = controller.begin_turn(decision)
        image = "data:image/png;base64," + "A" * (180 * 1024)
        batch = SimpleNamespace(items=(
            SimpleNamespace(result={
                "success": True,
                "contentItems": [
                    {"type": "inputText", "text": "X" * (100 * 1024)},
                    {"type": "inputImage", "imageUrl": image},
                    {"type": "inputImage", "imageUrl": image},
                ],
            }),
        ))

        runtime.observe_tool_batch(
            batch=batch,
            context_pressure_percent=64,
        )

        offload = runtime.feature_config(
            "tool_output_offloading",
            {"offload_trigger_bytes": 256 * 1024, "preview_chars": 4_000},
        )
        media = runtime.feature_config(
            "inline_media_history",
            {"max_inline_media_bytes": 1024 * 1024},
        )
        report = runtime.report()
        assert offload["offload_trigger_bytes"] == 256 * 1024
        assert offload["preview_chars"] == 4_000
        assert media["max_inline_media_bytes"] == 1024 * 1024
        assert "trigger_inline_media_bytes" not in media
        assert report["effective_profile"] == "protective"
        assert report["portal_inline_media_bytes_observed"] > 300 * 1024
        assert report["transitions"][-1]["source"] == "portal_tool_batch"


def test_quality_feedback_protects_reasoning_effort_under_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """质量反馈在上下文压力下保留较高推理强度"""
    controller = AdaptiveHarnessController()
    invocation = _invocation(reasoning_effort="high")
    scope = controller.resolve(invocation, {}).scope
    controller.state_store.save(scope, {
        "turn_count": 3,
        "last_context_pressure_percent": 68,
    })
    monkeypatch.setattr(
        controller.effectiveness_store,
        "reasoning_quality_signal",
        lambda **_kwargs: {
            "sample_count": 8,
            "failure_count": 3,
            "failure_rate": 0.375,
            "average_score": 0.6,
            "guard": True,
        },
    )

    decision = controller.resolve(invocation, {})
    governor = decision.features["native_tool_loop_governor"]

    assert governor["initial_reasoning_effort"] == "high"
    assert governor["reasoning_quality_guard"] is True
    assert decision.signals["reasoning_quality_sample_count"] == 8


def test_turn_runtime_uses_live_native_totals_instead_of_previous_turn_contracts(tmp_path, subtests, isolated_portal_case):
    """轮内运行时使用实时的原生用量而非上一轮数据

    Harness 判断上下文压力时读取本轮 Provider 实时报告的用量，不沿用上一轮的统计。

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_turn_runtime_uses_live_native_totals_instead_of_previous_turn'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(_invocation(), {})
        runtime = controller.begin_turn(decision)

        limits = runtime.observe_native_snapshot({
            "native_tool_calls": 31,
            "native_output_bytes": 210 * 1024,
            "inline_media_bytes": 0,
            "duplicate_read_calls": 3,
            "failed_tool_calls": 0,
            "context_pressure_percent": 58,
        })

        report = runtime.report()
        assert report["effective_profile"] == "protective"
        assert limits == {}
    with subtests.test(contract='test_high_pressure_compacts_before_turn_and_keeps_runtime_fallback'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        invocation = _invocation()
        scope = artifact_scope_for_invocation(invocation)
        controller.state_store.save(scope, {
            "turn_count": 5,
            "last_context_pressure_percent": 77,
            "severe_pressure_streak": 1,
        })

        decision = controller.resolve(invocation, {})

        assert decision.features["early_context_compaction"]["enabled"]
    with subtests.test(contract='test_context_pressure_uses_latest_context_not_cumulative_usage'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        first = controller.resolve(_invocation(), {})
        details = _details()
        details["provider_usage"] = {
            "context": {"inputTokens": 20_000},
            "total": {"inputTokens": 900_000},
            "modelContextWindow": 100_000,
        }
        controller.observe(decision=first, details=details, result={})

        second = controller.resolve(_invocation(), {})

        assert second.signals["context_pressure_percent"] == 20
    with subtests.test(contract='test_milestone_marker_does_not_replace_provider_session'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        first = controller.resolve(_invocation(), {})
        controller.observe(
            decision=first,
            details=_details(input_tokens=5_000),
            result={"output": "<portal-milestone-handoff>done</portal-milestone-handoff>"},
        )

        second = controller.resolve(_invocation(), {})
        assert "milestone_rollover" not in second.features
        assert "milestone_pending" not in controller.state_store.load(first.scope)

        # Persisted legacy state must not turn an ordinary turn into a protective
        # plan, even before the next observation overwrites that state.
        legacy_state = controller.state_store.load(first.scope)
        controller.state_store.save(first.scope, {**legacy_state, "milestone_pending": True})

        controller.observe(
            decision=second,
            details=_details(input_tokens=5_000),
            result={"output": "ordinary completion"},
        )
        third = controller.resolve(_invocation(), {})
        assert "milestone_rollover" not in third.features
        assert "milestone_pending" not in controller.state_store.load(third.scope)
    with subtests.test(contract='test_external_feature_keeps_explicit_contract'), isolated_portal_case() as case_path:
        tmp_path = case_path
        controller = AdaptiveHarnessController()
        decision = controller.resolve(
            _invocation(),
            {"community.example": {"enabled": True, "budget": 3}},
        )

        assert decision.features["community.example"] == {
            "enabled": True,
            "budget": 3,
        }
        assert decision.reasons["community.example"] == (
            "external_feature_explicit_contract"
        )


def test_feedback_state_survives_controller_recreation(tmp_path: Path) -> None:
    """重建控制器后恢复反馈状态和输出卸载策略"""
    first_controller = AdaptiveHarnessController()
    invocation = _invocation()
    first = first_controller.resolve(invocation, {})
    first_controller.observe(
        decision=first,
        details=_details(
            input_tokens=60_000,
            output_bytes=256 * 1024,
        ),
        result={},
    )
    assert first_controller.state_store.flush()

    second_controller = AdaptiveHarnessController()
    restored = second_controller.resolve(invocation, {})

    assert restored.turn_index == 2
    assert restored.features["tool_output_offloading"]["timing"] == (
        "after_tool_batch"
    )


def test_controller_failure_fails_open_to_manual_feature_contract() -> None:
    """自适应控制器失败时回退手动功能配置"""
    class FailingController:
        def resolve(self, invocation, configured_features):
            raise RuntimeError("policy unavailable")

    request = SimpleNamespace(
        execution_id="execution-auto-policy-failure",
        operation="turn",
        runtime_options={
            "agent_harness": {"mode": "auto", "features": {}}
        },
    )
    invocation = SimpleNamespace(request=request)
    harness = AgentHarness((), adaptive_controller=FailingController())

    assert harness.should_run(invocation) is False


def test_policy_cell_runs_its_challenger_on_a_fixed_share_of_sessions(isolated_portal_case):
    """挑战策略按固定会话比例分配且晋升后全量生效"""
    from management_portal.execution.harness_evolution import (
        HarnessEvolutionStore,
        PolicyCellCache,
    )

    with isolated_portal_case():
        controller = AdaptiveHarnessController()
        probe = controller.resolve(
            _invocation(binding_id="cell-probe", tool_count=4), {},
        )
        family = probe.signals["load_family"]
        assert probe.features["tool_output_offloading"]["offload_trigger_bytes"] == 64 * 1024

        store = HarnessEvolutionStore()
        store.start_challenger(
            "codex",
            family,
            {"offload_trigger_bytes": 32 * 1024},
            actor="unit-test",
            share=0.25,
        )
        PolicyCellCache.reset_for_tests()
        PolicyCellCache.shared().refresh()

        arms: dict[str, str] = {}
        for index in range(80):
            binding_id = f"cell-session-{index}"
            decision = controller.resolve(
                _invocation(binding_id=binding_id, tool_count=4), {},
            )
            signals = decision.signals
            assert signals["policy_cell"] == f"codex/{family}"
            assert signals["policy_version"] == 1
            assert signals["policy_selection_source"] == "policy_cell"
            threshold = decision.features["tool_output_offloading"]["offload_trigger_bytes"]
            if signals["policy_arm"] == "challenger":
                assert signals["policy_overrides"] == {"offload_trigger_bytes": 32 * 1024}
                assert threshold == 32 * 1024
            else:
                assert signals["policy_arm"] == "champion"
                assert signals["policy_overrides"] == {}
                assert threshold == 64 * 1024
            arms[binding_id] = signals["policy_arm"]
        challengers = sum(arm == "challenger" for arm in arms.values())
        assert 5 <= challengers <= 35

        # A session keeps its arm from turn to turn.
        for binding_id, arm in list(arms.items())[:20]:
            again = controller.resolve(
                _invocation(binding_id=binding_id, tool_count=4), {},
            )
            assert again.signals["policy_arm"] == arm

        # A promoted challenger becomes what every session runs.
        store.promote("codex", family, actor="unit-test")
        PolicyCellCache.reset_for_tests()
        PolicyCellCache.shared().refresh()
        promoted = controller.resolve(
            _invocation(binding_id="cell-session-0", tool_count=4), {},
        )
        assert promoted.signals["policy_arm"] == "champion"
        assert promoted.signals["policy_version"] == 2
        assert promoted.features["tool_output_offloading"]["offload_trigger_bytes"] == 32 * 1024


def test_reasoning_downgrade_pressure_is_a_policy_parameter():
    """推理降级压力阈值可通过策略参数调整"""
    from management_portal.execution.harness_auto_policy import (
        _adaptive_reasoning_effort,
    )

    assert _adaptive_reasoning_effort("high", context_pressure_percent=46) == "high"
    assert _adaptive_reasoning_effort(
        "high", context_pressure_percent=46, downgrade_pressure_percent=40,
    ) == "medium"
    assert _adaptive_reasoning_effort(
        "high", context_pressure_percent=58, downgrade_pressure_percent=64,
    ) == "high"


@pytest.mark.parametrize("operation", ["release", "fork", "create", "compact"])
def test_session_operations_do_not_resolve_adaptive_policy(operation):
    """会话操作不解析自适应策略"""
    invocation = _invocation()
    invocation.request.operation = operation
    def resolve(*_args):
        pytest.fail("session operation entered adaptive policy")
    harness = AgentHarness(adaptive_controller=SimpleNamespace(resolve=resolve))
    assert harness._effective_config(invocation)[1] is None


def test_invalid_challenger_falls_back_to_the_validated_champion(monkeypatch):
    """无效挑战策略回退到已校验的当前策略"""
    from management_portal.execution.harness_evolution import PolicyCell, PolicyCellCache, EXPLORING
    cell = PolicyCell("codex", "short_text", version=4, status=EXPLORING,
                      champion={"offload_trigger_bytes": 48 * 1024}, challenger={"offload_trigger_bytes": -1}, challenger_share=1)
    monkeypatch.setattr(PolicyCellCache, "shared", lambda: SimpleNamespace(lookup=lambda *_: cell))
    result = AdaptiveHarnessController().resolve(_invocation(), {})
    assert result.signals["policy_arm"] == "champion"
    assert result.signals["policy_overrides"] == cell.champion
    assert result.features["tool_output_offloading"]["offload_trigger_bytes"] == 48 * 1024


def test_first_policy_state_write_removes_expired_rows():
    """首次写入自适应策略状态时清除过期记录"""
    from management_portal.execution.harness_auto_policy import HarnessAutoPolicyStateStore
    store = HarnessAutoPolicyStateStore()
    with store._connect() as connection:
        connection.execute("INSERT INTO harness_auto_policy_state(scope,state_json,updated_at) VALUES ('expired','{}',TIMESTAMPTZ '1970-01-01 00:00:01+00')")
        connection.commit()
    assert store.save("current", {"turn_count": 1})
    assert store.flush()
    with store._connect() as connection:
        assert connection.execute("SELECT scope FROM harness_auto_policy_state WHERE scope='expired'").fetchone() is None
