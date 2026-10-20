"""Packet Operator inputs, decisions, and completion fences.

The caller owns inbox claims, Lineage locks, Resident sends, and persistence.
This module owns the values that must mean the same thing at dispatch and at
the later Resident job completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import lineage_operators, lineage_primitives, lineage_program_queries


@dataclass(frozen=True)
class OperatorInputs:
    packets: list[dict[str, Any]]
    enveloped_packet_ids: set[str]
    source_titles: dict[str, str]


@dataclass(frozen=True)
class OperatorOutput:
    packets: list[dict[str, Any]]
    by_source_port: dict[str, list[dict[str, Any]]] | None = None


def inputs_from_claim(
    claim: dict[str, Any], nodes: dict[str, dict[str, Any]]
) -> OperatorInputs:
    ordered = [
        (claim.get("inputs") or {}).get(wire_id) or {}
        for wire_id in claim.get("expected_wire_ids") or []
    ]
    packets = [
        dict(packet)
        for item in ordered
        for packet in item.get("packets") or []
        if isinstance(packet, dict)
    ]
    enveloped_ids = {
        str(packet.get("id") or "")
        for item in ordered
        if bool(item.get("include_envelope", True))
        for packet in item.get("packets") or []
        if isinstance(packet, dict) and str(packet.get("id") or "")
    }
    source_resident_ids = {
        str(item.get("source_node_id") or "")
        for item in ordered
        if str(item.get("source_node_id") or "").startswith("resident_")
    }
    return OperatorInputs(
        packets=packets,
        enveloped_packet_ids=enveloped_ids,
        source_titles={
            resident_id: str((nodes.get(resident_id) or {}).get("title") or "")
            for resident_id in source_resident_ids
        },
    )


def completed_status(kind: str) -> str:
    return {"filter": "filtered", "selector": "routed", "reducer": "reduced"}[kind]


def working_status(kind: str) -> str:
    return {"filter": "filtering", "selector": "routing", "reducer": "reducing"}[kind]


def evaluate_rules(
    kind: str, config: dict[str, Any], inputs: list[dict[str, Any]]
) -> OperatorOutput:
    if kind == "filter":
        return OperatorOutput(lineage_operators.rule_filter_result(config, inputs))
    if kind == "selector":
        return OperatorOutput([], lineage_operators.rule_selector_result(config, inputs))
    if not inputs:
        return OperatorOutput([])
    return OperatorOutput([lineage_operators.rule_reducer_result(config, inputs).to_dict()])


def parse_resident_reply(
    kind: str,
    reply: str,
    inputs: list[dict[str, Any]],
    config: dict[str, Any],
) -> OperatorOutput:
    if kind == "filter":
        return OperatorOutput(lineage_operators.parse_filter_result(reply, inputs))
    if kind == "selector":
        return OperatorOutput(
            [], lineage_operators.parse_selector_result(reply, inputs, config.get("branches"))
        )
    output = lineage_operators.parse_reducer_result(reply)
    # A Resident may synthesize the message, but cannot rewrite observed facts.
    output["events"] = [event for packet in inputs for event in packet.get("events") or []]
    output["parent_packet_ids"] = [packet["id"] for packet in inputs if packet.get("id")]
    return OperatorOutput([lineage_primitives.normalize_packet(output).to_dict()])


def resident_prompt(
    lineage_id: str,
    operator_id: str,
    kind: str,
    operator_snapshot: dict[str, Any],
    event: lineage_primitives.LineageEvent,
    inputs: OperatorInputs,
    nodes: dict[str, dict[str, Any]],
    plan: dict[str, Any],
) -> str:
    shared = {
        "lineage_id": lineage_id,
        "event": event,
        "packets": inputs.packets,
        "enveloped_packet_ids": inputs.enveloped_packet_ids,
        "source_titles": inputs.source_titles,
    }
    if kind == "filter":
        return lineage_operators.filter_event_prompt(
            filter_node=operator_snapshot, **shared
        )
    if kind == "selector":
        return lineage_operators.selector_event_prompt(
            selector_node=operator_snapshot, **shared
        )
    endpoint_ids = lineage_program_queries._program_effect_resident_ids_for_operator(
        plan, operator_id
    )
    endpoint_nodes = [nodes[resident_id] for resident_id in endpoint_ids if resident_id in nodes]
    return lineage_operators.reducer_event_prompt(
        reducer_node=operator_snapshot,
        target_node=(endpoint_nodes[0] if endpoint_nodes else operator_snapshot),
        target_nodes=endpoint_nodes,
        **shared,
    )


def pending_delivery(
    operator_id: str,
    kind: str,
    executor_resident_id: str,
    expected_execution_id: str,
    event: lineage_primitives.LineageEvent,
    claim: dict[str, Any],
    inputs: OperatorInputs,
    outgoing_wire_ids: list[str],
) -> dict[str, Any]:
    return {
        "message_id": expected_execution_id,
        "operator_id": operator_id,
        "operator_kind": kind,
        "executor_resident_id": executor_resident_id,
        "event": event.to_dict(),
        "source_wire_ids": sorted(str(item) for item in claim.get("expected_wire_ids") or []),
        "source_results": claim.get("inputs") or {},
        "input_packets": inputs.packets,
        "output_wire_ids": list(outgoing_wire_ids),
        "program": True,
    }


def has_changed_output_topology(
    pending: dict[str, Any], operator: dict[str, Any]
) -> bool:
    current = list(operator.get("outgoing_packet_wire_ids") or [])
    frozen = [str(value) for value in pending.get("output_wire_ids") or [] if str(value)]
    return frozen != current
