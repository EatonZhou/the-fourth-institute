"""Event deliveries waiting on a wire: staging, deferral, claims, settlement, retries and
expiry, and the queue view of each wire.
"""

from __future__ import annotations

import secrets
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

from . import lineage_primitives, lineage_program_runtime, lineage_recurring_schedule, trigger_paths
from .lineage_program_occurrences import _subscriber_cursor, _subscriber_occurrence_claimed
from .lineage_program_runtime import (
    MAX_EVENT_CURSORS, _iso_now, _item_id, _normalize_pending_delivery, _utc_now,
)


MAX_PENDING_EVENTS_PER_WIRE = 100
PENDING_EVENT_DISPATCH_LEASE_SECONDS = 360


def retry_delay_seconds(attempt_count: int) -> int:
    """Wait before retrying a failed delivery: 5 s, doubling to at most 60 s."""
    return min(60, 5 * 2 ** min(4, max(1, attempt_count) - 1))


def next_retry_seconds(lineage_id: str) -> int:
    """Seconds until the earliest waiting retry of this Lineage's queued Events; 0 when none waits."""
    now = _utc_now()
    due = [
        moment for wire in (lineage_program_runtime.read_runtime(lineage_id).get("event_wires") or {}).values()
        for item in wire.get("pending_events") or [] if not item.get("recovery_blocked")
        for moment in [lineage_recurring_schedule.parse_utc(item.get("retry_at"))]
        if moment is not None and moment > now
    ]
    return max(1, int((min(due) - now).total_seconds() + 0.999)) if due else 0


def _prune_expired_pending_events(
    runtime: dict[str, Any],
    *,
    lineage_id: str,
    now: datetime | None = None,
    wire_id: str = "",
) -> int:
    """Move expired queued deliveries to the expiry notices.

    A delivery of one wire prunes only that wire: other wires stay unlocked,
    and the backlog drain prunes every wire.
    """
    current = now or _utc_now()
    removed = 0
    wires = runtime.get("event_wires", {})
    selected = (
        [(wire_id, wires[wire_id])] if wire_id and wire_id in wires
        else [] if wire_id else list(wires.items())
    )
    for wire_id, wire in selected:
        if not isinstance(wire, dict):
            runtime["event_wires"].pop(wire_id, None)
            continue
        retained: list[dict[str, Any]] = []
        for pending in wire.get("pending_events") or []:
            if pending.get("recovery_blocked"):
                retained.append(pending)
                continue
            expires_at = lineage_recurring_schedule.parse_utc(
                pending.get("expires_at")
                if isinstance(pending, dict)
                else ""
            )
            if expires_at is not None and expires_at <= current:
                removed += 1
                if isinstance(pending, dict):
                    expired = {
                        "event_source_id": str(
                            pending.get("event_source_id") or ""
                        ),
                        "wire_id": wire_id,
                        "cursor": str(pending.get("cursor") or ""),
                        "event": deepcopy(pending.get("event") or {}),
                        "expired_at": _iso_now(),
                    }
                    runtime.setdefault("expired_deliveries", []).append(expired)
                    trigger_paths.delivery_settled(lineage_id, wire_id, pending, status="failed")
                continue
            retained.append(pending)
        if retained:
            wire["pending_events"] = retained
        else:
            runtime["event_wires"].pop(wire_id, None)
    return removed


def drain_expired_deliveries(
    lineage_id: str,
) -> list[dict[str, Any]]:
    """Claim TTL expirations not yet notified; their records stay in trigger_paths."""

    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        _prune_expired_pending_events(runtime, lineage_id=lineage_id)
        expired = [p for p in runtime.get("expired_deliveries") or [] if not p.get("recovery_blocked")]
        runtime["expired_deliveries"] = [p for p in runtime.get("expired_deliveries") or [] if p.get("recovery_blocked")]
        return deepcopy(expired)


def defer_event_for_wire(
    lineage_id: str,
    event_source_id: str,
    wire_id: str,
    cursor: str,
    event: dict[str, Any],
    *,
    policy: str,
    queue_limit: int = 10,
    expires_after_seconds: int = 0,
    subscriber_id: str = "",
    subscriber_generation: int | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Consume or retain one occurrence when this wire cannot deliver now.

    Claims and queue mutation share one database transaction. The queue stores
    only the Event envelope and delivery cursor; it never snapshots a Resident
    context or Workspace.
    """

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id or clean_wire_id,
        field="event subscriber",
    )
    clean_policy = str(policy or "").strip().lower() or "drop"
    if clean_policy not in {"drop", "latest", "queue"}:
        raise ValueError("event unavailable policy is invalid")
    clean_event = deepcopy(event) if isinstance(event, dict) else {}
    _item_id(clean_event.get("id"), field="pending event id")
    _item_id(clean_event.get("type"), field="pending event type")
    limit = max(1, min(MAX_PENDING_EVENTS_PER_WIRE, int(queue_limit or 10)))
    ttl = max(0, int(expires_after_seconds or 0))
    claim_generation = (
        max(0, int(subscriber_generation))
        if subscriber_generation is not None
        else None
    )
    claim_token = _subscriber_cursor(
        clean_cursor,
        clean_subscriber_id,
        claim_generation or 0,
    )
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        # The source is locked before the wire (program_work_storage.LOCK_ORDER).
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=clean_wire_id)
        cursors = list(source.get("consumed_cursors") or [])
        if _subscriber_occurrence_claimed(
            cursors,
            clean_cursor,
            clean_subscriber_id,
            claim_generation,
        ):
            return {
                "status": "duplicate",
                "wire_id": clean_wire_id,
                "pending_count": len(
                    (
                        runtime.get("event_wires", {})
                        .get(clean_wire_id, {})
                        .get("pending_events", [])
                    )
                ),
            }
        cursors.append(claim_token)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
        if clean_policy == "drop":
            return {
                "status": "dropped",
                "wire_id": clean_wire_id,
                "pending_count": 0,
            }

        now = _utc_now()
        expires_at = (
            (now + timedelta(seconds=ttl))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
            if ttl
            else ""
        )
        pending = {
            "event_source_id": source_id,
            "cursor": clean_cursor,
            "subscriber_id": clean_subscriber_id,
            "claim_token": claim_token,
            "event": clean_event,
            "queued_at": (
                now.isoformat(timespec="seconds").replace("+00:00", "Z")
            ),
            "expires_at": expires_at,
        }
        if isinstance(invocation, dict):
            pending["invocation"] = _normalize_pending_delivery(invocation)
        wire = runtime["event_wires"].setdefault(
            clean_wire_id,
            {"pending_events": []},
        )
        pending_events = list(wire.get("pending_events") or [])
        replaced_count = 0
        if clean_policy == "latest":
            replaced_count = len(pending_events)
            for previous in pending_events:
                trigger_paths.delivery_settled(lineage_id, clean_wire_id, previous, status="skipped")
            pending_events = [pending]
            status = "replaced" if replaced_count else "queued"
        elif len(pending_events) >= limit:
            return {
                "status": "queue_full",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
                "queue_limit": limit,
            }
        else:
            pending_events.append(pending)
            status = "queued"
        wire["pending_events"] = pending_events
        trigger_paths.delivery_waiting(lineage_id, clean_wire_id, pending)
        return {
            "status": status,
            "wire_id": clean_wire_id,
            "pending_count": len(pending_events),
            "replaced_count": replaced_count,
            "queue_limit": 1 if clean_policy == "latest" else limit,
            "expires_at": expires_at,
        }


def stage_event_delivery(
    lineage_id: str,
    event_source_id: str,
    wire_id: str,
    cursor: str,
    event: dict[str, Any],
    *,
    subscriber_id: str = "",
    subscriber_generation: int | None = None,
    invocation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a wire delivery intent before invoking its downstream."""

    source_id = _item_id(event_source_id, field="event_source_id")
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_cursor = _item_id(cursor, field="event cursor")
    clean_subscriber_id = _item_id(
        subscriber_id or clean_wire_id,
        field="event subscriber",
    )
    clean_event = deepcopy(event) if isinstance(event, dict) else {}
    _item_id(clean_event.get("id"), field="pending event id")
    _item_id(clean_event.get("type"), field="pending event type")
    generation = (
        max(0, int(subscriber_generation))
        if subscriber_generation is not None
        else 0
    )
    claim_token = _subscriber_cursor(
        clean_cursor,
        clean_subscriber_id,
        generation,
    )
    clean_invocation: dict[str, Any] | None = None
    if isinstance(invocation, dict):
        clean_invocation = _normalize_pending_delivery(invocation)

    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(
            source_id,
            {"consumed_cursors": []},
        )
        wire = runtime["event_wires"].setdefault(
            clean_wire_id,
            {"pending_events": []},
        )
        pending_events = list(wire.get("pending_events") or [])
        if any(
            str(item.get("claim_token") or "") == claim_token
            for item in pending_events
        ):
            return {
                "status": "pending",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
            }

        cursors = list(source.get("consumed_cursors") or [])
        if _subscriber_occurrence_claimed(
            cursors,
            clean_cursor,
            clean_subscriber_id,
            subscriber_generation,
        ):
            if not pending_events:
                runtime["event_wires"].pop(clean_wire_id, None)
            return {
                "status": "delivered",
                "wire_id": clean_wire_id,
                "pending_count": len(pending_events),
            }

        cursors.append(claim_token)
        source["consumed_cursors"] = cursors[-MAX_EVENT_CURSORS:]
        now = _iso_now()
        pending: dict[str, Any] = {
            "event_source_id": source_id,
            "cursor": clean_cursor,
            "subscriber_id": clean_subscriber_id,
            "claim_token": claim_token,
            "event": clean_event,
            "queued_at": now,
            "expires_at": "",
            "attempt_count": 0,
        }
        if clean_invocation is not None:
            pending["invocation"] = clean_invocation
        pending_events.append(pending)
        wire["pending_events"] = pending_events
        trigger_paths.delivery_waiting(lineage_id, clean_wire_id, pending)
        return {
            "status": "staged",
            "wire_id": clean_wire_id,
            "pending_count": len(pending_events),
            "queued_at": now,
        }


def claim_pending_event(
    lineage_id: str,
    wire_id: str,
    *,
    cursor: str = "",
    event_source_id: str = "",
    target_id: str = "",
) -> dict[str, Any] | None:
    """Claim the FIFO head of one wire for a single delivery attempt.

    A parallel wire names the occurrence instead: it is claimed even while
    earlier ones are still being delivered.

    A paused component returns an admission fact without a dispatch token;
    callers can report the pause without claiming or incrementing attempts.
    """

    clean_wire_id = _item_id(wire_id, field="event wire id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        # Source -> wire -> target is the Program work lock order. Both live
        # and replay admission supply their compiled source/target identities.
        if event_source_id and runtime.get("event_sources", {}).get(event_source_id, {}).get("paused"):
            return {"status": "paused", "component_id": event_source_id}
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=clean_wire_id)
        wire = runtime.get("event_wires", {}).get(clean_wire_id)
        if not isinstance(wire, dict):
            return None
        pending_events = [p for p in wire.get("pending_events") or [] if not p.get("recovery_blocked")]
        if not pending_events:
            return None
        pending = (
            next((item for item in pending_events if str(item.get("cursor") or "") == cursor), None)
            if cursor else pending_events[0]
        )
        if pending is None:
            return None
        if event_source_id and str(pending.get("event_source_id") or "") != event_source_id:
            return None
        if target_id and runtime.get("operators", {}).get(target_id, {}).get("paused"):
            return {"status": "paused", "component_id": target_id}
        retry_at = lineage_recurring_schedule.parse_utc(pending.get("retry_at"))
        if retry_at is not None and retry_at > _utc_now():
            # A failed attempt waits out its delay however often the Lineage is reconciled.
            return None
        if str(pending.get("dispatch_token") or ""):
            started_at = lineage_recurring_schedule.parse_utc(pending.get("dispatch_started_at"))
            if (
                started_at is not None
                and _utc_now() - started_at
                < timedelta(
                    seconds=PENDING_EVENT_DISPATCH_LEASE_SECONDS
                )
            ):
                return None
            pending.pop("dispatch_token", None)
            pending.pop("dispatch_started_at", None)
        dispatch_token = f"dispatch_{secrets.token_hex(12)}"
        pending["dispatch_token"] = dispatch_token
        pending["dispatch_started_at"] = _iso_now()
        pending["last_attempt_at"] = pending["dispatch_started_at"]
        pending["attempt_count"] = max(
            0,
            int(pending.get("attempt_count") or 0),
        ) + 1
        return deepcopy(pending)


def hold_pending_occurrence(item: dict[str, Any], hold: dict[str, Any], at: str) -> None:
    """Release a delivery lease into a durable hold; never admit it as a retry."""
    item.pop("dispatch_token", None)
    item.pop("dispatch_started_at", None)
    item.pop("retry_at", None)
    item["recovery_blocked"] = {**deepcopy(hold), "at": at}


def finish_pending_event(
    lineage_id: str,
    wire_id: str,
    dispatch_token: str,
    *,
    delivered: bool,
    error: str = "",
    terminal_status: str = "completed",
    retry_later: bool = False,
    hold: dict[str, Any] | None = None,
) -> bool:
    """Settle one claimed delivery; an undelivered one stays queued.

    ``retry_later`` marks a failed attempt: the next one waits out
    ``retry_delay_seconds``. A target that is closed or busy is retried as
    soon as it changes. ``hold`` keeps an occurrence that no retry can
    settle out of retries until ResolveEffectActivation ends it; the wire
    delivers its later occurrences meanwhile.
    """
    clean_wire_id = _item_id(wire_id, field="event wire id")
    clean_dispatch_token = _item_id(
        dispatch_token,
        field="pending event dispatch token",
    )
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        wire = runtime.get("event_wires", {}).get(clean_wire_id)
        if not isinstance(wire, dict):
            return False
        pending_events = list(wire.get("pending_events") or [])
        matched = next(
            (
                item
                for item in pending_events
                if str(item.get("dispatch_token") or "")
                == clean_dispatch_token
            ),
            None,
        )
        if matched is None:
            return False
        if delivered:
            trigger_paths.delivery_settled(lineage_id, clean_wire_id, matched, status=terminal_status, error=error)
            wire["pending_events"] = [
                item
                for item in pending_events
                if item is not matched
            ]
        else:
            matched.pop("dispatch_token", None)
            matched.pop("dispatch_started_at", None)
            diagnostic = str(error or "").strip()
            if diagnostic:
                matched["last_error"] = diagnostic[:1000]
            matched.pop("retry_at", None)
            if hold:
                hold_pending_occurrence(matched, hold, _iso_now())
            elif retry_later:
                delay = retry_delay_seconds(int(matched.get("attempt_count") or 1))
                matched["retry_at"] = (_utc_now() + timedelta(seconds=delay)).isoformat(
                    timespec="seconds").replace("+00:00", "Z")
        if not wire.get("pending_events"):
            runtime["event_wires"].pop(clean_wire_id, None)
    return True


def settle_effect_delivery(lineage_id: str, activation: dict[str, Any]) -> None:
    """Release only the queued occurrence terminated by an admin disposition."""
    wire_id = str(activation.get("source_wire_id") or "")
    event_id = str(activation.get("event_id") or "")
    if not wire_id or not event_id:
        return
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        wire = runtime["event_wires"].get(wire_id)
        if wire is None:
            return
        wire["pending_events"] = [item for item in wire.get("pending_events", [])
                                  if str((item.get("event") or {}).get("id") or "") != event_id]
        if not wire["pending_events"]:
            runtime["event_wires"].pop(wire_id, None)


def pending_wire_deliveries(
    lineage_id: str,
    wire_id: str,
) -> list[dict[str, Any]]:
    wire_id = _item_id(wire_id, field="event wire id")
    runtime = lineage_program_runtime.read_runtime(lineage_id)
    now = _utc_now()
    result: list[dict[str, Any]] = []
    pending_events = (runtime["event_wires"].get(wire_id) or {}).get(
        "pending_events", [],
    )
    for pending in pending_events:
        expires_at = lineage_recurring_schedule.parse_utc(pending.get("expires_at"))
        if expires_at is not None and expires_at <= now and not pending.get("recovery_blocked"):
            continue
        result.append(
            {
                "delivery_id": pending["claim_token"],
                "revision": pending["revision"],
                "event_id": pending["event"]["id"],
                "event_type": pending["event"]["type"],
                "queued_at": pending["queued_at"],
                "expires_at": pending["expires_at"],
                "status": (
                    "blocked" if pending.get("recovery_blocked") else "dispatching" if pending.get("dispatch_token") else "waiting"
                ),
                "recovery_blocked": deepcopy(pending.get("recovery_blocked")),
                "prompt_override": pending.get("prompt_override"),
            }
        )
    return result


def update_pending_wire_delivery(
    lineage_id: str,
    wire_id: str,
    delivery_id: str,
    *,
    prompt: str = "",
    delete: bool = False,
) -> dict[str, Any]:
    """Mutate one unclaimed delivery without changing its consumed cursor."""

    wire_id = _item_id(wire_id, field="event wire id")
    delivery_id = _item_id(delivery_id, field="pending invocation id")
    if not isinstance(prompt, str):
        raise ValueError("投递 prompt 必须是字符串")
    prompt = lineage_primitives.normalize_invocation_prompt(prompt)
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        _prune_expired_pending_events(runtime, lineage_id=lineage_id, wire_id=wire_id)
        wire = runtime["event_wires"].get(wire_id) or {}
        pending_events = wire.get("pending_events") or []
        pending = next(
            (item for item in pending_events if item["claim_token"] == delivery_id),
            None,
        )
        if pending is None:
            raise KeyError("这条 投递 已投递、过期或被替换，请刷新队列")
        if pending.get("dispatch_token"):
            raise ValueError("这条 投递 正在投递，不能编辑或删除")
        if delete:
            from . import trigger_paths
            trigger_paths.delivery_settled(lineage_id, wire_id, pending, status="cancelled")
            wire["pending_events"] = [
                item for item in pending_events if item is not pending
            ]
            if not wire["pending_events"]:
                runtime["event_wires"].pop(wire_id, None)
        else:
            if prompt.strip():
                pending["prompt_override"] = prompt
            else:
                pending.pop("prompt_override", None)
            pending["revision"] = int(pending.get("revision") or 0) + 1
        return {
            "delivery_id": delivery_id,
            "status": "deleted" if delete else "updated",
        }


def forget_event_wire(
    lineage_id: str,
    wire_id: str,
) -> None:
    clean_wire_id = _item_id(wire_id, field="event wire id")
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        wire = runtime["event_wires"].get(clean_wire_id) or {}
        from . import trigger_paths
        for pending in wire.get("pending_events", []):
            trigger_paths.delivery_settled(lineage_id, clean_wire_id, pending, status="cancelled")
        runtime["event_wires"].pop(clean_wire_id, None)


def event_wire_runtime_view(
    lineage_id: str,
    wire_configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return the small live queue projection used by the HATG edge UI."""

    runtime = lineage_program_runtime.read_runtime(lineage_id)
    states = runtime.get("event_wires") or {}
    result: dict[str, dict[str, Any]] = {}
    for raw_wire_id, raw_config in wire_configs.items():
        wire_id = _item_id(raw_wire_id, field="event wire id")
        config = raw_config if isinstance(raw_config, dict) else {}
        pending = list(
            (states.get(wire_id) or {}).get("pending_events") or []
        )
        now = _utc_now()
        pending = [
            item
            for item in pending
            if not item.get("recovery_blocked") and (
                lineage_recurring_schedule.parse_utc(item.get("expires_at")) is None
                or lineage_recurring_schedule.parse_utc(item.get("expires_at")) > now
            )
        ]
        result[wire_id] = {
            "unavailable_policy": str(
                config.get("unavailable_policy") or "drop"
            ),
            "queue_limit": int(config.get("queue_limit") or 10),
            "expires_after_seconds": int(
                config.get("expires_after_seconds") or 0
            ),
            "pending_count": len(pending),
            "dispatching_count": sum(
                1
                for item in pending
                if str(item.get("dispatch_token") or "")
            ),
            "oldest_queued_at": str(
                (pending[0] if pending else {}).get("queued_at") or ""
            ),
            "oldest_cursor": str(
                (pending[0] if pending else {}).get("cursor") or ""
            ),
            "oldest_event_source_id": str(
                (pending[0] if pending else {}).get("event_source_id") or ""
            ),
            "attempt_count": sum(
                max(0, int(item.get("attempt_count") or 0))
                for item in pending
            ),
            "last_attempt_at": max(
                (
                    str(item.get("last_attempt_at") or "")
                    for item in pending
                ),
                default="",
            ),
            "last_error": next(
                (
                    str(item.get("last_error") or "")
                    for item in reversed(pending)
                    if str(item.get("last_error") or "")
                ),
                "",
            ),
            "next_expires_at": min(
                (
                    str(item.get("expires_at") or "")
                    for item in pending
                    if str(item.get("expires_at") or "")
                ),
                default="",
            ),
        }
    return result
