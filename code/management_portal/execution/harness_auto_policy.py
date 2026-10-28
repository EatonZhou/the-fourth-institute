from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from management_common.database import DatabaseError
from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database
from management_common.database_time import instant

from .harness import capabilities_of, controlled_policy_overrides
from .harness_auto_selection import PlanInputs, select_features
from .harness_capabilities import (
    ACTIVE_TURN_INPUT,
    NATIVE_TOOL_EVENTS,
    PORTAL_TOOLS,
    TURN_REASONING_EFFORT,
)
from .harness_effectiveness import (
    HarnessEffectivenessStore,
    ManualHarnessTurn,
    load_family,
    plan_signature,
)
from .harness_evolution import (
    CHAMPION,
    ArmAssignment,
    PolicyCellCache,
    assign_arm,
)
from .harness_policy_table import (
    HarnessPolicyParameters,
    context_budget_tokens,
    policy_parameters,
    validate_overrides,
    with_overrides,
)


HARNESS_AUTO_POLICY_VERSION = 6
HARNESS_MODE_AUTO = "auto"
# The arm of a controlled experiment that names its own parameter overrides.
EXPERIMENT_ARM = "experiment"
LARGE_INPUT_IMAGE_BYTES = 1024 * 1024

from .harness_tool_semantics import inline_media_bytes
from .harness_artifact_recall import artifact_scope_for_invocation
from ..resource_refs import extract_resource_addresses

_REPO_ACTION_RE = re.compile(
    r"(?:实现|修改|修复|重构|代码|测试|排查|开发|优化|迁移|"
    r"implement|modify|fix|refactor|code|test|debug|develop|"
    r"optimi[sz]e|migrat)",
    re.IGNORECASE,
)
_REPO_OBJECT_RE = re.compile(
    r"(?:仓库|文件|模块|函数|类|接口|前端|后端|数据库|依赖|"
    r"组件|服务|测试|错误|"
    r"repo(?:sitory)?|file|module|function|class|api|frontend|"
    r"backend|database|dependency|component|service|test|bug|issue|error|"
    r"\.py\b|\.js\b|\.ts\b|\.go\b|"
    r"\.rs\b|\.java\b)",
    re.IGNORECASE,
)


def _names_repository_work(prompt: str) -> bool:
    return bool(_REPO_ACTION_RE.search(prompt) and _REPO_OBJECT_RE.search(prompt))


@dataclass(frozen=True)
class _TurnRequest:
    """What a turn's invocation tells the policy."""

    provider: str
    operation: str
    execution_id: str
    cwd: str
    prompt: str
    input_media: tuple[object, ...]
    metadata: Mapping[str, Any]
    # What the Backend's Adapter declares it can do; the policy never asks
    # which Backend this is.
    capabilities: frozenset[str]
    scope: str
    configured_reasoning_effort: str
    # Only a controlled paired experiment names its own parameter overrides.
    experiment_overrides: dict[str, object] | None
    portal_tool_count: int
    portal_tools_expected: bool
    is_new_backend_session: bool
    resource_reference: bool

    @classmethod
    def read(cls, invocation: Any) -> _TurnRequest:
        request = getattr(invocation, "request", None)
        runtime_options = getattr(request, "runtime_options", None)
        runtime_options = (
            runtime_options if isinstance(runtime_options, dict) else {}
        )
        harness_options = runtime_options.get("agent_harness")
        harness_options = (
            harness_options if isinstance(harness_options, dict) else {}
        )
        metadata = getattr(request, "metadata", None)
        metadata = metadata if isinstance(metadata, Mapping) else {}
        capabilities = capabilities_of(invocation)
        experiment_overrides = harness_options.get("policy_overrides")
        if experiment_overrides is not None:
            experiment_overrides = controlled_policy_overrides(experiment_overrides, metadata)
        portal_tool_count = len(tuple(getattr(invocation, "harness_tools", ()) or ()))
        prompt = str(getattr(invocation, "user_prompt", "") or "")
        return cls(
            provider=str(getattr(request, "provider", "") or ""),
            operation=str(getattr(request, "operation", "") or ""),
            execution_id=str(getattr(request, "execution_id", "") or ""),
            cwd=str(getattr(request, "cwd", "") or ""),
            prompt=prompt,
            input_media=tuple(getattr(request, "input_media", ()) or ()),
            metadata=metadata,
            capabilities=capabilities,
            scope=artifact_scope_for_invocation(invocation),
            configured_reasoning_effort=str(
                runtime_options.get("effort") or ""
            ).strip().casefold(),
            experiment_overrides=experiment_overrides,
            portal_tool_count=portal_tool_count,
            # The plan is made before the Harness tools are mounted, so the
            # count misses them.  A Resident turn always gets the ISA tool,
            # and a session keeps the tools it has seen; their results pass
            # through the tool-batch features, which therefore have to be
            # planned.
            portal_tools_expected=bool(
                portal_tool_count
                or (
                    PORTAL_TOOLS in capabilities
                    and (
                        str(metadata.get("resident_id") or "").startswith("resident_")
                        or getattr(invocation, "available_harness_tool_names", None)
                    )
                )
            ),
            is_new_backend_session=bool(
                getattr(invocation, "is_new_backend_session", False)
                or getattr(getattr(invocation, "session", None), "is_new_backend_session", False)
            ),
            resource_reference=bool(extract_resource_addresses(prompt)),
        )

    @property
    def turn_steering(self) -> bool:
        return (
            ACTIVE_TURN_INPUT in self.capabilities
            and NATIVE_TOOL_EVENTS in self.capabilities
        )

    @property
    def adaptive_reasoning(self) -> bool:
        # Lowering the effort for one turn only pays where the session keeps
        # running; elsewhere a changed effort restarts the Provider session.
        return TURN_REASONING_EFFORT in self.capabilities

    @property
    def prompt_tokens_estimated(self) -> int:
        return max(1, len(self.prompt) // 4)

    @property
    def long_prompt(self) -> bool:
        return self.prompt_tokens_estimated >= 24_000


@dataclass(frozen=True)
class _SessionHistory:
    """The cold-start hints the session's earlier turns left for this one."""

    turn_index: int
    tool_calls: int
    output_bytes: int
    duplicate_reads: int
    discovery_hold: int
    image_hold: int
    output_hold: int
    duplicate_hold: int
    fragmented: bool
    last_context_pressure_percent: float
    last_context_window_tokens: int
    last_input_tokens: int

    @classmethod
    def read(cls, state: Mapping[str, Any]) -> _SessionHistory:
        discovery_hold = _int(state.get("discovery_hold_turns"))
        return cls(
            turn_index=max(1, _int(state.get("turn_count")) + 1),
            tool_calls=max(
                _int(state.get("last_tool_calls")),
                round(_bounded_float(state.get("ewma_tool_calls"))),
            ),
            output_bytes=max(
                _int(state.get("last_output_bytes")),
                round(_bounded_float(state.get("ewma_output_bytes"))),
            ),
            duplicate_reads=max(
                _int(state.get("last_duplicate_reads")),
                round(_bounded_float(state.get("ewma_duplicate_reads"))),
            ),
            discovery_hold=discovery_hold,
            image_hold=_int(state.get("image_hold_turns")),
            output_hold=_int(state.get("large_output_hold_turns")),
            duplicate_hold=_int(state.get("duplicate_hold_turns")),
            fragmented=bool(
                discovery_hold > 0
                or _int(state.get("last_singleton_read_streak")) >= 5
                or _int(state.get("last_discovery_opportunities")) >= 2
            ),
            last_context_pressure_percent=_bounded_float(
                state.get("last_context_pressure_percent")
            ),
            last_context_window_tokens=_int(state.get("last_context_window_tokens")),
            last_input_tokens=_int(state.get("last_input_tokens")),
        )

    def context_pressure(self, parameters: HarnessPolicyParameters) -> float:
        pressure = self.last_context_pressure_percent
        # A Backend with a bounded compaction threshold fills its context
        # budget long before it fills the model's window.
        context_budget = context_budget_tokens(
            parameters,
            self.last_context_window_tokens,
        )
        if context_budget and parameters.compaction_max_context_tokens:
            pressure = max(pressure, min(
                100.0,
                self.last_input_tokens * 100.0 / context_budget,
            ))
        return pressure


def _decision_signals(
    turn: _TurnRequest,
    history: _SessionHistory,
    inputs: PlanInputs,
    media: Mapping[str, int],
    reasoning_quality: Mapping[str, Any],
    assignment: ArmAssignment,
) -> dict[str, Any]:
    """What the decision reports about the turn it planned."""

    return {
        "context_pressure_percent": round(inputs.context_pressure_percent, 2),
        "previous_tool_calls": history.tool_calls,
        "previous_output_bytes": history.output_bytes,
        "previous_duplicate_reads": history.duplicate_reads,
        "current_media_count": inputs.current_media_count,
        "current_media_source_bytes_total": media["total"],
        "current_media_source_bytes_max": media["maximum"],
        "current_media_large_image_count": media["large_count"],
        "large_input_image_signal": bool(media["large_count"] > 0),
        "portal_tool_count": turn.portal_tool_count,
        "portal_tools_expected": turn.portal_tools_expected,
        "prompt_tokens_estimated": turn.prompt_tokens_estimated,
        "configured_reasoning_effort": inputs.configured_reasoning_effort,
        "initial_reasoning_effort": inputs.initial_reasoning_effort,
        "adaptive_reasoning_effort": turn.adaptive_reasoning,
        "reasoning_task_complexity": inputs.reasoning_task_complexity,
        "reasoning_quality_guard": inputs.reasoning_quality_guard,
        "reasoning_quality_sample_count": int(
            reasoning_quality.get("sample_count") or 0
        ),
        "reasoning_quality_failure_rate": float(
            reasoning_quality.get("failure_rate") or 0.0
        ),
        "resource_reference": turn.resource_reference,
        "repo_task_signal": inputs.repo_task,
        "fragmented_discovery_signal": inputs.fragmented,
        "history_protection_signal": inputs.history_protection,
        "is_new_backend_session": turn.is_new_backend_session,
        "operation": turn.operation,
        "queue_wait_ms": _bounded_float(
            turn.metadata.get("execution_queue_wait_ms")
        ),
        "queue_wait_observed": (
            "execution_queue_wait_ms" in turn.metadata
            and turn.metadata.get("execution_queue_wait_ms") is not None
        ),
        "cross_turn_feedback_role": "cold_start_only",
        **assignment.signals(),
        "policy_variant": assignment.arm,
        "policy_selection_source": (
            "controlled_experiment"
            if assignment.arm == EXPERIMENT_ARM
            else "policy_cell"
        ),
        "capabilities": sorted(turn.capabilities),
        "turn_steering_available": turn.turn_steering,
    }


def _adaptive_reasoning_effort(
    configured: object,
    *,
    context_pressure_percent: float,
    task_complexity: bool = False,
    quality_guard: bool = False,
    downgrade_pressure_percent: float = 52.0,
) -> str:
    """Choose a reversible lower-latency effort from observed load."""

    effort = str(configured or "").strip().casefold()
    if context_pressure_percent < downgrade_pressure_percent:
        return effort
    if quality_guard:
        return effort
    if task_complexity and context_pressure_percent < 82:
        return effort
    if task_complexity and effort in {"xhigh", "max", "ultra"}:
        return "high"
    if effort in {"high", "xhigh", "max", "ultra"}:
        return "medium"
    return effort


@dataclass(frozen=True)
class AdaptiveHarnessDecision:
    scope: str
    turn_index: int
    features: dict[str, dict[str, Any]]
    signals: dict[str, Any]
    reasons: dict[str, str]
    feedback_scope_available: bool
    execution_id: str = ""
    provider: str = ""
    # What the Adapter of this turn can do, and the features the signals
    # chose but the Adapter cannot run (feature ID -> ``requires_<capability>``).
    capabilities: tuple[str, ...] = ()
    unavailable: dict[str, str] = field(default_factory=dict)

    def report(self) -> dict[str, Any]:
        enabled = sorted(
            feature_id
            for feature_id, config in self.features.items()
            if isinstance(config, dict) and config.get("enabled") is True
        )
        report = {
            "mode": HARNESS_MODE_AUTO,
            "policy_version": HARNESS_AUTO_POLICY_VERSION,
            "primary_feedback_scope": "current_turn",
            "cross_turn_role": "cold_start_only",
            "turn_index": self.turn_index,
            "feedback_scope_available": self.feedback_scope_available,
            "enabled_feature_ids": enabled,
            "enabled_feature_count": len(enabled),
            "reactive_feature_ids": [
                feature_id
                for feature_id in (
                    "early_context_compaction",
                    "hash_aware_read_dedup",
                    "inline_media_history",
                    "native_tool_loop_governor",
                    "tool_output_offloading",
                )
                if feature_id in enabled
            ],
            "signals": dict(self.signals),
            "reasons": {
                feature_id: self.reasons[feature_id]
                for feature_id in enabled
                if feature_id in self.reasons
            },
        }
        report["plan_signature"] = plan_signature(self.features)
        report["capabilities"] = list(self.capabilities)
        report["unavailable_features"] = dict(self.unavailable)
        return report


class AdaptiveHarnessTurnRuntime:
    """Account for one turn without changing its saved plan.

    Tool batches and native events update observations and descriptive load
    profiles. The next turn can use those observations to choose a new plan.
    """

    _PROFILE_RANK = {
        "baseline": 0,
        "exploratory": 1,
        "protective": 2,
        "emergency": 3,
    }

    def __init__(self, decision: AdaptiveHarnessDecision) -> None:
        self._lock = threading.RLock()
        self._profile = "baseline"
        self._prompt_tokens_estimated = _int(
            decision.signals.get("prompt_tokens_estimated")
        )
        self._current_input_media_count = _int(
            decision.signals.get("current_media_count")
        )
        self._sequence = 0
        self._tool_batches = 0
        self._tool_results = 0
        self._portal_output_bytes = 0
        self._portal_inline_media_bytes = 0
        self._native_output_bytes = 0
        self._native_inline_media_bytes = 0
        self._context_pressure_percent = 0.0
        self._failed_tool_calls = 0
        self._duplicate_reads = 0
        self._transitions: list[dict[str, Any]] = []
        self._actions: dict[str, dict[str, Any]] = {}
        if (
            self._prompt_tokens_estimated >= 24_000
            or self._current_input_media_count > 0
        ):
            self._profile = "protective"
            self._sequence = 1
            self._transitions.append({
                "sequence": 1,
                "source": "turn_input",
                "from": "baseline",
                "to": "protective",
                "reason": "large_prompt_or_input_media",
            })

    def observe_tool_batch(
        self,
        *,
        batch: Any,
        context_pressure_percent: float,
    ) -> None:
        items = tuple(getattr(batch, "items", ()) or ())
        results = tuple(getattr(item, "result", None) for item in items)
        output_bytes = _serialized_bytes(results)
        media_bytes = inline_media_bytes(results)
        failed = sum(
            1
            for result in results
            if not isinstance(result, dict) or result.get("success") is not True
        )
        with self._lock:
            self._tool_batches += 1
            self._tool_results += len(items)
            self._portal_output_bytes += output_bytes
            self._portal_inline_media_bytes += media_bytes
            self._failed_tool_calls += failed
            self._context_pressure_percent = max(
                self._context_pressure_percent,
                _bounded_float(context_pressure_percent),
            )
            self._reevaluate(source="portal_tool_batch")

    def observe_native_snapshot(
        self,
        snapshot: Mapping[str, object],
    ) -> dict[str, int]:
        """Record one live Provider ledger without overriding planned limits."""

        with self._lock:
            self._native_output_bytes = max(
                self._native_output_bytes,
                _int(snapshot.get("native_output_bytes")),
            )
            self._native_inline_media_bytes = max(
                self._native_inline_media_bytes,
                _int(snapshot.get("inline_media_bytes")),
            )
            self._failed_tool_calls = max(
                self._failed_tool_calls,
                _int(snapshot.get("failed_tool_calls")),
            )
            self._duplicate_reads = max(
                self._duplicate_reads,
                _int(snapshot.get("duplicate_read_calls")),
            )
            self._context_pressure_percent = max(
                self._context_pressure_percent,
                _bounded_float(snapshot.get("context_pressure_percent")),
            )
            self._reevaluate(source="native_provider_event")
            return {}

    def feature_config(
        self,
        feature_id: str,
        configured: Mapping[str, object],
    ) -> dict[str, Any]:
        """Return a batch-local config without mutating request settings."""

        with self._lock:
            config = dict(configured)
            if feature_id == "native_tool_loop_governor":
                config["_adaptive_turn_runtime"] = self
            return config

    def record_feature_result(
        self,
        feature_id: str,
        *,
        applied: bool,
        metrics: Mapping[str, object],
    ) -> None:
        with self._lock:
            if feature_id == "hash_aware_read_dedup":
                self._duplicate_reads += _int(
                    metrics.get("duplicate_output_count_removed")
                )
            if applied:
                self._record_action(
                    feature_id,
                    {"applied_batches": 1},
                    reason="current_turn_transform_applied",
                    accumulate=True,
                )
            self._reevaluate(source=f"feature:{feature_id}")

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "feedback_scope": "current_turn",
                "effective_profile": self._profile,
                "prompt_tokens_estimated": self._prompt_tokens_estimated,
                "current_input_media_count": self._current_input_media_count,
                "tool_batches_observed": self._tool_batches,
                "tool_results_observed": self._tool_results,
                "portal_output_bytes_observed": self._portal_output_bytes,
                "portal_inline_media_bytes_observed": (
                    self._portal_inline_media_bytes
                ),
                "native_output_bytes_observed": self._native_output_bytes,
                "native_inline_media_bytes_observed": (
                    self._native_inline_media_bytes
                ),
                "context_pressure_percent": round(
                    self._context_pressure_percent,
                    2,
                ),
                "failed_tool_calls_observed": self._failed_tool_calls,
                "duplicate_reads_observed": self._duplicate_reads,
                "transitions": list(self._transitions),
                "effective_actions": {
                    key: dict(value) for key, value in self._actions.items()
                },
            }

    def _reevaluate(self, *, source: str) -> None:
        total_output = self._portal_output_bytes + self._native_output_bytes
        total_media = (
            self._portal_inline_media_bytes + self._native_inline_media_bytes
        )
        if self._context_pressure_percent >= 82:
            proposed = "emergency"
            reason = "context_pressure_critical"
        elif (
            self._context_pressure_percent >= 60
            or total_output >= 192 * 1024
            or total_media >= 512 * 1024
            or self._failed_tool_calls >= 3
        ):
            proposed = "protective"
            reason = "current_turn_resource_pressure"
        elif (
            self._tool_batches >= 8
            or self._duplicate_reads >= 2
            or total_output >= 96 * 1024
        ):
            proposed = "exploratory"
            reason = "current_turn_workload_growth"
        else:
            proposed = "baseline"
            reason = "within_baseline_budget"
        if self._PROFILE_RANK[proposed] <= self._PROFILE_RANK[self._profile]:
            return
        previous = self._profile
        self._profile = proposed
        self._sequence += 1
        self._transitions.append({
            "sequence": self._sequence,
            "source": source,
            "from": previous,
            "to": proposed,
            "reason": reason,
        })


    def _record_action(
        self,
        feature_id: str,
        values: Mapping[str, object],
        *,
        reason: str,
        accumulate: bool = False,
    ) -> None:
        previous = self._actions.get(feature_id)
        candidate = dict(values)
        if accumulate and previous:
            candidate = dict(previous)
            for key, value in values.items():
                candidate[key] = _int(candidate.get(key)) + _int(value)
        candidate["reason"] = reason
        if previous != candidate:
            self._actions[feature_id] = candidate


class HarnessAutoPolicyStateStore:
    """Cold-start hints keyed by provider session binding.

    Live adaptation belongs to the current-turn event and tool-batch loops.  This
    store only chooses the initial profile and thresholds before those loops run.
    """

    def __init__(self) -> None:
        self.database = portal_database(schema="harness_auto_policy")
        self._lock = threading.RLock()
        self._initialize_lock = threading.Lock()
        self._initialized = False
        self._last_cleanup_at = 0.0
        self._cache: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, str] = {}
        self._writer_event = threading.Event()
        self._writer_thread: threading.Thread | None = None
        self._writing = False

    def load(self, scope: str) -> dict[str, Any]:
        if not scope:
            return {}
        with self._lock:
            cached = self._cache.get(scope)
            if cached is not None:
                return dict(cached)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    'SELECT state_json FROM harness_auto_policy_state WHERE scope = %s',
                    (scope,),
                ).fetchone()
        except (OSError, DatabaseError):
            row = None
        if not row:
            with self._lock:
                current = self._cache.setdefault(scope, {})
                return dict(current)
        try:
            value = json_value(row[0] or "{}")
        except (TypeError, json.JSONDecodeError):
            value = {}
        value = value if isinstance(value, dict) else {}
        with self._lock:
            current = self._cache.setdefault(scope, dict(value))
            return dict(current)

    def save(self, scope: str, state: Mapping[str, Any]) -> bool:
        if not scope:
            return False
        value = dict(state)
        try:
            payload = json_text(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except (TypeError, ValueError):
            return False
        if len(payload.encode("utf-8")) > 64 * 1024:
            return False
        with self._lock:
            self._cache[scope] = value
            self._pending[scope] = payload
            if self._writer_thread is None or not self._writer_thread.is_alive():
                self._writer_thread = threading.Thread(
                    target=self._writer_loop,
                    name="portal-harness-auto-policy-writer",
                    daemon=True,
                )
                self._writer_thread.start()
            self._writer_event.set()
        return True

    def flush(self, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        self._writer_event.set()
        while time.monotonic() < deadline:
            with self._lock:
                if not self._pending and not self._writing:
                    return True
            time.sleep(0.005)
        return False

    def _writer_loop(self) -> None:
        while True:
            if not self._writer_event.wait(timeout=30):
                with self._lock:
                    if not self._pending:
                        self._writer_thread = None
                        return
                continue
            # Coalesce bursts from concurrent sessions into one transaction.
            time.sleep(0.01)
            with self._lock:
                batch = self._pending
                self._pending = {}
                self._writer_event.clear()
                self._writing = bool(batch)
                cleanup = not self._last_cleanup_at or time.monotonic() - self._last_cleanup_at >= 3600
            if not batch:
                with self._lock:
                    self._writing = False
                continue
            self._write_batch(batch, cleanup=cleanup)
            with self._lock:
                self._writing = False
                if self._pending:
                    self._writer_event.set()

    def _write_batch(self, batch: Mapping[str, str], *, cleanup: bool) -> None:
        try:
            with self._connect() as connection:
                now = int(time.time())
                connection.executemany(
                    'INSERT INTO harness_auto_policy_state (scope, state_json, updated_at) VALUES (%s, %s, %s) ON CONFLICT(scope) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at',
                    (
                        (scope, payload, instant(now))
                        for scope, payload in batch.items()
                    ),
                )
                if cleanup:
                    connection.execute(
                        'DELETE FROM harness_auto_policy_state WHERE updated_at < %s',
                        (instant(now - 45 * 24 * 60 * 60),),
                    )
                connection.commit()
                if cleanup:
                    self._last_cleanup_at = time.monotonic()
        except (OSError, DatabaseError):
            # Feedback is advisory. The in-memory state remains authoritative
            # for this process and a later turn will enqueue a fresh snapshot.
            return

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            if not self._initialized:
                with self._initialize_lock:
                    if not self._initialized:
                        connection.execute(
                            "CREATE TABLE IF NOT EXISTS "
                            "harness_auto_policy_state ("
                            "scope TEXT PRIMARY KEY, "
                            "state_json JSONB NOT NULL, "
                            "updated_at TIMESTAMPTZ NOT NULL"
                            ")"
                        )
                        connection.execute(
                            "CREATE INDEX IF NOT EXISTS "
                            "idx_harness_auto_policy_updated_at "
                            "ON harness_auto_policy_state(updated_at)"
                        )
                        connection.commit()
                        self._initialized = True
            yield connection


class AdaptiveHarnessController:
    """Arm current-turn feedback loops and retain cold-start hints."""

    def __init__(
        self,
        *,
        artifact_store: Any = None,
    ) -> None:
        self.state_store = HarnessAutoPolicyStateStore()
        self.effectiveness_store = HarnessEffectivenessStore.shared()
        self.artifact_store = artifact_store
        self._repo_cache: dict[str, bool] = {}
        self._repo_cache_lock = threading.RLock()

    def resolve(
        self,
        invocation: Any,
        configured_features: Mapping[str, object],
    ) -> AdaptiveHarnessDecision:
        turn = _TurnRequest.read(invocation)
        history = _SessionHistory.read(self.state_store.load(turn.scope))
        # How the features are used on this Backend: measured parameters,
        # looked up in one table.
        backend_parameters = policy_parameters(turn.provider)
        media = _local_input_media_source_bytes(turn.input_media)
        has_artifacts = self._has_artifacts(turn.scope)
        repo_task = bool(
            self._repo_available(turn.cwd)
            and (_names_repository_work(turn.prompt) or history.discovery_hold > 0)
        )
        family = load_family({
            "current_media_count": len(turn.input_media),
            "current_media_large_image_count": media["large_count"],
            "prompt_tokens_estimated": turn.prompt_tokens_estimated,
            "portal_tool_count": turn.portal_tool_count,
            "previous_tool_calls": history.tool_calls,
            "context_pressure_percent": history.context_pressure(backend_parameters),
            "repo_task_signal": repo_task,
        })
        assignment, parameters, override_error = self._policy_arm(
            turn, family, backend_parameters,
        )
        pressure = history.context_pressure(parameters)
        task_complexity = bool(repo_task or turn.long_prompt or turn.resource_reference)
        reasoning_quality = self.effectiveness_store.reasoning_quality_signal(
            scope=turn.scope,
            provider=turn.provider,
            workload_family=family,
        ) if turn.adaptive_reasoning else {}
        quality_guard = bool(reasoning_quality.get("guard"))
        inputs = PlanInputs(
            parameters=parameters,
            context_pressure_percent=pressure,
            high_tool_load=history.tool_calls >= parameters.high_tool_load_calls,
            fragmented=history.fragmented,
            history_protection=bool(
                pressure >= parameters.protect_history_pressure_percent
                or history.output_hold > 0
                or history.duplicate_hold > 0
                or history.image_hold > 0
            ),
            output_hold=history.output_hold,
            duplicate_hold=history.duplicate_hold,
            image_hold=history.image_hold,
            turn_steering=turn.turn_steering,
            granularity_by_reminder=(
                turn.turn_steering and parameters.granularity_reminders_during_turn
            ),
            portal_tools_expected=turn.portal_tools_expected,
            has_artifacts=has_artifacts,
            current_media_count=len(turn.input_media),
            resource_reference=turn.resource_reference,
            long_prompt=turn.long_prompt,
            repo_task=repo_task,
            configured_reasoning_effort=turn.configured_reasoning_effort,
            initial_reasoning_effort=(
                _adaptive_reasoning_effort(
                    turn.configured_reasoning_effort,
                    context_pressure_percent=pressure,
                    task_complexity=task_complexity,
                    quality_guard=quality_guard,
                    downgrade_pressure_percent=parameters.reasoning_downgrade_pressure_percent,
                )
                if turn.adaptive_reasoning
                else turn.configured_reasoning_effort
            ),
            reasoning_task_complexity=task_complexity,
            reasoning_quality_guard=quality_guard,
        )
        features, reasons = select_features(configured_features, inputs)
        signals = _decision_signals(
            turn, history, inputs, media, reasoning_quality, assignment,
        )
        if override_error:
            signals["policy_override_error"] = override_error
        signals["load_family"] = load_family(signals)
        return AdaptiveHarnessDecision(
            scope=turn.scope,
            turn_index=history.turn_index,
            features=features,
            signals=signals,
            reasons=reasons,
            feedback_scope_available=bool(turn.scope),
            execution_id=turn.execution_id,
            provider=turn.provider,
            capabilities=tuple(sorted(turn.capabilities)),
            # The same signals choose the same features on every Backend; what
            # a Backend's Adapter cannot do is then taken out, with the reason.
            unavailable={},
        )

    @staticmethod
    def _policy_arm(
        turn: _TurnRequest,
        family: str,
        parameters: HarnessPolicyParameters,
    ) -> tuple[ArmAssignment, HarnessPolicyParameters, str]:
        """The arm this turn runs, the parameters it brings, and why an arm was refused.

        The cell of a turn is its Provider and load family; the arm it runs
        brings the parameters that differ from the Backend's.
        """

        cell = None
        if turn.experiment_overrides is not None:
            assignment = ArmAssignment(
                f"{turn.provider}/{family}", EXPERIMENT_ARM, 0,
                dict(turn.experiment_overrides),
            )
        else:
            cell = PolicyCellCache.shared().lookup(turn.provider, family)
            assignment = assign_arm(
                cell,
                provider=turn.provider,
                workload_family=family,
                session_key=turn.scope or turn.execution_id,
            )
        if not assignment.overrides:
            return assignment, parameters, ""
        try:
            return assignment, with_overrides(parameters, assignment.overrides), ""
        except ValueError as exc:
            # A cell this code cannot run never fails the turn.
            override_error = str(exc)[:200]
            fallback = validate_overrides(cell.champion) if cell is not None else {}
            return (
                ArmAssignment(assignment.cell, CHAMPION, assignment.version, fallback),
                with_overrides(parameters, fallback),
                override_error,
            )

    @staticmethod
    def begin_turn(
        decision: AdaptiveHarnessDecision,
    ) -> AdaptiveHarnessTurnRuntime:
        return AdaptiveHarnessTurnRuntime(decision)

    def observe(
        self,
        *,
        decision: AdaptiveHarnessDecision,
        details: Mapping[str, object],
        result: object,
    ) -> bool:
        if not decision.scope:
            return False
        previous = self.state_store.load(decision.scope)
        provider_loop = _mapping(details.get("provider_event_loop"))
        tool_lifecycle = _mapping(details.get("after_tool_batch"))
        context = _mapping(details.get("context_savings"))
        usage = _mapping(details.get("provider_usage"))

        tool_calls = max(
            _int(provider_loop.get("native_tool_calls_observed")),
            _int(tool_lifecycle.get("tool_result_count")),
        )
        output_bytes = _int(provider_loop.get("native_output_bytes_estimated"))
        duplicate_reads = _int(
            provider_loop.get("duplicate_read_calls_observed")
        )
        image_results = _int(
            tool_lifecycle.get("image_or_multimodal_result_count")
        )
        current_media = _int(decision.signals.get("current_media_count"))
        discovery_opportunities = _int(
            provider_loop.get("discovery_opportunities_observed")
        )
        singleton_streak = _int(
            provider_loop.get("max_consecutive_singleton_read_batches")
        )
        observed_pressure = _bounded_float(
            context.get("context_pressure_percent")
        )
        input_tokens = max(
            _int(context.get("observed_input_tokens")),
            _usage_context_input_tokens(usage),
        )
        context_window = max(
            _int(context.get("context_window_tokens")),
            _int(usage.get("modelContextWindow")),
            _int(usage.get("model_context_window")),
        )
        if input_tokens and context_window:
            observed_pressure = max(
                observed_pressure,
                min(100.0, input_tokens * 100.0 / context_window),
            )
        elif observed_pressure <= 0:
            observed_pressure = _bounded_float(
                previous.get("last_context_pressure_percent")
            )
        large_output = bool(
            _int(provider_loop.get("large_native_outputs_observed"))
            or output_bytes >= 128 * 1024
        )
        fragmented = bool(
            discovery_opportunities >= 2 or singleton_streak >= 5
        )
        next_state = {
            "schema_version": 1,
            "turn_count": max(
                decision.turn_index,
                _int(previous.get("turn_count")) + 1,
            ),
            "last_context_pressure_percent": round(observed_pressure, 3),
            "last_input_tokens": input_tokens,
            "last_context_window_tokens": context_window,
            "last_tool_calls": tool_calls,
            "last_output_bytes": output_bytes,
            "last_duplicate_reads": duplicate_reads,
            "last_discovery_opportunities": discovery_opportunities,
            "last_singleton_read_streak": singleton_streak,
            "ewma_tool_calls": _ewma(previous.get("ewma_tool_calls"), tool_calls),
            "ewma_output_bytes": _ewma(previous.get("ewma_output_bytes"), output_bytes),
            "ewma_duplicate_reads": _ewma(
                previous.get("ewma_duplicate_reads"),
                duplicate_reads,
            ),
            "image_hold_turns": _next_hold(
                previous.get("image_hold_turns"),
                active=bool(current_media or image_results),
                hold=1,
            ),
            "large_output_hold_turns": _next_hold(
                previous.get("large_output_hold_turns"),
                active=large_output,
                hold=1,
            ),
            "duplicate_hold_turns": _next_hold(
                previous.get("duplicate_hold_turns"),
                active=duplicate_reads > 0,
                hold=1,
            ),
            "discovery_hold_turns": _next_hold(
                previous.get("discovery_hold_turns"),
                active=fragmented,
                hold=2,
            ),
            "updated_at": int(time.time()),
        }
        state_updated = self.state_store.save(decision.scope, next_state)
        try:
            self.effectiveness_store.record_turn(
                decision=decision,
                details=details,
                result=result,
            )
        except Exception:
            # Effectiveness telemetry must never fail the provider turn.
            pass
        return state_updated


    def observe_manual(
        self,
        *,
        invocation: Any,
        features: Mapping[str, object],
        details: Mapping[str, object],
        result: object,
    ) -> None:
        """Keep the component reports of a turn whose plan a person set.

        The policy learns nothing from it and keeps no state for it; the
        ledger row is the only lasting record of what its components did.
        """

        request = getattr(invocation, "request", None)
        self.effectiveness_store.record_turn(
            decision=ManualHarnessTurn(
                execution_id=str(getattr(request, "execution_id", "") or ""),
                scope=artifact_scope_for_invocation(invocation),
                provider=str(getattr(request, "provider", "") or ""),
                features={
                    str(feature_id): dict(config)
                    for feature_id, config in features.items()
                    if isinstance(config, Mapping)
                },
                capabilities=tuple(sorted(capabilities_of(invocation))),
            ),
            details=details,
            result=result,
        )

    def _has_artifacts(self, scope: str) -> bool:
        if not scope or self.artifact_store is None:
            return False
        has_scope = getattr(self.artifact_store, "has_scope", None)
        if not callable(has_scope):
            return False
        try:
            return bool(has_scope(scope))
        except Exception:
            return False

    def _repo_available(self, cwd: str) -> bool:
        clean_cwd = str(cwd or "").strip()
        if not clean_cwd:
            return False
        with self._repo_cache_lock:
            cached = self._repo_cache.get(clean_cwd)
            if cached is not None:
                return cached
        try:
            current = Path(clean_cwd).resolve()
            available = False
            for _ in range(8):
                if (current / ".git").exists():
                    available = True
                    break
                if current.parent == current:
                    break
                current = current.parent
        except OSError:
            available = False
        with self._repo_cache_lock:
            if len(self._repo_cache) >= 256:
                self._repo_cache.clear()
            self._repo_cache[clean_cwd] = available
        return available







def _serialized_bytes(value: object) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError):
        return len(str(value).encode("utf-8", errors="replace"))




def _local_input_media_source_bytes(
    input_media: tuple[object, ...],
) -> dict[str, int]:
    total = 0
    maximum = 0
    large_count = 0
    for item in input_media:
        if (
            str(getattr(item, "media_type", "") or "") != "image"
            or str(getattr(item, "source_type", "") or "") != "local_path"
        ):
            continue
        try:
            size = Path(str(getattr(item, "source", "") or "")).stat().st_size
        except (OSError, ValueError):
            continue
        total += max(0, int(size))
        maximum = max(maximum, int(size))
        if size >= LARGE_INPUT_IMAGE_BYTES:
            large_count += 1
    return {
        "total": total,
        "maximum": maximum,
        "large_count": large_count,
    }


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _bounded_float(value: object) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if number != number:
        return 0.0
    return max(0.0, min(number, 1_000_000_000.0))


def _ewma(previous: object, current: object, *, alpha: float = 0.4) -> float:
    value = _bounded_float(current)
    prior = _bounded_float(previous)
    if prior <= 0:
        return round(value, 3)
    return round((alpha * value) + ((1.0 - alpha) * prior), 3)


def _next_hold(previous: object, *, active: bool, hold: int) -> int:
    return max(hold if active else 0, max(0, _int(previous) - 1))


def _usage_context_input_tokens(usage: Mapping[str, object]) -> int:
    # Codex reports both cumulative totals and the latest active context.
    # Only the latter represents context pressure; summing/maxing nested usage
    # would classify a long agent loop as a full context window too early.
    current = _mapping(usage.get("context"))
    if current:
        return max(
            _int(current.get("inputTokens")),
            _int(current.get("input_tokens")),
        )
    return max(
        _int(usage.get("inputTokens")),
        _int(usage.get("input_tokens")),
    )
