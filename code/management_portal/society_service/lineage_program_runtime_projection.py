"""Project transient Program execution state for Lineage views."""
from __future__ import annotations

from typing import Any

from management_common.limit_contract import normalize_optional_limit
from . import (
    lineage_automation_identity, lineage_program_effect_results, lineage_program_occurrences,
    lineage_program_wire_queue, wire_delivery_budget,
)
from .lineage_values import _clone_json
from .lineage_program_queries import _compiled_program
from .lineage_program_contracts import _program_invoke_wire_execution_configs


def _program_invoke_wire_runtime_view(
    lineage: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project transient Event-wire backlog without making it graph state."""

    try:
        plan = _compiled_program(lineage)
        contracts = _program_invoke_wire_execution_configs(plan)
        wire_configs = {
            wire_id: dict(contract.get("config") or {})
            for wire_id, contract in contracts.items()
        }
        queues = lineage_program_wire_queue.event_wire_runtime_view(
            str(lineage.get("lineage_id") or ""),
            wire_configs,
        )
        budgets = wire_delivery_budget.views(str(lineage["lineage_id"]), wire_configs)
        return {wire_id: {**queue, **budgets[wire_id]} for wire_id, queue in queues.items()}
    except (OSError, ValueError):
        return {}


def event_source_runtime_view(
    lineage: dict[str, Any], source_configs: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Combine persisted lifecycle and current unattended authority once."""
    result = lineage_program_occurrences.event_source_runtime_view(
        str(lineage.get("lineage_id") or ""), source_configs,
    )
    automatic = [source_id for source_id, config in source_configs.items()
                 if config.get("event_type") in lineage_automation_identity.AUTOMATIC_EVENT_TYPES
                 and result[source_id]["state"] == "listening"]
    if automatic:
        error = lineage_automation_identity.readiness_error(lineage)
        if error:
            for source_id in automatic:
                result[source_id].update(state="blocked", error=error)
    return result


def event_source_diagnostics(runtime: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Use the source readiness projection for both public diagnostic views."""
    return [{"code": "event.automation_identity_unavailable", "item_id": source_id,
             "level": "error", "message": source["error"]}
            for source_id, source in runtime.items()
            if source.get("state") == "blocked" and source.get("error")]


def _program_event_source_runtime_view(
    lineage: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project Event Source fire budgets without making them graph state."""

    try:
        plan = _compiled_program(lineage)
        source_configs = {
            str(event_source_id): {
                "max_fires": normalize_optional_limit(
                    event_source.get("max_fires")
                ),
                "event_type": str(
                    event_source.get("event_type") or ""
                ),
                **{
                    key: _clone_json(event_source.get(key))
                    for key in (
                        "schedule_mode",
                        "scheduled_at",
                        "threshold_percent",
                        "duration_seconds",
                        "pressure_metric",
                        "queue_length_threshold",
                        "wait_seconds_threshold",
                        "key",
                        "operator",
                    )
                    if key in event_source
                },
            }
            for event_source_id, event_source in (
                plan.get("event_sources") or {}
            ).items()
            if isinstance(event_source, dict)
        }
        return event_source_runtime_view(lineage, source_configs)
    except (OSError, TypeError, ValueError):
        return {}


def _program_effect_runtime_view(
    lineage: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Project each Effect's replaceable current execution state."""

    try:
        plan = _compiled_program(lineage)
        return lineage_program_effect_results.effect_runtime_view(
            str(lineage.get("lineage_id") or ""),
            set((plan.get("effects") or {}).keys()),
        )
    except (OSError, TypeError, ValueError):
        return {}
