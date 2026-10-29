"""Generic Event subscriber delivery and queued receipt settlement."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable
from management_common.operation_errors import OperationError

from . import lineage_program_runtime
from .domain_ports import failure_notification_port

LOGGER = logging.getLogger(__name__)


def pending_event_delivery_consumed(result: dict[str, Any], wire_id: str) -> bool:
    """Return whether a queued occurrence reached a terminal outcome."""
    if result.get("duplicate"):
        return True
    terminal_failures = {
        "failed",
        "paused",
        "unavailable",
        "program_incomplete",
        "budget_full",
        "cycle_finishing",
    }
    matching = [
        item
        for item in result.get("results") or []
        if isinstance(item, dict) and str(item.get("wire_id") or "") == wire_id
    ]
    for item in matching:
        status = str(item.get("status") or "")
        if status not in terminal_failures:
            return True
        outputs = (
            item.get("typed_outputs")
            if isinstance(item.get("typed_outputs"), dict)
            else {}
        )
        # Stored failure receipts cannot make progress by replaying. A committed
        # instruction can still recover projections using its original receipt.
        if status == "failed" and (outputs.get("retryable") is False or (
            item.get("replayed") and outputs.get("commit_state") != "committed"
        )):
            return True
    return False


def delivery_failure(wire_id: str, target_id: str, exc: Exception) -> dict[str, Any]:
    """Keep an instruction's retry contract; handoff interruptions can still retry."""
    result: dict[str, Any] = {"status": "failed", "wire_id": wire_id,
                              "target_id": target_id, "error": str(exc)}
    if isinstance(exc, OperationError):
        result["typed_outputs"] = exc.failure_payload()
        receipt = exc.details.get("instruction_result") or {}
        result["replayed"] = bool(receipt.get("replayed"))
    return result


RESOLUTION_REQUIRED = "effect.resolution_required"
_HOLD_FIELDS = ("activation_key", "effect_id", "trigger_path_id", "instruction", "instruction_id", "receipt")


def resolution_hold(result: dict[str, Any], wire_id: str) -> dict[str, Any] | None:
    """What a held occurrence records when its Effect waits for ResolveEffectActivation."""
    for item in result.get("results") or []:
        if not isinstance(item, dict):
            continue
        outputs = item.get("typed_outputs")
        if (item.get("wire_id") == wire_id and item.get("status") == "failed"
                and isinstance(outputs, dict) and outputs.get("code") == RESOLUTION_REQUIRED):
            details = outputs.get("details") if isinstance(outputs.get("details"), dict) else {}
            return {"requires_resolution": True, "error": str(outputs.get("message") or "")[:1000],
                    **{name: str(details.get(name) or "") for name in _HOLD_FIELDS}}
    return None


def hold_for_resolution(lineage_id: str, result: dict[str, Any], wire_id: str) -> dict[str, Any] | None:
    """The hold of an occurrence no retry can settle, once people were asked to resolve it.

    Asking comes first: if it fails, the occurrence is retried as before and
    the next retry, reading the same receipt, asks again. It never raises,
    because the caller still has to finish the claim it holds.
    """
    try:
        hold = resolution_hold(result, wire_id)
        if hold is None:
            return None
        failure_notification_port.ask_resolution(lineage_id, wire_id, hold)
    except Exception:  # noqa: BLE001 - keep retrying until someone is told
        LOGGER.exception("Asking to resolve an Effect activation on %s wire %s failed", lineage_id, wire_id)
        return None
    return hold


def pending_event_delivery_error(result: dict[str, Any], wire_id: str) -> str:
    return next((str(item.get("error") or item.get("reason") or "Event delivery failed")
                 for item in result.get("results") or []
                 if item.get("wire_id") == wire_id and item.get("status") == "failed"), "")


@dataclass(frozen=True)
class DeliverySettlement:
    result: dict[str, Any]
    delivered: bool
    error: str
    hold: dict[str, Any] | None


def settle_delivery(
    lineage_id: str, wire_id: str, dispatch_token: str, result: dict[str, Any],
) -> DeliverySettlement:
    """Interpret and settle one attempt, regardless of who claimed it.

    A nested dispatch may return evidence but only the holder of the token
    settles it (including asking for resolution). Busy/paused outcomes keep
    their occurrence without being turned into failures.
    """
    outcome = result if "results" in result or result.get("duplicate") else {"results": [result]}
    delivered = pending_event_delivery_consumed(outcome, wire_id)
    error = pending_event_delivery_error(outcome, wire_id)
    if not delivered and not error and not outcome.get("paused") and not any(
        isinstance(item, dict) and item.get("wire_id") == wire_id for item in outcome.get("results") or []
    ):
        # A busy or closed target names its wire and is retried when it
        # changes. An attempt that says nothing about this wire is a failed
        # one: it waits out the retry delay instead of being claimed again at once.
        error = f"Event delivery returned no outcome for wire {wire_id}"
    for item in outcome.get("results") or []:
        if item.get("wire_id") == wire_id and item.get("status") == "failed":
            item["retry_pending"] = not delivered
    hold = None
    if dispatch_token:
        if not delivered:
            hold = hold_for_resolution(lineage_id, outcome, wire_id)
        lineage_program_runtime.finish_pending_event(
            lineage_id, wire_id, dispatch_token, delivered=delivered,
            error=error, terminal_status="failed" if error else "completed",
            retry_later=bool(error), hold=hold,
        )
    return DeliverySettlement(result, delivered, error, hold)


def deliver_and_settle(
    lineage_id: str, wire_id: str, target_id: str, dispatch_token: str,
    deliver: Callable[[], dict[str, Any]],
) -> DeliverySettlement:
    """Deliver outside the state transaction, then use the one settlement rule."""
    try:
        result = deliver()
    except Exception as exc:
        result = delivery_failure(wire_id, target_id, exc)
    return settle_delivery(lineage_id, wire_id, dispatch_token, result)


def dispatch_generic_routes(
    lineage_id: str,
    claimed_routes: list[dict[str, Any]],
    local_dispatch_tokens: dict[str, str],
    deliver: Callable[[dict[str, Any]], dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Deliver claimed generic routes and settle their pending-event leases."""
    results: list[dict[str, Any]] = []
    matched_count = 0
    for route in claimed_routes:
        wire_id = str(route.get("wire_id") or "")
        local_dispatch_token = local_dispatch_tokens.get(wire_id, "")
        settled = deliver_and_settle(
            lineage_id, wire_id, str(route.get("target_id") or ""),
            local_dispatch_token, lambda: deliver(route),
        )
        routed = settled.result
        results.append(routed)
        if str(routed.get("status") or "") not in {"discarded", "duplicate"}:
            matched_count += 1
    return results, matched_count


def block_unrecoverable_events(database: Any, lineage_id: str) -> int:
    """Retain original work and error evidence, but retire it from retry admission."""
    from . import program_work_storage
    from .lineage_recovery_policy import permanent_error
    from .lineage_primitives import now_iso
    blocked = 0
    with program_work_storage.transaction(database, lineage_id) as state:
        items = [*(p for w in state.get("event_wires", {}).values() for p in w.get("pending_events", [])),
                 *state.get("expired_deliveries", []),
                 *(p for o in state.get("operators", {}).values() for p in o.get("pending_deliveries", {}).values())]
        for item in items:
            if item.get("recovery_blocked"):
                continue
            error = permanent_error(item.get("event") or {})
            if error:
                lineage_program_runtime.hold_pending_occurrence(item, {"error": error, "requires_reissue": True}, now_iso())
                blocked += 1
    return blocked
