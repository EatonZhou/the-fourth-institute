from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterator, Protocol

from .contracts import ExecutionControlSignal
from .harness_capabilities import (
    PORTAL_TOOLS,
    adapter_capabilities,
    feature_requirement,
    invocation_capabilities,
    unmet_requirement,
)
from .harness_effectiveness import classify_failure


AGENT_HARNESS_RUNTIME_KEY = "agent_harness"
HARNESS_TIMING_BEFORE_TURN = "before_turn"
HARNESS_TIMING_AFTER_TOOL_BATCH = "after_tool_batch"
HARNESS_TIMING_BOTH = "both"
HARNESS_TIMINGS = frozenset({
    HARNESS_TIMING_BEFORE_TURN,
    HARNESS_TIMING_AFTER_TOOL_BATCH,
    HARNESS_TIMING_BOTH,
})
@dataclass(frozen=True)
class HarnessFeatureDefinition:
    module: str
    class_name: str
    enabled: bool
    parameters: frozenset[str]
    rewrites_model_input: bool
    adaptive: bool = True

    def feature_class(self) -> type:
        from importlib import import_module
        return getattr(import_module(f".harness_{self.module}", __package__), self.class_name)


HARNESS_FEATURES = {
    'tool_call_granularity': HarnessFeatureDefinition(
        module='tool_granularity',
        class_name='ToolCallGranularityFeature',
        enabled=False,
        parameters=frozenset(),
        rewrites_model_input=True,
    ),
    'unified_resource_recall': HarnessFeatureDefinition(
        module='unified_resource_recall',
        class_name='UnifiedResourceRecallFeature',
        enabled=True,
        parameters=frozenset({'max_resource_bytes'}),
        rewrites_model_input=True,
    ),
    'native_tool_loop_governor': HarnessFeatureDefinition(
        module='native_loop',
        class_name='NativeToolLoopGovernorFeature',
        enabled=True,
        parameters=frozenset({'max_checkpoints', 'large_output_bytes', 'checkpoint_interval', 'duplicate_read_threshold'}),
        rewrites_model_input=False,
    ),
    'provider_error_continue': HarnessFeatureDefinition(
        module='provider_error_continue',
        class_name='ProviderErrorContinueFeature',
        enabled=True,
        parameters=frozenset(),
        rewrites_model_input=False,
    ),
    'early_context_compaction': HarnessFeatureDefinition(
        module='early_compaction',
        class_name='EarlyContextCompactionFeature',
        enabled=True,
        parameters=frozenset(),
        rewrites_model_input=False,
    ),
    'hash_aware_read_dedup': HarnessFeatureDefinition(
        module='read_dedup',
        class_name='HashAwareReadDedupFeature',
        enabled=True,
        parameters=frozenset({'min_output_tokens'}),
        rewrites_model_input=True,
    ),
    'tool_output_offloading': HarnessFeatureDefinition(
        module='tool_output_offloading',
        class_name='ToolOutputOffloadingFeature',
        enabled=True,
        parameters=frozenset({'preview_chars', 'offload_trigger_bytes', 'max_outputs_per_turn'}),
        rewrites_model_input=True,
    ),
    'artifact_search_and_slice': HarnessFeatureDefinition(
        module='artifact_search',
        class_name='ArtifactSearchAndSliceFeature',
        enabled=True,
        parameters=frozenset(),
        rewrites_model_input=False,
    ),
    'inline_media_history': HarnessFeatureDefinition(
        module='inline_media',
        class_name='InlineMediaHistoryFeature',
        enabled=True,
        parameters=frozenset({'trigger_inline_media_bytes', 'keep_recent_images', 'max_inline_media_bytes'}),
        rewrites_model_input=True,
    ),
    'jev_decision_tool': HarnessFeatureDefinition(
        module='jev',
        class_name='JevDecisionToolFeature',
        enabled=True,
        parameters=frozenset(),
        rewrites_model_input=False,
        adaptive=False,
    ),
}
DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS = frozenset(
    name for name, definition in HARNESS_FEATURES.items() if definition.enabled
)

# Results from these sources are still inside Portal's provider-neutral loop and
# can be transformed synchronously before the next model call. Native app-server
# results never enter this contract.
PORTAL_CONTROLLED_TOOL_RESULT_SOURCES = frozenset({
    "harness",
})








@dataclass(frozen=True)
class InvocationTransformResult:
    invocation: Any
    applied: bool
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextWindowObservation:
    tokens: int
    reason: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HarnessFeatureOutcome:
    invocation: Any
    status: str
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)
    action_applied: bool | None = None


@dataclass(frozen=True)
class AgentLoopToolResult:
    """One provider tool result before it re-enters the model loop."""

    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]
    source: str = "harness"


@dataclass(frozen=True)
class AgentLoopToolBatch:
    """A provider-neutral batch of tool results awaiting model consumption."""

    items: tuple[AgentLoopToolResult, ...]


@dataclass(frozen=True)
class AgentLoopToolBatchTransform:
    batch: AgentLoopToolBatch
    applied: bool
    reason: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentLoopToolBatchProcessor:
    """Turn-scoped hook installed by AgentHarness for provider adapters."""

    process: Callable[[AgentLoopToolBatch], AgentLoopToolBatch]
    output_bytes_cap: int = 64 * 1024


@dataclass(frozen=True)
class AgentLoopProviderEventProcessor:
    """Turn-scoped native provider event observer and soft control queue."""

    feature_id: str
    process_event: Callable[[dict[str, Any]], None]
    next_control: Callable[[], dict[str, Any] | None]
    record_control_result: Callable[[dict[str, Any], bool, str], None]
    report: Callable[[], dict[str, Any]]


@dataclass(frozen=True)
class ProviderErrorRecoveryPolicy:
    """Harness-owned policy consumed by a Provider adapter."""

    max_continue_attempts: int = 1
    continuation_prompt: str = field(default="continue", init=False)
    # The continuation goes out this long after the error, not at once: an
    # overloaded or restarting Provider gets a moment before the next request.
    continue_delay_seconds: float = field(default=3.0, init=False)

    def __post_init__(self) -> None:
        try:
            attempts = int(self.max_continue_attempts)
        except (TypeError, ValueError):
            attempts = 1
        object.__setattr__(
            self,
            "max_continue_attempts",
            max(0, min(1, attempts)),
        )


@dataclass(frozen=True)
class HarnessTool:
    """Provider-neutral internal tool supplied by a Harness feature."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    expose_schema: bool = True
    # Whether a call (with these arguments) only reads.  Two identical reads
    # with the same result may then be answered once.
    read_only: bool | Callable[[dict[str, Any]], bool] = False
    # The part of a result two identical reads share, without per-call
    # identifiers; None: the whole result.
    stable_result: Callable[[str], str] | None = None
    # Selection identity is separate from the provider-facing alias.
    capability_ref: str = ""
    connection_ref: str = ""
    local_name: str = ""


def stable_json_result(*volatile_keys: str) -> Callable[[str], str]:
    """A ``stable_result`` for JSON results: the result without ``volatile_keys``."""

    def stable(text: str) -> str:
        try:
            value = json.loads(text)
        except (TypeError, ValueError):
            return text
        if not isinstance(value, dict):
            return text
        return json.dumps(
            {key: item for key, item in value.items() if key not in volatile_keys},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    return stable


_TOOL_CALL_ID: ContextVar[str] = ContextVar("harness_tool_call_id", default="")


@contextmanager
def tool_call_scope(call_id: object) -> Iterator[None]:
    """Make the provider's tool-call ID visible to the Harness tool handler."""

    token = _TOOL_CALL_ID.set(str(call_id or "").strip())
    try:
        yield
    finally:
        _TOOL_CALL_ID.reset(token)


def current_tool_call_id() -> str:
    return _TOOL_CALL_ID.get()


def capabilities_of(invocation: Any, provider: Any = None) -> frozenset[str]:
    """The capabilities of the Adapter that runs this invocation.

    AgentHarness attaches them to the invocation when it plans the turn, so
    every feature judges the same Adapter by the same declaration.
    """

    attached = getattr(invocation, "harness_capabilities", None)
    if attached is not None:
        return frozenset(attached)
    if provider is not None:
        return adapter_capabilities(provider, invocation)
    return invocation_capabilities(invocation)




def harness_feature_config(
    invocation: Any,
    feature_id: str,
) -> dict[str, Any]:
    """Return one feature's request-local config without mutating the request."""

    request = getattr(invocation, "request", None)
    runtime_options = getattr(request, "runtime_options", None)
    runtime_options = runtime_options if isinstance(runtime_options, dict) else {}
    harness = runtime_options.get(AGENT_HARNESS_RUNTIME_KEY)
    harness = harness if isinstance(harness, dict) else {}
    features = harness.get("features")
    features = features if isinstance(features, dict) else {}
    config = features.get(str(feature_id or "").strip())
    return dict(config) if isinstance(config, dict) else {}


def harness_feature_enabled(invocation: Any, feature_id: str) -> bool:
    return harness_feature_config(invocation, feature_id).get("enabled") is True


def normalize_harness_timing(
    value: object,
    *,
    default: str = HARNESS_TIMING_BEFORE_TURN,
) -> str:
    clean_default = str(default or "").strip()
    if clean_default not in HARNESS_TIMINGS:
        clean_default = HARNESS_TIMING_BEFORE_TURN
    clean_value = str(value or "").strip()
    return clean_value if clean_value in HARNESS_TIMINGS else clean_default


def harness_timing_includes(
    config: dict[str, Any],
    phase: str,
    *,
    default: str = HARNESS_TIMING_BEFORE_TURN,
) -> bool:
    timing = normalize_harness_timing(config.get("timing"), default=default)
    clean_phase = str(phase or "").strip()
    if clean_phase == HARNESS_TIMING_BEFORE_TURN:
        return timing in {HARNESS_TIMING_BEFORE_TURN, HARNESS_TIMING_BOTH}
    if clean_phase == HARNESS_TIMING_AFTER_TOOL_BATCH:
        return timing in {
            HARNESS_TIMING_AFTER_TOOL_BATCH,
            HARNESS_TIMING_BOTH,
        }
    return False


def has_harness_tool(invocation: Any, tool_name: str) -> bool:
    clean_name = str(tool_name or "").strip()
    return bool(clean_name) and any(
        str(getattr(tool, "name", "") or "").strip() == clean_name
        for tool in tuple(getattr(invocation, "harness_tools", ()) or ())
    )


def attach_harness_tools(
    invocation: Any,
    tools: tuple[HarnessTool, ...],
) -> tuple[Any, int, str]:
    """Attach provider-neutral tools after capability and name checks.

    Only an Adapter that offers Portal-executed tools to the model takes them.
    """

    if PORTAL_TOOLS not in capabilities_of(invocation):
        return invocation, 0, f"requires_{PORTAL_TOOLS}"
    current = tuple(getattr(invocation, "harness_tools", ()) or ())
    requested = tuple(tools)
    for tool in requested:
        name = str(getattr(tool, "name", "") or "").strip()
        if not name:
            raise ValueError("Harness tool name required")
    updated = list(current)
    positions = {
        str(getattr(tool, "name", "") or "").strip(): index
        for index, tool in enumerate(updated)
        if str(getattr(tool, "name", "") or "").strip()
    }
    changed = 0
    for tool in requested:
        name = str(getattr(tool, "name", "") or "").strip()
        if name in positions:
            index = positions[name]
            existing = updated[index]
            if (
                not bool(getattr(existing, "expose_schema", True))
                and bool(getattr(tool, "expose_schema", True))
            ):
                updated[index] = tool
                changed += 1
            continue
        positions[name] = len(updated)
        updated.append(tool)
        changed += 1
    if not changed:
        return invocation, 0, "harness_tools_already_attached"
    return (
        replace(invocation, harness_tools=tuple(updated)),
        changed,
        "harness_tools_attached",
    )


class HarnessFeature(Protocol):
    feature_id: str
    # Capabilities the feature needs from the Adapter, as alternatives (see
    # ``harness_capabilities.requires``).  A feature that declares nothing
    # runs on every Backend.
    requires: tuple[frozenset[str], ...]

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        ...


class AdaptiveHarnessPolicy(Protocol):
    def resolve(
        self,
        invocation: Any,
        configured_features: dict[str, Any],
    ) -> Any:
        ...

    def observe(
        self,
        *,
        decision: Any,
        details: dict[str, Any],
        result: object,
    ) -> bool:
        ...

    def begin_turn(self, decision: Any) -> Any:
        ...


class AgentHarness:
    """Optional feature chain; only local optimization failures are fail-open.

    Lifecycle hooks must preserve cancellation and execution control. These
    control signals belong to the execution worker, not the feature fallback.
    """

    def __init__(
        self,
        features: tuple[HarnessFeature, ...] = (),
        *,
        default_enabled_feature_ids: frozenset[str] = frozenset(),
        adaptive_controller: AdaptiveHarnessPolicy | None = None,
    ) -> None:
        self._features: dict[str, HarnessFeature] = {}
        self._adaptive_controller = adaptive_controller
        self._adaptive_decisions: dict[str, tuple[dict[str, Any], Any]] = {}
        self._adaptive_decisions_lock = threading.RLock()
        self._default_enabled_feature_ids = frozenset(
            str(feature_id or "").strip()
            for feature_id in default_enabled_feature_ids
            if str(feature_id or "").strip()
        )
        for feature in features:
            self.register(feature)
        unknown_defaults = (
            self._default_enabled_feature_ids - self._features.keys()
        )
        if unknown_defaults:
            raise ValueError(
                "default AgentHarness features are not registered: "
                + ", ".join(sorted(unknown_defaults))
            )

    def register(self, feature: HarnessFeature) -> None:
        feature_id = str(getattr(feature, "feature_id", "") or "").strip()
        if not feature_id:
            raise ValueError("AgentHarness feature_id required")
        if feature_id in self._features:
            raise ValueError(f"duplicate AgentHarness feature: {feature_id}")
        self._features[feature_id] = feature

    def _config(self, invocation: Any) -> dict[str, Any]:
        request = getattr(invocation, "request", None)
        runtime_options = getattr(request, "runtime_options", None)
        runtime_options = runtime_options if isinstance(runtime_options, dict) else {}
        raw_config = runtime_options.get(AGENT_HARNESS_RUNTIME_KEY)
        if raw_config is None:
            config: dict[str, Any] = {"mode": "auto", "features": {}}
        elif isinstance(raw_config, dict):
            config = dict(raw_config)
            if "mode" not in config:
                raise ValueError("AgentHarness settings require mode")
        else:
            raise ValueError("AgentHarness settings must be an object")
        unknown_fields = set(config) - {"features", "mode", "policy_overrides"}
        if unknown_fields:
            raise ValueError(
                "AgentHarness settings contain unsupported fields: "
                + ", ".join(sorted(unknown_fields))
            )
        raw_features = config.get("features")
        features = dict(raw_features) if isinstance(raw_features, dict) else {}
        for feature_id in self._default_enabled_feature_ids:
            if feature_id not in features:
                features[feature_id] = {"enabled": True}
        config["features"] = features
        raw_mode = str(config.get("mode") or "").strip().casefold()
        if raw_mode not in {"auto", "manual"}:
            raise ValueError(f"unsupported AgentHarness mode: {raw_mode}")
        config["mode"] = raw_mode
        if "policy_overrides" in config:
            from .harness_policy_table import HARNESS_EXPERIMENT_SCHEMA
            from .harness_policy_table import validate_overrides

            metadata = getattr(request, "metadata", None) or {}
            if not (
                schema_matches(metadata.get("experiment_schema"), "tfi.agent-harness-live-experiment.v1")
                and str(metadata.get("experiment_run_id") or "").strip()
                and str(metadata.get("experiment_pair_id") or "").strip()
            ):
                raise ValueError("AgentHarness policy_overrides are only accepted in a controlled experiment")
            config["policy_overrides"] = validate_overrides(config["policy_overrides"])
        return config

    def unavailable_features(self, invocation: Any) -> dict[str, str]:
        """Registered features this invocation's Adapter cannot run, and why.

        A feature states the capabilities it needs; the Adapter states the
        capabilities it has.  The reason is ``requires_<capability>``.
        """

        capabilities = capabilities_of(invocation)
        unavailable: dict[str, str] = {}
        for feature_id, feature in self._features.items():
            reason = unmet_requirement(
                feature_requirement(feature),
                capabilities,
            )
            if reason:
                unavailable[feature_id] = reason
        return unavailable

    @staticmethod
    def _without_unavailable(
        config: dict[str, Any],
        unavailable: dict[str, str],
    ) -> dict[str, Any]:
        """Turn off the features the Adapter cannot run; keep the reason in the plan."""

        if not unavailable:
            return config
        raw_features = config.get("features")
        features = dict(raw_features) if isinstance(raw_features, dict) else {}
        for feature_id, reason in unavailable.items():
            feature_config = features.get(feature_id)
            if not isinstance(feature_config, dict):
                continue
            if (
                feature_config.get("enabled") is not True
                and "unavailable" not in feature_config
            ):
                continue
            features[feature_id] = {
                **feature_config,
                "enabled": False,
                "unavailable": reason,
            }
        return {**config, "features": features}

    @staticmethod
    def _saved_plan(invocation: Any) -> dict[str, Any] | None:
        """The plan an earlier attempt of this Execution ran, if it saved one."""

        checkpoint = getattr(invocation, "recovery_checkpoint", None)
        saved = (
            checkpoint.get("harness_plan")
            if isinstance(checkpoint, dict)
            else None
        )
        plan = saved.get("features") if isinstance(saved, dict) else None
        if not isinstance(plan, dict):
            return None
        return {
            str(feature_id): dict(feature_config)
            for feature_id, feature_config in plan.items()
            if isinstance(feature_config, dict)
        }

    @staticmethod
    def _replayed_plan(
        features: dict[str, Any],
        saved_plan: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """One Execution runs one plan: a recovered attempt repeats the saved one.

        A new decision could enable other features and change the prompt or
        the tools, although the Provider is asked to carry on the same turn.
        """

        if saved_plan is None:
            return features
        replayed: dict[str, Any] = {}
        for feature_id, feature_config in features.items():
            config = dict(feature_config) if isinstance(feature_config, dict) else {}
            config["enabled"] = False
            replayed[feature_id] = config
        for feature_id, feature_config in saved_plan.items():
            replayed[feature_id] = {**feature_config, "enabled": True}
        return replayed

    @staticmethod
    def _record_plan(
        context: Any,
        invocation: Any,
        config: dict[str, Any],
    ) -> None:
        """Save the enabled features with the Execution's recovery checkpoint."""

        record = getattr(context, "record_harness_plan", None)
        if not callable(record):
            return
        if AgentHarness._saved_plan(invocation) is not None:
            return
        raw_features = config.get("features")
        features = raw_features if isinstance(raw_features, dict) else {}
        try:
            record({
                "features": {
                    str(feature_id): {
                        key: value
                        for key, value in feature_config.items()
                        if not str(key).startswith("_")
                    }
                    for feature_id, feature_config in features.items()
                    if isinstance(feature_config, dict)
                    and feature_config.get("enabled") is True
                },
            })
        except (InterruptedError, ExecutionControlSignal):
            raise
        except Exception:
            # The checkpoint is an aid for recovery; the turn does not depend on it.
            return

    def _record_attempt_plan(
        self,
        context: Any,
        *,
        original: Any,
        prepared: Any,
        config: dict[str, Any],
    ) -> Any:
        """Record the first attempt's plan for later recovery."""

        checkpoint = getattr(original, "recovery_checkpoint", None)
        if not (isinstance(checkpoint, dict) and checkpoint.get("turn_id")):
            self._record_plan(context, original, config)
        return prepared

    @staticmethod
    def _with_capabilities(invocation: Any, provider: Any) -> Any:
        """Attach capabilities an Adapter object declares for itself.

        A Backend's capabilities normally come from the Provider catalog, and
        the invocation stays untouched.  An Adapter that declares its own set
        (a test double, or an Adapter not in the catalog) gets it attached,
        so the plan and every feature judge the Adapter that runs the turn.
        """

        declared = getattr(provider, "harness_capabilities", None)
        if declared is None or callable(declared):
            return invocation
        if (
            getattr(invocation, "harness_capabilities", None) is not None
            or not hasattr(invocation, "harness_capabilities")
        ):
            return invocation
        try:
            return replace(
                invocation,
                harness_capabilities=frozenset(str(item) for item in declared),
            )
        except (TypeError, ValueError):
            return invocation

    @staticmethod
    def _decision_key(invocation: Any) -> str:
        request = getattr(invocation, "request", None)
        return str(getattr(request, "execution_id", "") or "").strip()

    def _effective_config(
        self,
        invocation: Any,
    ) -> tuple[dict[str, Any], Any]:
        key = self._decision_key(invocation)
        if key:
            with self._adaptive_decisions_lock:
                cached = self._adaptive_decisions.get(key)
            if cached is not None:
                return cached
        config = self._config(invocation)
        unavailable = self.unavailable_features(invocation)
        if (
            self._adaptive_controller is None
            or config.get("mode") != "auto"
            or getattr(getattr(invocation, "request", None), "operation", "") != "turn"
        ):
            return self._without_unavailable(config, unavailable), None
        try:
            decision = self._adaptive_controller.resolve(
                invocation,
                config.get("features") or {},
            )
        except (InterruptedError, ExecutionControlSignal):
            raise
        except Exception:
            # Adaptive policy is optional. Its failure must not make the
            # underlying Provider execution depend on this optimization.
            saved_plan = self._saved_plan(invocation)
            return self._without_unavailable(
                {
                    **config,
                    "mode": "manual",
                    **(
                        {"features": self._replayed_plan({}, saved_plan)}
                        if saved_plan is not None
                        else {}
                    ),
                },
                unavailable,
            ), None
        effective = self._without_unavailable(
            {
                "mode": "manual",
                "features": self._replayed_plan(
                    dict(decision.features),
                    self._saved_plan(invocation),
                ),
            },
            unavailable,
        )
        # The decision and the plan that runs must name the same features.
        if isinstance(getattr(decision, "features", None), dict):
            decision.features.clear()
            decision.features.update(effective["features"])
            if isinstance(getattr(decision, "unavailable", None), dict):
                decision.unavailable.update({name: reason for name, reason in unavailable.items() if effective["features"].get(name, {}).get("unavailable")})
                decision.signals["unavailable_features"] = dict(decision.unavailable)
        if key:
            with self._adaptive_decisions_lock:
                self._adaptive_decisions[key] = (effective, decision)
        return effective, decision

    @staticmethod
    def _with_config(invocation: Any, config: dict[str, Any]) -> Any:
        request = getattr(invocation, "request", None)
        runtime_options = getattr(request, "runtime_options", None)
        if request is None or not isinstance(runtime_options, dict):
            return invocation
        options = dict(runtime_options)
        options[AGENT_HARNESS_RUNTIME_KEY] = config
        try:
            return replace(
                invocation,
                request=replace(request, runtime_options=options),
            )
        except (TypeError, ValueError):
            return invocation

    def should_run(self, invocation: Any) -> bool:
        request = getattr(invocation, "request", None)
        operation = str(getattr(request, "operation", "") or "")
        if not operation:
            return False
        config, decision = self._effective_config(invocation)
        raw_feature_configs = config.get("features")
        feature_configs = (
            raw_feature_configs
            if isinstance(raw_feature_configs, dict)
            else {}
        )
        return decision is not None or any(
            isinstance(feature_configs.get(feature_id), dict)
            and feature_configs[feature_id].get("enabled") is True
            and self._supports_operation(feature, operation)
            for feature_id, feature in self._features.items()
        )


    @staticmethod
    def _supports_operation(feature: HarnessFeature, operation: str) -> bool:
        raw_operations = getattr(
            feature,
            "supported_operations",
            frozenset({"turn"}),
        )
        return str(operation or "") in {
            str(item or "").strip()
            for item in raw_operations
            if str(item or "").strip()
        }

    def prepare_invocation(self, invocation: Any, provider: Any = None) -> Any:
        """Resolve this turn's plan and restore the session's Harness tools."""

        invocation = self._with_capabilities(invocation, provider)
        config, decision = self._effective_config(invocation)
        current = (
            self._with_config(invocation, config)
            if decision is not None
            else invocation
        )
        unavailable = self.unavailable_features(current)
        from .harness_isa_tool import ResidentIsaTool

        current = ResidentIsaTool().prepare_invocation(current)
        for feature in self._features.values():
            if feature.feature_id in unavailable:
                continue
            prepare = getattr(feature, "prepare_invocation", None)
            if not callable(prepare):
                continue
            try:
                current = prepare(current)
            except (InterruptedError, ExecutionControlSignal):
                raise
            except Exception:
                continue
        return current

    @staticmethod
    def _emit(context: Any, event_type: str, details: dict[str, Any]) -> None:
        try:
            context.emit(event_type, details)
        except Exception:
            # Harness telemetry is observational and must never make Portal depend
            # on the optional Harness path.
            return

    def execute(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        execute: Callable[[Any, Any], Any],
    ) -> Any:
        invocation = self._with_capabilities(invocation, provider)
        from .harness_observer import ContextSavingsObserver

        started_ns = time.monotonic_ns()
        current_invocation = invocation
        reports: list[dict[str, Any]] = []
        observer = ContextSavingsObserver(
            provider=provider,
            invocation=invocation,
        )
        config, adaptive_decision = self._effective_config(invocation)
        adaptive_turn_runtime: Any = None
        if adaptive_decision is not None:
            begin_turn = getattr(self._adaptive_controller, "begin_turn", None)
            if callable(begin_turn):
                try:
                    adaptive_turn_runtime = begin_turn(adaptive_decision)
                except Exception:
                    adaptive_turn_runtime = None
        raw_feature_configs = config.get("features")
        feature_configs = (
            raw_feature_configs
            if isinstance(raw_feature_configs, dict)
            else {}
        )
        operation = str(
            getattr(getattr(invocation, "request", None), "operation", "")
            or ""
        )
        enabled_features: list[tuple[str, HarnessFeature, dict[str, Any]]] = []
        for feature_id, feature in self._features.items():
            raw_feature_config = feature_configs.get(feature_id)
            feature_config = (
                raw_feature_config
                if isinstance(raw_feature_config, dict)
                else {}
            )
            if (
                feature_config.get("enabled") is not True
                or not self._supports_operation(feature, operation)
            ):
                continue
            enabled_features.append((
                feature_id,
                feature,
                dict(feature_config),
            ))
        # Features the plan asked for but this Adapter cannot run: the report
        # keeps the missing capability instead of silently dropping them.
        for feature_id, raw_feature_config in feature_configs.items():
            if (
                feature_id in self._features
                and isinstance(raw_feature_config, dict)
                and raw_feature_config.get("unavailable")
            ):
                reports.append({
                    "feature_id": feature_id,
                    "lifecycle": "plan",
                    "status": "skipped",
                    "action_applied": False,
                    "reason": str(raw_feature_config["unavailable"]),
                    "prepare_ms": 0.0,
                    "metrics": {},
                })

        before_turn_features = [
            item
            for item in enabled_features
            if (
                not callable(getattr(item[1], "after_tool_batch", None))
                or harness_timing_includes(
                    item[2],
                    HARNESS_TIMING_BEFORE_TURN,
                    default=str(
                        getattr(
                            item[1],
                            "default_timing",
                            HARNESS_TIMING_BEFORE_TURN,
                        )
                    ),
                )
                or (
                    bool(getattr(
                        item[1],
                        "requires_before_turn_setup_for_after_tool_batch",
                        False,
                    ))
                    and harness_timing_includes(
                        item[2],
                        HARNESS_TIMING_AFTER_TOOL_BATCH,
                        default=str(
                            getattr(
                                item[1],
                                "default_timing",
                                HARNESS_TIMING_BEFORE_TURN,
                            )
                        ),
                    )
                )
            )
        ]
        if getattr(invocation, "recovery_checkpoint", {}).get("turn_id"):
            recovering = {
                name for name, definition in HARNESS_FEATURES.items()
                if definition.rewrites_model_input
            }
            for feature_id, _feature, _config in before_turn_features:
                if feature_id in recovering:
                    reports.append({
                        "feature_id": feature_id,
                        "lifecycle": "before_turn",
                        "status": "skipped",
                        "action_applied": False,
                        "reason": "turn_recovery_in_progress",
                        "prepare_ms": 0.0,
                        "metrics": {},
                    })
            before_turn_features = [
                item for item in before_turn_features if item[0] not in recovering
            ]
        after_tool_batch_features = [
            item
            for item in enabled_features
            if callable(getattr(item[1], "after_tool_batch", None))
            and harness_timing_includes(
                item[2],
                HARNESS_TIMING_AFTER_TOOL_BATCH,
                default=str(
                    getattr(
                        item[1],
                        "default_timing",
                        HARNESS_TIMING_BEFORE_TURN,
                    )
                ),
            )
        ]
        def run_feature(
            item: tuple[str, HarnessFeature, dict[str, Any]],
        ) -> None:
            nonlocal current_invocation
            feature_id, feature, feature_config = item
            effective_feature_config = feature_config
            if adaptive_turn_runtime is not None:
                try:
                    effective_feature_config = (
                        adaptive_turn_runtime.feature_config(
                            feature_id,
                            feature_config,
                        )
                    )
                except Exception:
                    effective_feature_config = feature_config
            feature_started_ns = time.monotonic_ns()
            try:
                outcome = feature.apply(
                    observer=observer,
                    provider=provider,
                    invocation=current_invocation,
                    context=context,
                    config=effective_feature_config,
                )
            except (InterruptedError, ExecutionControlSignal):
                raise
            except Exception as exc:
                report = {
                    "feature_id": feature_id,
                    "lifecycle": HARNESS_TIMING_BEFORE_TURN,
                    "status": "failed_open",
                    "action_applied": False,
                    "reason": type(exc).__name__,
                    "prepare_ms": round(
                        (time.monotonic_ns() - feature_started_ns) / 1_000_000,
                        3,
                    ),
                    "metrics": {},
                }
                reports.append(report)
                observer.record_feature(
                    feature_id,
                    status=report["status"],
                    metrics=report["metrics"],
                )
                return
            current_invocation = outcome.invocation
            report = {
                "feature_id": feature_id,
                "lifecycle": HARNESS_TIMING_BEFORE_TURN,
                "status": str(outcome.status or "skipped"),
                "action_applied": (
                    bool(outcome.action_applied)
                    if outcome.action_applied is not None
                    else str(outcome.status or "skipped") == "applied"
                ),
                "reason": str(outcome.reason or ""),
                "prepare_ms": round(
                    (time.monotonic_ns() - feature_started_ns) / 1_000_000,
                    3,
                ),
                "metrics": dict(outcome.metrics),
            }
            reports.append(report)
            observer.record_feature(
                feature_id,
                status=report["status"],
                metrics=report["metrics"],
            )

        for item in before_turn_features:
            run_feature(item)

        current_invocation = self._record_attempt_plan(
            context,
            original=invocation,
            prepared=current_invocation,
            config=config,
        )

        step_report_lock = threading.Lock()
        step_feature_states: dict[str, dict[str, Any]] = {
            feature_id: {}
            for feature_id, _feature, _feature_config in (
                after_tool_batch_features
            )
        }
        step_reports: dict[str, dict[str, Any]] = {
            feature_id: {
                "feature_id": feature_id,
                "lifecycle": HARNESS_TIMING_AFTER_TOOL_BATCH,
                "status": "skipped",
                "reason": "no_tool_batches",
                "prepare_ms": 0.0,
                "metrics": {
                    "tool_batch_count": 0,
                    "tool_result_count": 0,
                    "applied_batch_count": 0,
                    "failed_open_batch_count": 0,
                },
            }
            for feature_id, _feature, _feature_config in (
                after_tool_batch_features
            )
        }
        step_lifecycle_metrics: dict[str, Any] = {
            "tool_batch_count": 0,
            "tool_result_count": 0,
            "plain_text_result_count": 0,
            "image_or_multimodal_result_count": 0,
            "structured_result_count": 0,
            "failed_result_count": 0,
            "source_counts": {},
        }

        def process_tool_batch(
            batch: AgentLoopToolBatch,
        ) -> AgentLoopToolBatch:
            if not isinstance(batch, AgentLoopToolBatch):
                raise TypeError("AgentHarness tool batch contract required")
            with step_report_lock:
                before_tokens = observer.observe_tool_batch_before(
                    tuple(item.result for item in batch.items)
                )
                step_lifecycle_metrics["tool_batch_count"] += 1
                step_lifecycle_metrics["tool_result_count"] += len(
                    batch.items
                )
                source_counts = step_lifecycle_metrics["source_counts"]
                for item in batch.items:
                    source = str(item.source or "unknown")
                    source_counts[source] = int(
                        source_counts.get(source) or 0
                    ) + 1
                    classification = _tool_result_classification(
                        item.result
                    )
                    metric_key = {
                        "plain_text": "plain_text_result_count",
                        "image_or_multimodal": (
                            "image_or_multimodal_result_count"
                        ),
                        "failed": "failed_result_count",
                    }.get(classification, "structured_result_count")
                    step_lifecycle_metrics[metric_key] += 1
                current_batch = batch
                if adaptive_turn_runtime is not None:
                    try:
                        adaptive_turn_runtime.observe_tool_batch(
                            batch=batch,
                            context_pressure_percent=(
                                observer.context_pressure_percent
                            ),
                        )
                    except Exception:
                        pass
                for feature_id, feature, feature_config in (
                    after_tool_batch_features
                ):
                    report = step_reports[feature_id]
                    metrics = report["metrics"]
                    metrics["tool_batch_count"] += 1
                    metrics["tool_result_count"] += len(
                        current_batch.items
                    )
                    effective_feature_config = feature_config
                    if adaptive_turn_runtime is not None:
                        try:
                            effective_feature_config = (
                                adaptive_turn_runtime.feature_config(
                                    feature_id,
                                    feature_config,
                                )
                            )
                        except Exception:
                            effective_feature_config = feature_config
                    feature_started_ns = time.monotonic_ns()
                    try:
                        transformed = feature.after_tool_batch(
                            observer=observer,
                            batch=current_batch,
                            provider=provider,
                            invocation=current_invocation,
                            context=context,
                            config=effective_feature_config,
                            state=step_feature_states[feature_id],
                        )
                        if not isinstance(
                            transformed,
                            AgentLoopToolBatchTransform,
                        ):
                            raise TypeError(
                                "after_tool_batch must return "
                                "AgentLoopToolBatchTransform"
                            )
                    except (InterruptedError, ExecutionControlSignal):
                        raise
                    except Exception as exc:
                        metrics["failed_open_batch_count"] += 1
                        if report["status"] != "applied":
                            report["status"] = "failed_open"
                            report["reason"] = type(exc).__name__
                        else:
                            report["reason"] = (
                                "applied_with_failed_open_batches:"
                                f"{type(exc).__name__}"
                            )
                    else:
                        current_batch = transformed.batch
                        if transformed.applied:
                            metrics["applied_batch_count"] += 1
                            report["status"] = "applied"
                        elif report["status"] != "applied":
                            report["status"] = "skipped"
                        if transformed.applied or report["status"] != "applied":
                            report["reason"] = str(
                                transformed.reason or report["reason"]
                            )
                        _accumulate_numeric_metrics(
                            metrics,
                            transformed.metrics,
                        )
                        if adaptive_turn_runtime is not None:
                            try:
                                adaptive_turn_runtime.record_feature_result(
                                    feature_id,
                                    applied=bool(transformed.applied),
                                    metrics=transformed.metrics,
                                )
                            except Exception:
                                pass
                    report["prepare_ms"] = round(
                        float(report["prepare_ms"])
                        + (
                            time.monotonic_ns() - feature_started_ns
                        ) / 1_000_000,
                        3,
                    )
                observer.observe_tool_batch_after(
                    tuple(item.result for item in current_batch.items),
                    before_tokens=before_tokens,
                )
                return current_batch

        if after_tool_batch_features:
            output_bytes_cap = 64 * 1024
            for _feature_id, feature, feature_config in (
                after_tool_batch_features
            ):
                capture_bytes = getattr(
                    feature,
                    "tool_output_capture_bytes",
                    None,
                )
                if not callable(capture_bytes):
                    continue
                try:
                    output_bytes_cap = max(
                        output_bytes_cap,
                        int(capture_bytes(feature_config)),
                    )
                except (TypeError, ValueError):
                    continue
            current_invocation = replace(
                current_invocation,
                tool_batch_processor=AgentLoopToolBatchProcessor(
                    process=process_tool_batch,
                    output_bytes_cap=output_bytes_cap,
                ),
            )

        prepared_reports = [
            {
                **report,
                "metrics": dict(report.get("metrics") or {}),
            }
            for report in reports
        ]
        prepared_details: dict[str, Any] = {
            "lifecycle": "before_provider",
            "status": "prepared",
            "provider": str(getattr(provider, "provider_id", "") or ""),
            "capabilities": sorted(capabilities_of(current_invocation, provider)),
            "features": prepared_reports,
            "applied_feature_count": len({
                str(report.get("feature_id") or "")
                for report in prepared_reports
                if report.get("status") == "applied"
                and str(report.get("feature_id") or "")
            }),
            "applied_lifecycle_count": sum(
                1
                for report in prepared_reports
                if report.get("status") == "applied"
            ),
            "prepare_wall_ms": round(
                (time.monotonic_ns() - started_ns) / 1_000_000,
                3,
            ),
            "measurement_model_calls": 0,
        }
        if adaptive_decision is not None:
            prepared_details["adaptive_policy"] = adaptive_decision.report()
            if adaptive_turn_runtime is not None:
                try:
                    prepared_details["adaptive_policy"]["turn_runtime"] = (
                        adaptive_turn_runtime.report()
                    )
                except Exception as exc:
                    prepared_details["adaptive_policy"][
                        "turn_runtime_error"
                    ] = type(exc).__name__
        # This event deliberately precedes the provider call. A stalled or
        # cancelled provider must not erase evidence about which strategies
        # transformed the invocation that the provider actually received.
        self._emit(context, "agent_harness_prepared", prepared_details)
        observe_invocation = getattr(context, "observe_invocation", None)
        if callable(observe_invocation):
            observe_invocation(current_invocation, prepared_reports)

        provider_started_ns = time.monotonic_ns()
        status = "completed"
        result: Any = None
        execution_error: BaseException | None = None
        try:
            result = execute(current_invocation, context)
            return result
        except BaseException as exc:
            status = "failed"
            execution_error = exc
            raise
        finally:
            finished_ns = time.monotonic_ns()
            with step_report_lock:
                finalized_step_reports = [
                    {
                        **report,
                        "metrics": dict(report["metrics"]),
                    }
                    for report in step_reports.values()
                ]
            reports.extend(finalized_step_reports)
            for report in finalized_step_reports:
                observer.record_feature(
                    (
                        f"{report['feature_id']}:"
                        f"{HARNESS_TIMING_AFTER_TOOL_BATCH}"
                    ),
                    status=report["status"],
                    metrics=report["metrics"],
                )
            provider_event_loop: dict[str, Any] = {}
            event_processor = getattr(
                current_invocation,
                "provider_event_processor",
                None,
            )
            if isinstance(event_processor, AgentLoopProviderEventProcessor):
                try:
                    provider_event_loop = dict(event_processor.report())
                except Exception as exc:
                    provider_event_loop = {
                        "report_error": type(exc).__name__,
                    }
                for report in reports:
                    if report.get("feature_id") != event_processor.feature_id:
                        continue
                    report["metrics"] = {
                        **dict(report.get("metrics") or {}),
                        **provider_event_loop,
                    }
                    observer.record_feature(
                        event_processor.feature_id,
                        status=str(report.get("status") or "skipped"),
                        metrics=report["metrics"],
                    )
                    break
            provider_usage = _numeric_observation(
                getattr(result, "usage", None)
            )
            details = {
                "lifecycle": "after_turn",
                "status": status,
                "provider": str(
                    getattr(provider, "provider_id", "") or ""
                ),
                "capabilities": sorted(
                    capabilities_of(current_invocation, provider)
                ),
                "features": reports,
                "applied_feature_count": len({
                    str(report.get("feature_id") or "")
                    for report in reports
                    if report.get("status") == "applied"
                    and str(report.get("feature_id") or "")
                }),
                "applied_lifecycle_count": sum(
                    1
                    for report in reports
                    if report.get("status") == "applied"
                ),
                "provider_wall_ms": round(
                    (finished_ns - provider_started_ns) / 1_000_000,
                    3,
                ),
                "total_wall_ms": round(
                    (finished_ns - started_ns) / 1_000_000,
                    3,
                ),
                "measurement_model_calls": 0,
                "after_tool_batch_count": max(
                    (
                        int(report["metrics"].get("tool_batch_count") or 0)
                        for report in finalized_step_reports
                    ),
                    default=0,
                ),
                "after_tool_batch": {
                    **step_lifecycle_metrics,
                    "source_counts": dict(
                        step_lifecycle_metrics["source_counts"]
                    ),
                },
                "provider_event_loop": provider_event_loop,
                "context_savings": observer.report(
                    invocation=current_invocation
                ),
            }
            if provider_usage:
                details["provider_usage"] = provider_usage
            if execution_error is not None:
                details["error_type"] = type(execution_error).__name__
                details["failure_kind"] = classify_failure(
                    "failed",
                    execution_error,
                )
            if adaptive_decision is not None:
                details["adaptive_policy"] = adaptive_decision.report()
                if adaptive_turn_runtime is not None:
                    try:
                        details["adaptive_policy"]["turn_runtime"] = (
                            adaptive_turn_runtime.report()
                        )
                    except Exception as exc:
                        details["adaptive_policy"]["turn_runtime_error"] = (
                            type(exc).__name__
                        )
                try:
                    details["adaptive_policy"]["state_updated"] = bool(
                        self._adaptive_controller.observe(
                            decision=adaptive_decision,
                            details=details,
                            result=result,
                        )
                    )
                except Exception as exc:
                    details["adaptive_policy"]["state_updated"] = False
                    details["adaptive_policy"]["state_error"] = (
                        type(exc).__name__
                    )
            else:
                # A hand-set plan's component reports are kept as well; the
                # completion event below is not stored anywhere.
                observe_manual = getattr(
                    self._adaptive_controller,
                    "observe_manual",
                    None,
                )
                if callable(observe_manual):
                    try:
                        observe_manual(
                            invocation=current_invocation,
                            features=feature_configs,
                            details=details,
                            result=result,
                        )
                    except Exception:
                        pass
            decision_key = self._decision_key(invocation)
            if decision_key:
                with self._adaptive_decisions_lock:
                    self._adaptive_decisions.pop(decision_key, None)
            self._emit(
                context,
                "agent_harness_completed",
                details,
            )


def _tool_result_classification(result: object) -> str:
    """Classify a provider-neutral result without interpreting its content."""

    if not isinstance(result, dict) or result.get("success") is not True:
        return "failed"
    content_items = result.get("contentItems")
    if not isinstance(content_items, list) or not content_items:
        return "structured"
    item_types = {
        str(item.get("type") or "")
        for item in content_items
        if isinstance(item, dict)
    }
    if item_types == {"inputText"} and all(
        isinstance(item, dict) and isinstance(item.get("text"), str)
        for item in content_items
    ):
        return "plain_text"
    if any("image" in item_type.casefold() for item_type in item_types):
        return "image_or_multimodal"
    return "structured"


def default_agent_harness(
    *,
    state_root: Any = None,
    namespace: str = "",
) -> AgentHarness:
    from .harness_auto_policy import AdaptiveHarnessController

    offloading = HARNESS_FEATURES["tool_output_offloading"].feature_class()(
        state_root=state_root, namespace=namespace,
    )
    features = []
    for name, definition in HARNESS_FEATURES.items():
        if name == "tool_output_offloading":
            feature = offloading
        elif name == "artifact_search_and_slice":
            feature = definition.feature_class()(offloading.artifact_store)
        else:
            feature = definition.feature_class()()
        features.append(feature)
    return AgentHarness(
        tuple(features),
        default_enabled_feature_ids=DEFAULT_ENABLED_AGENT_HARNESS_FEATURE_IDS,
        adaptive_controller=AdaptiveHarnessController(artifact_store=offloading.artifact_store),
    )


def _numeric_observation(value: object, *, depth: int = 0) -> dict[str, Any]:
    if depth > 3 or not isinstance(value, dict):
        return {}
    observed: dict[str, Any] = {}
    for raw_key, raw_value in list(value.items())[:100]:
        key = str(raw_key or "")[:80]
        if not key:
            continue
        if isinstance(raw_value, bool):
            continue
        if isinstance(raw_value, (int, float)):
            observed[key] = raw_value
            continue
        nested = _numeric_observation(raw_value, depth=depth + 1)
        if nested:
            observed[key] = nested
    return observed


def _accumulate_numeric_metrics(
    target: dict[str, Any],
    incoming: dict[str, Any],
) -> None:
    for raw_key, value in dict(incoming or {}).items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            target[key] = value
            continue
        target[key] = target.get(key, 0) + value
