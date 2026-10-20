from __future__ import annotations

from management_common.format_tags import schema_matches

import copy
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from . import lineage_primitives, lineage_program_runtime
from .lineage_program_runtime import _item_id


OPERATOR_RUNTIME_SCHEMA = "tfi.lineage-operator-runtime.v1"
OPERATOR_RUNTIME_FIELDS = frozenset({
    "schema",
    "operator_id",
    "inbox",
    "pending_deliveries",
    "cancelled_event_ids",
})
MAX_CANCELLED_EVENT_IDS = 128
_OPERATOR_ID_RE = lineage_primitives.PACKET_SOURCE_NODE_ID_RE


class PacketOperatorEventCancelledError(RuntimeError):
    """The Event was fenced and must not be reconstructed as partial input."""


def _operator_id(value: object, *, field: str = "operator_id") -> str:
    item_id = str(value or "").strip().lower()
    if not _OPERATOR_ID_RE.fullmatch(item_id):
        raise ValueError(f"{field} must be a Program Operator or Resident id")
    return item_id


def _source_node_id(value: object, *, field: str) -> str:
    node_id = str(value or "").strip().lower()
    if not _OPERATOR_ID_RE.fullmatch(node_id):
        raise ValueError(f"{field} must be a Resident or Packet Operator id")
    return node_id


def empty_operator_runtime(operator_id: str) -> dict[str, Any]:
    clean_id = _operator_id(operator_id)
    return {
        "schema": OPERATOR_RUNTIME_SCHEMA,
        "operator_id": clean_id,
        "inbox": {},
        "pending_deliveries": {},
        "cancelled_event_ids": [],
    }


def _normalize_event(raw: object) -> dict[str, Any]:
    if not isinstance(raw, (dict, lineage_primitives.LineageEvent)):
        raise ValueError("operator Event must be an Event object")
    event = lineage_primitives.normalize_event(raw).to_dict()
    event["id"] = _item_id(event.get("id"), field="event.id")
    return event


def _normalize_packet(
    raw: object,
    *,
    event_id: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("operator input must contain Packet objects")
    item = copy.deepcopy(dict(raw))
    packet = lineage_primitives.normalize_packet(
        {
            key: item[key]
            for key in ("schema", "key", "message", "events", "priority", "parent_packet_ids")
            if key in item
        }
    )
    if str(item.get("event_id") or "") != event_id:
        raise ValueError("Packet Event identity does not match operator barrier")
    # Input records own the current hop; the packet retains its producer.
    source_wire_id = _item_id(item.get("source_wire_id"), field="packet.source_wire_id")
    source_node_id = _source_node_id(item.get("from_node_id"), field="packet.from_node_id")
    result = {
        **packet.to_dict(),
        "id": _item_id(item.get("id"), field="packet.id"),
        "event_id": event_id,
        "from_node_id": source_node_id,
        "source_wire_id": source_wire_id,
    }
    if source_node_id.startswith("resident_"):
        if str(item.get("from_resident_id") or "") != source_node_id:
            raise ValueError("Resident Packet is missing its Resident source identity")
        result["from_resident_id"] = source_node_id
    elif item.get("from_resident_id"):
        raise ValueError("Operator Packet cannot claim a Resident source identity")
    return result


def _normalize_inbox_item(
    raw: object,
    operator_id: str,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("operator inbox item must be a dict")
    item = copy.deepcopy(dict(raw))
    if _operator_id(item.get("operator_id") or operator_id) != operator_id:
        raise ValueError("operator inbox belongs to another component")
    event = _normalize_event(item.get("event"))
    expected = item.get("expected_wire_ids")
    if not isinstance(expected, list) or not expected:
        raise ValueError("operator expected_wire_ids must be a non-empty list")
    expected_ids = list(
        dict.fromkeys(
            _item_id(value, field="expected_wire_ids") for value in expected
        )
    )
    raw_inputs = item.get("inputs")
    if not isinstance(raw_inputs, Mapping):
        raise ValueError("operator inputs must be a dict")
    inputs: dict[str, dict[str, Any]] = {}
    for raw_wire_id, raw_input in raw_inputs.items():
        wire_id = _item_id(raw_wire_id, field="operator input wire")
        if wire_id not in expected_ids or not isinstance(raw_input, Mapping):
            raise ValueError("operator input is outside its immutable barrier")
        input_item = copy.deepcopy(dict(raw_input))
        source_node_id = _source_node_id(
            input_item.get("source_node_id"),
            field="source_node_id",
        )
        packets = input_item.get("packets")
        if not isinstance(packets, list):
            raise ValueError("operator input packets must be a list")
        normalized_packets = [
            _normalize_packet(
                packet,
                event_id=str(event["id"]),
            )
            for packet in packets
        ]
        packet_ids = [str(packet["id"]) for packet in normalized_packets]
        if len(packet_ids) != len(set(packet_ids)):
            raise ValueError("operator input Packet ids must be unique")
        state = str(input_item.get("state") or "continue").strip().lower()
        if state not in lineage_primitives.OPERATOR_INPUT_STATES:
            raise ValueError("operator input state is invalid")
        include_envelope = input_item.get("include_envelope", True)
        if not isinstance(include_envelope, bool):
            raise ValueError("include_envelope must be boolean")
        inputs[wire_id] = {
            "source_wire_id": wire_id,
            "source_node_id": source_node_id,
            "packets": normalized_packets,
            "state": state,
            "include_envelope": include_envelope,
        }
    return {
        "operator_id": operator_id,
        "event": event,
        "expected_wire_ids": expected_ids,
        "inputs": inputs,
    }


def _normalize_pending(raw: object, operator_id: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("pending operator invocation must be a dict")
    item = copy.deepcopy(dict(raw))
    if _operator_id(item.get("operator_id")) != operator_id:
        raise ValueError("pending invocation belongs to another operator")
    item["message_id"] = _item_id(item.get("message_id"), field="message_id")
    item["executor_resident_id"] = lineage_primitives.normalize_resident_id(
        item.get("executor_resident_id"),
        field_name="executor_resident_id",
    )
    item["event"] = _normalize_event(item.get("event"))
    source_wire_ids = item.get("source_wire_ids")
    if not isinstance(source_wire_ids, list) or not source_wire_ids:
        raise ValueError("source_wire_ids must be a non-empty list")
    item["source_wire_ids"] = list(
        dict.fromkeys(
            _item_id(value, field="source_wire_ids")
            for value in source_wire_ids
        )
    )
    return item


def _normalize_runtime(raw: object, operator_id: str) -> dict[str, Any]:
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise ValueError("operator runtime must be a dict")
    item = dict(raw)
    has_operator_payload = bool(OPERATOR_RUNTIME_FIELDS & set(item))
    if (
        has_operator_payload
        and not schema_matches(item.get("schema"), "tfi.lineage-operator-runtime.v1")
    ):
        raise ValueError("operator runtime schema 无效")
    if (
        has_operator_payload
        and _operator_id(item.get("operator_id")) != operator_id
    ):
        raise ValueError("operator runtime belongs to another component")
    runtime = empty_operator_runtime(operator_id)
    raw_inbox = item.get("inbox") or {}
    if not isinstance(raw_inbox, Mapping):
        raise ValueError("operator inbox must be a dict")
    for event_id, inbox in raw_inbox.items():
        clean_event_id = _item_id(event_id, field="operator inbox Event")
        normalized = _normalize_inbox_item(inbox, operator_id)
        if str(normalized["event"]["id"]) != clean_event_id:
            raise ValueError("operator inbox Event id mismatch")
        runtime["inbox"][clean_event_id] = normalized
    raw_pending = item.get("pending_deliveries") or {}
    if not isinstance(raw_pending, Mapping):
        raise ValueError("pending_deliveries must be a dict")
    for message_id, pending in raw_pending.items():
        clean_message_id = _item_id(message_id, field="pending Message")
        normalized = _normalize_pending(pending, operator_id)
        if normalized["message_id"] != clean_message_id:
            raise ValueError("pending operator Message id mismatch")
        runtime["pending_deliveries"][clean_message_id] = normalized
    raw_cancelled = item.get("cancelled_event_ids") or []
    if not isinstance(raw_cancelled, list):
        raise ValueError("cancelled_event_ids must be a list")
    runtime["cancelled_event_ids"] = list(
        dict.fromkeys(
            _item_id(value, field="cancelled Event") for value in raw_cancelled
        )
    )[-MAX_CANCELLED_EVENT_IDS:]
    return runtime


def read_operator_runtime(
    lineage_id: str,
    operator_id: str,
) -> dict[str, Any]:
    clean_id = _operator_id(operator_id)
    return _normalize_runtime(
        lineage_program_runtime.read_operator_state(lineage_id, clean_id),
        clean_id,
    )


@contextmanager
def _runtime_transaction(
    lineage_id: str,
    operator_id: str,
) -> Iterator[dict[str, Any]]:
    clean_id = _operator_id(operator_id)
    with lineage_program_runtime.operator_state_transaction(
        lineage_id, clean_id
    ) as stored:
        runtime = _normalize_runtime(stored, clean_id)
        yield runtime
        normalized = _normalize_runtime(runtime, clean_id)
        for field in OPERATOR_RUNTIME_FIELDS:
            stored.pop(field, None)
        stored.update(normalized)


def prepare_inbox(
    lineage_id: str,
    operator_id: str,
    *,
    event: Mapping[str, Any] | lineage_primitives.LineageEvent,
    expected_wire_ids: list[str],
) -> dict[str, Any]:
    clean_id = _operator_id(operator_id)
    normalized_event = _normalize_event(event)
    expected = list(
        dict.fromkeys(
            _item_id(value, field="expected_wire_ids")
            for value in expected_wire_ids
        )
    )
    if not expected:
        raise ValueError("expected_wire_ids must not be empty")
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        event_id = str(normalized_event["id"])
        if event_id in runtime["cancelled_event_ids"]:
            raise PacketOperatorEventCancelledError(
                "Packet Operator Event barrier was explicitly cancelled"
            )
        existing = runtime["inbox"].get(event_id)
        inbox = existing or {
            "operator_id": clean_id,
            "event": normalized_event,
            "expected_wire_ids": expected,
            "inputs": {},
        }
        if existing and (
            existing["event"] != normalized_event
            or set(existing["expected_wire_ids"]) != set(expected)
        ):
            raise ValueError("Event id identifies a different operator barrier")
        normalized = _normalize_inbox_item(inbox, clean_id)
        runtime["inbox"][event_id] = normalized
    return copy.deepcopy(normalized)


def update_inbox(
    lineage_id: str,
    operator_id: str,
    *,
    event: Mapping[str, Any] | lineage_primitives.LineageEvent,
    source_wire_id: str,
    source_node_id: str,
    packets: list[dict[str, Any]],
    state: str = "continue",
    include_envelope: bool = True,
) -> dict[str, Any] | None:
    clean_id = _operator_id(operator_id)
    normalized_event = _normalize_event(event)
    wire_id = _item_id(source_wire_id, field="source_wire_id")
    source_id = _source_node_id(source_node_id, field="source_node_id")
    if not isinstance(packets, list):
        raise ValueError("packets must be a list")
    if not isinstance(include_envelope, bool):
        raise ValueError("include_envelope must be boolean")
    clean_state = str(state or "continue").strip().lower()
    if clean_state not in lineage_primitives.OPERATOR_INPUT_STATES:
        raise ValueError("operator input state is invalid")
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        event_id = str(normalized_event["id"])
        inbox = runtime["inbox"].get(event_id)
        if inbox is None:
            if event_id in runtime["cancelled_event_ids"]:
                return None
            raise ValueError("operator input requires a prepared barrier")
        if wire_id not in set(inbox.get("expected_wire_ids") or []):
            raise ValueError("source wire is outside the operator barrier")
        inbox["inputs"][wire_id] = {
            "source_wire_id": wire_id,
            "source_node_id": source_id,
            "packets": copy.deepcopy(packets),
            "state": clean_state,
            "include_envelope": include_envelope,
        }
        normalized = _normalize_inbox_item(inbox, clean_id)
        runtime["inbox"][event_id] = normalized
    return copy.deepcopy(normalized)


def pop_inbox(
    lineage_id: str,
    operator_id: str,
    event_id: str,
) -> dict[str, Any] | None:
    clean_id = _operator_id(operator_id)
    clean_event_id = _item_id(event_id, field="event_id")
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        removed = runtime["inbox"].pop(clean_event_id, None)
    return copy.deepcopy(removed)


def update_pending_delivery(
    lineage_id: str,
    operator_id: str,
    invocation: Mapping[str, Any],
) -> dict[str, Any]:
    clean_id = _operator_id(operator_id)
    normalized = _normalize_pending(invocation, clean_id)
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        runtime["pending_deliveries"][normalized["message_id"]] = normalized
    return copy.deepcopy(normalized)


def pop_pending_delivery(
    lineage_id: str,
    operator_id: str,
    message_id: str,
) -> dict[str, Any] | None:
    clean_id = _operator_id(operator_id)
    clean_message_id = _item_id(message_id, field="message_id")
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        removed = runtime["pending_deliveries"].pop(clean_message_id, None)
    return copy.deepcopy(removed)


def cancel_event(
    lineage_id: str,
    operator_id: str,
    event_id: str,
) -> dict[str, list[str]]:
    clean_id = _operator_id(operator_id)
    clean_event_id = _item_id(event_id, field="event_id")
    removed_Messages: list[str] = []
    removed_inbox: list[str] = []
    with _runtime_transaction(lineage_id, clean_id) as runtime:
        if runtime["inbox"].pop(clean_event_id, None) is not None:
            removed_inbox.append(clean_event_id)
        for message_id, pending in list(runtime["pending_deliveries"].items()):
            if str((pending.get("event") or {}).get("id") or "") == clean_event_id:
                runtime["pending_deliveries"].pop(message_id, None)
                removed_Messages.append(str(message_id))
        runtime["cancelled_event_ids"] = [
            value
            for value in runtime["cancelled_event_ids"]
            if value != clean_event_id
        ]
        runtime["cancelled_event_ids"].append(clean_event_id)
        runtime["cancelled_event_ids"] = runtime["cancelled_event_ids"][
            -MAX_CANCELLED_EVENT_IDS:
        ]
    return {
        "removed_inbox_event_ids": removed_inbox,
        "removed_operator_message_ids": sorted(removed_Messages),
    }


def clear_operator_runtime(
    lineage_id: str,
    operator_id: str,
) -> None:
    clean_id = _operator_id(operator_id)
    with lineage_program_runtime.operator_state_transaction(
        lineage_id, clean_id
    ) as runtime:
        for field in OPERATOR_RUNTIME_FIELDS:
            runtime.pop(field, None)
