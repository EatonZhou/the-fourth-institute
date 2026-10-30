"""Route packet fanout while preserving producer identity and delivery order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from . import lineage_primitives, lineage_program_arguments, lineage_program_vocabulary
from .lineage_values import _clone_json, _text


def input_prompt(packet: dict[str, Any], arguments: dict[str, Any], mapping: dict[str, Any], *, overridden: bool = False) -> str:
    """Compose a route's fixed task and message once, respecting explicit mapping."""
    def references_message(value):
        if isinstance(value, dict):
            return value.get("from") in {"packet.message.text", "event.payload.prompt"} or any(references_message(item) for item in value.values())
        return isinstance(value, list) and any(references_message(item) for item in value)

    authored = lineage_primitives.normalize_invocation_prompt(arguments.get("prompt"))
    if overridden or references_message(mapping.get("prompt")):
        return authored
    message = packet.get("message") or {}
    emitted = lineage_primitives.normalize_invocation_prompt(message.get("text"))
    return lineage_primitives.normalize_invocation_prompt("\n\n".join(part for part in (authored, emitted) if part))


@dataclass(frozen=True)
class PacketWireBatch:
    wire_id: str
    target_id: str
    target_kind: str
    target_node: dict[str, Any] | None
    config: dict[str, Any]
    packets: list[dict[str, Any]]
    target_port: str = "invoke"


def compiled_invocation(plan: dict[str, Any], wire_id: object) -> dict[str, Any]:
    route = (
        (plan.get("invoke_routes") or {}).get(str(wire_id or ""))
        if isinstance(plan.get("invoke_routes"), dict)
        else None
    )
    invocation = (
        route.get("invocation")
        if isinstance(route, dict) and isinstance(route.get("invocation"), dict)
        else {}
    )
    return {
        "profile_key": _text(invocation.get("profile_key"), 96),
        "arguments": _clone_json(
            invocation.get("arguments")
            if isinstance(invocation.get("arguments"), dict)
            else {}
        ),
    }


def resolved_invoke_arguments(
    plan: dict[str, Any],
    wire_id: object,
    event: lineage_primitives.LineageEvent,
    *,
    packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lineage_program_arguments.resolve_invocation_argument_mapping(
        compiled_invocation(plan, wire_id)["arguments"], event, packet=packet
    )


def packet_invoke_prompt(
    event: lineage_primitives.LineageEvent,
    *,
    source_node_id: str,
    source_resident_id: str,
    routed_by_node_id: str,
    prompt: str,
    include_envelope: bool,
    packet_id: str = "",
    packet_key: str = "",
    packet: dict[str, Any] | None = None,
) -> str:
    import json
    prompt = prompt or "请继续推进当前职责，并给出可复核结果。"
    if not include_envelope:
        return prompt
    source_label = "来源 Resident" if source_resident_id else "来源组件"
    source_identity = source_resident_id or source_node_id
    provenance_lines = [f"{source_label}：{source_identity}"]
    if routed_by_node_id and routed_by_node_id != source_node_id:
        provenance_lines.append(f"经由组件：{routed_by_node_id}")
    return lineage_primitives.normalize_invocation_prompt("\n\n".join(
        [
            "[HATG Packet]",
            (
                "\n".join(provenance_lines)
                + "\n"
                f"触发 Event：{event.type} ({event.id})\n"
                f"Packet：{packet_key or 'default'}"
                + (f" ({packet_id})" if packet_id else "")
            ),
            prompt,
            json.dumps({key: value for key, value in {
                "events": (packet or {}).get("events"),
                "data": ((packet or {}).get("message") or {}).get("data"),
            }.items() if value}, ensure_ascii=False) if packet and (packet.get("events") or (packet.get("message") or {}).get("data")) else "",
        ]
    ))


def packet_envelopes(
    wire: dict[str, Any],
    event: lineage_primitives.LineageEvent,
    source_node_id: str,
    packets: list[lineage_primitives.Packet | dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve producer identity when a Filter or Selector forwards a Packet."""
    wire_id = str(wire.get("id") or "")
    envelopes: list[dict[str, Any]] = []
    for index, packet in enumerate(packets):
        if (
            isinstance(packet, dict)
            and str(packet.get("id") or "")
            and str(packet.get("event_id") or "") == event.id
            and str(packet.get("from_node_id") or "")
            and str(packet.get("source_wire_id") or "")
        ):
            lineage_primitives.normalize_packet(
                {
                    key: packet[key]
                    for key in ("schema", "key", "message", "events", "priority", "parent_packet_ids")
                    if key in packet
                }
            )
            envelopes.append(dict(packet))
            continue
        envelopes.append(
            lineage_primitives.packet_envelope(
                packet,
                event_id=event.id,
                from_node_id=source_node_id,
                source_wire_id=wire_id,
                index=index,
            )
        )
    return envelopes


def iter_packet_batches(
    lineage: dict[str, Any],
    plan: dict[str, Any],
    source_node_id: str,
    event: lineage_primitives.LineageEvent,
    packets: list[lineage_primitives.Packet | dict[str, Any]],
    packet_wires: Iterable[dict[str, Any]],
    packets_by_source_port: dict[
        str, list[lineage_primitives.Packet | dict[str, Any]]
    ] | None,
) -> Iterator[PacketWireBatch]:
    """Yield one batch just before its side effects, never validate future wires early."""
    wires = [
        wire
        for wire in packet_wires
        if (
            isinstance(wire, dict)
            and str(wire.get("kind") or "") == "route"
            and str(wire.get("source") or "") == source_node_id
        )
    ]
    nodes = {
        str(node.get("id") or ""): node
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
    }
    components = {
        str(component.get("id") or ""): component
        for component in (plan.get("program") or {}).get("components") or []
        if isinstance(component, dict)
    }
    for wire in wires:
        wire_id = str(wire.get("id") or "")
        target_id = str(wire.get("target") or "")
        config = wire.get("config") if isinstance(wire.get("config"), dict) else {}
        wire_packets = (
            packets_by_source_port.get(str(wire.get("source_port") or ""), [])
            if packets_by_source_port is not None
            else packets
        )
        selected = packet_envelopes(wire, event, source_node_id, wire_packets)
        component = components.get(target_id)
        component_kind = str((component or {}).get("kind") or "")
        target_kind = (
            "operator" if component_kind in lineage_program_vocabulary.PACKET_OPERATOR_KINDS
            else "effect" if component_kind == "effect"
            else "loop" if component_kind == "loop"
            else "agent"
        )
        yield PacketWireBatch(
            wire_id, target_id, target_kind, nodes.get(target_id), config, selected,
            str(wire.get("target_port") or "invoke"),
        )
