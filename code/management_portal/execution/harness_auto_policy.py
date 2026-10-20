from __future__ import annotations

from management_common.format_tags import schema_matches

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

from .harness import capabilities_of
from .harness_capabilities import (
    ACTIVE_TURN_INPUT,
    NATIVE_TOOL_EVENTS,
    PORTAL_TOOLS,
    TURN_REASONING_EFFORT,
    Requirement,
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
from .harness_policy_table import HARNESS_EXPERIMENT_SCHEMA
from .harness_policy_table import (
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
from .harness import HARNESS_FEATURES
from .harness_artifact_recall import artifact_scope_for_invocation
from ..resource_refs import extract_resource_addresses

BUILTIN_HARNESS_FEATURE_IDS = frozenset(name for name, definition in HARNESS_FEATURES.items() if definition.adaptive)
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

    def flush(self, timeout: float = 2.0) -> bool:
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
        request = getattr(invocation, "request", None)
        provider = str(getattr(request, "provider", "") or "")
        operation = str(getattr(request, "operation", "") or "")
        prompt = str(getattr(invocation, "user_prompt", "") or "")
        input_media = tuple(getattr(request, "input_media", ()) or ())
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
        # The policy never asks which Backend this is.  It reads what the
        # Backend's Adapter declares it can do.
        capabilities = capabilities_of(invocation)
        turn_steering = (
            ACTIVE_TURN_INPUT in capabilities
            and NATIVE_TOOL_EVENTS in capabilities
        )
        # How the features are used on this Backend: measured parameters,
        # looked up in one table.
        parameters = policy_parameters(provider)
        # The read-batch advice goes either into the turn as a reminder or
        # into the prompt as a standing policy, never both.
        granularity_by_reminder = (
            turn_steering and parameters.granularity_reminders_during_turn
        )
        # A turn's parameter overrides come from the arm of its cell.  Only a
        # controlled paired experiment names its own.
        experiment_overrides = harness_options.get("policy_overrides")
        if experiment_overrides is not None:
            if not (
                schema_matches(metadata.get("experiment_schema"), "tfi.agent-harness-live-experiment.v1")
                and str(metadata.get("experiment_run_id") or "").strip()
                and str(metadata.get("experiment_pair_id") or "").strip()
            ):
                raise ValueError(
                    "AgentHarness policy_overrides are only accepted in a controlled experiment"
                )
            experiment_overrides = validate_overrides(experiment_overrides)
        portal_tool_count = len(tuple(getattr(invocation, "harness_tools", ()) or ()))
        # The plan is made before the Harness tools are mounted, so the count
        # above misses them.  A Resident turn always gets the ISA tool, and a
        # session keeps the tools it has seen; their results pass through the
        # tool-batch features, which therefore have to be planned.
        portal_tools_expected = bool(
            portal_tool_count
            or (
                PORTAL_TOOLS in capabilities
                and (
                    str(metadata.get("resident_id") or "").startswith("resident_")
                    or getattr(invocation, "available_harness_tool_names", None)
                )
            )
        )
        scope = artifact_scope_for_invocation(invocation)
        state = self.state_store.load(scope)
        turn_index = max(1, _int(state.get("turn_count")) + 1)

        def context_pressure(parameters: Any) -> tuple[float, int]:
            pressure = _bounded_float(state.get("last_context_pressure_percent"))
            # A Backend with a bounded compaction threshold fills its context
            # budget long before it fills the model's window.
            context_budget = context_budget_tokens(
                parameters,
                _int(state.get("last_context_window_tokens")),
            )
            if context_budget and parameters.compaction_max_context_tokens:
                pressure = max(pressure, min(
                    100.0,
                    _int(state.get("last_input_tokens")) * 100.0 / context_budget,
                ))
            return pressure, context_budget

        pressure, context_budget = context_pressure(parameters)
        tool_calls = max(
            _int(state.get("last_tool_calls")),
            round(_bounded_float(state.get("ewma_tool_calls"))),
        )
        output_bytes = max(
            _int(state.get("last_output_bytes")),
            round(_bounded_float(state.get("ewma_output_bytes"))),
        )
        duplicates = max(
            _int(state.get("last_duplicate_reads")),
            round(_bounded_float(state.get("ewma_duplicate_reads"))),
        )
        discovery_hold = _int(state.get("discovery_hold_turns"))
        image_hold = _int(state.get("image_hold_turns"))
        output_hold = _int(state.get("large_output_hold_turns"))
        duplicate_hold = _int(state.get("duplicate_hold_turns"))
        is_new_session = bool(
            getattr(invocation, "is_new_backend_session", False)
            or getattr(getattr(invocation, "session", None), "is_new_backend_session", False)
        )
        resource_reference = bool(extract_resource_addresses(prompt))
        current_media_count = len(input_media)
        input_media_source_bytes = _local_input_media_source_bytes(input_media)
        large_input_images = bool(
            input_media_source_bytes["large_count"] > 0
        )
        prompt_tokens_estimated = max(1, len(prompt) // 4)
        configured_reasoning_effort = str(
            runtime_options.get("effort") or ""
        ).strip().casefold()
        # Lowering the effort for one turn only pays where the session keeps
        # running; elsewhere a changed effort restarts the Provider session.
        adaptive_reasoning_enabled = (
            TURN_REASONING_EFFORT in capabilities
        )
        has_artifacts = self._has_artifacts(scope)
        repo_available = self._repo_available(
            str(getattr(request, "cwd", "") or "")
        )
        repo_task = bool(
            repo_available
            and (
                (
                    _REPO_ACTION_RE.search(prompt)
                    and _REPO_OBJECT_RE.search(prompt)
                )
                or discovery_hold > 0
            )
        )
        fragmented = bool(
            discovery_hold > 0
            or _int(state.get("last_singleton_read_streak")) >= 5
            or _int(state.get("last_discovery_opportunities")) >= 2
        )
        long_prompt = prompt_tokens_estimated >= 24_000
        reasoning_task_complexity = bool(
            repo_task or long_prompt or resource_reference
        )
        quality_family = load_family({
            "current_media_count": current_media_count,
            "current_media_large_image_count": input_media_source_bytes[
                "large_count"
            ],
            "prompt_tokens_estimated": prompt_tokens_estimated,
            "portal_tool_count": portal_tool_count,
            "previous_tool_calls": tool_calls,
            "context_pressure_percent": pressure,
            "repo_task_signal": repo_task,
        })
        # The cell of this turn is its Provider and load family; the arm it
        # runs brings the parameters that differ from the Backend's.
        cell = None
        if experiment_overrides is not None:
            assignment = ArmAssignment(
                f"{provider}/{quality_family}", EXPERIMENT_ARM, 0,
                dict(experiment_overrides),
            )
        else:
            cell = PolicyCellCache.shared().lookup(provider, quality_family)
            assignment = assign_arm(
                cell,
                provider=provider,
                workload_family=quality_family,
                session_key=scope or str(getattr(request, "execution_id", "") or ""),
            )
        override_error = ""
        if assignment.overrides:
            try:
                parameters = with_overrides(parameters, assignment.overrides)
            except ValueError as exc:
                # A cell this code cannot run never fails the turn.
                override_error = str(exc)[:200]
                fallback = validate_overrides(cell.champion) if cell is not None else {}
                parameters = with_overrides(parameters, fallback)
                assignment = ArmAssignment(assignment.cell, CHAMPION, assignment.version, fallback)
            pressure, context_budget = context_pressure(parameters)
        reasoning_quality = self.effectiveness_store.reasoning_quality_signal(
            scope=scope,
            provider=provider,
            workload_family=quality_family,
        ) if adaptive_reasoning_enabled else {}
        reasoning_quality_guard = bool(reasoning_quality.get("guard"))
        initial_reasoning_effort = (
            _adaptive_reasoning_effort(
                configured_reasoning_effort,
                context_pressure_percent=pressure,
                task_complexity=reasoning_task_complexity,
                quality_guard=reasoning_quality_guard,
                downgrade_pressure_percent=parameters.reasoning_downgrade_pressure_percent,
            )
            if adaptive_reasoning_enabled
            else configured_reasoning_effort
        )
        high_tool_load = tool_calls >= parameters.high_tool_load_calls
        protect_pressure = parameters.protect_history_pressure_percent
        history_protection = bool(
            pressure >= protect_pressure
            or output_hold > 0
            or duplicate_hold > 0
            or image_hold > 0
        )
        features = {
            str(feature_id): dict(config)
            for raw_feature_id, raw_config in configured_features.items()
            if (feature_id := str(raw_feature_id or "").strip())
            for config in [raw_config if isinstance(raw_config, dict) else {}]
        }
        provider_error_continue_enabled = (
            features.get("provider_error_continue", {}).get("enabled", True)
            is True
        )
        reasons: dict[str, str] = {}
        for feature_id in BUILTIN_HARNESS_FEATURE_IDS:
            features.setdefault(feature_id, {})["enabled"] = False

        def select(
            feature_id: str,
            enabled: bool,
            reason: str,
            **overrides: Any,
        ) -> None:
            config = features.setdefault(feature_id, {})
            config.update(overrides)
            config["enabled"] = enabled
            if enabled:
                reasons[feature_id] = reason

        checkpoint_interval = (
            55
            if high_tool_load or fragmented
            else 80
        )
        select(
            "native_tool_loop_governor",
            True,
            "selective_current_turn_feedback_loop",
            checkpoint_interval=checkpoint_interval,
            max_checkpoints=5,
            duplicate_read_threshold=4 if duplicate_hold else 6 if fragmented else 8,
            large_output_bytes=32 * 1024 if output_hold or pressure >= 60 else 64 * 1024,
            granularity_strategy=(
                "auto" if granularity_by_reminder or not turn_steering else "off"
            ),
            cumulative_output_bytes=96 * 1024 if history_protection else 256 * 1024,
            media_pressure_bytes=256 * 1024 if image_hold else 512 * 1024,
            max_granularity_steers=2,
            failure_threshold=3,
            post_steer_read_batches=4,
            context_pressure_threshold_percent=70,
            provider_latency_threshold_seconds=600,
            configured_reasoning_effort=configured_reasoning_effort,
            initial_reasoning_effort=initial_reasoning_effort,
            reasoning_task_complexity=reasoning_task_complexity,
            reasoning_quality_guard=reasoning_quality_guard,
            timing="before_turn",
        )
        select(
            "provider_error_continue",
            provider_error_continue_enabled,
            "bounded_provider_error_recovery",
            timing="before_turn",
        )
        select(
            "early_context_compaction",
            bool(history_protection or long_prompt),
            "selective_provider_context_guard",
            policy_threshold_percent=parameters.compaction_threshold_percent,
            max_context_tokens=parameters.compaction_max_context_tokens,
            # Existing high-pressure sessions compact before their next
            # expensive model call. The runtime controller remains armed
            # so growth during this Portal turn has an observed fallback.
        )
        select(
            "unified_resource_recall",
            resource_reference,
            "resource_recall_contract",
            timing="before_turn",
        )

        offload_history = bool(output_hold or pressure >= protect_pressure)
        offload_enabled = bool(
            portal_tools_expected or offload_history or has_artifacts
        )
        offload_base = parameters.offload_trigger_bytes
        offload_threshold = (
            offload_base // 2
            if pressure >= 65 or output_hold
            else offload_base * 3 // 4
            if pressure >= 45
            else offload_base
        )
        select(
            "tool_output_offloading",
            offload_enabled,
            (
                "intercept_large_portal_tool_outputs"
                if portal_tools_expected
                else "current_turn_large_output_interceptor"
            ),
            offload_trigger_bytes=offload_threshold,
            timing="after_tool_batch",
        )
        select(
            "hash_aware_read_dedup",
            bool(
                portal_tools_expected
                or history_protection
                or fragmented
            ),
            (
                "intercept_exact_duplicate_portal_reads"
                if portal_tools_expected
                else "current_turn_duplicate_read_interceptor"
            ),
            min_output_tokens=128 if duplicate_hold or pressure >= 65 else 256,
            timing="after_tool_batch",
        )
        select(
            "artifact_search_and_slice",
            has_artifacts,
            "session_has_offloaded_artifacts",
            timing="before_turn",
        )
        select(
            "inline_media_history",
            bool(
                current_media_count
                or image_hold > 0
                or portal_tools_expected
            ),
            "current_turn_inline_media_budget_interceptor",
            timing="after_tool_batch",
            max_tool_image_edge=1536,
            tool_image_jpeg_quality=82,
        )
        # The read-batch advice: a reminder during the turn where the Backend
        # takes one and the parameter table says so, else a standing prompt
        # policy (the only way where the Provider takes no input during a turn).
        select(
            "tool_call_granularity",
            bool(
                not granularity_by_reminder
                and (repo_task or fragmented or high_tool_load)
            ),
            "prompt_policy_without_turn_steering"
            if not turn_steering
            else "prompt_policy_by_backend_parameter",
            timing="before_turn",
        )

        # The same signals choose the same features on every Backend; what a
        # Backend's Adapter cannot do is then taken out, with the reason.
        unavailable = {}

        # Third-party feature contracts remain explicit because this controller
        # has no cost/effect model for them.
        for feature_id, config in features.items():
            if feature_id in BUILTIN_HARNESS_FEATURE_IDS:
                continue
            if config.get("enabled") is True:
                reasons[feature_id] = "external_feature_explicit_contract"

        signals = {
            "context_pressure_percent": round(pressure, 2),
            "previous_tool_calls": tool_calls,
            "previous_output_bytes": output_bytes,
            "previous_duplicate_reads": duplicates,
            "current_media_count": current_media_count,
            "current_media_source_bytes_total": input_media_source_bytes[
                "total"
            ],
            "current_media_source_bytes_max": input_media_source_bytes[
                "maximum"
            ],
            "current_media_large_image_count": input_media_source_bytes[
                "large_count"
            ],
            "large_input_image_signal": large_input_images,
            "portal_tool_count": portal_tool_count,
            "portal_tools_expected": portal_tools_expected,
            "prompt_tokens_estimated": prompt_tokens_estimated,
            "configured_reasoning_effort": configured_reasoning_effort,
            "initial_reasoning_effort": initial_reasoning_effort,
            "adaptive_reasoning_effort": adaptive_reasoning_enabled,
            "reasoning_task_complexity": reasoning_task_complexity,
            "reasoning_quality_guard": reasoning_quality_guard,
            "reasoning_quality_sample_count": int(
                reasoning_quality.get("sample_count") or 0
            ),
            "reasoning_quality_failure_rate": float(
                reasoning_quality.get("failure_rate") or 0.0
            ),
            "resource_reference": resource_reference,
            "repo_task_signal": repo_task,
            "fragmented_discovery_signal": fragmented,
            "history_protection_signal": history_protection,
            "is_new_backend_session": is_new_session,
            "operation": operation,
            "queue_wait_ms": _bounded_float(
                metadata.get("execution_queue_wait_ms")
            ),
            "queue_wait_observed": (
                "execution_queue_wait_ms" in metadata
                and metadata.get("execution_queue_wait_ms") is not None
            ),
            "cross_turn_feedback_role": "cold_start_only",
            **assignment.signals(),
            "policy_variant": assignment.arm,
            "policy_selection_source": (
                "controlled_experiment"
                if assignment.arm == EXPERIMENT_ARM
                else "policy_cell"
            ),
            "capabilities": sorted(capabilities),
            "turn_steering_available": turn_steering,
        }
        if override_error:
            signals["policy_override_error"] = override_error
        signals["load_family"] = load_family(signals)
        return AdaptiveHarnessDecision(
            scope=scope,
            turn_index=turn_index,

            features=features,
            signals=signals,
            reasons=reasons,
            feedback_scope_available=bool(scope),
            execution_id=str(getattr(request, "execution_id", "") or ""),
            provider=provider,
            capabilities=tuple(sorted(capabilities)),
            unavailable=unavailable,
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
