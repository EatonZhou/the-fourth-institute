"""Which Harness features the adaptive policy enables for one turn, and why.

Each built-in feature has one selector: from the turn's planning inputs it
says whether the feature runs, the reason, and the parameters it runs with.
The same inputs choose the same features on every Backend.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .harness import HARNESS_FEATURES
from .harness_policy_table import HarnessPolicyParameters


BUILTIN_HARNESS_FEATURE_IDS = frozenset(name for name, definition in HARNESS_FEATURES.items() if definition.adaptive)


@dataclass(frozen=True)
class PlanInputs:
    """What the policy knows about a turn when it chooses its features."""

    parameters: HarnessPolicyParameters
    context_pressure_percent: float
    high_tool_load: bool
    fragmented: bool
    history_protection: bool
    output_hold: int
    duplicate_hold: int
    image_hold: int
    turn_steering: bool
    # The read-batch advice goes either into the turn as a reminder or into
    # the prompt as a standing policy, never both.
    granularity_by_reminder: bool
    portal_tools_expected: bool
    has_artifacts: bool
    current_media_count: int
    resource_reference: bool
    long_prompt: bool
    repo_task: bool
    configured_reasoning_effort: str
    initial_reasoning_effort: str
    reasoning_task_complexity: bool
    reasoning_quality_guard: bool


@dataclass(frozen=True)
class FeatureSelection:
    enabled: bool
    reason: str
    parameters: dict[str, Any] = field(default_factory=dict)


# A selector reads the turn's inputs and the features as configured.
FeatureSelector = Callable[[PlanInputs, Mapping[str, dict[str, Any]]], FeatureSelection]


def _native_tool_loop_governor(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(True, "selective_current_turn_feedback_loop", {
        "checkpoint_interval": 55 if inputs.high_tool_load or inputs.fragmented else 80,
        "max_checkpoints": 5,
        "duplicate_read_threshold": 4 if inputs.duplicate_hold else 6 if inputs.fragmented else 8,
        "large_output_bytes": (
            32 * 1024
            if inputs.output_hold or inputs.context_pressure_percent >= 60
            else 64 * 1024
        ),
        "granularity_strategy": (
            "auto" if inputs.granularity_by_reminder or not inputs.turn_steering else "off"
        ),
        "cumulative_output_bytes": 96 * 1024 if inputs.history_protection else 256 * 1024,
        "media_pressure_bytes": 256 * 1024 if inputs.image_hold else 512 * 1024,
        "max_granularity_steers": 2,
        "failure_threshold": 3,
        "post_steer_read_batches": 4,
        "context_pressure_threshold_percent": 70,
        "provider_latency_threshold_seconds": 600,
        "configured_reasoning_effort": inputs.configured_reasoning_effort,
        "initial_reasoning_effort": inputs.initial_reasoning_effort,
        "reasoning_task_complexity": inputs.reasoning_task_complexity,
        "reasoning_quality_guard": inputs.reasoning_quality_guard,
        "timing": "before_turn",
    })


def _provider_error_continue(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(
        configured.get("provider_error_continue", {}).get("enabled", True) is True,
        "bounded_provider_error_recovery",
        {"timing": "before_turn"},
    )


def _early_context_compaction(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    # Existing high-pressure sessions compact before their next expensive
    # model call. The runtime controller remains armed so growth during this
    # Portal turn has an observed fallback.
    return FeatureSelection(
        bool(inputs.history_protection or inputs.long_prompt),
        "selective_provider_context_guard",
        {
            "policy_threshold_percent": inputs.parameters.compaction_threshold_percent,
            "max_context_tokens": inputs.parameters.compaction_max_context_tokens,
        },
    )


def _unified_resource_recall(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(inputs.resource_reference, "resource_recall_contract", {"timing": "before_turn"})


def _tool_output_offloading(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    pressure = inputs.context_pressure_percent
    offload_history = bool(
        inputs.output_hold
        or pressure >= inputs.parameters.protect_history_pressure_percent
    )
    base = inputs.parameters.offload_trigger_bytes
    threshold = (
        base // 2
        if pressure >= 65 or inputs.output_hold
        else base * 3 // 4
        if pressure >= 45
        else base
    )
    return FeatureSelection(
        bool(inputs.portal_tools_expected or offload_history or inputs.has_artifacts),
        (
            "intercept_large_portal_tool_outputs"
            if inputs.portal_tools_expected
            else "current_turn_large_output_interceptor"
        ),
        {"offload_trigger_bytes": threshold, "timing": "after_tool_batch"},
    )


def _hash_aware_read_dedup(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(
        bool(inputs.portal_tools_expected or inputs.history_protection or inputs.fragmented),
        (
            "intercept_exact_duplicate_portal_reads"
            if inputs.portal_tools_expected
            else "current_turn_duplicate_read_interceptor"
        ),
        {
            "min_output_tokens": (
                128 if inputs.duplicate_hold or inputs.context_pressure_percent >= 65 else 256
            ),
            "timing": "after_tool_batch",
        },
    )


def _artifact_search_and_slice(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(inputs.has_artifacts, "session_has_offloaded_artifacts", {"timing": "before_turn"})


def _inline_media_history(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    return FeatureSelection(
        bool(inputs.current_media_count or inputs.image_hold > 0 or inputs.portal_tools_expected),
        "current_turn_inline_media_budget_interceptor",
        {"timing": "after_tool_batch", "max_tool_image_edge": 1536, "tool_image_jpeg_quality": 82},
    )


def _tool_call_granularity(inputs: PlanInputs, configured: Mapping[str, dict[str, Any]]) -> FeatureSelection:
    # A reminder during the turn where the Backend takes one and the parameter
    # table says so, else a standing prompt policy (the only way where the
    # Provider takes no input during a turn).
    return FeatureSelection(
        bool(
            not inputs.granularity_by_reminder
            and (inputs.repo_task or inputs.fragmented or inputs.high_tool_load)
        ),
        (
            "prompt_policy_without_turn_steering"
            if not inputs.turn_steering
            else "prompt_policy_by_backend_parameter"
        ),
        {"timing": "before_turn"},
    )


FEATURE_SELECTORS: tuple[tuple[str, FeatureSelector], ...] = (
    ("native_tool_loop_governor", _native_tool_loop_governor),
    ("provider_error_continue", _provider_error_continue),
    ("early_context_compaction", _early_context_compaction),
    ("unified_resource_recall", _unified_resource_recall),
    ("tool_output_offloading", _tool_output_offloading),
    ("hash_aware_read_dedup", _hash_aware_read_dedup),
    ("artifact_search_and_slice", _artifact_search_and_slice),
    ("inline_media_history", _inline_media_history),
    ("tool_call_granularity", _tool_call_granularity),
)


def select_features(
    configured_features: Mapping[str, object],
    inputs: PlanInputs,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """The turn's feature plan and the reason of each enabled feature."""

    features = {
        str(feature_id): dict(config)
        for raw_feature_id, raw_config in configured_features.items()
        if (feature_id := str(raw_feature_id or "").strip())
        for config in [raw_config if isinstance(raw_config, dict) else {}]
    }
    configured = {feature_id: dict(config) for feature_id, config in features.items()}
    reasons: dict[str, str] = {}
    for feature_id in BUILTIN_HARNESS_FEATURE_IDS:
        features.setdefault(feature_id, {})["enabled"] = False
    for feature_id, selector in FEATURE_SELECTORS:
        selection = selector(inputs, configured)
        config = features.setdefault(feature_id, {})
        config.update(selection.parameters)
        config["enabled"] = selection.enabled
        if selection.enabled:
            reasons[feature_id] = selection.reason
    # Third-party feature contracts remain explicit because this policy has
    # no cost/effect model for them.
    for feature_id, config in features.items():
        if feature_id in BUILTIN_HARNESS_FEATURE_IDS:
            continue
        if config.get("enabled") is True:
            reasons[feature_id] = "external_feature_explicit_contract"
    return features, reasons
