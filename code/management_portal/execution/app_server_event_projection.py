"""Runtime details of app-server notifications.

Codex's app-server reports a turn as JSON-RPC notifications (``method`` and
``params``).  Portal took their vocabulary as the common shape of Provider
events (``native_tool_events``): the Claude Code, Kimi Code, DSH and OpenCode
Adapters report their Providers' turns in it too.  The plan, approval-review
and safety-warning methods only Codex sends.  ``NOTIFICATIONS`` maps each
projected method to its handler; other methods have no card.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from management_common import provider_turn_artifacts
from management_common.message_identity import derived_message_identity

from .provider_item_projection import ITEM_PHASES, project_item
from .runtime_details import EventContext, RuntimeDetails, mapping, nonnegative_int, plan_update_id

Notification = Callable[[dict[str, Any], dict[str, Any], EventContext, RuntimeDetails], None]

_TOKEN_USAGE_NAMES = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


def token_usage(value: object) -> dict[str, int]:
    """The last request's token usage in a ``tokenUsage`` payload, in Portal's names."""

    data = mapping(mapping(value).get("last"))
    return {
        target: number
        for target, source in _TOKEN_USAGE_NAMES.items()
        if (number := nonnegative_int(data.get(source), 0)) > 0
    }


def _plan_content(params: dict[str, Any]) -> str:
    parts = [str(params.get("explanation") or "").strip()]
    plan = params.get("plan") if isinstance(params.get("plan"), list) else []
    for item in plan:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or "").strip()
        status = str(item.get("status") or "").strip()
        if step:
            parts.append(f"[{status or 'pending'}] {step}")
    return "\n".join(part for part in parts if part)


def _text_delta(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    if not details.include_updates:
        return
    item_id = str(params.get("itemId") or "").strip()
    delta = str(params.get("delta") or "")
    if not (item_id and delta):
        return
    update_id = context.scoped(item_id)
    content = details.streamed_text.get(update_id, "") + delta
    details.streamed_text[update_id] = content
    detail = context.detail(kind="update", title="Update", content=content, status="running")
    detail.update(execution_id=context.execution_id, turn_id=str(params.get("turnId") or ""), provider_item_id=item_id)
    details.add_update(detail, update_id)


def _plan_updated(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    thread_id = str(params.get("threadId") or "")
    turn_id = str(params.get("turnId") or "")
    detail = context.detail(kind="plan", title="Plan updated", content=_plan_content(params), status="running")
    if context.execution_id and thread_id and turn_id:
        detail.update(execution_id=context.execution_id, turn_id=turn_id)
        detail["native_plan"] = provider_turn_artifacts.content_part({"artifacts": {
            "thread_id": thread_id,
            "turn_id": turn_id,
            "status": "running",
            "plan": [
                {"step": str(step.get("step") or ""), "status": str(step.get("status") or "")}
                for step in params.get("plan") or [] if isinstance(step, dict)
            ],
            "explanation": str(params.get("explanation") or ""),
        }}, context.execution_id)
        detail["native_plan"]["message_id"] = derived_message_identity(
            "resident-execution-message", context.execution_id, "turn-artifacts:" + turn_id,
        )
    details.add_update(detail, context.scoped(plan_update_id(thread_id, turn_id)))


def _error(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    error = mapping(params.get("error"))
    will_retry = bool(params.get("willRetry"))
    details.add(context.detail(
        kind="error",
        title="Reconnecting" if will_retry else "Provider error",
        content=str(error.get("message") or error.get("additionalDetails") or "Provider error"),
        status="running" if will_retry else "failed",
        phase="reconnecting" if will_retry else "",
    ))


def _safety_warning(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.detail(
        kind="warning", title="Safety warning",
        content=str(params.get("message") or params.get("warning") or ""), status="failed",
    ))


def _approval_review(completed: bool) -> Notification:
    def project(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
        details.add(context.detail(
            kind="approval",
            title="Approval review completed" if completed else "Approval review",
            status="completed" if completed else "running",
        ))

    return project


def _item(phase: str) -> Notification:
    def project(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
        millis = nonnegative_int(
            params.get("startedAtMs") or params.get("updatedAtMs") or params.get("completedAtMs"), 0,
        )
        if millis:
            context = context.at(
                datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
            )
        project_item(mapping(params.get("item")), phase, params, context, details)

    return project


def _turn_started(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.turn_started(
        reason=str(event.get("turn_start_reason") or "initial"),
        thread_id=str(event.get("thread_id") or ""),
        turn_id=str(mapping(params.get("turn")).get("id") or ""),
    ))


def _turn_completed(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    turn = mapping(params.get("turn"))
    thread_id = str(params.get("threadId") or "")
    turn_id = str(turn.get("id") or params.get("turnId") or "")
    if context.execution_id and thread_id and turn_id:
        # Cancelled/failed turns can end without a final artifact.
        details.end_plan(context, thread_id, turn_id)
    details.add(context.turn_ended(
        turn_id=turn_id,
        status=str(turn.get("status") or "unknown"),
        usage=mapping(params.get("usage")),
        error=turn.get("error"),
        interruption_reason=str(event.get("interruption_reason") or ""),
    ))


def _thread_attached(event: dict[str, Any], params: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.thread_attached(
        thread_id=str(params.get("threadId") or ""), turn_id=str(event.get("turn_id") or ""),
    ))


NOTIFICATIONS: dict[str, Notification] = {
    "item/plan/delta": _text_delta,
    "turn/plan/updated": _plan_updated,
    "error": _error,
    "guardianWarning": _safety_warning,
    "item/autoApprovalReview/started": _approval_review(False),
    "item/autoApprovalReview/completed": _approval_review(True),
    **{f"item/{phase}": _item(phase) for phase in ITEM_PHASES},
    "turn/started": _turn_started,
    "turn/completed": _turn_completed,
    "thread/attached": _thread_attached,
}
