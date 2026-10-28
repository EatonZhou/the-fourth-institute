"""The vocabulary every part of the Codex app-server client shares.

Errors and their classification, the waiters an active turn, goal or
compaction is followed through, token usage accounting and the helpers that
read or build app-server payloads.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


TASK_COMPLETE_GOAL_STATUSES = frozenset({"blocked", "complete", "usagelimited", "budgetlimited"})


class CodexAppServerError(RuntimeError):
    pass


class CodexTurnDetached(CodexAppServerError):
    """A retiring follower leaves the accepted native turn running."""

    def __init__(self, thread_id: str, turn_id: str) -> None:
        self.thread_id, self.turn_id = thread_id, turn_id
        super().__init__(f"Codex follower detached: {thread_id}/{turn_id}")


class CodexTurnUsageLimitExceeded(CodexAppServerError):
    """The account needs an external quota change, not another input."""

    error_code = "provider_usage_limit"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        super().__init__(f"Codex turn failed: {reason}")
        self.thread_id = thread_id
        self.turn_id = turn_id
        self.error_details = {"provider": "codex", "retryable": False}


class CodexTurnAuthenticationError(CodexAppServerError):
    """The account requires renewed authentication before work can resume."""

    error_code = "provider_authentication_failed"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        detail = str(reason or "Codex authentication failed; please sign in again").strip()
        super().__init__(f"Codex authentication failed: {detail}")
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.error_details = {
            "provider": "codex",
            "retryable": False,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "provider_reason": detail,
        }


class CodexTurnSafetyBlocked(CodexAppServerError):
    """The provider safety system rejected the request as terminal."""

    error_code = "provider_safety_blocked"

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        detail = str(reason or "provider safety system blocked the request").strip()
        super().__init__(f"Codex turn blocked by safety systems: {detail}")
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.error_details = {
            "provider": "codex",
            "retryable": False,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if detail:
            self.error_details["provider_reason"] = detail


class CodexAppServerUnavailable(CodexAppServerError):
    pass


class CodexThreadWriterBusy(CodexAppServerError):
    """The durable thread is temporarily owned by another Codex process."""


class CodexCompactionInProgress(CodexThreadWriterBusy):
    """The Provider is still compacting a thread that rejected turn/start."""

    retry_after_seconds = 5.0


class CodexTurnConnectionLost(CodexAppServerError):
    def __init__(self, thread_id: str, turn_id: str = "") -> None:
        super().__init__(f"Codex app-server connection lost during accepted turn {turn_id or '(unknown)'}")
        self.thread_id = thread_id
        self.turn_id = turn_id


class CodexTurnInterrupted(CodexAppServerError):
    error_code = "provider_turn_interrupted"

    def __init__(
        self,
        thread_id: str,
        turn_id: str,
        provider_reason: str = "",
    ) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.provider_reason = str(provider_reason or "").strip()
        self.error_details = {
            "provider": "codex",
            "provider_status": "interrupted",
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.provider_reason:
            self.error_details["provider_reason"] = self.provider_reason
        message = (
            f"Codex turn interrupted: {self.provider_reason}"
            if self.provider_reason
            else "Codex turn interrupted without a provider reason"
        )
        super().__init__(message)


class CodexTurnRetryableFailure(CodexAppServerError):
    """A terminal Provider failure that is safe to retry once in-place."""

    error_code = "provider_turn_retryable_failure"

    def __init__(
        self,
        thread_id: str,
        turn_id: str,
        provider_reason: str,
        failure_kind: str,
    ) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.provider_reason = str(provider_reason or "").strip()
        self.failure_kind = str(failure_kind or "transient").strip()
        self.error_details = {
            "provider": "codex",
            "provider_status": "failed",
            "provider_failure_kind": self.failure_kind,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
        }
        if self.provider_reason:
            self.error_details["provider_reason"] = self.provider_reason
        detail = self.provider_reason or "provider returned no error details"
        super().__init__(f"Codex turn failed: {detail}")


class CodexThreadNotFound(CodexAppServerError):
    """The requested thread is absent from the native server and rollout store."""


class CodexTurnContinuationRequired(CodexAppServerError):
    """A provider turn ended before the logical execution produced an answer."""

    def __init__(self, thread_id: str, turn_id: str, reason: str) -> None:
        self.thread_id = str(thread_id or "")
        self.turn_id = str(turn_id or "")
        self.reason = str(reason or "missing_final_response")
        super().__init__(
            "Codex turn ended before the logical execution produced a final response"
        )


def is_active_compaction_conflict(exc: BaseException) -> bool:
    message = str(exc or "").casefold()
    return "activeturnnotsteerable" in message and "compact" in message


def is_loaded_thread_writer_conflict(error: object) -> bool:
    message = str(error or "").casefold()
    return "thread" in message and "already has an active writer" in message


def is_context_compaction_item(item: object) -> bool:
    """A thread item that compacted the thread's context (ThreadItem ``contextCompaction``)."""

    return isinstance(item, Mapping) and item.get("type") == "contextCompaction"


def terminal_turn_error(
    thread_id: str,
    turn: dict[str, Any],
) -> CodexAppServerError:
    status = str(turn.get("status") or "").strip()
    raw_error = turn.get("error")
    from .execution.provider_failures import failure_message, is_usage_limit_failure

    reason = failure_message(raw_error)
    turn_id = str(turn.get("id") or "")
    normalized_reason = reason.casefold()
    error_info = raw_error.get("codexErrorInfo") if isinstance(raw_error, dict) else None
    if isinstance(error_info, dict):
        error_info = next(iter(error_info), "")
    if error_info == "unauthorized" or any(marker in normalized_reason for marker in (
        "your access token could not be refreshed", "provided authentication token is expired",
    )):
        return CodexTurnAuthenticationError(thread_id, turn_id, reason)
    if error_info in {"cyberPolicy", "misalignmentPolicyViolation"} or any(marker in normalized_reason for marker in (
        "blocked by our safety systems", "potentially unintended activity", "cyber_policy",
    )):
        return CodexTurnSafetyBlocked(thread_id, turn_id, reason)
    if error_info == "usageLimitExceeded" or is_usage_limit_failure(reason):
        return CodexTurnUsageLimitExceeded(thread_id, turn_id, reason)
    if status == "interrupted":
        return CodexTurnInterrupted(thread_id, turn_id, reason)
    if error_info == "serverOverloaded" or "selected model is at capacity" in normalized_reason:
        return CodexTurnRetryableFailure(thread_id, turn_id, reason, "capacity")
    detail = reason or "provider returned no error details"
    return CodexAppServerError(
        f"Codex turn {status or 'failed'}: {detail}"
    )


@dataclass
class CodexRequestTimeouts:
    """How long the client waits for app-server's answers, in seconds."""

    # Every request that names no timeout of its own.
    request: float
    # Starting, forking, resuming and unloading threads, goal commands and turn controls.
    lifecycle: float
    # ``turn/start`` and ``review/start``.
    turn_start: float


@dataclass
class CodexTurnResult:
    thread_id: str
    turn_id: str
    response: str
    turn: dict[str, Any]
    token_usage: dict[str, Any]


@dataclass
class CodexGoalResult:
    thread_id: str
    turn_id: str
    response: str
    goal: dict[str, Any] | None
    token_usage: dict[str, Any]


@dataclass
class PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None
    error: BaseException | None = None


@dataclass
class TurnWaiter:
    thread_id: str
    next_reconcile_at: float = 0.0
    event: threading.Event = field(default_factory=threading.Event)
    turn_id: str = ""
    pending_notifications: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    completed: dict[str, Any] | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    agent_messages: list[str] = field(default_factory=list)
    error: BaseException | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None
    thread_idle: bool = False
    terminal_goal: dict[str, Any] | None = None
    final_answer_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    final_answer_seen: bool = False
    completed_item_ids: set[str] = field(default_factory=set)
    unphased_items: dict[str, dict[str, Any]] = field(default_factory=dict)
    completion_deferred: bool = False
    turn_snapshot_transform: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    interrupt_requests: dict[str, PendingRequest] = field(default_factory=dict)


@dataclass
class GoalWaiter:
    thread_id: str
    event: threading.Event = field(default_factory=threading.Event)
    turn_id: str = ""
    goal: dict[str, Any] | None = None
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    agent_messages: list[str] = field(default_factory=list)
    error: BaseException | None = None
    event_callback: Callable[[dict[str, Any]], None] | None = None


@dataclass
class CompactWaiter:
    thread_id: str
    event: threading.Event = field(default_factory=threading.Event)
    token_usage: dict[str, Any] = field(default_factory=dict)
    token_usage_events: set[tuple[Any, ...]] = field(default_factory=set)
    error: BaseException | None = None
    thread_idle: bool = False
    last_progress_at: float = field(default_factory=time.monotonic)


def fail_waiter(
    waiter: PendingRequest | TurnWaiter | GoalWaiter | CompactWaiter,
    error: BaseException,
) -> None:
    # Preserve the causal failure; an explicit rejection supersedes recovery.
    if waiter.error is None or (
        getattr(error, "error_details", {}).get("retryable") is False
        and getattr(waiter.error, "error_details", {}).get("retryable") is not False
    ):
        waiter.error = error
    waiter.event.set()


def is_final_answer(item: Mapping[str, Any]) -> bool:
    return (
        item.get("type") == "agentMessage"
        and str(item.get("phase") or "").strip().casefold() == "final_answer"
    )


def collect_agent_message(waiter: TurnWaiter | GoalWaiter, item: dict[str, Any]) -> bool:
    text = str(item.get("text") or "").strip()
    if item.get("type") != "agentMessage" or not text:
        return False
    waiter.agent_messages.append(text)
    return True


def collect_final_answer(waiter: TurnWaiter, item: dict[str, Any]) -> None:
    waiter.final_answer_seen = True
    waiter.final_answer_items[str(item.get("id") or item.get("text"))] = dict(item)


_TOKEN_USAGE_KEYS = (
    "inputTokens",
    "cachedInputTokens",
    "outputTokens",
    "reasoningOutputTokens",
    "totalTokens",
)


def _nonnegative_int(value: object) -> int:
    try:
        number = int(value or 0)
    except Exception:
        return 0
    return number if number > 0 else 0


def _clean_token_usage_part(value: object) -> dict[str, int]:
    data = value if isinstance(value, dict) else {}
    return {
        key: number
        for key in _TOKEN_USAGE_KEYS
        for number in [_nonnegative_int(data.get(key))]
        if number > 0
    }


def _sum_token_usage_parts(left: object, right: object) -> dict[str, int]:
    left_data = _clean_token_usage_part(left)
    right_data = _clean_token_usage_part(right)
    out: dict[str, int] = {}
    for key in _TOKEN_USAGE_KEYS:
        value = _nonnegative_int(left_data.get(key)) + _nonnegative_int(right_data.get(key))
        if value > 0:
            out[key] = value
    return out


def collect_token_usage(waiter: TurnWaiter | GoalWaiter | CompactWaiter, usage: Any) -> None:
    if not isinstance(usage, dict):
        return
    fingerprint = token_usage_event_fingerprint(usage)
    if fingerprint not in waiter.token_usage_events:
        waiter.token_usage_events.add(fingerprint)
        waiter.token_usage = merge_turn_token_usage(waiter.token_usage, usage)


def token_usage_event_fingerprint(value: object) -> tuple[Any, ...]:
    payload = value if isinstance(value, dict) else {}
    last = _clean_token_usage_part(payload.get("last"))
    total = _clean_token_usage_part(payload.get("total"))
    return (
        tuple((key, last.get(key, 0)) for key in _TOKEN_USAGE_KEYS),
        tuple((key, total.get(key, 0)) for key in _TOKEN_USAGE_KEYS),
        _nonnegative_int(payload.get("modelContextWindow")),
    )


def consumed_token_usage_part(value: object) -> dict[str, int]:
    """The ``last`` part of an update when it reports a model request.

    After a context compaction Codex reports the estimated size of the
    compacted history as a ``last`` part holding only ``totalTokens``; its
    cumulative total does not change.  That is a context size, not usage.
    """

    part = _clean_token_usage_part(value)
    if not part.get("inputTokens") and not part.get("outputTokens"):
        return {}
    return part


def merge_turn_token_usage(current: object, update: object) -> dict[str, Any]:
    current_payload = current if isinstance(current, dict) else {}
    update_payload = update if isinstance(update, dict) else {}
    merged: dict[str, Any] = {}
    last_total = _sum_token_usage_parts(
        current_payload.get("last"), consumed_token_usage_part(update_payload.get("last")),
    )
    if last_total:
        merged["last"] = last_total
    context = _clean_token_usage_part(update_payload.get("last"))
    if context:
        merged["context"] = context
    total = _clean_token_usage_part(update_payload.get("total")) or _clean_token_usage_part(current_payload.get("total"))
    if total:
        merged["total"] = total
    context_window = _nonnegative_int(update_payload.get("modelContextWindow")) or _nonnegative_int(current_payload.get("modelContextWindow"))
    if context_window:
        merged["modelContextWindow"] = context_window
    return merged


def notification_turn_id(params: Mapping[str, Any]) -> str:
    """The turn a notification names, by ``turnId`` or its ``turn`` object."""

    return str(params.get("turnId") or (params.get("turn") or {}).get("id") or "")


def sandbox_policy(sandbox: str) -> dict[str, Any]:
    value = str(sandbox or "").strip().lower()
    if value == "danger-full-access":
        return {"type": "dangerFullAccess"}
    if value == "workspace-write":
        return {"type": "workspaceWrite"}
    return {"type": "readOnly"}


def thread_sandbox_from_policy(
    sandbox_policy: dict[str, Any] | None,
) -> str:
    policy_type = str(
        (sandbox_policy or {}).get("type") or ""
    ).strip()
    return {
        "readOnly": "read-only",
        "workspaceWrite": "workspace-write",
        "dangerFullAccess": "danger-full-access",
    }.get(policy_type, "")


def config_fingerprint(config: object) -> str:
    payload = config if isinstance(config, dict) else {}
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def item_identity(item: Mapping[str, Any]) -> str:
    """An item's id, or a digest of its content when the Provider gave none."""

    item_id = str(item.get("id") or "").strip()
    if item_id:
        return item_id
    return hashlib.sha256(
        json.dumps(
            item,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def codex_final_agent_response(
    turn: dict[str, Any],
    streamed_messages: list[str],
) -> str:
    final_messages: list[str] = []
    other_messages: list[str] = []
    for item in turn.get("items") or []:
        if not isinstance(item, dict) or item.get("type") != "agentMessage":
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if str(item.get("phase") or "") == "final_answer":
            final_messages.append(text)
        else:
            other_messages.append(text)
    if final_messages:
        # Runtime controls can race with the provider's terminal event and
        # elicit a second acknowledgement after the actual answer.  The first
        # final answer is the canonical response for the user's turn.
        return final_messages[0]
    if other_messages:
        return other_messages[-1]
    return streamed_messages[-1] if streamed_messages else ""
