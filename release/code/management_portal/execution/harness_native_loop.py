from __future__ import annotations

import json
import os
import re
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
from .harness_tool_semantics import inline_media_bytes, tool_call_reads
from .control_messages import AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG
from .native_tool_events import PROVIDER_ACTIVITY_EVENT


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
NATIVE_TOOL_ITEM_TYPES = frozenset({
    "commandExecution",
    "mcpToolCall",
    "dynamicToolCall",
    "webSearch",
    "imageView",
    "collabAgentToolCall",
})
_READ_COMMAND_ACTION_TYPES = frozenset({"read", "listFiles", "search"})

_TEST_COMMAND_RE = re.compile(
    r"(?:^|[;&|]\s*)(?:[^\s]+/)?(?:pytest|playwright\s+test|"
    r"npm\s+(?:run\s+)?test|pnpm\s+(?:run\s+)?test)(?:\s|$)",
    re.IGNORECASE,
)
_READ_ONLY_TOOL_RE = re.compile(
    r"(?:^|[_.-])(read|search|find|list|get|fetch|open)(?:$|[_.-])",
    re.IGNORECASE,
)
_PATH_CANDIDATE_RE = re.compile(
    r"(?<![\w.-])"
    r"((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]{1,12})"
    r"(?=[:\s\"'()\[\],]|$)"
)
_SHELL_READ_COMMAND_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:command\s+-v|pwd|ls|find|rg|grep|cat|head|tail|wc|stat|file|"
    r"readlink|sed\s+-n|git\s+(?:status|diff|show|log|ls-files))\b",
    re.IGNORECASE,
)
_SHELL_DISCOVERY_COMMAND_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:ls\b|find\b|rg\b[^;&|]*\s--files\b|"
    r"git\s+ls-files\b|(?:rg|grep)\b[^;&|]+)",
    re.IGNORECASE,
)
_SHELL_MUTATION_RE = re.compile(
    r"(?:^|&&|\|\||;|\||\bdo\s+)\s*"
    r"(?:rm|mv|cp|install|mkdir|rmdir|touch|truncate|tee|chmod|chown|"
    r"ln|patch|apply_patch|git\s+(?:add|commit|checkout|reset|restore|"
    r"clean|merge|rebase|cherry-pick)|sed\s+-[^;&|]*i)\b|"
    r"(?<![0-9])>(?![>&])",
    re.IGNORECASE,
)


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "")[:2_048].split()).casefold()


def _path_key(value: object, cwd: str) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    if not os.path.isabs(path) and cwd:
        path = os.path.join(cwd, path)
    return os.path.normpath(path)


def _command_text(item: dict[str, Any]) -> str:
    command = item.get("command")
    if isinstance(command, list):
        return str(command[-1] if command else "")
    return str(command or "")


def _command_actions(item: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    actions = item.get("commandActions")
    if not isinstance(actions, list):
        actions = item.get("parsed_cmd")
    return tuple(
        action for action in (actions if isinstance(actions, list) else ())
        if isinstance(action, dict)
    )


def _command_action_type(action: dict[str, Any]) -> str:
    clean = re.sub(
        r"[^a-z]",
        "",
        str(action.get("type") or "").casefold(),
    )
    return {
        "read": "read",
        "listfiles": "listFiles",
        "search": "search",
    }.get(clean, clean)


def _shell_read_matches(command: str) -> tuple[str, ...]:
    if (
        not command
        or _TEST_COMMAND_RE.search(command)
        or _SHELL_MUTATION_RE.search(command)
    ):
        return ()
    return tuple(
        match.group(0).strip()
        for match in _SHELL_READ_COMMAND_RE.finditer(command)
    )


def _shell_read_keys(item: dict[str, Any]) -> tuple[str, ...]:
    command = _command_text(item)
    matches = _shell_read_matches(command)
    normalized = _normalized_text(command)
    return tuple(
        f"command:shell-read:{normalized}:{index}:{_normalized_text(match)}"
        for index, match in enumerate(matches, start=1)
    )


def _bounded_value_key(value: object, *, budget: int = 2_048) -> str:
    if budget <= 0:
        return "…"
    if value is None or isinstance(value, (bool, int, float)):
        return str(value)
    if isinstance(value, str):
        return value[:budget]
    if isinstance(value, dict):
        parts: list[str] = []
        remaining = budget
        for key in sorted(value, key=str)[:32]:
            prefix = f"{str(key)[:80]}:"
            rendered = _bounded_value_key(
                value[key],
                budget=max(0, remaining - len(prefix)),
            )
            part = prefix + rendered
            parts.append(part)
            remaining -= len(part)
            if remaining <= 0:
                break
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        parts = []
        remaining = budget
        for item in value[:32]:
            rendered = _bounded_value_key(item, budget=remaining)
            parts.append(rendered)
            remaining -= len(rendered)
            if remaining <= 0:
                break
        return "[" + ",".join(parts) + "]"
    return str(value)[:budget]


def _approximate_output_bytes(item: dict[str, Any]) -> int:
    """Use bounded/shallow accounting on the shared app-server reader."""

    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return len(str(
            item.get("aggregatedOutput")
            or item.get("aggregated_output")
            or item.get("formatted_output")
            or item.get("stdout")
            or ""
        ))
    if item_type == "mcpToolCall":
        result = item.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                return sum(
                    len(str(part.get("text") or ""))
                    for part in content[:128]
                    if isinstance(part, dict)
                )
        return len(str(result or "")) if isinstance(result, str) else 0
    if item_type == "dynamicToolCall":
        content_items = item.get("contentItems")
        if isinstance(content_items, list):
            return sum(
                len(str(part.get("text") or part.get("imageUrl") or ""))
                for part in content_items[:128]
                if isinstance(part, dict)
            )
    return 0




def _approximate_inline_media_bytes(item: dict[str, Any]) -> int:
    """Estimate model-facing image bytes from payloads or ImageView paths."""

    item_type = str(item.get("type") or "")
    if item_type == "imageView":
        path = str(item.get("path") or "").strip()
        if not path or not os.path.isabs(path):
            return 0
        try:
            source_bytes = os.path.getsize(path)
        except OSError:
            return 0
        # view_image returns a base64 data URL.  Include the base64 expansion;
        # the small MIME prefix is immaterial for pressure thresholds.
        return 4 * ((max(0, source_bytes) + 2) // 3)
    if item_type == "dynamicToolCall":
        content = item.get("contentItems")
    elif item_type == "mcpToolCall":
        result = item.get("result")
        result = result if isinstance(result, dict) else {}
        content = result.get("content")
    else:
        content = None
    if not isinstance(content, list):
        return 0
    return inline_media_bytes(content[:128])


def _tool_failed(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().casefold()
    if status in {"failed", "declined", "error"}:
        return True
    exit_code = item.get("exitCode", item.get("exit_code"))
    try:
        if exit_code is not None and int(exit_code) != 0:
            return True
    except (TypeError, ValueError):
        pass
    return bool(item.get("error"))


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)


def _native_tool_item(item: dict[str, Any]) -> bool:
    return str(item.get("type") or "") in NATIVE_TOOL_ITEM_TYPES


def _item_identity(item: dict[str, Any]) -> str:
    return str(
        item.get("id")
        or item.get("callId")
        or item.get("call_id")
        or ""
    ).strip()


def _granularity_strategy(value: object) -> str:
    clean = str(value or "").strip().casefold()
    return clean if clean in GRANULARITY_STRATEGIES else DEFAULT_GRANULARITY_STRATEGY


def _output_text(item: dict[str, Any], *, limit: int = 64 * 1024) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "commandExecution":
        return str(
            item.get("aggregatedOutput")
            or item.get("aggregated_output")
            or item.get("formatted_output")
            or item.get("stdout")
            or ""
        )[:limit]
    if item_type == "mcpToolCall":
        result = item.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list):
                return "\n".join(
                    str(part.get("text") or "")
                    for part in content[:128]
                    if isinstance(part, dict)
                )[:limit]
        return str(result or "")[:limit] if isinstance(result, str) else ""
    if item_type == "dynamicToolCall":
        content_items = item.get("contentItems")
        if isinstance(content_items, list):
            return "\n".join(
                str(part.get("text") or "")
                for part in content_items[:128]
                if isinstance(part, dict)
            )[:limit]
    return ""


def _discovery_target_count(item: dict[str, Any]) -> int:
    return len(set(_PATH_CANDIDATE_RE.findall(_output_text(item))))


def _read_profile(
    item: dict[str, Any],
    *,
    count_discovery_targets: bool = True,
) -> dict[str, Any]:
    item_type = str(item.get("type") or "")
    keys = _semantic_read_keys(item)
    if not keys:
        return {
            "read_only": False,
            "read_operations": 0,
            "discovery": False,
            "discovery_targets": 0,
        }
    discovery = item_type in {"webSearch"}
    read_only = True
    if item_type == "commandExecution":
        actions = _command_actions(item)
        typed_actions = tuple(
            _command_action_type(action)
            for action in actions
        )
        if typed_actions and all(
            action_type in _READ_COMMAND_ACTION_TYPES
            for action_type in typed_actions
        ):
            read_only = all(
                action_type in _READ_COMMAND_ACTION_TYPES
                for action_type in typed_actions
            )
            discovery = any(
                action_type in {"listFiles", "search"}
                for action_type in typed_actions
            )
        else:
            shell_matches = _shell_read_matches(_command_text(item))
            read_only = bool(shell_matches)
            discovery = bool(
                read_only
                and _SHELL_DISCOVERY_COMMAND_RE.search(_command_text(item))
            )
    return {
        "read_only": read_only,
        "read_operations": len(keys) if read_only else 0,
        "discovery": discovery,
        "discovery_targets": (
            _discovery_target_count(item)
            if discovery and read_only and count_discovery_targets
            else 0
        ),
    }


def _semantic_read_keys(item: dict[str, Any]) -> tuple[str, ...]:
    item_type = str(item.get("type") or "")
    keys: list[str] = []
    if item_type == "commandExecution":
        cwd = str(item.get("cwd") or "")
        actions = _command_actions(item)
        for action in actions:
            action_type = _command_action_type(action)
            if action_type == "read":
                keys.append(
                    "command:read:"
                    f"{_path_key(action.get('path'), cwd)}"
                )
            elif action_type == "listFiles":
                keys.append(
                    "command:list:"
                    f"{_path_key(action.get('path'), cwd) or cwd}"
                )
            elif action_type == "search":
                keys.append(
                    "command:search:"
                    f"{_path_key(action.get('path'), cwd) or cwd}:"
                    f"{_normalized_text(action.get('query'))}"
                )
        if not keys:
            keys.extend(_shell_read_keys(item))
    elif item_type == "webSearch":
        keys.append(f"web:{_normalized_text(item.get('query'))}")
    elif item_type == "mcpToolCall" and item.get("readOnlyHint") is True:
        keys.append(
            "mcp:"
            f"{_normalized_text(item.get('server'))}:"
            f"{_normalized_text(item.get('tool'))}:"
            f"{_bounded_value_key(item.get('arguments'))}"
        )
    elif item_type == "dynamicToolCall":
        tool_name = str(item.get("tool") or "")
        if tool_call_reads(tool_name, item.get("arguments") or {}):
            keys.append(
                "dynamic:"
                f"{_normalized_text(tool_name)}:"
                f"{_bounded_value_key(item.get('arguments'))}"
            )
    return tuple(key for key in keys if key and not key.endswith(":{}"))


class _NativeToolLoopRuntime:
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
        # Whether the Provider takes input while the turn runs.  Without it
        # the runtime still keeps the ledger; reminders it would have sent
        # are counted as withheld, and the policy reads them after the turn.
        self.steering_available = bool(steering_available)
        self.withheld_runtime_controls = 0
        self._withheld_reasons: set[str] = set()
        self._controls: deque[dict[str, Any]] = deque()
        self._read_keys: Counter[str] = Counter()
        self._item_types: Counter[str] = Counter()
        self._active_tool_ids: set[str] = set()
        self._active_batch_items: list[dict[str, Any]] = []
        self._anonymous_tool_sequence = 0
        self._emit = emit
        self.checkpoint_interval = checkpoint_interval
        self.max_checkpoints = max_checkpoints
        self.duplicate_read_threshold = duplicate_read_threshold
        self.large_output_bytes = large_output_bytes
        self.granularity_strategy = granularity_strategy
        self.fragmented_read_batch_threshold = (
            fragmented_read_batch_threshold
        )
        self.discovery_target_threshold = discovery_target_threshold
        self.confirm_singleton_reads = confirm_singleton_reads
        self.max_granularity_steers = max_granularity_steers
        self.failure_threshold = failure_threshold
        self.post_steer_read_batches = post_steer_read_batches
        self.context_pressure_threshold_percent = (
            context_pressure_threshold_percent
        )
        self.media_pressure_bytes = media_pressure_bytes
        self.provider_latency_threshold_seconds = (
            provider_latency_threshold_seconds
        )
        self._monotonic = monotonic
        self._adaptive_turn_runtime = adaptive_turn_runtime
        self._planned_limits: dict[str, int] = {
            "cumulative_output_bytes": 256 * 1024,
            **(planned_limits or {}),
        }
        self._final_answer_seen = False
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
        self._auto_pending_discovery_targets = 0
        self._auto_pending_singleton_reads = 0
        self._auto_candidate_batch_age = 0
        self._auto_awaiting_post_steer_read_batch = False
        self._auto_post_steer_window_remaining = 0
        self._auto_post_steer_window_singletons = 0
        self.controls_enqueued = 0
        self.safety_controls_enqueued = 0
        self.regular_safety_controls_enqueued = 0
        self.critical_safety_controls_enqueued = 0
        self.granularity_steers_enqueued = 0
        self.controls_requested = 0
        self.controls_applied = 0
        self.controls_failed = 0
        self.control_errors: Counter[str] = Counter()
        self.suppressed_runtime_controls = 0
        self.runtime_action_outcomes: list[dict[str, str]] = []
        self.context_transition_pending = False
        self._safety_reasons_enqueued: set[str] = set()
        self._last_provider_event_at: float | None = None
        # Start observing provider latency at turn submission, rather than
        # waiting for the first tool batch.  A provider can stall while
        # ingesting a long history before it emits any event at all.
        self._latency_watch_started_at: float | None = self._monotonic()
        self._max_provider_silence_seconds = 0.0
        self._provider_latency_checks = 0

    def process_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        with self._lock:
            now = self._monotonic()
            if self._last_provider_event_at is not None:
                self._max_provider_silence_seconds = max(
                    self._max_provider_silence_seconds,
                    max(0.0, now - self._last_provider_event_at),
                )
            self._last_provider_event_at = now
            self.provider_events_observed += 1
            method = str(event.get("method") or "")
            params = event.get("params")
            params = params if isinstance(params, dict) else {}
            if method == PROVIDER_ACTIVITY_EVENT:
                return
            if method == "thread/tokenUsage/updated":
                self._observe_token_usage(params)
                return
            if method == "turn/started":
                self._final_answer_seen = False
                self.suppressed_runtime_controls += len(self._controls)
                self._controls.clear()
                self._active_tool_ids.clear()
                self._latency_watch_started_at = now
                return
            if method == "turn/completed":
                self._final_answer_seen = True
                self.suppressed_runtime_controls += len(self._controls)
                self._controls.clear()
                return
            if method not in {"item/started", "item/completed"}:
                return
            item = params.get("item")
            if not isinstance(item, dict):
                return
            item_type = str(item.get("type") or "unknown")
            if method == "item/started":
                if _native_tool_item(item):
                    self._start_tool_item(item)
                return

            if (
                item_type == "agentMessage"
                and str(item.get("phase") or "").strip().casefold()
                == "final_answer"
            ):
                self._final_answer_seen = True
                self.suppressed_runtime_controls += len(self._controls)
                self._controls.clear()
                return

            self._item_types[item_type] += 1
            if _native_tool_item(item):
                self.native_tool_calls_observed += 1
                if _tool_failed(item):
                    self.failed_tool_calls_observed += 1
                    if (
                        self.failed_tool_calls_observed
                        >= self.failure_threshold
                    ):
                        self._queue_control("failure_loop_pressure")
            if item_type == "commandExecution":
                self.command_calls_observed += 1
                if _TEST_COMMAND_RE.search(_command_text(item)):
                    self.test_commands_observed += 1
            elif item_type == "fileChange":
                self.file_change_items_observed += 1
            elif item_type == "contextCompaction":
                self.context_compactions_observed += 1

            output_bytes = _approximate_output_bytes(item)
            self.native_output_bytes_observed += output_bytes
            is_large_output = output_bytes >= self.large_output_bytes
            if is_large_output:
                self.large_native_outputs_observed += 1
            media_bytes = _approximate_inline_media_bytes(item)
            if media_bytes:
                self.native_inline_media_bytes_observed += media_bytes
                self.native_inline_media_items_observed += 1
                # imageView is an observation of media already owned by the
                # Provider thread. Interrupting after it has been decoded is
                # both too late to reduce that cost and can discard useful
                # progress. Only newly returned tool media is governable at
                # this boundary; input/history media remains telemetry for
                # latency feedback and future-turn policy selection.
                governable_media = (
                    item_type != "imageView" and _native_tool_item(item)
                )
                if governable_media:
                    self.native_tool_inline_media_bytes_observed += media_bytes
                if (
                    governable_media
                    and self._ungoverned_inline_media_bytes()
                    >= self.media_pressure_bytes
                ):
                    self._queue_control("native_inline_media_pressure")

            for key in _semantic_read_keys(item):
                self._read_keys[key] += 1
                if self._read_keys[key] > 1:
                    self.duplicate_read_calls_observed += 1

            if _native_tool_item(item):
                self._complete_tool_item(item)

            if (
                self.native_tool_calls_observed >= self.next_tool_checkpoint
            ):
                self.next_tool_checkpoint = 2**63 - 1
                self._queue_control("tool_call_checkpoint")
            if (
                self.duplicate_read_calls_observed
                >= self.next_duplicate_checkpoint
            ):
                self.next_duplicate_checkpoint = 2**63 - 1
                self._queue_control("duplicate_read_pressure")
            if (
                is_large_output
                and not media_bytes
                and self.large_native_outputs_observed == 1
            ):
                self._queue_control("large_output_pressure")
            self._apply_adaptive_controls()

    def _observe_token_usage(self, params: dict[str, Any]) -> None:
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
                self._queue_control("context_pressure")
        self._apply_adaptive_controls()

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
            self._queue_control("large_output_pressure")

    def _start_tool_item(self, item: dict[str, Any]) -> None:
        item_id = _item_identity(item)
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
        item_id = _item_identity(item)
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
        self._latency_watch_started_at = self._monotonic()
        if self.context_transition_pending and not self._active_tool_ids:
            self.context_transition_pending = False
            self._queue_control("context_pressure")
        profiles = tuple(
            _read_profile(
                item,
                count_discovery_targets=(
                    self.granularity_strategy
                    in {"auto", "discovery", "adaptive"}
                ),
            )
            for item in items
        )
        if not profiles or not all(
            bool(profile["read_only"]) for profile in profiles
        ):
            self.consecutive_singleton_read_batches = 0
            self._age_auto_candidate()
            return

        read_operations = sum(
            int(profile["read_operations"]) for profile in profiles
        )
        if read_operations <= 0:
            self.consecutive_singleton_read_batches = 0
            self._age_auto_candidate()
            return
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

        self._record_auto_post_steer_read_batch(read_operations)
        if self.granularity_strategy == "auto":
            self._observe_auto_read_batch(
                read_operations=read_operations,
                discovery_targets=discovery_targets,
            )
            return

        self._maybe_queue_granularity_control(
            discovery_targets=discovery_targets,
        )

    def _observe_auto_read_batch(
        self,
        *,
        read_operations: int,
        discovery_targets: int,
    ) -> None:
        if discovery_targets >= self.discovery_target_threshold:
            if (
                self.granularity_steers_enqueued
                >= self.max_granularity_steers
            ):
                self._clear_auto_candidate()
                return
            self.auto_discovery_candidates_armed += 1
            self._auto_pending_discovery_targets = discovery_targets
            self._auto_pending_singleton_reads = 0
            self._auto_candidate_batch_age = 0
            return

        if not self._auto_pending_discovery_targets:
            if (
                read_operations == 1
                and self.granularity_steers_enqueued == 0
                and self.consecutive_singleton_read_batches
                >= self.fragmented_read_batch_threshold
            ):
                self.auto_confirmed_steers += 1
                self._queue_granularity_control(
                    "auto_serial_fragmentation",
                    discovery_targets=0,
                )
            return
        if read_operations > 1:
            self.auto_batched_followups_observed += 1
            self._clear_auto_candidate()
            return

        self.auto_singleton_followups_observed += 1
        self._auto_pending_singleton_reads += 1
        if (
            self._auto_pending_singleton_reads
            < self.confirm_singleton_reads
        ):
            return

        discovery_targets = self._auto_pending_discovery_targets
        self.auto_confirmed_steers += 1
        self._clear_auto_candidate()
        self._queue_granularity_control(
            "auto_confirmed_fragmentation",
            discovery_targets=discovery_targets,
        )

    def _age_auto_candidate(self) -> None:
        if (
            self.granularity_strategy != "auto"
            or not self._auto_pending_discovery_targets
        ):
            return
        self._auto_candidate_batch_age += 1
        if self._auto_candidate_batch_age >= _AUTO_CANDIDATE_MAX_BATCH_AGE:
            self.auto_candidates_cancelled += 1
            self._clear_auto_candidate()

    def _clear_auto_candidate(self) -> None:
        self._auto_pending_discovery_targets = 0
        self._auto_pending_singleton_reads = 0
        self._auto_candidate_batch_age = 0

    def _record_auto_post_steer_read_batch(
        self,
        read_operations: int,
    ) -> None:
        if self._auto_post_steer_window_remaining <= 0:
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
            self._auto_post_steer_window_singletons += 1
        self._auto_post_steer_window_remaining -= 1
        if self._auto_post_steer_window_remaining > 0:
            return
        self._auto_awaiting_post_steer_read_batch = False
        persistent_threshold = max(
            2,
            (self.post_steer_read_batches * 3 + 3) // 4,
        )
        if (
            self._auto_post_steer_window_singletons
            >= persistent_threshold
            and self.granularity_steers_enqueued
            < self.max_granularity_steers
        ):
            self.auto_persistent_fragmentation_steers += 1
            self._queue_granularity_control(
                "auto_persistent_fragmentation",
                discovery_targets=0,
            )

    def _maybe_queue_granularity_control(
        self,
        *,
        discovery_targets: int,
    ) -> None:
        if (
            self.granularity_strategy == "off"
            or self.granularity_steers_enqueued
            >= self.max_granularity_steers
        ):
            return
        reason = ""
        if (
            self.granularity_strategy in {"discovery", "adaptive"}
            and discovery_targets >= self.discovery_target_threshold
        ):
            reason = "read_discovery_opportunity"
        elif (
            self.granularity_strategy in {"fragmented", "adaptive"}
            and self.consecutive_singleton_read_batches
            >= self.fragmented_read_batch_threshold
        ):
            reason = "fragmented_read_sequence"
        elif (
            self.granularity_strategy == "count"
            and self.read_operations_observed
            >= self.fragmented_read_batch_threshold
        ):
            reason = "read_call_count"
        if not reason:
            return
        self._queue_granularity_control(
            reason,
            discovery_targets=discovery_targets,
        )

    def _queue_granularity_control(
        self,
        reason: str,
        *,
        discovery_targets: int,
    ) -> None:
        if self.granularity_steers_enqueued >= self.max_granularity_steers:
            return
        if not self.steering_available:
            self._withhold(reason)
            return
        self.granularity_steers_enqueued += 1
        self.controls_enqueued += 1
        if reason == "auto_confirmed_fragmentation":
            evidence = (
                "只读发现已返回至少 "
                f"{discovery_targets} 个候选目标，随后仍连续执行了 "
                f"{self.confirm_singleton_reads} 个单项只读批次。"
            )
        elif reason == "auto_serial_fragmentation":
            evidence = (
                "未经过批量发现也已连续执行 "
                f"{self.consecutive_singleton_read_batches} 个单项只读批次。"
            )
        elif reason == "auto_persistent_fragmentation":
            evidence = (
                "第一次提示后的观测窗口仍有 "
                f"{self._auto_post_steer_window_singletons}/"
                f"{self.post_steer_read_batches} 个单项只读批次。"
            )
        elif reason == "read_discovery_opportunity":
            evidence = (
                f"最近一次只读发现已返回至少 {discovery_targets} 个候选目标。"
            )
        elif reason == "fragmented_read_sequence":
            evidence = (
                "已连续执行 "
                f"{self.consecutive_singleton_read_batches} 个单项只读批次。"
            )
        else:
            evidence = (
                f"本轮已经执行 {self.read_operations_observed} 个只读操作。"
            )
        ledger = self._ledger()
        text = f"""\
<{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG} reason="{reason}">
当前任务的工具调用优化提示：{evidence}
把当前已经知道且互不依赖的读取、搜索和检查合并为一个并行批次；
只有确实依赖前序结果的步骤才继续串行。不要并行写入、破坏性操作或有先后依赖的动作，
也不要重新读取未变化的证据。任务很小或没有可安全合并的调用时，直接继续即可。
{_ORIGINAL_TASK_CONTINUATION}
观测账本：{json.dumps(ledger, ensure_ascii=False, separators=(',', ':'))}
</{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG}>"""
        self._controls.append({
            "control_id": (
                f"granularity-steer-{self.granularity_steers_enqueued}"
            ),
            "reason": reason,
            "input": [{"type": "text", "text": text}],
            "metadata": {
                "kind": "tool_granularity",
                "strategy": self.granularity_strategy,
                "ledger": ledger,
            },
        })

    def _ledger(self) -> dict[str, Any]:
        repeated = [
            {"key": key, "count": count}
            for key, count in self._read_keys.most_common(5)
            if count > 1
        ]
        return {
            "native_tool_calls": self.native_tool_calls_observed,
            "commands": self.command_calls_observed,
            "tool_batches": self.tool_batches_observed,
            "read_batches": self.read_batches_observed,
            "singleton_read_batches": (
                self.singleton_read_batches_observed
            ),
            "batched_read_batches": self.batched_read_batches_observed,
            "max_read_batch_width": self.max_read_batch_width,
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
                self._current_provider_silence_seconds(),
                3,
            ),
            "context_pressure_percent": round(
                self.max_context_pressure_percent,
                2,
            ),
            "repeated_reads": repeated,
        }

    def _ungoverned_inline_media_bytes(self) -> int:
        return self.native_tool_inline_media_bytes_observed

    def _current_provider_silence_seconds(self) -> float:
        progress_at = self._last_provider_event_at
        if progress_at is None:
            progress_at = self._latency_watch_started_at
        if progress_at is None:
            return 0.0
        return max(0.0, self._monotonic() - progress_at)

    def _observe_runtime_latency(self) -> None:
        self._provider_latency_checks += 1
        if (
            self._latency_watch_started_at is None
            or self._active_tool_ids
            or "provider_latency_pressure" in self._safety_reasons_enqueued
        ):
            return
        silence = self._current_provider_silence_seconds()
        self._max_provider_silence_seconds = max(
            self._max_provider_silence_seconds,
            silence,
        )
        if silence >= self.provider_latency_threshold_seconds:
            self._queue_control("provider_latency_pressure")

    def _withhold(self, reason: str) -> None:
        if reason not in self._withheld_reasons:
            self._withheld_reasons.add(reason)
            self.withheld_runtime_controls += 1

    def _queue_control(self, reason: str) -> None:
        if self._final_answer_seen:
            self.suppressed_runtime_controls += 1
            return
        if not self.steering_available:
            self._withhold(reason)
            return
        if reason in self._safety_reasons_enqueued:
            self.suppressed_runtime_controls += 1
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
            self.suppressed_runtime_controls += 1
            return
        self._safety_reasons_enqueued.add(reason)
        self.safety_controls_enqueued += 1
        if critical:
            self.critical_safety_controls_enqueued += 1
        else:
            self.regular_safety_controls_enqueued += 1
        self.controls_enqueued += 1
        ledger = self._ledger()
        guidance = {
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
        }.get(reason, "复用已经取得的证据，继续完成当前用户的原任务。")
        text = f"""\
<{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG} reason="{reason}">
当前任务的运行优化提示：{guidance}
只运行与已修改责任方直接相关的验证；不要仅为再次确认而重复已经通过的等价测试。
{_ORIGINAL_TASK_CONTINUATION}
观测账本：{json.dumps(ledger, ensure_ascii=False, separators=(',', ':'))}
</{AGENT_HARNESS_RUNTIME_CHECKPOINT_TAG}>"""
        self._controls.append({
            "control_id": (
                f"native-checkpoint-{self.safety_controls_enqueued}"
            ),
            "reason": reason,
            "input": [{"type": "text", "text": text}],
            "metadata": {"kind": "safety", "ledger": ledger},
        })

    def next_control(self) -> dict[str, Any] | None:
        with self._lock:
            if self._final_answer_seen:
                return None
            self._observe_runtime_latency()
            if not self._controls:
                return None
            self.controls_requested += 1
            return self._controls.popleft()

    def record_control_result(
        self,
        control: dict[str, Any],
        applied: bool,
        error: str,
    ) -> None:
        with self._lock:
            metadata = control.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            if applied:
                self.controls_applied += 1
                if (
                    metadata.get("kind") == "tool_granularity"
                    and self.granularity_strategy == "auto"
                ):
                    self._auto_awaiting_post_steer_read_batch = True
                    self._auto_post_steer_window_remaining = (
                        self.post_steer_read_batches
                    )
                    self._auto_post_steer_window_singletons = 0
            else:
                self.controls_failed += 1
                self.control_errors[str(error or "unknown")] += 1
            action = str(control.get("action") or "steer").strip()[:64]
            reason = str(control.get("reason") or "unspecified").strip()[:64]
            self.runtime_action_outcomes.append({
                "action": f"native_runtime_control:{action}:{reason}",
                "status": "applied" if applied else "failed",
                "reason": "" if applied else str(error or "unknown")[:128],
            })
            details = {
                "feature_id": NATIVE_TOOL_LOOP_GOVERNOR_FEATURE_ID,
                "control_id": str(control.get("control_id") or ""),
                "reason": str(control.get("reason") or ""),
                "applied": bool(applied),
                "error": str(error or ""),
                "action": str(control.get("action") or "steer"),
            }
        try:
            self._emit(details)
        except Exception:
            pass

    def report(self) -> dict[str, Any]:
        with self._lock:
            if self.controls_applied:
                effectiveness_state = "control_applied"
            elif self.controls_enqueued:
                effectiveness_state = "control_triggered"
            elif self.provider_events_observed:
                effectiveness_state = "observing"
            else:
                effectiveness_state = "idle"
            return {
                "effectiveness_state": effectiveness_state,
                "runtime_control_available": self.steering_available,
                "runtime_controls_withheld": self.withheld_runtime_controls,
                "runtime_control_reasons_withheld": sorted(
                    self._withheld_reasons
                ),
                "adaptive_turn_policy_attached": bool(
                    self._adaptive_turn_runtime is not None
                ),
                "planned_turn_limits": dict(self._planned_limits),
                "granularity_strategy": self.granularity_strategy,
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
                "provider_latency_checks": self._provider_latency_checks,
                "max_provider_silence_seconds": round(
                    self._max_provider_silence_seconds,
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
                "read_batches_observed": self.read_batches_observed,
                "singleton_read_batches_observed": (
                    self.singleton_read_batches_observed
                ),
                "batched_read_batches_observed": (
                    self.batched_read_batches_observed
                ),
                "max_read_batch_width": self.max_read_batch_width,
                "read_operations_observed": (
                    self.read_operations_observed
                ),
                "max_consecutive_singleton_read_batches": (
                    self.max_consecutive_singleton_read_batches
                ),
                "discovery_opportunities_observed": (
                    self.discovery_opportunities_observed
                ),
                "max_discovery_targets_observed": (
                    self.max_discovery_targets_observed
                ),
                "auto_discovery_candidates_armed": (
                    self.auto_discovery_candidates_armed
                ),
                "auto_singleton_followups_observed": (
                    self.auto_singleton_followups_observed
                ),
                "auto_batched_followups_observed": (
                    self.auto_batched_followups_observed
                ),
                "auto_candidates_cancelled": (
                    self.auto_candidates_cancelled
                ),
                "auto_confirmed_steers": self.auto_confirmed_steers,
                "auto_candidate_pending": bool(
                    self._auto_pending_discovery_targets
                ),
                "auto_post_steer_read_batches_observed": (
                    self.auto_post_steer_read_batches_observed
                ),
                "auto_post_steer_batched_read_batches": (
                    self.auto_post_steer_batched_read_batches
                ),
                "auto_post_steer_singleton_read_batches": (
                    self.auto_post_steer_singleton_read_batches
                ),
                "auto_post_steer_max_read_batch_width": (
                    self.auto_post_steer_max_read_batch_width
                ),
                "auto_post_steer_observation_pending": (
                    self._auto_awaiting_post_steer_read_batch
                ),
                "auto_post_steer_window_remaining": (
                    self._auto_post_steer_window_remaining
                ),
                "auto_persistent_fragmentation_steers": (
                    self.auto_persistent_fragmentation_steers
                ),
                "runtime_controls_enqueued": self.controls_enqueued,
                "safety_controls_enqueued": (
                    self.safety_controls_enqueued
                ),
                "regular_safety_controls_enqueued": (
                    self.regular_safety_controls_enqueued
                ),
                "critical_safety_controls_enqueued": (
                    self.critical_safety_controls_enqueued
                ),
                "granularity_steers_enqueued": (
                    self.granularity_steers_enqueued
                ),
                "runtime_controls_requested": self.controls_requested,
                "runtime_controls_applied": self.controls_applied,
                "runtime_controls_failed": self.controls_failed,
                "runtime_action_outcomes": [
                    dict(item) for item in self.runtime_action_outcomes
                ],
                "runtime_controls_suppressed": (
                    self.suppressed_runtime_controls
                ),
                "context_transition_pending": self.context_transition_pending,
                "runtime_control_reasons": sorted(
                    self._safety_reasons_enqueued
                ),
                "runtime_control_errors": dict(self.control_errors),
                "item_type_counts": dict(self._item_types),
                "top_semantic_reads": [
                    {"key": key, "count": count}
                    for key, count in self._read_keys.most_common(10)
                ],
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
        effort_adjustable = TURN_REASONING_EFFORT in capabilities
        if getattr(invocation, "provider_event_processor", None) is not None:
            return HarnessFeatureOutcome(
                invocation=invocation,
                status="skipped",
                reason="provider_event_processor_already_attached",
            )
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
        reasoning_effort_adjusted = bool(
            effective_effort
            and effective_effort != configured_effort
            and request is not None
        )
        if reasoning_effort_adjusted:
            runtime_options["effort"] = effective_effort
            invocation = replace(
                invocation,
                request=replace(
                    request,
                    runtime_options=runtime_options,
                ),
            )
        checkpoint_interval = _bounded_int(
            config.get("checkpoint_interval"),
            default=DEFAULT_NATIVE_CHECKPOINT_INTERVAL,
            minimum=20,
            maximum=500,
        )
        max_checkpoints = _bounded_int(
            config.get("max_checkpoints"),
            default=DEFAULT_NATIVE_MAX_CHECKPOINTS,
            minimum=1,
            maximum=8,
        )
        duplicate_read_threshold = _bounded_int(
            config.get("duplicate_read_threshold"),
            default=DEFAULT_DUPLICATE_READ_THRESHOLD,
            minimum=2,
            maximum=100,
        )
        large_output_bytes = _bounded_int(
            config.get("large_output_bytes"),
            default=DEFAULT_NATIVE_LARGE_OUTPUT_BYTES,
            minimum=16 * 1024,
            maximum=16 * 1024 * 1024,
        )
        granularity_strategy = _granularity_strategy(
            config.get("granularity_strategy")
        )
        fragmented_read_batch_threshold = _bounded_int(
            config.get("fragmented_read_batch_threshold"),
            default=DEFAULT_FRAGMENTED_READ_BATCH_THRESHOLD,
            minimum=2,
            maximum=20,
        )
        discovery_target_threshold = _bounded_int(
            config.get("discovery_target_threshold"),
            default=DEFAULT_DISCOVERY_TARGET_THRESHOLD,
            minimum=2,
            maximum=100,
        )
        confirm_singleton_reads = _bounded_int(
            config.get("confirm_singleton_reads"),
            default=DEFAULT_CONFIRM_SINGLETON_READS,
            minimum=1,
            maximum=5,
        )
        max_granularity_steers = _bounded_int(
            config.get("max_granularity_steers"),
            default=DEFAULT_MAX_GRANULARITY_STEERS,
            minimum=1,
            maximum=3,
        )
        failure_threshold = _bounded_int(
            config.get("failure_threshold"),
            default=DEFAULT_FAILURE_THRESHOLD,
            minimum=2,
            maximum=10,
        )
        post_steer_read_batches = _bounded_int(
            config.get("post_steer_read_batches"),
            default=DEFAULT_POST_STEER_READ_BATCHES,
            minimum=2,
            maximum=8,
        )
        context_pressure_threshold_percent = _bounded_int(
            config.get("context_pressure_threshold_percent"),
            default=DEFAULT_CONTEXT_PRESSURE_THRESHOLD_PERCENT,
            minimum=70,
            maximum=95,
        )
        media_pressure_bytes = _bounded_int(
            config.get("media_pressure_bytes"),
            default=DEFAULT_NATIVE_MEDIA_PRESSURE_BYTES,
            minimum=64 * 1024,
            maximum=16 * 1024 * 1024,
        )
        provider_latency_threshold_seconds = _bounded_int(
            config.get("provider_latency_threshold_seconds"),
            default=DEFAULT_PROVIDER_LATENCY_THRESHOLD_SECONDS,
            minimum=5,
            maximum=15 * 60,
        )
        runtime = _NativeToolLoopRuntime(
            checkpoint_interval=checkpoint_interval,
            max_checkpoints=max_checkpoints,
            duplicate_read_threshold=duplicate_read_threshold,
            large_output_bytes=large_output_bytes,
            granularity_strategy=granularity_strategy,
            fragmented_read_batch_threshold=(
                fragmented_read_batch_threshold
            ),
            discovery_target_threshold=discovery_target_threshold,
            confirm_singleton_reads=confirm_singleton_reads,
            max_granularity_steers=max_granularity_steers,
            failure_threshold=failure_threshold,
            post_steer_read_batches=post_steer_read_batches,
            context_pressure_threshold_percent=(
                context_pressure_threshold_percent
            ),
            media_pressure_bytes=media_pressure_bytes,
            provider_latency_threshold_seconds=(
                provider_latency_threshold_seconds
            ),
            adaptive_turn_runtime=config.get("_adaptive_turn_runtime"),
            planned_limits={
                "cumulative_output_bytes": _bounded_int(
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
                "checkpoint_interval": checkpoint_interval,
                "max_checkpoints": max_checkpoints,
                "duplicate_read_threshold": duplicate_read_threshold,
                "large_output_bytes": large_output_bytes,
                "granularity_strategy": granularity_strategy,
                "fragmented_read_batch_threshold": (
                    fragmented_read_batch_threshold
                ),
                "discovery_target_threshold": discovery_target_threshold,
                "confirm_singleton_reads": confirm_singleton_reads,
                "max_granularity_steers": max_granularity_steers,
                "failure_threshold": failure_threshold,
                "post_steer_read_batches": post_steer_read_batches,
                "context_pressure_threshold_percent": (
                    context_pressure_threshold_percent
                ),
                "configured_reasoning_effort": configured_effort,
                "effective_reasoning_effort": effective_effort,
                "reasoning_effort_adjusted": reasoning_effort_adjusted,
                "media_pressure_bytes": media_pressure_bytes,
                "provider_latency_threshold_seconds": (
                    provider_latency_threshold_seconds
                ),
            },
        )
