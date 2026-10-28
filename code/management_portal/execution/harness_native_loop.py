from __future__ import annotations

import json
import threading
import time
from collections import Counter, deque
from dataclasses import replace
from typing import Any, Callable

from .harness import (
    AgentLoopProviderEventProcessor,
    HarnessFeatureOutcome,
    capabilities_of,
)
from .harness_capabilities import (
    ACTIVE_TURN_INPUT,
    NATIVE_TOOL_EVENTS,
    TURN_REASONING_EFFORT,
    requires,
)
from .harness_history import bounded_int
from .harness_native_items import (
    approximate_inline_media_bytes,
    approximate_output_bytes,
    item_identity,
    native_tool_item,
    read_profile,
    runs_tests,
    semantic_read_keys,
    tool_failed,
)
from .control_messages import AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG


NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID = "native_tool_loop_governor"
DEFAULT_NATIVE_CHECKPOINT_INTERVAL = 80
DEFAULT_NATIVE_MAX_CHECKPOINTS = 3
DEFAULT_DUPLICATE_READ_THRESHOLD = 8
DEFAULT_NATIVE_LARGE_OUTPUT_BYTES = 64 * 1024
DEFAULT_GRANULARITY_STRATEGY = "auto"
DEFAULT_FRAGMENTED_READ_BATCH_THRESHOLD = 3
DEFAULT_DISCOVERY_TARGET_THRESHOLD = 3
DEFAULT_CONFIRM_SINGLETON_READS = 1
DEFAULT_MAX_GRANULARITY_STEERS = 2
DEFAULT_FAILURE_THRESHOLD = 3
DEFAULT_POST_STEER_READ_BATCHES = 4
DEFAULT_CONTEXT_PRESSURE_THRESHOLD_PERCENT = 82
DEFAULT_NATIVE_MEDIA_PRESSURE_BYTES = 512 * 1024
DEFAULT_PROVIDER_LATENCY_THRESHOLD_SECONDS = 600
_ORIGINAL_TASK_CONTINUATION = (
    "继续推进当前任务，原有目标与完成条件保持不变。依据已确认的进度和证据完成"
    "尚未完成的工作。不要回复、确认或复述本检查点，最终只向用户交付原任务结果。"
)
_AUTO_CANDIDATE_MAX_BATCH_AGE = 4
_CRITICAL_SAFETY_REASONS = frozenset({
    "context_pressure",
    "native_inline_media_pressure",
    "provider_latency_pressure",
})

GRANULARITY_STRATEGIES = frozenset({
    "off",
    "auto",
    "count",
    "fragmented",
    "discovery",
    "adaptive",
})

_SAFETY_GUIDANCE = {
    "duplicate_read_pressure": (
        "把已经读取的目标和结论保留在当前证据账本中；仅在内容可能变化或缺少"
        "具体事实时重读。"
    ),
    "large_output_pressure": (
        "先搜索定位，再读取命中附近的小切片；避免再次把完整日志或大文件送回"
        "当前上下文。"
    ),
    "native_inline_media_pressure": (
        "检测到新的大图工具结果。优先复用文件路径、已取得的视觉结论或压缩图，"
        "避免再次把原图写入历史。"
    ),
    "provider_latency_pressure": (
        "Provider 长时间没有产生进展事件。依据当前已确认的进度推进尚未完成的工作。"
    ),
    "failure_loop_pressure": (
        "停止重复同一种失败调用；先归纳共同错误，检查一次前置条件，再改用不同的"
        "命令、参数或实现路径。"
    ),
    "context_pressure": (
        "当前上下文已接近容量上限。保留当前目标、约束、关键证据和未完成项，"
        "复用已取得的结论，按需读取必要资料。"
    ),
    "tool_call_checkpoint": (
        "先核对当前目标、已完成项和仍缺少的证据，再决定是否还需要新的工具调用。"
    ),
}
_DEFAULT_SAFETY_GUIDANCE = "复用已经取得的证据，继续完成当前用户的原任务。"

_STEER_EVIDENCE = {
    "auto_confirmed_fragmentation": (
        "只读发现已返回至少 {discovery_targets} 个候选目标，随后仍连续执行了 "
        "{confirm_singleton_reads} 个单项只读批次。"
    ),
    "auto_serial_fragmentation": (
        "未经过批量发现也已连续执行 {consecutive_singletons} 个单项只读批次。"
    ),
    "auto_persistent_fragmentation": (
        "第一次提示后的观测窗口仍有 {window_singletons}/"
        "{post_steer_read_batches} 个单项只读批次。"
    ),
    "read_discovery_opportunity": (
        "最近一次只读发现已返回至少 {discovery_targets} 个候选目标。"
    ),
    "fragmented_read_sequence": (
        "已连续执行 {consecutive_singletons} 个单项只读批次。"
    ),
}
_DEFAULT_STEER_EVIDENCE = "本轮已经执行 {read_operations} 个只读操作。"


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _granularity_strategy(value: object) -> str:
    clean = str(value or "").strip().casefold()
    return clean if clean in GRANULARITY_STRATEGIES else DEFAULT_GRANULARITY_STRATEGY


def _ledger_json(ledger: dict[str, Any]) -> str:
    return json.dumps(ledger, ensure_ascii=False, separators=(',', ':'))


class RuntimeControlQueue:
    """The reminders a native turn still receives, within their budgets.

    Safety reminders share ``max_checkpoints`` and keep a reserve for the
    critical pressures; read-batch advice has its own budget.  Where the
    Provider takes no input during a turn, a reminder is withheld instead.
    """

    def __init__(
        self,
        *,
        max_checkpoints: int,
        max_granularity_steers: int,
        steering_available: bool,
        ledger: Callable[[], dict[str, Any]],
    ) -> None:
        self.max_checkpoints = max_checkpoints
        self.max_granularity_steers = max_granularity_steers
        self.steering_available = bool(steering_available)
        self._ledger = ledger
        self._pending: deque[dict[str, Any]] = deque()
        # A final answer (or the turn's end) closes the turn to reminders.
        self.turn_answered = False
        self.withheld = 0
        self.withheld_reasons: set[str] = set()
        self.safety_reasons: set[str] = set()
        self.controls_enqueued = 0
        self.safety_controls_enqueued = 0
        self.regular_safety_controls_enqueued = 0
        self.critical_safety_controls_enqueued = 0
        self.granularity_steers_enqueued = 0
        self.controls_requested = 0
        self.controls_applied = 0
        self.controls_failed = 0
        self.control_errors: Counter[str] = Counter()
        self.suppressed = 0
        self.action_outcomes: list[dict[str, str]] = []

    @property
    def granularity_budget_left(self) -> bool:
        return self.granularity_steers_enqueued < self.max_granularity_steers

    def discard_pending(self) -> None:
        self.suppressed += len(self._pending)
        self._pending.clear()

    def pop(self) -> dict[str, Any] | None:
        if not self._pending:
            return None
        self.controls_requested += 1
        return self._pending.popleft()

    def queue_safety(self, reason: str) -> None:
        if self.turn_answered:
            self.suppressed += 1
            return
        if not self.steering_available:
            self._withhold(reason)
            return
        if reason in self.safety_reasons:
            self.suppressed += 1
            return
        critical = reason in _CRITICAL_SAFETY_REASONS
        critical_reserve = min(
            len(_CRITICAL_SAFETY_REASONS),
            max(0, self.max_checkpoints - 1),
        )
        regular_limit = max(1, self.max_checkpoints - critical_reserve)
        if (
            self.safety_controls_enqueued >= self.max_checkpoints
            or (
                not critical
                and self.regular_safety_controls_enqueued >= regular_limit
            )
        ):
            self.suppressed += 1
            return
        self.safety_reasons.add(reason)
        self.safety_controls_enqueued += 1
        if critical:
            self.critical_safety_controls_enqueued += 1
        else:
            self.regular_safety_controls_enqueued += 1
        self.controls_enqueued += 1
        ledger = self._ledger()
        guidance = _SAFETY_GUIDANCE.get(reason, _DEFAULT_SAFETY_GUIDANCE)
        text = f"""\
<{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG} reason="{reason}">
当前任务的运行优化提示：{guidance}
只运行与已修改责任方直接相关的验证；不要仅为再次确认而重复已经通过的等价测试。
{_ORIGINAL_TASK_CONTINUATION}
观测账本：{_ledger_json(ledger)}
</{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG}>"""
        self._pending.append({
            "control_id": (
                f"native-checkpoint-{self.safety_controls_enqueued}"
            ),
            "reason": reason,
            "input": [{"type": "text", "text": text}],
            "metadata": {"kind": "safety", "ledger": ledger},
        })

    def queue_granularity(
        self,
        reason: str,
        *,
        evidence: str,
        strategy: str,
    ) -> None:
        if not self.granularity_budget_left:
            return
        if not self.steering_available:
            self._withhold(reason)
            return
        self.granularity_steers_enqueued += 1
        self.controls_enqueued += 1
        ledger = self._ledger()
        text = f"""\
<{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG} reason="{reason}">
当前任务的工具调用优化提示：{evidence}
把当前已经知道且互不依赖的读取、搜索和检查合并为一个并行批次；
只有确实依赖前序结果的步骤才继续串行。不要并行写入、破坏性操作或有先后依赖的动作，
也不要重新读取未变化的证据。任务很小或没有可安全合并的调用时，直接继续即可。
{_ORIGINAL_TASK_CONTINUATION}
观测账本：{_ledger_json(ledger)}
</{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG}>"""
        self._pending.append({
            "control_id": (
                f"granularity-steer-{self.granularity_steers_enqueued}"
            ),
            "reason": reason,
            "input": [{"type": "text", "text": text}],
            "metadata": {
                "kind": "tool_granularity",
                "strategy": strategy,
                "ledger": ledger,
            },
        })

    def record_result(
        self,
        control: dict[str, Any],
        applied: bool,
        error: str,
    ) -> dict[str, Any]:
        """Count a delivered or failed reminder; return its checkpoint event."""

        if applied:
            self.controls_applied += 1
        else:
            self.controls_failed += 1
            self.control_errors[str(error or "unknown")] += 1
        action = str(control.get("action") or "steer").strip()[:64]
        reason = str(control.get("reason") or "unspecified").strip()[:64]
        self.action_outcomes.append({
            "action": f"native_runtime_control:{action}:{reason}",
            "status": "applied" if applied else "failed",
            "reason": "" if applied else str(error or "unknown")[:128],
        })
        return {
            "feature_id": NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID,
            "control_id": str(control.get("control_id") or ""),
            "reason": str(control.get("reason") or ""),
            "applied": bool(applied),
            "error": str(error or ""),
            "action": str(control.get("action") or "steer"),
        }

    def _withhold(self, reason: str) -> None:
        if reason not in self.withheld_reasons:
            self.withheld_reasons.add(reason)
            self.withheld += 1


class ReadGranularityTracker:
    """The shape of a turn's read batches, and when to advise batching them.

    ``auto`` arms a candidate when a read-only discovery returns several
    targets and steers once single reads confirm it; after a delivered steer
    it watches a window of read batches for persistent fragmentation.  The
    other strategies steer on one fixed condition.
    """

    def __init__(
        self,
        *,
        strategy: str,
        fragmented_read_batch_threshold: int,
        discovery_target_threshold: int,
        confirm_singleton_reads: int,
        post_steer_read_batches: int,
        controls: RuntimeControlQueue,
    ) -> None:
        self.strategy = strategy
        self.fragmented_read_batch_threshold = fragmented_read_batch_threshold
        self.discovery_target_threshold = discovery_target_threshold
        self.confirm_singleton_reads = confirm_singleton_reads
        self.post_steer_read_batches = post_steer_read_batches
        self._controls = controls
        self.read_batches_observed = 0
        self.singleton_read_batches_observed = 0
        self.batched_read_batches_observed = 0
        self.max_read_batch_width = 0
        self.read_operations_observed = 0
        self.consecutive_singleton_read_batches = 0
        self.max_consecutive_singleton_read_batches = 0
        self.discovery_opportunities_observed = 0
        self.max_discovery_targets_observed = 0
        self.auto_discovery_candidates_armed = 0
        self.auto_singleton_followups_observed = 0
        self.auto_batched_followups_observed = 0
        self.auto_candidates_cancelled = 0
        self.auto_confirmed_steers = 0
        self.auto_post_steer_read_batches_observed = 0
        self.auto_post_steer_batched_read_batches = 0
        self.auto_post_steer_singleton_read_batches = 0
        self.auto_post_steer_max_read_batch_width = 0
        self.auto_persistent_fragmentation_steers = 0
        self.pending_discovery_targets = 0
        self._pending_singleton_reads = 0
        self._candidate_batch_age = 0
        self.awaiting_post_steer_read_batch = False
        self.post_steer_window_remaining = 0
        self._post_steer_window_singletons = 0

    def observe_batch(self, items: tuple[dict[str, Any], ...]) -> None:
        profiles = tuple(
            read_profile(
                item,
                count_discovery_targets=(
                    self.strategy in {"auto", "discovery", "adaptive"}
                ),
            )
            for item in items
        )
        if not all(bool(profile["read_only"]) for profile in profiles):
            self._end_read_sequence()
            return
        read_operations = sum(
            int(profile["read_operations"]) for profile in profiles
        )
        if read_operations <= 0:
            self._end_read_sequence()
            return
        self._count_read_batch(read_operations)
        discovery_targets = max(
            (
                int(profile["discovery_targets"])
                for profile in profiles
                if bool(profile["discovery"])
            ),
            default=0,
        )
        self.max_discovery_targets_observed = max(
            self.max_discovery_targets_observed,
            discovery_targets,
        )
        if discovery_targets >= self.discovery_target_threshold:
            self.discovery_opportunities_observed += 1

        self._record_post_steer_read_batch(read_operations)
        if self.strategy == "auto":
            self._observe_auto_read_batch(
                read_operations=read_operations,
                discovery_targets=discovery_targets,
            )
            return
        self._maybe_steer(discovery_targets=discovery_targets)

    def request_steer(self, reason: str, *, discovery_targets: int) -> None:
        evidence = _STEER_EVIDENCE.get(reason, _DEFAULT_STEER_EVIDENCE).format(
            discovery_targets=discovery_targets,
            confirm_singleton_reads=self.confirm_singleton_reads,
            consecutive_singletons=self.consecutive_singleton_read_batches,
            window_singletons=self._post_steer_window_singletons,
            post_steer_read_batches=self.post_steer_read_batches,
            read_operations=self.read_operations_observed,
        )
        self._controls.queue_granularity(
            reason,
            evidence=evidence,
            strategy=self.strategy,
        )

    def start_post_steer_window(self) -> None:
        self.awaiting_post_steer_read_batch = True
        self.post_steer_window_remaining = self.post_steer_read_batches
        self._post_steer_window_singletons = 0

    def _count_read_batch(self, read_operations: int) -> None:
        self.read_batches_observed += 1
        self.read_operations_observed += read_operations
        self.max_read_batch_width = max(
            self.max_read_batch_width,
            read_operations,
        )
        if read_operations == 1:
            self.singleton_read_batches_observed += 1
            self.consecutive_singleton_read_batches += 1
            self.max_consecutive_singleton_read_batches = max(
                self.max_consecutive_singleton_read_batches,
                self.consecutive_singleton_read_batches,
            )
        else:
            self.batched_read_batches_observed += 1
            self.consecutive_singleton_read_batches = 0

    def _end_read_sequence(self) -> None:
        self.consecutive_singleton_read_batches = 0
        if self.strategy != "auto" or not self.pending_discovery_targets:
            return
        self._candidate_batch_age += 1
        if self._candidate_batch_age >= _AUTO_CANDIDATE_MAX_BATCH_AGE:
            self.auto_candidates_cancelled += 1
            self._clear_auto_candidate()

    def _observe_auto_read_batch(
        self,
        *,
        read_operations: int,
        discovery_targets: int,
    ) -> None:
        if discovery_targets >= self.discovery_target_threshold:
            if not self._controls.granularity_budget_left:
                self._clear_auto_candidate()
                return
            self.auto_discovery_candidates_armed += 1
            self.pending_discovery_targets = discovery_targets
            self._pending_singleton_reads = 0
            self._candidate_batch_age = 0
            return

        if not self.pending_discovery_targets:
            if (
                read_operations == 1
                and self._controls.granularity_steers_enqueued == 0
                and self.consecutive_singleton_read_batches
                >= self.fragmented_read_batch_threshold
            ):
                self.auto_confirmed_steers += 1
                self.request_steer(
                    "auto_serial_fragmentation",
                    discovery_targets=0,
                )
            return
        if read_operations > 1:
            self.auto_batched_followups_observed += 1
            self._clear_auto_candidate()
            return

        self.auto_singleton_followups_observed += 1
        self._pending_singleton_reads += 1
        if self._pending_singleton_reads < self.confirm_singleton_reads:
            return

        discovery_targets = self.pending_discovery_targets
        self.auto_confirmed_steers += 1
        self._clear_auto_candidate()
        self.request_steer(
            "auto_confirmed_fragmentation",
            discovery_targets=discovery_targets,
        )

    def _clear_auto_candidate(self) -> None:
        self.pending_discovery_targets = 0
        self._pending_singleton_reads = 0
        self._candidate_batch_age = 0

    def _record_post_steer_read_batch(self, read_operations: int) -> None:
        if self.post_steer_window_remaining <= 0:
            return
        self.auto_post_steer_read_batches_observed += 1
        self.auto_post_steer_max_read_batch_width = max(
            self.auto_post_steer_max_read_batch_width,
            read_operations,
        )
        if read_operations > 1:
            self.auto_post_steer_batched_read_batches += 1
        else:
            self.auto_post_steer_singleton_read_batches += 1
            self._post_steer_window_singletons += 1
        self.post_steer_window_remaining -= 1
        if self.post_steer_window_remaining > 0:
            return
        self.awaiting_post_steer_read_batch = False
        persistent_threshold = max(
            2,
            (self.post_steer_read_batches * 3 + 3) // 4,
        )
        if (
            self._post_steer_window_singletons >= persistent_threshold
            and self._controls.granularity_budget_left
        ):
            self.auto_persistent_fragmentation_steers += 1
            self.request_steer(
                "auto_persistent_fragmentation",
                discovery_targets=0,
            )

    def _maybe_steer(self, *, discovery_targets: int) -> None:
        if self.strategy == "off" or not self._controls.granularity_budget_left:
            return
        reason = ""
        if (
            self.strategy in {"discovery", "adaptive"}
            and discovery_targets >= self.discovery_target_threshold
        ):
            reason = "read_discovery_opportunity"
        elif (
            self.strategy in {"fragmented", "adaptive"}
            and self.consecutive_singleton_read_batches
            >= self.fragmented_read_batch_threshold
        ):
            reason = "fragmented_read_sequence"
        elif (
            self.strategy == "count"
            and self.read_operations_observed
            >= self.fragmented_read_batch_threshold
        ):
            reason = "read_call_count"
        if not reason:
            return
        self.request_steer(reason, discovery_targets=discovery_targets)


class _ProviderSilenceWatch:
    """How long the Provider has gone without reporting progress."""

    def __init__(self, monotonic: Callable[[], float]) -> None:
        self._monotonic = monotonic
        self.last_event_at: float | None = None
        # Start observing provider latency at turn submission, rather than
        # waiting for the first tool batch.  A provider can stall while
        # ingesting a long history before it emits any event at all.
        self.started_at: float | None = monotonic()
        self.max_silence_seconds = 0.0
        self.checks = 0

    def note_event(self, now: float) -> None:
        if self.last_event_at is not None:
            self.max_silence_seconds = max(
                self.max_silence_seconds,
                max(0.0, now - self.last_event_at),
            )
        self.last_event_at = now

    def restart(self, now: float) -> None:
        self.started_at = now

    def current_silence_seconds(self) -> float:
        progress_at = self.last_event_at
        if progress_at is None:
            progress_at = self.started_at
        if progress_at is None:
            return 0.0
        return max(0.0, self._monotonic() - progress_at)

    def observe_silence(self) -> float:
        silence = self.current_silence_seconds()
        self.max_silence_seconds = max(self.max_silence_seconds, silence)
        return silence


class NativeToolLoopRuntime:
    """One native turn's tool-loop ledger and the reminders it earns.

    The runtime is the Provider event processor of the governor feature: it
    counts what the Provider's native tool loop does, groups tool items into
    batches, and queues bounded reminders when a pressure threshold is met.
    """

    def __init__(
        self,
        *,
        checkpoint_interval: int,
        max_checkpoints: int,
        duplicate_read_threshold: int,
        large_output_bytes: int,
        granularity_strategy: str,
        fragmented_read_batch_threshold: int,
        discovery_target_threshold: int,
        confirm_singleton_reads: int,
        max_granularity_steers: int,
        emit: Callable[[dict[str, Any]], None],
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
        post_steer_read_batches: int = DEFAULT_POST_STEER_READ_BATCHES,
        context_pressure_threshold_percent: int = (
            DEFAULT_CONTEXT_PRESSURE_THRESHOLD_PERCENT
        ),
        media_pressure_bytes: int = DEFAULT_NATIVE_MEDIA_PRESSURE_BYTES,
        provider_latency_threshold_seconds: int = (
            DEFAULT_PROVIDER_LATENCY_THRESHOLD_SECONDS
        ),
        adaptive_turn_runtime: Any = None,
        planned_limits: dict[str, int] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        steering_available: bool = True,
    ) -> None:
        self._lock = threading.RLock()
        self._emit = emit
        self._monotonic = monotonic
        # Whether the Provider takes input while the turn runs.  Without it
        # the runtime still keeps the ledger; reminders it would have sent
        # are counted as withheld, and the policy reads them after the turn.
        self.controls = RuntimeControlQueue(
            max_checkpoints=max_checkpoints,
            max_granularity_steers=max_granularity_steers,
            steering_available=steering_available,
            ledger=self._ledger,
        )
        self.granularity = ReadGranularityTracker(
            strategy=granularity_strategy,
            fragmented_read_batch_threshold=fragmented_read_batch_threshold,
            discovery_target_threshold=discovery_target_threshold,
            confirm_singleton_reads=confirm_singleton_reads,
            post_steer_read_batches=post_steer_read_batches,
            controls=self.controls,
        )
        self._silence = _ProviderSilenceWatch(monotonic)
        self.checkpoint_interval = checkpoint_interval
        self.duplicate_read_threshold = duplicate_read_threshold
        self.large_output_bytes = large_output_bytes
        self.failure_threshold = failure_threshold
        self.context_pressure_threshold_percent = (
            context_pressure_threshold_percent
        )
        self.media_pressure_bytes = media_pressure_bytes
        self.provider_latency_threshold_seconds = (
            provider_latency_threshold_seconds
        )
        self._adaptive_turn_runtime = adaptive_turn_runtime
        self._planned_limits: dict[str, int] = {
            "cumulative_output_bytes": 256 * 1024,
            **(planned_limits or {}),
        }
        self._read_keys: Counter[str] = Counter()
        self._item_types: Counter[str] = Counter()
        self._active_tool_ids: set[str] = set()
        self._active_batch_items: list[dict[str, Any]] = []
        self._anonymous_tool_sequence = 0
        self.next_tool_checkpoint = checkpoint_interval
        self.next_duplicate_checkpoint = duplicate_read_threshold
        self.provider_events_observed = 0
        self.native_tool_calls_observed = 0
        self.command_calls_observed = 0
        self.test_commands_observed = 0
        self.file_change_items_observed = 0
        self.context_compactions_observed = 0
        self.failed_tool_calls_observed = 0
        self.native_output_bytes_observed = 0
        self.large_native_outputs_observed = 0
        self.native_inline_media_bytes_observed = 0
        self.native_tool_inline_media_bytes_observed = 0
        self.native_inline_media_items_observed = 0
        self.current_context_input_tokens = 0
        self.model_context_window = 0
        self.max_context_pressure_percent = 0.0
        self.duplicate_read_calls_observed = 0
        self.tool_batches_observed = 0
        self.context_transition_pending = False

    def process_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        with self._lock:
            now = self._monotonic()
            self._silence.note_event(now)
            self.provider_events_observed += 1
            params = event.get("params")
            handler = _EVENT_HANDLERS.get(str(event.get("method") or ""))
            if handler is not None:
                handler(self, params if isinstance(params, dict) else {}, now)

    def next_control(self) -> dict[str, Any] | None:
        with self._lock:
            if self.controls.turn_answered:
                return None
            self._observe_runtime_latency()
            return self.controls.pop()

    def record_control_result(
        self,
        control: dict[str, Any],
        applied: bool,
        error: str,
    ) -> None:
        with self._lock:
            metadata = control.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            details = self.controls.record_result(control, applied, error)
            if (
                applied
                and metadata.get("kind") == "tool_granularity"
                and self.granularity.strategy == "auto"
            ):
                self.granularity.start_post_steer_window()
        try:
            self._emit(details)
        except Exception:
            pass

    def _on_token_usage(self, params: dict[str, Any], now: float) -> None:
        payload = params.get("tokenUsage")
        payload = payload if isinstance(payload, dict) else {}
        current = payload.get("context") or payload.get("last")
        current = current if isinstance(current, dict) else {}
        input_tokens = _nonnegative_int(current.get("inputTokens"))
        context_window = _nonnegative_int(payload.get("modelContextWindow"))
        if input_tokens:
            self.current_context_input_tokens = input_tokens
        if context_window:
            self.model_context_window = context_window
        if not self.current_context_input_tokens or not self.model_context_window:
            return
        pressure = (
            100.0
            * self.current_context_input_tokens
            / self.model_context_window
        )
        self.max_context_pressure_percent = max(
            self.max_context_pressure_percent,
            pressure,
        )
        if pressure >= self.context_pressure_threshold_percent:
            if self._active_tool_ids or self.tool_batches_observed == 0:
                self.context_transition_pending = True
            else:
                self.controls.queue_safety("context_pressure")
        self._apply_adaptive_controls()

    def _on_turn_started(self, params: dict[str, Any], now: float) -> None:
        self.controls.turn_answered = False
        self.controls.discard_pending()
        self._active_tool_ids.clear()
        self._silence.restart(now)

    def _on_turn_completed(self, params: dict[str, Any], now: float) -> None:
        self.controls.turn_answered = True
        self.controls.discard_pending()

    def _on_item_started(self, params: dict[str, Any], now: float) -> None:
        item = params.get("item")
        if isinstance(item, dict) and native_tool_item(item):
            self._start_tool_item(item)

    def _on_item_completed(self, params: dict[str, Any], now: float) -> None:
        item = params.get("item")
        if not isinstance(item, dict):
            return
        item_type = str(item.get("type") or "unknown")
        if (
            item_type == "agentMessage"
            and str(item.get("phase") or "").strip().casefold()
            == "final_answer"
        ):
            self._on_turn_completed(params, now)
            return
        self._item_types[item_type] += 1
        native = native_tool_item(item)
        if native:
            self._count_tool_call(item)
        self._count_item_kind(item_type, item)
        output_bytes = approximate_output_bytes(item)
        self.native_output_bytes_observed += output_bytes
        is_large_output = output_bytes >= self.large_output_bytes
        if is_large_output:
            self.large_native_outputs_observed += 1
        media_bytes = approximate_inline_media_bytes(item)
        if media_bytes:
            self._count_inline_media(media_bytes, governable=(
                # imageView is an observation of media already owned by the
                # Provider thread. Interrupting after it has been decoded is
                # both too late to reduce that cost and can discard useful
                # progress. Only newly returned tool media is governable at
                # this boundary; input/history media remains telemetry for
                # latency feedback and future-turn policy selection.
                item_type != "imageView" and native
            ))
        for key in semantic_read_keys(item):
            self._read_keys[key] += 1
            if self._read_keys[key] > 1:
                self.duplicate_read_calls_observed += 1
        if native:
            self._complete_tool_item(item)
        self._check_item_thresholds(
            large_output=is_large_output,
            media_bytes=media_bytes,
        )
        self._apply_adaptive_controls()

    def _count_tool_call(self, item: dict[str, Any]) -> None:
        self.native_tool_calls_observed += 1
        if tool_failed(item):
            self.failed_tool_calls_observed += 1
            if self.failed_tool_calls_observed >= self.failure_threshold:
                self.controls.queue_safety("failure_loop_pressure")

    def _count_item_kind(self, item_type: str, item: dict[str, Any]) -> None:
        if item_type == "commandExecution":
            self.command_calls_observed += 1
            if runs_tests(item):
                self.test_commands_observed += 1
        elif item_type == "fileChange":
            self.file_change_items_observed += 1
        elif item_type == "contextCompaction":
            self.context_compactions_observed += 1

    def _count_inline_media(self, media_bytes: int, *, governable: bool) -> None:
        self.native_inline_media_bytes_observed += media_bytes
        self.native_inline_media_items_observed += 1
        if governable:
            self.native_tool_inline_media_bytes_observed += media_bytes
        if (
            governable
            and self._ungoverned_inline_media_bytes()
            >= self.media_pressure_bytes
        ):
            self.controls.queue_safety("native_inline_media_pressure")

    def _check_item_thresholds(self, *, large_output: bool, media_bytes: int) -> None:
        if self.native_tool_calls_observed >= self.next_tool_checkpoint:
            self.next_tool_checkpoint = 2**63 - 1
            self.controls.queue_safety("tool_call_checkpoint")
        if (
            self.duplicate_read_calls_observed
            >= self.next_duplicate_checkpoint
        ):
            self.next_duplicate_checkpoint = 2**63 - 1
            self.controls.queue_safety("duplicate_read_pressure")
        if (
            large_output
            and not media_bytes
            and self.large_native_outputs_observed == 1
        ):
            self.controls.queue_safety("large_output_pressure")

    def _apply_adaptive_controls(self) -> None:
        """Observe current activity and evaluate the plan's fixed limits."""
        observe = getattr(self._adaptive_turn_runtime, "observe_native_snapshot", None)
        if callable(observe):
            try:
                observe(self._ledger())
            except Exception:
                pass
        cumulative_output = self._planned_limits.get(
            "cumulative_output_bytes",
            0,
        )
        if (
            cumulative_output
            and self.native_output_bytes_observed >= cumulative_output
        ):
            self.controls.queue_safety("large_output_pressure")

    def _start_tool_item(self, item: dict[str, Any]) -> None:
        item_id = item_identity(item)
        if not item_id:
            self._anonymous_tool_sequence += 1
            item_id = f"anonymous-{self._anonymous_tool_sequence}"
        if not self._active_tool_ids:
            self._active_batch_items = []
        self._active_tool_ids.add(item_id)

    def _complete_tool_item(self, item: dict[str, Any]) -> None:
        if not self._active_tool_ids:
            self._finalize_tool_batch((item,))
            return
        item_id = item_identity(item)
        if item_id:
            self._active_tool_ids.discard(item_id)
        elif len(self._active_tool_ids) == 1:
            self._active_tool_ids.pop()
        self._active_batch_items.append(item)
        if self._active_tool_ids:
            return
        completed = tuple(self._active_batch_items)
        self._active_batch_items = []
        self._finalize_tool_batch(completed)

    def _finalize_tool_batch(
        self,
        items: tuple[dict[str, Any], ...],
    ) -> None:
        if not items:
            return
        self.tool_batches_observed += 1
        self._silence.restart(self._monotonic())
        if self.context_transition_pending and not self._active_tool_ids:
            self.context_transition_pending = False
            self.controls.queue_safety("context_pressure")
        self.granularity.observe_batch(items)

    def _observe_runtime_latency(self) -> None:
        self._silence.checks += 1
        if (
            self._silence.started_at is None
            or self._active_tool_ids
            or "provider_latency_pressure" in self.controls.safety_reasons
        ):
            return
        if self._silence.observe_silence() >= self.provider_latency_threshold_seconds:
            self.controls.queue_safety("provider_latency_pressure")

    def _ungoverned_inline_media_bytes(self) -> int:
        return self.native_tool_inline_media_bytes_observed

    def _ledger(self) -> dict[str, Any]:
        granularity = self.granularity
        repeated = [
            {"key": key, "count": count}
            for key, count in self._read_keys.most_common(5)
            if count > 1
        ]
        return {
            "native_tool_calls": self.native_tool_calls_observed,
            "commands": self.command_calls_observed,
            "tool_batches": self.tool_batches_observed,
            "read_batches": granularity.read_batches_observed,
            "singleton_read_batches": (
                granularity.singleton_read_batches_observed
            ),
            "batched_read_batches": granularity.batched_read_batches_observed,
            "max_read_batch_width": granularity.max_read_batch_width,
            "test_commands": self.test_commands_observed,
            "file_change_items": self.file_change_items_observed,
            "failed_tool_calls": self.failed_tool_calls_observed,
            "context_compactions": self.context_compactions_observed,
            "duplicate_read_calls": self.duplicate_read_calls_observed,
            "large_outputs": self.large_native_outputs_observed,
            "native_output_bytes": self.native_output_bytes_observed,
            "inline_media_bytes": self.native_inline_media_bytes_observed,
            "ungoverned_inline_media_bytes": (
                self._ungoverned_inline_media_bytes()
            ),
            "provider_silence_seconds": round(
                self._silence.current_silence_seconds(),
                3,
            ),
            "context_pressure_percent": round(
                self.max_context_pressure_percent,
                2,
            ),
            "repeated_reads": repeated,
        }

    def report(self) -> dict[str, Any]:
        with self._lock:
            controls = self.controls
            granularity = self.granularity
            if controls.controls_applied:
                effectiveness_state = "control_applied"
            elif controls.controls_enqueued:
                effectiveness_state = "control_triggered"
            elif self.provider_events_observed:
                effectiveness_state = "observing"
            else:
                effectiveness_state = "idle"
            return {
                "effectiveness_state": effectiveness_state,
                "runtime_control_available": controls.steering_available,
                "runtime_controls_withheld": controls.withheld,
                "runtime_control_reasons_withheld": sorted(
                    controls.withheld_reasons
                ),
                "adaptive_turn_policy_attached": bool(
                    self._adaptive_turn_runtime is not None
                ),
                "planned_turn_limits": dict(self._planned_limits),
                "granularity_strategy": granularity.strategy,
                "native_output_transform_available": False,
                "native_output_transform_reason": (
                    "app_server_output_transform_hook_unavailable"
                ),
                "provider_events_observed": self.provider_events_observed,
                "native_tool_calls_observed": self.native_tool_calls_observed,
                "command_calls_observed": self.command_calls_observed,
                "test_commands_observed": self.test_commands_observed,
                "file_change_items_observed": (
                    self.file_change_items_observed
                ),
                "context_compactions_observed": (
                    self.context_compactions_observed
                ),
                "failed_tool_calls_observed": (
                    self.failed_tool_calls_observed
                ),
                "native_output_bytes_estimated": (
                    self.native_output_bytes_observed
                ),
                "large_native_outputs_observed": (
                    self.large_native_outputs_observed
                ),
                "native_inline_media_bytes_estimated": (
                    self.native_inline_media_bytes_observed
                ),
                "native_inline_media_items_observed": (
                    self.native_inline_media_items_observed
                ),
                "native_tool_inline_media_bytes_estimated": (
                    self.native_tool_inline_media_bytes_observed
                ),
                "ungoverned_inline_media_bytes": (
                    self._ungoverned_inline_media_bytes()
                ),
                "provider_latency_threshold_seconds": (
                    self.provider_latency_threshold_seconds
                ),
                "provider_latency_checks": self._silence.checks,
                "max_provider_silence_seconds": round(
                    self._silence.max_silence_seconds,
                    3,
                ),
                "current_context_input_tokens": (
                    self.current_context_input_tokens
                ),
                "model_context_window": self.model_context_window,
                "max_context_pressure_percent": round(
                    self.max_context_pressure_percent,
                    2,
                ),
                "semantic_read_keys_observed": len(self._read_keys),
                "duplicate_read_calls_observed": (
                    self.duplicate_read_calls_observed
                ),
                "tool_batches_observed": self.tool_batches_observed,
                "read_batches_observed": granularity.read_batches_observed,
                "singleton_read_batches_observed": (
                    granularity.singleton_read_batches_observed
                ),
                "batched_read_batches_observed": (
                    granularity.batched_read_batches_observed
                ),
                "max_read_batch_width": granularity.max_read_batch_width,
                "read_operations_observed": (
                    granularity.read_operations_observed
                ),
                "max_consecutive_singleton_read_batches": (
                    granularity.max_consecutive_singleton_read_batches
                ),
                "discovery_opportunities_observed": (
                    granularity.discovery_opportunities_observed
                ),
                "max_discovery_targets_observed": (
                    granularity.max_discovery_targets_observed
                ),
                "auto_discovery_candidates_armed": (
                    granularity.auto_discovery_candidates_armed
                ),
                "auto_singleton_followups_observed": (
                    granularity.auto_singleton_followups_observed
                ),
                "auto_batched_followups_observed": (
                    granularity.auto_batched_followups_observed
                ),
                "auto_candidates_cancelled": (
                    granularity.auto_candidates_cancelled
                ),
                "auto_confirmed_steers": granularity.auto_confirmed_steers,
                "auto_candidate_pending": bool(
                    granularity.pending_discovery_targets
                ),
                "auto_post_steer_read_batches_observed": (
                    granularity.auto_post_steer_read_batches_observed
                ),
                "auto_post_steer_batched_read_batches": (
                    granularity.auto_post_steer_batched_read_batches
                ),
                "auto_post_steer_singleton_read_batches": (
                    granularity.auto_post_steer_singleton_read_batches
                ),
                "auto_post_steer_max_read_batch_width": (
                    granularity.auto_post_steer_max_read_batch_width
                ),
                "auto_post_steer_observation_pending": (
                    granularity.awaiting_post_steer_read_batch
                ),
                "auto_post_steer_window_remaining": (
                    granularity.post_steer_window_remaining
                ),
                "auto_persistent_fragmentation_steers": (
                    granularity.auto_persistent_fragmentation_steers
                ),
                "runtime_controls_enqueued": controls.controls_enqueued,
                "safety_controls_enqueued": (
                    controls.safety_controls_enqueued
                ),
                "regular_safety_controls_enqueued": (
                    controls.regular_safety_controls_enqueued
                ),
                "critical_safety_controls_enqueued": (
                    controls.critical_safety_controls_enqueued
                ),
                "granularity_steers_enqueued": (
                    controls.granularity_steers_enqueued
                ),
                "runtime_controls_requested": controls.controls_requested,
                "runtime_controls_applied": controls.controls_applied,
                "runtime_controls_failed": controls.controls_failed,
                "runtime_action_outcomes": [
                    dict(item) for item in controls.action_outcomes
                ],
                "runtime_controls_suppressed": controls.suppressed,
                "context_transition_pending": self.context_transition_pending,
                "runtime_control_reasons": sorted(controls.safety_reasons),
                "runtime_control_errors": dict(controls.control_errors),
                "item_type_counts": dict(self._item_types),
                "top_semantic_reads": [
                    {"key": key, "count": count}
                    for key, count in self._read_keys.most_common(10)
                ],
            }


# The Provider events the runtime reacts to; provider activity and any other
# event count only as progress.
_EVENT_HANDLERS: dict[str, Callable[[NativeToolLoopRuntime, dict[str, Any], float], None]] = {
    "thread/tokenUsage/updated": NativeToolLoopRuntime._on_token_usage,
    "turn/started": NativeToolLoopRuntime._on_turn_started,
    "turn/completed": NativeToolLoopRuntime._on_turn_completed,
    "item/started": NativeToolLoopRuntime._on_item_started,
    "item/completed": NativeToolLoopRuntime._on_item_completed,
}

# The governor's bounded parameters: name -> (default, minimum, maximum).
_RUNTIME_LIMITS: dict[str, tuple[int, int, int]] = {
    "checkpoint_interval": (DEFAULT_NATIVE_CHECKPOINT_INTERVAL, 20, 500),
    "max_checkpoints": (DEFAULT_NATIVE_MAX_CHECKPOINTS, 1, 8),
    "duplicate_read_threshold": (DEFAULT_DUPLICATE_READ_THRESHOLD, 2, 100),
    "large_output_bytes": (
        DEFAULT_NATIVE_LARGE_OUTPUT_BYTES, 16 * 1024, 16 * 1024 * 1024,
    ),
    "fragmented_read_batch_threshold": (
        DEFAULT_FRAGMENTED_READ_BATCH_THRESHOLD, 2, 20,
    ),
    "discovery_target_threshold": (DEFAULT_DISCOVERY_TARGET_THRESHOLD, 2, 100),
    "confirm_singleton_reads": (DEFAULT_CONFIRM_SINGLETON_READS, 1, 5),
    "max_granularity_steers": (DEFAULT_MAX_GRANULARITY_STEERS, 1, 3),
    "failure_threshold": (DEFAULT_FAILURE_THRESHOLD, 2, 10),
    "post_steer_read_batches": (DEFAULT_POST_STEER_READ_BATCHES, 2, 8),
    "context_pressure_threshold_percent": (
        DEFAULT_CONTEXT_PRESSURE_THRESHOLD_PERCENT, 70, 95,
    ),
    "media_pressure_bytes": (
        DEFAULT_NATIVE_MEDIA_PRESSURE_BYTES, 64 * 1024, 16 * 1024 * 1024,
    ),
    "provider_latency_threshold_seconds": (
        DEFAULT_PROVIDER_LATENCY_THRESHOLD_SECONDS, 5, 15 * 60,
    ),
}


def _runtime_limits(config: dict[str, Any]) -> dict[str, int]:
    return {
        name: bounded_int(
            config.get(name),
            default=default,
            minimum=minimum,
            maximum=maximum,
        )
        for name, (default, minimum, maximum) in _RUNTIME_LIMITS.items()
    }


def _with_reasoning_effort(
    invocation: Any,
    config: dict[str, Any],
    *,
    effort_adjustable: bool,
) -> tuple[Any, dict[str, Any]]:
    """Apply the plan's initial reasoning effort where it can change per turn."""

    request = getattr(invocation, "request", None)
    runtime_options = getattr(request, "runtime_options", None)
    runtime_options = (
        dict(runtime_options) if isinstance(runtime_options, dict) else {}
    )
    configured_effort = str(
        config.get("configured_reasoning_effort")
        or runtime_options.get("effort")
        or ""
    ).strip().casefold()
    effective_effort = str(
        (config.get("initial_reasoning_effort") if effort_adjustable else "")
        or configured_effort
    ).strip().casefold()
    adjusted = bool(
        effective_effort
        and effective_effort != configured_effort
        and request is not None
    )
    if adjusted:
        runtime_options["effort"] = effective_effort
        invocation = replace(
            invocation,
            request=replace(
                request,
                runtime_options=runtime_options,
            ),
        )
    return invocation, {
        "configured_reasoning_effort": configured_effort,
        "effective_reasoning_effort": effective_effort,
        "reasoning_effort_adjusted": adjusted,
    }


class NativeToolLoopGovernorFeature:
    """Observe the Provider's native tool loop and apply bounded runtime controls.

    Observation needs the Adapter's native tool events.  Reminders are sent
    only when the Provider also takes input during a turn; the reasoning
    effort is adjusted only when it can change between turns of a session.
    """

    feature_id = NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID
    requires = requires({NATIVE_TOOL_EVENTS})

    def apply(
        self,
        *,
        provider: Any,
        invocation: Any,
        context: Any,
        config: dict[str, Any],
        observer: Any = None,
    ) -> HarnessFeatureOutcome:
        del observer
        capabilities = capabilities_of(invocation, provider)
        steering_available = ACTIVE_TURN_INPUT in capabilities
        if getattr(invocation, "provider_event_processor", None) is not None:
            return HarnessFeatureOutcome(
                invocation=invocation,
                status="skipped",
                reason="provider_event_processor_already_attached",
            )
        invocation, effort = _with_reasoning_effort(
            invocation,
            config,
            effort_adjustable=TURN_REASONING_EFFORT in capabilities,
        )
        limits = _runtime_limits(config)
        granularity_strategy = _granularity_strategy(
            config.get("granularity_strategy")
        )
        runtime = NativeToolLoopRuntime(
            **limits,
            granularity_strategy=granularity_strategy,
            adaptive_turn_runtime=config.get("_adaptive_turn_runtime"),
            planned_limits={
                "cumulative_output_bytes": bounded_int(
                    config.get("cumulative_output_bytes"),
                    default=256 * 1024,
                    minimum=16 * 1024,
                    maximum=16 * 1024 * 1024,
                ),
            },
            steering_available=steering_available,
            monotonic=(
                config.get("_monotonic")
                if callable(config.get("_monotonic"))
                else time.monotonic
            ),
            emit=lambda details: context.emit(
                "agent_harness_runtime_checkpoint",
                details,
            ),
        )
        processor = AgentLoopProviderEventProcessor(
            feature_id=self.feature_id,
            process_event=runtime.process_event,
            next_control=runtime.next_control,
            record_control_result=runtime.record_control_result,
            report=runtime.report,
        )
        return HarnessFeatureOutcome(
            invocation=replace(
                invocation,
                provider_event_processor=processor,
            ),
            status="applied",
            reason="native_tool_loop_governor_attached",
            action_applied=False,
            metrics={
                "runtime_control_available": steering_available,
                "checkpoint_interval": limits["checkpoint_interval"],
                "max_checkpoints": limits["max_checkpoints"],
                "duplicate_read_threshold": limits["duplicate_read_threshold"],
                "large_output_bytes": limits["large_output_bytes"],
                "granularity_strategy": granularity_strategy,
                "fragmented_read_batch_threshold": (
                    limits["fragmented_read_batch_threshold"]
                ),
                "discovery_target_threshold": limits["discovery_target_threshold"],
                "confirm_singleton_reads": limits["confirm_singleton_reads"],
                "max_granularity_steers": limits["max_granularity_steers"],
                "failure_threshold": limits["failure_threshold"],
                "post_steer_read_batches": limits["post_steer_read_batches"],
                "context_pressure_threshold_percent": (
                    limits["context_pressure_threshold_percent"]
                ),
                **effort,
                "media_pressure_bytes": limits["media_pressure_bytes"],
                "provider_latency_threshold_seconds": (
                    limits["provider_latency_threshold_seconds"]
                ),
            },
        )
