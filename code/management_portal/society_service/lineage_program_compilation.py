"""Compiling a Program into its read-only execution plan, cached by content."""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Iterable

from .lineage_definition_contract import PROGRAM_SCHEMA
from .lineage_program import normalize_program
from .lineage_program_arguments import compiled_invocation
from .lineage_program_vocabulary import (
    LOOP_OUTPUT_PORTS, PACKET_OPERATOR_KINDS, _HANDOFF_ID_RE, authoring_component_config,
    authoring_event_type,
)


def _effect_endpoint_routes(
    effect_id: str,
    wires: list[dict[str, Any]],
) -> dict[str, dict[str, str]]:
    return {
        str(wire.get("source_port") or ""): {
            "wire_id": str(wire["id"]),
            "target_id": str(wire["target"]),
        }
        for wire in wires
        if wire["kind"] == "target" and wire["source"] == effect_id
    }


def _event_source_watch_projection(
    event_source_id: str,
    wires: list[dict[str, Any]],
    components: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Project the canonical Watch relation into the runtime event contract.

    Several Watch wires make ``subject_resident_ids`` or ``subject_effect_ids``;
    the Event Source fires for any one of the watched subjects.
    """

    watches = [
        wire
        for wire in wires
        if wire.get("kind") == "watch"
        and str(wire.get("source") or "") == event_source_id
    ]
    if not watches:
        return {}
    if len(watches) > 1:
        ordered = sorted(watches, key=lambda wire: str(wire.get("target") or ""))
        effect_watch = (components.get(str(ordered[0].get("target"))) or {}).get("kind") == "effect"
        return {
            ("subject_effect_ids" if effect_watch else "subject_resident_ids"): [str(wire.get("target") or "") for wire in ordered],
            **({"subject_effect_id": str(ordered[0].get("target"))} if effect_watch else {}),
            "watch_wire_id": str(ordered[0].get("id") or ""),
            "watch_wire_ids": [str(wire.get("id") or "") for wire in ordered],
        }
    watch = watches[0]
    subject_id = str(watch.get("target") or "")
    subject_component = components.get(subject_id) or {}
    if subject_id.startswith("resident_"):
        key = "subject_resident_id"
    elif subject_component.get("kind") == "effect":
        key = "subject_effect_id"
    elif subject_component.get("kind") == "event_source":
        key = "subject_event_source_id"
    elif _HANDOFF_ID_RE.fullmatch(subject_id):
        key = "subject_handoff_id"
    else:
        key = "subject_wire_id"
    return {
        key: subject_id,
        "watch_wire_id": str(watch.get("id") or ""),
    }


def loop_source_id(loop_id: str, port: str) -> str:
    """Dispatch source that carries a loop's continue or stop output."""

    return f"{loop_id}.{port}"


def event_source_id_for_wire(
    wire: dict[str, Any],
    components: dict[str, dict[str, Any]],
) -> str:
    """Return the dispatch source that feeds an Event wire."""

    source = str(wire.get("source") or "")
    if str((components.get(source) or {}).get("kind") or "") == "loop":
        return loop_source_id(source, str(wire.get("source_port") or "continue"))
    return source


# Plans by the content they were compiled from. Nearly every Program step
# compiles the definition it just read, and that definition rarely changes;
# compiling a large Lineage costs about ten times its fingerprint. The plan is
# shared, so callers must not change it; it is compiled from a private copy, so
# changing the inputs afterwards cannot reach it.
# Every live plan together took 8.2 MB on 2026-10-19 (130 Lineages); the limit
# leaves room for each Lineage's previous and current version.
_COMPILED_PLANS: OrderedDict[str, dict[str, Any]] = OrderedDict()
_COMPILED_PLAN_LIMIT = 512
_COMPILED_PLANS_LOCK = threading.Lock()
# All a plan reads from a node. Status, timestamps and the last execution
# change on every member run and must not make the plan miss.
_COMPILED_NODE_FIELDS = ("id", "operator_executor")


def compile_program(
    program: object,
    nodes: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """The read-only execution plan of a program and its Lineage nodes."""
    node_list = [
        {field: node[field] for field in _COMPILED_NODE_FIELDS if field in node}
        for node in nodes if isinstance(node, dict)
    ]
    key = hashlib.sha256(json.dumps(
        [program, node_list], sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()
    with _COMPILED_PLANS_LOCK:
        plan = _COMPILED_PLANS.get(key)
        if plan is not None:
            _COMPILED_PLANS.move_to_end(key)
            return plan
    plan = _compile_program(deepcopy(program), deepcopy(node_list))
    with _COMPILED_PLANS_LOCK:
        _COMPILED_PLANS[key] = plan
        while len(_COMPILED_PLANS) > _COMPILED_PLAN_LIMIT:
            _COMPILED_PLANS.popitem(last=False)
    return plan


def _incoming_wire_ids(
    wires: list[dict[str, Any]],
    target_id: str,
    kinds: set[str],
    port: str | None = None,
) -> list[str]:
    """The wires of ``kinds`` that enter ``target_id`` (at ``port`` when given), by ID."""

    return sorted(
        str(wire["id"])
        for wire in wires
        if wire["target"] == target_id
        and wire["kind"] in kinds
        and (port is None or str(wire.get("target_port") or "invoke") == port)
    )


def _compiled_effect(component: dict[str, Any], wires: list[dict[str, Any]]) -> dict[str, Any]:
    """An Effect's script, named endpoints and the Routes that invoke it."""

    component_id = str(component["id"])
    config = authoring_component_config(component)
    routes = _effect_endpoint_routes(component_id, wires)
    return {
        "effect_id": component_id,
        "title": str(component.get("title") or ""),
        "script": str(config.get("script") or ""),
        "endpoint_names": list(config.get("endpoints") or []),
        "endpoints": {name: route["target_id"] for name, route in routes.items()},
        "endpoint_routes": routes,
        "incoming_packet_wire_ids": _incoming_wire_ids(wires, component_id, {"route"}),
        "incoming_invocation_wire_ids": _incoming_wire_ids(wires, component_id, {"route"}),
    }


def _compiled_loop(component: dict[str, Any], wires: list[dict[str, Any]]) -> dict[str, Any]:
    """A Loop's round limit, its round and done inputs and its two output sources."""

    component_id = str(component["id"])
    config = authoring_component_config(component)
    return {
        "loop_id": component_id,
        "title": str(component.get("title") or ""),
        "max_rounds": int(config.get("max_rounds") or 1),
        "completion": str(config.get("completion") or "complete"),
        "round_wire_ids": _incoming_wire_ids(wires, component_id, {"route"}, "invoke"),
        "done_wire_ids": _incoming_wire_ids(wires, component_id, {"route"}, "done"),
        "continue_source_id": loop_source_id(component_id, "continue"),
        "stop_source_id": loop_source_id(component_id, "stop"),
    }


def _compiled_packet_operator(
    component: dict[str, Any],
    wires: list[dict[str, Any]],
    components_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """A Packet Operator's config, executor and Packet wires in and out, by output port."""

    operator_id = str(component["id"])
    config = deepcopy(authoring_component_config(component))
    outgoing = [wire for wire in wires if wire["kind"] == "route" and wire["source"] == operator_id]
    return {
        "operator_id": operator_id,
        "kind": str(component["kind"]),
        "title": str(component.get("title") or ""),
        "config": config,
        "resident_executor_id": str(config.get("resident_executor_id") or ""),
        "incoming_packet_wire_ids": _incoming_wire_ids(wires, operator_id, {"route"}),
        "outgoing_packet_wire_ids": sorted(str(wire["id"]) for wire in outgoing),
        "outgoing_packet_ports": {
            port: sorted(str(wire["id"]) for wire in outgoing if str(wire.get("source_port") or "") == port)
            for port in sorted({str(wire.get("source_port") or "") for wire in outgoing})
        },
        "effect_ids": sorted(
            {
                str(wire["target"])
                for wire in outgoing
                if (components_by_id.get(str(wire["target"])) or {}).get("kind") == "effect"
            }
        ),
    }


def _compiled_invoke_route(
    wire: dict[str, Any],
    residents: dict[str, dict[str, Any]],
    components_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """One Route: its ends, delivery settings and the Invocation arguments of its target."""

    return {
        "wire_id": str(wire["id"]),
        "source_kind": str(wire["kind"]),
        "source_id": str(wire["source"]),
        "target_id": str(wire["target"]),
        "target_port": str(wire.get("target_port") or "invoke"),
        "delivery": deepcopy(wire.get("config") or {}),
        "invocation": compiled_invocation(
            wire,
            residents=residents,
            components=components_by_id,
        ),
    }


def _route_entry(
    wire: dict[str, Any],
    source_port: str,
    components_by_id: dict[str, dict[str, Any]],
    invoke_routes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """One subscriber Route of a dispatch source, as the source fans out to it."""

    target_id = str(wire["target"])
    target_component = components_by_id.get(target_id)
    return {
        "wire_id": str(wire["id"]),
        "target_id": target_id,
        "target_kind": str(target_component.get("kind") or "") if target_component else "agent",
        "target_port": str(wire.get("target_port") or "invoke"),
        "config": deepcopy(wire.get("config") or {}),
        "invocation": deepcopy(invoke_routes[str(wire["id"])]["invocation"]),
        "source_port": source_port,
    }


def _route_summary(routes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """The subscribers of a dispatch source, by target kind."""

    return {
        "subscriber_resident_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "agent"),
        "subscriber_operator_ids": sorted(
            route["target_id"] for route in routes if route["target_kind"] in PACKET_OPERATOR_KINDS
        ),
        "subscriber_effect_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "effect"),
        "subscriber_loop_ids": sorted(route["target_id"] for route in routes if route["target_kind"] == "loop"),
    }


def _compiled_event_source(
    component: dict[str, Any],
    wires: list[dict[str, Any]],
    components_by_id: dict[str, dict[str, Any]],
    invoke_routes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """An Event Source's runtime config, Watch subject and subscriber Routes."""

    component_id = str(component["id"])
    event_type = authoring_event_type(component)
    runtime_config = deepcopy(authoring_component_config(component))
    runtime_config.pop("event_type", None)
    routes = [
        _route_entry(wire, "output", components_by_id, invoke_routes)
        for wire in wires
        if wire["kind"] == "route" and wire["source"] == component_id
    ]
    return {
        **{key: deepcopy(value) for key, value in component.items() if key != "config"},
        **runtime_config,
        **_event_source_watch_projection(component_id, wires, components_by_id),
        "component_id": component_id,
        "source_port": "output",
        "event_type": event_type,
        "subscriber_routes": routes,
        **_route_summary(routes),
    }


def _compiled_loop_sources(
    component: dict[str, Any],
    wires: list[dict[str, Any]],
    components_by_id: dict[str, dict[str, Any]],
    invoke_routes: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """A Loop's continue and stop outputs, each a dispatch source of its own."""

    component_id = str(component["id"])
    sources: dict[str, dict[str, Any]] = {}
    for port in sorted(LOOP_OUTPUT_PORTS):
        routes = [
            _route_entry(wire, port, components_by_id, invoke_routes)
            for wire in wires
            if wire["kind"] == "route" and wire["source"] == component_id
            and str(wire.get("source_port") or "continue") == port
        ]
        sources[loop_source_id(component_id, port)] = {
            "id": component_id,
            "kind": "loop",
            "title": str(component.get("title") or ""),
            "component_id": component_id,
            "source_port": port,
            "event_type": "",
            "max_fires": None,
            "subscriber_routes": routes,
            **_route_summary(routes),
        }
    return sources


def _compile_program(program: object, node_list: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = normalize_program(program, lineage_nodes=node_list)
    residents = {
        str(node.get("id") or ""): node for node in node_list
    }
    wires = normalized["wires"]
    components = normalized["components"]
    components_by_id = {
        str(component["id"]): component
        for component in components
    }
    effects = {
        str(component["id"]): _compiled_effect(component, wires)
        for component in components
        if component["kind"] == "effect"
    }
    loops = {
        str(component["id"]): _compiled_loop(component, wires)
        for component in components
        if component["kind"] == "loop"
    }
    packet_operators = {
        str(component["id"]): _compiled_packet_operator(component, wires, components_by_id)
        for component in components
        if component.get("kind") in PACKET_OPERATOR_KINDS
    }
    invoke_routes = {
        str(wire["id"]): _compiled_invoke_route(wire, residents, components_by_id)
        for wire in wires
        if wire["kind"] in {"route"}
    }
    # Event Sources and Loop outputs in component order: dispatch iterates them.
    event_sources: dict[str, dict[str, Any]] = {}
    for component in components:
        if component["kind"] == "event_source":
            event_sources[str(component["id"])] = _compiled_event_source(
                component, wires, components_by_id, invoke_routes,
            )
        elif component["kind"] == "loop":
            event_sources.update(_compiled_loop_sources(component, wires, components_by_id, invoke_routes))
    return {
        "schema": PROGRAM_SCHEMA,
        "program": normalized,
        "event_sources": event_sources,
        "packet_operators": packet_operators,
        "operator_executors": {
            operator["resident_executor_id"]: operator_id
            for operator_id, operator in packet_operators.items()
            if operator.get("resident_executor_id")
        },
        "effects": effects,
        "loops": loops,
        "invoke_routes": invoke_routes,
        "wires": wires,
    }
