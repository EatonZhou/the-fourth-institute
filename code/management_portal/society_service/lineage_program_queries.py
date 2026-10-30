"""Queries over a compiled Lineage Program and its typed graph.

Changes to graph ownership and route discovery belong here.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from . import lineage_program_compilation


def _compiled_program(lineage: dict[str, Any]) -> dict[str, Any]:
    return lineage_program_compilation.compile_program(
        lineage.get("program"),
        lineage.get("nodes") or [],
    )


def _plan_packet_wires(plan: dict[str, Any], source_id: str) -> list[dict[str, Any]]:
    return [
        wire
        for wire in plan.get("wires") or []
        if (
            isinstance(wire, dict)
            and str(wire.get("kind") or "") == "route"
            and str(wire.get("source") or "") == source_id
        )
    ]


def _program_packet_wires(
    lineage: dict[str, Any],
    source_id: str,
) -> list[dict[str, Any]]:
    """Packet wires leaving one Resident or operator, instance ports resolved."""

    return _plan_packet_wires(_compiled_program(lineage), source_id)


def _program_operator_barriers(
    lineage: dict[str, Any],
    event_source_id: str,
    *, route_ids: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Freeze reachable operator inputs for this occurrence's active routes.

    Traverse operator branches, including branches that may emit no packets.
    Stop at Resident boundaries: their replies start independent occurrences.
    """

    plan = _compiled_program(lineage)
    wires_by_id = {
        str(wire.get("id") or ""): wire
        for wire in plan.get("wires") or []
        if isinstance(wire, dict)
    }
    operators = plan.get("packet_operators") or {}
    grouped: dict[str, set[str]] = {}
    frontier = [wires_by_id[wire_id] for wire_id in route_ids if wire_id in wires_by_id]
    expanded_operator_ids: set[str] = set()
    while frontier:
        wire = frontier.pop(0)
        operator_id = str(wire.get("target") or "")
        wire_id = str(wire.get("id") or "")
        if operator_id not in operators or not wire_id:
            continue
        grouped.setdefault(operator_id, set()).add(wire_id)
        if operator_id in expanded_operator_ids:
            continue
        expanded_operator_ids.add(operator_id)
        operator = operators.get(operator_id) or {}
        frontier.extend(
            wires_by_id[wire_id]
            for wire_id in operator.get("outgoing_packet_wire_ids") or []
            if wire_id in wires_by_id
        )
    return [
        {
            "event_source_id": event_source_id,
            "operator_id": operator_id,
            "expected_packet_wire_ids": sorted(packet_wire_ids),
        }
        for operator_id, packet_wire_ids in sorted(grouped.items())
    ]


def _program_runtime_resident_ids(
    lineage: dict[str, Any],
) -> set[str]:
    """Residents whose turns are owned by a Packet Operator they execute."""

    plan = _compiled_program(lineage)
    return {
        str(operator.get("resident_executor_id") or "")
        for operator in (plan.get("packet_operators") or {}).values()
        if (
            isinstance(operator, dict)
            and operator.get("incoming_packet_wire_ids")
            and operator.get("resident_executor_id")
        )
    }


def _program_effect_resident_ids(effect: dict[str, Any]) -> list[str]:
    """Residents an Effect's named endpoints point at, in endpoint order."""

    endpoints = effect.get("endpoints") if isinstance(effect.get("endpoints"), dict) else {}
    result: list[str] = []
    for name in effect.get("endpoint_names") or list(endpoints):
        resident_id = str(endpoints.get(name) or "")
        if resident_id.startswith("resident_") and resident_id not in result:
            result.append(resident_id)
    return result


def _program_effect_resident_ids_for_operator(
    plan: dict[str, Any],
    operator_id: str,
) -> list[str]:
    """Resident endpoints of the Effects one operator feeds directly."""

    operator = (plan.get("packet_operators") or {}).get(operator_id) or {}
    targets: list[str] = []
    for effect_id in operator.get("effect_ids") or []:
        effect = (plan.get("effects") or {}).get(effect_id) or {}
        for resident_id in _program_effect_resident_ids(effect):
            if resident_id not in targets:
                targets.append(resident_id)
    return targets
