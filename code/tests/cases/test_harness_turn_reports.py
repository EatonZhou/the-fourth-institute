"""Every turn's component reports outlive the turn.

Found in the Harness experiment: the full component report of a turn exists
only in the ``agent_harness_completed`` event, which the scheduler keeps in
memory.  The effectiveness ledger kept just the components that acted or
failed, and only for automatic plans, so afterwards a component that never
had a chance looked the same as one that did not work, and a hand-planned
turn (the "all optional components off" arms) left nothing.  Setting the
Provider's compaction point was not counted as an action at all.

:module: resident.fork-handoff
:importance: P1
"""
from __future__ import annotations

from types import SimpleNamespace

from management_common.json_columns import json_value
from management_portal.execution.backend_contracts import BackendContext, BackendInvocation, BackendResult
from management_portal.execution.backend_services import AgentSessionService
from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.harness import (
    AgentHarness,
    HarnessFeatureOutcome,
    InvocationTransformResult,
)
from management_portal.execution.harness_auto_policy import AdaptiveHarnessController
from management_portal.execution.harness_capabilities import AUTO_COMPACT_THRESHOLD
from management_portal.execution.harness_early_compaction import EarlyContextCompactionFeature
from management_portal.execution.harness_effectiveness import (
    HarnessEffectivenessStore,
    collect_action_outcomes,
)
from management_portal.execution.session_manager import RuntimeSession
from tests.cases.database_test_support import connect


def _invocation(harness: dict, *, capabilities: frozenset[str] = frozenset()) -> BackendInvocation:
    request = ExecutionRequest.new(
        actor="sample_admin", prompt="continue", service_type="agent_session", provider="codex",
        operation="turn", cwd="/tmp", runtime_options={"agent_harness": harness},
        session=SessionOptions(mode="persistent", binding_id="resident:test", backend_route_id="zero"),
    )
    return BackendInvocation(
        request=request,
        session=RuntimeSession(
            mode="persistent", binding_id="resident:test",
            backend_session_id="019f0000-0000-7000-8000-000000000001",
            backend_route_id="zero", is_new_backend_session=False,
        ),
        user_prompt=request.prompt, system_context="", attach_system_context=False,
        is_new_backend_session=False, harness_capabilities=capabilities,
    )


class _Provider:
    provider_id = "codex"

    def execute(self, invocation: BackendInvocation, _context: BackendContext) -> BackendResult:
        return BackendResult(
            output={"message": "done"}, backend_session_id=invocation.session.backend_session_id,
            usage={"last": {"inputTokens": 10, "cachedInputTokens": 8, "outputTokens": 2}},
        )

    def interrupt(self, **_kwargs: object) -> bool:
        return False


class _NothingToDo:
    feature_id = "tool_output_offloading"

    def apply(self, *, invocation, **_kwargs: object) -> HarnessFeatureOutcome:
        return HarnessFeatureOutcome(
            invocation=invocation, status="skipped", reason="no_oversized_tool_outputs",
            metrics={"tool_output_count": 4, "largest_tool_output_tokens": 900},
        )


def test_every_component_leaves_an_outcome_with_its_reason_and_counters() -> None:
    """每个 Harness 组件都留下结果、原因与计数

    本轮每个参与的组件都在报告中写明结果、原因和计数器。
    """
    outcomes = collect_action_outcomes([
        {"feature_id": "tool_output_offloading", "lifecycle": "after_tool_batch", "status": "applied",
         "action_applied": True, "reason": "", "metrics": {"tool_output_count_offloaded": 1}},
        {"feature_id": "tool_output_offloading", "lifecycle": "before_turn", "status": "skipped",
         "reason": "no_oversized_tool_outputs",
         "metrics": {"tool_output_count": 3, "strategy": "/home/someone/secret.txt", "nested": {"a": 1}}},
        {"feature_id": "native_tool_loop_governor", "lifecycle": "before_turn", "status": "applied",
         "action_applied": False, "reason": "native_tool_loop_governor_attached"},
        {"feature_id": "hash_aware_read_dedup", "lifecycle": "before_turn", "status": "skipped",
         "reason": "requires_portal_tools"},
        {"feature_id": "tool_call_granularity", "lifecycle": "before_turn", "status": "failed_open",
         "action_applied": False, "reason": "RuntimeError"},
    ], {})

    by_key = {(item["action"], item.get("lifecycle")): item for item in outcomes}
    assert by_key[("tool_output_offloading", "after_tool_batch")]["status"] == "applied"
    no_chance = by_key[("tool_output_offloading", "before_turn")]
    assert (no_chance["status"], no_chance["reason"]) == ("skipped", "no_oversized_tool_outputs")
    # Counters are kept; text and nested values never reach the ledger.
    assert no_chance["metrics"] == {"tool_output_count": 3}
    assert by_key[("native_tool_loop_governor", "before_turn")]["status"] == "no_action"
    assert by_key[("hash_aware_read_dedup", "before_turn")]["reason"] == "requires_portal_tools"
    assert by_key[("tool_call_granularity", "before_turn")]["status"] == "failed_open"


def test_setting_the_compaction_point_is_the_features_action() -> None:
    """设置压缩点是压缩功能自己的动作

    压缩点由压缩功能设置并记录在它的报告中，不归属其他组件。
    """
    provider = SimpleNamespace(
        provider_id="codex",
        configure_auto_compact_threshold=lambda *, invocation, threshold_percent, **_: InvocationTransformResult(
            invocation, True, "native_auto_compact_threshold_configured",
        ),
    )
    invocation = _invocation({"mode": "manual"}, capabilities=frozenset({AUTO_COMPACT_THRESHOLD}))
    configured = EarlyContextCompactionFeature().apply(
        provider=provider, invocation=invocation, context=None, config={"enabled": True},
    )
    assert (configured.status, configured.action_applied) == ("applied", True)

    [outcome] = collect_action_outcomes([{
        "feature_id": "early_context_compaction", "status": configured.status,
        "action_applied": configured.action_applied, "reason": configured.reason,
    }], {})
    assert outcome["status"] == "applied"


def test_a_hand_planned_turn_keeps_its_component_reports(isolated_portal_case, monkeypatch) -> None:
    """手工计划的轮次保留组件报告

    使用手工 Harness 计划的轮次同样记录每个组件的执行报告。
    """
    with isolated_portal_case():
        # A process-wide shared writer may belong to an earlier disposable
        # database (and its recording policy); this case owns its own writer.
        owned_store = HarnessEffectivenessStore()
        monkeypatch.setattr(HarnessEffectivenessStore, 'shared', classmethod(lambda cls: owned_store))
        controller = AdaptiveHarnessController()
        store: HarnessEffectivenessStore = controller.effectiveness_store
        service = AgentSessionService(
            {"codex": _Provider()},
            harness=AgentHarness((_NothingToDo(),), adaptive_controller=controller),
        )
        invocation = _invocation({"mode": "manual", "features": {"tool_output_offloading": {"enabled": True}}})

        result = service.execute(invocation, BackendContext(
            emit_event=lambda *_args: None, cancellation_requested=lambda: False,
            set_interrupt_target=lambda *_args: None,
        ))
        store.flush()

        assert result.output["message"] == "done"
        with connect(store.database) as connection:
            row = connection.execute(
                "SELECT production_variant, load_family, production_plan_json, action_outcomes_json, "
                "actual_actions_json, input_tokens FROM harness_strategy_observations WHERE execution_id = %s",
                (invocation.request.execution_id,),
            ).fetchone()
        variant, family, plan, outcomes, actions, input_tokens = row
        # Its own variant and load family: never evidence about the automatic policy.
        assert (variant, family) == ("manual", "manual")
        assert json_value(plan) == ["tool_output_offloading"]
        assert json_value(actions) == []
        assert json_value(outcomes) == [{
            "action": "tool_output_offloading", "lifecycle": "before_turn", "status": "skipped",
            "reason": "no_oversized_tool_outputs",
            "metrics": {"tool_output_count": 4, "largest_tool_output_tokens": 900},
        }]
        assert input_tokens == 10
        store.close()
