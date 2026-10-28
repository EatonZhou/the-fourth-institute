"""Runtime details of Codex exec stream records.

``codex exec --json`` reports a turn as records named by their ``type``
(``thread.started``, ``turn.started``, ``item.completed``, ``turn.completed``)
that carry their fields at the top level, and a finished turn's token usage
in Portal's names.  ``RECORDS`` maps each projected record type to its
handler.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .provider_item_projection import ITEM_PHASES, project_item
from .runtime_details import EventContext, RuntimeDetails, mapping

Handler = Callable[[dict[str, Any], EventContext, RuntimeDetails], None]


def _item(phase: str) -> Handler:
    def project(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
        project_item(mapping(event.get("item")), phase, mapping(event.get("params")), context, details)

    return project


def _turn_started(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.turn_started(
        reason=str(event.get("turn_start_reason") or "initial"),
        thread_id=str(event.get("thread_id") or ""),
        turn_id=str(event.get("turn_id") or ""),
    ))


def _thread_started(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.thread_attached(
        thread_id=str(event.get("thread_id") or ""), turn_id=str(event.get("turn_id") or ""),
    ))


def _turn_completed(event: dict[str, Any], context: EventContext, details: RuntimeDetails) -> None:
    details.add(context.turn_ended(
        turn_id=str(event.get("turn_id") or ""),
        status=str(event.get("status", "completed")),
        usage=mapping(event.get("usage")),
        error=event.get("error"),
        interruption_reason=str(event.get("interruption_reason") or ""),
    ))


RECORDS: dict[str, Handler] = {
    **{f"item.{phase}": _item(phase) for phase in ITEM_PHASES},
    "turn.started": _turn_started,
    "thread.started": _thread_started,
    "turn.completed": _turn_completed,
}
