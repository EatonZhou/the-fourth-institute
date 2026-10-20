from __future__ import annotations

from management_common.format_tags import schema_matches

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import lineage_primitive_definitions
from .lineage_values import _text

EVENT_TYPES = frozenset(
    lineage_primitive_definitions.type_definitions("event")
)
RUNTIME_EVENT_TYPES = EVENT_TYPES | {"timer.fired"}
OPERATOR_INPUT_STATES = {"continue", "stop"}
PACKET_SCHEMA = "tfi.hatg-packet.v1"
RESIDENT_ID_RE = re.compile(r"^resident_[0-9a-f]{12}$")

MAX_EVENT_PAYLOAD_TEXT = 2_000
MAX_INVOCATION_ARGUMENT_BYTES = 64_000
MAX_PROMPT_CHARS = 16_000
MAX_INVOCATION_PROMPT_CHARS = 1_000_000
MAX_PACKETS = 24
MAX_PACKET_KEY_CHARS = 64
MAX_PACKET_PRIORITY = 100
MAX_PACKET_FILTER_CONDITIONS = 16
MAX_SELECTOR_BRANCHES = 12
MAX_SELECTOR_BRANCH_LABEL_CHARS = 80
MAX_SELECTOR_BRANCH_DESCRIPTION_CHARS = 1000
PACKET_FILTER_OPERATORS = {
    "exists",
    "missing",
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "greater_than",
    "greater_or_equal",
    "less_than",
    "less_or_equal",
}
PACKET_FILTER_MODES = {"all", "any"}
PACKET_FILTER_ORDERINGS = {"input", "priority_desc"}

_PACKET_KEY_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SELECTOR_BRANCH_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SELECTOR_BRANCH_PORT_RE = re.compile(
    r"^branch:([a-z][a-z0-9_-]{0,31})$"
)
_PACKET_FILTER_PATH_RE = re.compile(
    r"^packet\.(?:id|key|priority|from_node_id|from_resident_id|source_wire_id|event_id|message|events|parent_packet_ids)"
    r"(?:\.(?:[a-z][a-z0-9_]{0,79}|[0-9]+))*$"
)
PACKET_SOURCE_NODE_ID_RE = re.compile(
    r"^(?:resident_[0-9a-f]{12}|(?:filter|selector|reducer|event_source|loop)_"
    r"[a-z0-9][a-z0-9_-]{2,95})$"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00",
        "Z",
    )


def _boolean(value: object, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value, 16).lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def normalize_resident_id(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> str:
    resident_id = _text(value, 80).lower()
    if not resident_id and allow_empty:
        return ""
    if not RESIDENT_ID_RE.fullmatch(resident_id):
        raise ValueError(f"{field_name} 必须是规范的 resident_* ID")
    return resident_id


def normalize_workspace_relative_path(
    value: object,
    *,
    required: bool = True,
    max_chars: int = 800,
) -> str:
    """Normalize one path that must stay inside a Resident Workspace.

    Validate the authored path before any filesystem-style normalization so
    absolute paths, drive paths, duplicate separators, and traversal segments
    cannot be silently rewritten into a different relative path.
    """

    raw = _text(value, max_chars).replace("\\", "/")
    if not raw and not required:
        return ""
    parts = raw.split("/")
    candidate = PurePosixPath(raw)
    if (
        not raw
        or raw.startswith("/")
        or re.match(r"^[a-zA-Z]:", raw)
        or candidate.is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError("文件路径必须是目标 Workspace 内的相对路径")
    return candidate.as_posix()


def resolve_workspace_relative_path(
    workspace_root: str | Path,
    value: object,
) -> Path:
    """Resolve a relative locator without allowing symlink escape."""

    relative_path = normalize_workspace_relative_path(value)
    root = Path(workspace_root).resolve(strict=False)
    candidate = (root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "文件路径越出了目标 Workspace"
        ) from exc
    return candidate


def _event_id(
    event_type: str,
    subject_resident_id: str,
    correlation: str,
) -> str:
    material = "\0".join((event_type, subject_resident_id, correlation))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"event_{digest}"


@dataclass(frozen=True)
class LineageEvent:
    """A transient fact routed by the HATG program.

    The payload only locates the live source. It never contains a reply,
    Workspace copy, file body, or observation snapshot.
    """

    id: str
    type: str
    subject_resident_id: str = ""
    occurred_at: str = field(default_factory=now_iso)
    payload: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Packet:
    """The transport value shared by every Route: message, facts, or both.

    The delivery correlation is attached by packet_envelope. It is independent
    of the facts carried here, so a Resident output remains message-only.
    """

    key: str
    message: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    priority: int = 0
    parent_packet_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PACKET_SCHEMA,
            **asdict(self),
        }


@dataclass(frozen=True)
class EffectInvocation:
    """One activation of an Effect.

    ``arguments`` carries the values the Invoke wire supplied for this call;
    the Effect's own argument table and endpoints stay on its definition.
    Route packets lower to this contract; activation retains execution context.
    """

    id: str
    activation: LineageEvent
    arguments: dict[str, Any] = field(default_factory=dict)
    source_kind: str = "packet"
    source_id: str = ""
    source_wire_id: str = ""
    from_resident_id: str = ""
    include_envelope: bool = True
    packet: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "activation": self.activation.to_dict(),
            "arguments": json.loads(
                json.dumps(self.arguments, ensure_ascii=False)
            ),
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "source_wire_id": self.source_wire_id,
            "from_resident_id": self.from_resident_id,
            "include_envelope": self.include_envelope,
            "packet": deepcopy(self.packet),
        }


def make_event(
    event_type: str,
    *,
    subject_resident_id: str = "",
    occurred_at: str = "",
    payload: dict[str, Any] | None = None,
    correlation: str = "",
    identity: dict[str, Any] | None = None,
) -> LineageEvent:
    normalized_type = _text(event_type, 80).lower()
    if normalized_type not in RUNTIME_EVENT_TYPES:
        raise ValueError(f"未知的 Lineage Event：{normalized_type or '（空）'}")
    subject = normalize_resident_id(
        subject_resident_id,
        field_name="Event subject_resident_id",
        allow_empty=True,
    )
    clean_payload = normalize_event_payload(payload)
    correlation_value = (
        _text(correlation, 500)
        or _text(clean_payload.get("message_id"), 180)
        or _text(clean_payload.get("execution_id"), 180)
        or _text(clean_payload.get("path"), 800)
        or _text(clean_payload.get("scheduled_at"), 80)
        or now_iso()
    )
    return LineageEvent(
        id=_event_id(normalized_type, subject, correlation_value),
        type=normalized_type,
        subject_resident_id=subject,
        occurred_at=_text(occurred_at, 80) or now_iso(),
        payload=clean_payload,
        identity=_operation_identity(identity),
    )


def normalize_event(raw: object) -> LineageEvent:
    if isinstance(raw, LineageEvent):
        _operation_identity(raw.identity)
        if raw.type not in RUNTIME_EVENT_TYPES:
            raise ValueError(f"未知的 Lineage Event：{raw.type}")
        normalize_resident_id(
            raw.subject_resident_id,
            field_name="Event subject_resident_id",
            allow_empty=True,
        )
        return raw
    item = raw if isinstance(raw, dict) else {}
    event_type = _text(item.get("type"), 80).lower()
    if event_type not in RUNTIME_EVENT_TYPES:
        raise ValueError(f"未知的 Lineage Event：{event_type or '（空）'}")
    subject = normalize_resident_id(
        item.get("subject_resident_id"),
        field_name="Event subject_resident_id",
        allow_empty=True,
    )
    payload = normalize_event_payload(item.get("payload"))
    event_id = _text(item.get("id"), 80)
    if not event_id:
        return make_event(
            event_type,
            subject_resident_id=subject,
            occurred_at=_text(item.get("occurred_at"), 80),
            payload=payload,
            identity=_operation_identity(item.get("identity")),
        )
    return LineageEvent(
        id=event_id,
        type=event_type,
        subject_resident_id=subject,
        occurred_at=_text(item.get("occurred_at"), 80) or now_iso(),
        payload=payload,
        identity=_operation_identity(item.get("identity")),
    )


def _operation_identity(raw: object) -> dict[str, Any]:
    from management_common.principal import OperationIdentity

    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("operation identity must be an object")
    return OperationIdentity.from_dict(raw).to_dict()


def normalize_invocation_prompt(raw: object) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError("Invocation prompt 必须是字符串")
    prompt = raw.strip()
    if len(prompt) > MAX_INVOCATION_PROMPT_CHARS:
        raise ValueError(
            f"Invocation prompt 不能超过 {MAX_INVOCATION_PROMPT_CHARS} 字符"
        )
    return prompt


def normalize_event_payload(raw: object) -> dict[str, Any]:
    item = raw if isinstance(raw, dict) else {}
    result: dict[str, Any] = {}
    if "cause" in item:
        from .trigger_path_storage import CAUSE_KINDS
        cause = item["cause"]
        if not isinstance(cause, dict) or set(cause) != {"kind", "id"} or cause["kind"] not in CAUSE_KINDS or not isinstance(cause["id"], str) or not cause["id"].strip():
            raise ValueError("Event cause requires a known kind and occurrence ID")
        result["cause"] = {"kind": cause["kind"], "id": cause["id"].strip()}
    prompt = normalize_invocation_prompt(item.get("prompt"))
    if prompt:
        result["prompt"] = prompt
    if "content_parts" in item:
        result["content_parts"] = normalize_invocation_content_parts(item["content_parts"])
    for key in (
        "edge_id",
        "message_id",
        "execution_id",
        "input_business_ref",
        "resident_id",
        "account_id",
        "provider",
        "path",
        "scheduled_at",
        "observed_at",
        "schedule_mode",
        "file_signature",
        "triggered_by",
        "state_domain",
        "previous_state",
        "state",
        "heat_level",
        "resident_scope",
        "input_state",
        "started_at",
        "oldest_queued_at",
        "pressure_metric",
        "key",
        "operator",
        "name",
        "effect_id",
        "instruction",
        "source_port",
        "instruction_id",
        "script_run_id",
        "handoff_id",
        "status",
        "loop_id",
        "source_event_id",
        "invocation_id",
        "event_source_id",
        "wire_id",
        "expired_at",
        "error_code",
        "diagnostic",
        "route_path",
        "trigger_path_id",
        "trigger_step_id",
        "author_id",
        "member_resident_id",
        "member_message_id",
        "quoted_member_id",
        "commit",
        "completed_at",
        "publication_id",
        "previous_commit",
        "source",
    ):
        value = _text(item.get(key), MAX_EVENT_PAYLOAD_TEXT)
        if value:
            result[key] = value
    for key in (
        "resident_count",
        "previous_running_count",
        "running_count",
        "fire_count",
        "max_fires",
        "generation",
        "missed_count",
        "failure_count",
        "runtime_event_depth",
        "context_tokens",
        "model_context_window",
        "threshold_seconds",
        "duration_seconds",
        "pressure_threshold",
        "pressure_value",
        "queue_count",
        "oldest_wait_seconds",
        "state_revision",
        "previous_revision",
        "revision",
        "round",
        "max_rounds",
        "relay_depth",
        "sequence",
    ):
        try:
            value = int(item.get(key))
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 2_147_483_647:
            result[key] = value
    for key in (
        "threshold_percent",
        "remaining_percent",
        "previous_used_percent",
        "used_percent",
    ):
        raw_value = item.get(key)
        if raw_value is None:
            if key == "previous_used_percent" and key in item:
                result[key] = None
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0 <= value <= 100_000:
            result[key] = round(value, 1)
    for key in ("previous_matched", "matched"):
        if isinstance(item.get(key), bool):
            result[key] = bool(item[key])
    wire_ids = item.get("wire_ids")
    if isinstance(wire_ids, list):
        normalized_wire_ids = list(
            dict.fromkeys(
                _text(value, 110).lower()
                for value in wire_ids[:64]
                if _text(value, 110)
            )
        )
        if normalized_wire_ids:
            result["wire_ids"] = normalized_wire_ids
    resident_ids = item.get("resident_ids")
    if isinstance(resident_ids, list):
        normalized_resident_ids = list(
            dict.fromkeys(
                normalize_resident_id(
                    value,
                    field_name="Event resident_ids item",
                )
                for value in resident_ids[:64]
            )
        )
        if normalized_resident_ids:
            result["resident_ids"] = normalized_resident_ids
    diagnostics = item.get("diagnostics")
    if isinstance(diagnostics, list):
        normalized_diagnostics = [
            _text(value, 500)
            for value in diagnostics[:20]
            if _text(value, 500)
        ]
        if normalized_diagnostics:
            result["diagnostics"] = normalized_diagnostics
    if isinstance(item.get("retryable"), bool):
        result["retryable"] = bool(item["retryable"])
    if "script_run_id" in result:
        exit_code = item.get("exit_code")
        if exit_code is None or type(exit_code) is int:
            result["exit_code"] = exit_code
        duration = item.get("duration_seconds")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
            result["duration_seconds"] = duration
    if isinstance(item.get("outputs"), dict):
        # Effect outputs are the called instruction's declared result fields.
        result["outputs"] = normalize_argument_object(item.get("outputs"), max_bytes=65536 if "script_run_id" in result else MAX_INVOCATION_ARGUMENT_BYTES)
    if isinstance(item.get("arguments"), dict):
        result["arguments"] = normalize_invocation_arguments(
            item.get("arguments")
        )
    if isinstance(item.get("data"), dict):
        # Caller-supplied structured payload of an emitted Event.
        result["data"] = normalize_argument_object(item.get("data"))
    return result


def normalize_invocation_arguments(raw: object) -> dict[str, Any]:
    """Runtime text has its own budget; all other arguments share the JSON budget."""
    if not isinstance(raw, dict) or "prompt" not in raw:
        return normalize_argument_object(raw)
    prompt = normalize_invocation_prompt(raw["prompt"])
    result = normalize_argument_object({key: value for key, value in raw.items() if key != "prompt"})
    result["prompt"] = prompt
    return result


def normalize_argument_object(raw: object, *, max_bytes: int = MAX_INVOCATION_ARGUMENT_BYTES) -> dict[str, Any]:
    """Bound structured arguments and authored mapping definitions without exemptions."""

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("Effect Invocation arguments 必须是 JSON object")
    try:
        encoded = json.dumps(
            raw,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Effect Invocation arguments 必须可以编码为 JSON"
        ) from exc
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError(
            "Effect Invocation arguments 不能超过 "
            f"{max_bytes} bytes"
        )
    return json.loads(encoded)


def normalize_invocation_content_parts(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or len(raw) > 32:
        raise ValueError("Invocation content_parts 必须是最多 32 项的数组")
    for part in raw:
        if not isinstance(part, dict) or part.get("type") not in {"image", "file", "context_reference"}:
            raise ValueError("Invocation content_parts 只接受图像、文件与上下文引用；文本使用 prompt")
    return normalize_invocation_arguments({"content_parts": raw})["content_parts"]


def make_effect_invocation(
    activation: LineageEvent | dict[str, Any],
    *,
    arguments: dict[str, Any] | None = None,
    invocation_id: object = "",
    source_kind: object = "packet",
    source_id: object = "",
    source_wire_id: object = "",
    from_resident_id: object = "",
    include_envelope: object = True,
    packet: dict[str, Any] | None = None,
) -> EffectInvocation:
    """Build the sole runtime input accepted by an Effect."""

    event = normalize_event(activation)
    normalized_source_kind = _text(source_kind, 40).lower() or "packet"
    normalized_source_id = _text(source_id, 110).lower()
    normalized_wire_id = _text(source_wire_id, 110).lower()
    normalized_from = normalize_resident_id(
        from_resident_id,
        field_name="Effect Invocation from_resident_id",
        allow_empty=True,
    )
    normalized_arguments = normalize_invocation_arguments(arguments)
    normalized_id = _text(invocation_id, 240)
    if not normalized_id:
        material = "\0".join(
            (
                event.id,
                normalized_source_kind,
                normalized_source_id,
                normalized_wire_id,
                normalized_from,
                str((packet or {}).get("id") or ""),
            )
        )
        normalized_id = "invocation_" + hashlib.sha256(
            material.encode("utf-8")
        ).hexdigest()[:16]
    return EffectInvocation(
        id=normalized_id,
        activation=event,
        arguments=normalized_arguments,
        source_kind=normalized_source_kind,
        source_id=normalized_source_id,
        source_wire_id=normalized_wire_id,
        from_resident_id=normalized_from,
        include_envelope=_boolean(include_envelope, True),
        packet=deepcopy(packet) if isinstance(packet, dict) else {},
    )


def normalize_packet(raw: object) -> Packet:
    """Normalize one Packet without adding routing or source identity."""

    item = raw.to_dict() if isinstance(raw, Packet) else raw
    if not isinstance(item, dict):
        raise ValueError("Packet 必须是 JSON object")
    unknown = sorted(
        set(item) - {"schema", "key", "message", "events", "priority", "parent_packet_ids"}
    )
    if unknown:
        raise ValueError("Packet 不接受字段：" + "、".join(unknown))
    schema = _text(item.get("schema"), 80)
    if schema and not schema_matches(schema, "tfi.hatg-packet.v1"):
        raise ValueError("Packet schema 无效")
    key = _text(item.get("key") or "default", MAX_PACKET_KEY_CHARS).lower()
    if not _PACKET_KEY_RE.fullmatch(key):
        raise ValueError(
            "Packet key 必须以小写字母开头，且只包含小写字母、数字、_ 或 -"
        )
    raw_message = item.get("message")
    message = None
    if raw_message is not None:
        if not isinstance(raw_message, dict) or set(raw_message) - {"text", "data", "content_parts"}:
            raise ValueError("Packet message 只接受 text、data、content_parts")
        message = {}
        if "text" in raw_message:
            message["text"] = normalize_invocation_prompt(raw_message["text"])
        if "data" in raw_message:
            message["data"] = normalize_argument_object(raw_message["data"])
        if "content_parts" in raw_message:
            message["content_parts"] = normalize_invocation_content_parts(raw_message["content_parts"])
        if not any(message.values()):
            raise ValueError("Packet message 不能为空")
    raw_events = item.get("events") or []
    if not isinstance(raw_events, list) or len(raw_events) > 256:
        raise ValueError("Packet events 必须是最多 256 项的数组")
    events = []
    seen_events = {}
    for raw_event in raw_events:
        event = normalize_event(raw_event).to_dict()
        if event["id"] in seen_events and seen_events[event["id"]] != event:
            raise ValueError("同一个 Event ID 不能携带不同事实")
        if event["id"] not in seen_events:
            events.append(event)
            seen_events[event["id"]] = event
    if message is None and not events:
        raise ValueError("Packet 至少包含消息或事件")
    parents = item.get("parent_packet_ids") or []
    if not isinstance(parents, list) or len(parents) > 256 or any(not isinstance(p, str) or not p for p in parents):
        raise ValueError("Packet parent_packet_ids 必须是非空 ID 数组")
    try:
        priority = int(item.get("priority") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("Packet priority 必须是整数") from exc
    priority = max(
        -MAX_PACKET_PRIORITY,
        min(MAX_PACKET_PRIORITY, priority),
    )
    return Packet(
        key=key,
        message=message,
        events=events,
        priority=priority,
        parent_packet_ids=list(dict.fromkeys(parents)),
    )


def make_packet(
    key: object,
    text: object = None,
    *,
    data: dict[str, Any] | None = None,
    content_parts: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    parent_packet_ids: list[str] | None = None,
    priority: int = 0,
) -> Packet:
    message = {}
    if text:
        message["text"] = text
    if data:
        message["data"] = data
    if content_parts:
        message["content_parts"] = content_parts
    return normalize_packet(
        {
            "key": key,
            "message": message or None,
            "events": events or [],
            "parent_packet_ids": parent_packet_ids or [],
            "priority": priority,
        }
    )


def event_packet(event: LineageEvent | dict[str, Any]) -> Packet:
    """Extract optional message content while retaining the originating fact."""
    fact = normalize_event(event)
    return make_packet(
        "event", fact.payload.get("prompt"), data=fact.payload.get("data"),
        content_parts=fact.payload.get("content_parts"), events=[fact.to_dict()],
    )


def packet_prompt(packet: Packet | dict[str, Any]) -> str:
    """Render an input for text consumers; forwarding retains the typed packet."""
    item = packet.to_dict() if isinstance(packet, Packet) else packet
    message = item.get("message") or {}
    sections = [normalize_invocation_prompt(message.get("text"))]
    if message.get("data"):
        sections.append(json.dumps(message["data"], ensure_ascii=False))
    if item.get("events"):
        sections.append(json.dumps({"events": item["events"]}, ensure_ascii=False))
    if not any(sections) and message.get("content_parts"):
        sections.append("请处理所附内容。")
    return normalize_invocation_prompt("\n\n".join(part for part in sections if part))


def packet_envelope(
    packet: Packet | dict[str, Any],
    *,
    event_id: object,
    from_node_id: object = "",
    from_resident_id: object = "",
    source_wire_id: object,
    index: int = 0,
) -> dict[str, Any]:
    """Attach deterministic graph-routing identity to one Packet."""

    normalized = normalize_packet(
        {
            key: packet[key]
            for key in ("schema", "key", "message", "events", "priority", "parent_packet_ids")
            if isinstance(packet, dict) and key in packet
        }
        if isinstance(packet, dict)
        else packet
    )
    if isinstance(packet, dict) and packet.get("id"):
        normalized = normalize_packet({**normalized.to_dict(), "parent_packet_ids": [*normalized.parent_packet_ids, packet["id"]]})
    clean_event_id = _text(event_id, 240)
    clean_node_id = _text(from_node_id or from_resident_id, 110).lower()
    if not PACKET_SOURCE_NODE_ID_RE.fullmatch(clean_node_id):
        raise ValueError(
            "Packet from_node_id 必须是 Resident 或包生产组件"
        )
    clean_resident_id = (
        normalize_resident_id(
            from_resident_id or clean_node_id,
            field_name="Packet from_resident_id",
        )
        if clean_node_id.startswith("resident_")
        else ""
    )
    if from_resident_id and clean_resident_id != clean_node_id:
        raise ValueError("Packet Resident 来源必须与 from_node_id 一致")
    clean_wire_id = _text(source_wire_id, 240)
    if not clean_event_id or not clean_wire_id:
        raise ValueError("Packet envelope 缺少 Event 或 source wire identity")
    material = "\0".join(
        (
            clean_event_id,
            clean_node_id,
            clean_wire_id,
            normalized.key,
            str(max(0, int(index))),
        )
    )
    packet_id = "packet_" + hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()[:16]
    result = {
        **normalized.to_dict(),
        "id": packet_id,
        "event_id": clean_event_id,
        "from_node_id": clean_node_id,
        "source_wire_id": clean_wire_id,
    }
    if clean_resident_id:
        result["from_resident_id"] = clean_resident_id
    return result


def normalize_packet_filter(raw: object) -> dict[str, Any]:
    """Normalize the shared, bounded Packet selection language."""

    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("Packet filter 必须是 JSON object")
    unknown = sorted(
        set(raw) - {"mode", "conditions", "order_by", "limit"}
    )
    if unknown:
        raise ValueError("Packet filter 不接受字段：" + "、".join(unknown))
    mode = _text(raw.get("mode"), 16).lower() or "all"
    if mode not in PACKET_FILTER_MODES:
        raise ValueError("Packet filter mode 必须是 all 或 any")
    raw_conditions = raw.get("conditions") or []
    if not isinstance(raw_conditions, list):
        raise ValueError("Packet filter conditions 必须是数组")
    if len(raw_conditions) > MAX_PACKET_FILTER_CONDITIONS:
        raise ValueError(
            "Packet filter 最多包含 "
            f"{MAX_PACKET_FILTER_CONDITIONS} 个条件"
        )
    conditions: list[dict[str, Any]] = []
    for raw_condition in raw_conditions:
        if not isinstance(raw_condition, dict):
            raise ValueError("Packet filter condition 必须是 JSON object")
        condition_unknown = sorted(
            set(raw_condition) - {"from", "operator", "value"}
        )
        if condition_unknown:
            raise ValueError(
                "Packet filter condition 不接受字段："
                + "、".join(condition_unknown)
            )
        source = _text(raw_condition.get("from"), 240).lstrip("$")
        if not _PACKET_FILTER_PATH_RE.fullmatch(source):
            raise ValueError("Packet filter from 不是受支持的 Packet 字段")
        operator = _text(raw_condition.get("operator"), 32).lower()
        if operator not in PACKET_FILTER_OPERATORS:
            raise ValueError("Packet filter operator 无效")
        condition: dict[str, Any] = {
            "from": source,
            "operator": operator,
        }
        if operator not in {"exists", "missing"}:
            if "value" not in raw_condition:
                raise ValueError("Packet filter condition 缺少 value")
            condition["value"] = deepcopy(raw_condition["value"])
        conditions.append(condition)
    order_by = _text(raw.get("order_by"), 24).lower() or "input"
    if order_by not in PACKET_FILTER_ORDERINGS:
        raise ValueError(
            "Packet filter order_by 必须是 input 或 priority_desc"
        )
    limit: int | None = None
    if raw.get("limit") not in (None, ""):
        try:
            limit = int(raw["limit"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Packet filter limit 必须是正整数或 null") from exc
        if not 1 <= limit <= MAX_PACKETS:
            raise ValueError(
                f"Packet filter limit 必须在 1 到 {MAX_PACKETS} 之间"
            )
    return {
        "mode": mode,
        "conditions": conditions,
        "order_by": order_by,
        "limit": limit,
    }


def selector_branch_port(branch_id: object) -> str:
    """Return the canonical typed output port for one Selector branch."""

    clean_id = _text(branch_id, 32).lower()
    if not _SELECTOR_BRANCH_ID_RE.fullmatch(clean_id):
        raise ValueError("Selector branch id 无效")
    return f"branch:{clean_id}"


def selector_branch_id_from_port(port_id: object) -> str:
    """Resolve a canonical Selector branch port without guessing aliases."""

    match = _SELECTOR_BRANCH_PORT_RE.fullmatch(_text(port_id, 80).lower())
    return match.group(1) if match else ""


def normalize_selector_branches(raw: object) -> list[dict[str, Any]]:
    """Normalize the authoritative single-input, multi-output contract.

    Each branch owns its matching rule.  Packet wires only transport the
    branch result and never carry selection behavior themselves.
    """

    if not isinstance(raw, list):
        raise ValueError("Selector branches 必须是数组")
    if not 2 <= len(raw) <= MAX_SELECTOR_BRANCHES:
        raise ValueError(
            "Selector 必须包含 2 到 "
            f"{MAX_SELECTOR_BRANCHES} 个输出分支"
        )
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_branch in raw:
        if not isinstance(raw_branch, dict):
            raise ValueError("Selector branch 必须是 JSON object")
        unknown = sorted(
            set(raw_branch) - {"id", "label", "description", "match"}
        )
        if unknown:
            raise ValueError(
                "Selector branch 不接受字段：" + "、".join(unknown)
            )
        branch_id = _text(raw_branch.get("id"), 32).lower()
        if not _SELECTOR_BRANCH_ID_RE.fullmatch(branch_id):
            raise ValueError("Selector branch id 无效")
        if branch_id in seen_ids:
            raise ValueError("Selector branch id 不能重复")
        seen_ids.add(branch_id)
        label = _text(
            raw_branch.get("label"),
            MAX_SELECTOR_BRANCH_LABEL_CHARS,
        ) or branch_id
        normalized.append(
            {
                "id": branch_id,
                "label": label,
                "description": _text(
                    raw_branch.get("description"),
                    MAX_SELECTOR_BRANCH_DESCRIPTION_CHARS,
                ),
                "match": normalize_packet_filter(
                    raw_branch.get("match")
                ),
            }
        )
    return normalized


def route_packets_to_selector_branches(
    packets: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    branches: object,
) -> dict[str, list[dict[str, Any]]]:
    """Apply every branch rule and preserve Packet content and identity."""

    return {
        selector_branch_port(branch["id"]): select_packets(
            packets,
            branch["match"],
        )
        for branch in normalize_selector_branches(branches)
    }


def _packet_filter_value(
    packet: Mapping[str, Any],
    source: str,
) -> tuple[bool, object]:
    root: object = packet
    for part in source.split(".")[1:]:
        if isinstance(root, list) and part.isdigit():
            if int(part) >= len(root):
                return False, None
            root = root[int(part)]
            continue
        if not isinstance(root, Mapping) or part not in root:
            return False, None
        root = root[part]
    return True, root


def _packet_condition_matches(
    packet: Mapping[str, Any],
    condition: Mapping[str, Any],
) -> bool:
    existed, value = _packet_filter_value(
        packet,
        str(condition.get("from") or ""),
    )
    operator = str(condition.get("operator") or "")
    if operator == "exists":
        return existed
    if operator == "missing":
        return not existed
    if not existed:
        return False
    expected = condition.get("value")
    if operator == "equals":
        return value == expected
    if operator == "not_equals":
        return value != expected
    if operator in {"contains", "not_contains"}:
        if isinstance(value, str):
            contained = isinstance(expected, str) and expected in value
        elif isinstance(value, dict):
            contained = isinstance(expected, (str, int, float, bool)) and (
                expected in value
            )
        elif isinstance(value, (list, tuple, set)):
            contained = any(item == expected for item in value)
        else:
            contained = False
        return contained if operator == "contains" else not contained
    numeric_types = (int, float)
    if (
        isinstance(value, bool)
        or isinstance(expected, bool)
        or not isinstance(value, numeric_types)
        or not isinstance(expected, numeric_types)
        or not math.isfinite(float(value))
        or not math.isfinite(float(expected))
    ):
        return False
    if operator == "greater_than":
        return value > expected
    if operator == "greater_or_equal":
        return value >= expected
    if operator == "less_than":
        return value < expected
    if operator == "less_or_equal":
        return value <= expected
    return False


def select_packets(
    packets: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    selection: object,
) -> list[dict[str, Any]]:
    """Select canonical Packet envelopes without changing their content."""

    normalized = normalize_packet_filter(selection)
    conditions = normalized["conditions"]
    mode = normalized["mode"]
    selected: list[dict[str, Any]] = []
    for raw_packet in packets:
        if not isinstance(raw_packet, dict):
            raise ValueError("Packet envelope 必须是 JSON object")
        matches = [
            _packet_condition_matches(raw_packet, condition)
            for condition in conditions
        ]
        accepted = (
            not matches
            or (all(matches) if mode == "all" else any(matches))
        )
        if accepted:
            selected.append(deepcopy(raw_packet))
    if normalized["order_by"] == "priority_desc":
        selected.sort(
            key=lambda item: -int(item.get("priority") or 0)
        )
    limit = normalized["limit"]
    return selected if limit is None else selected[:limit]


_REPLY_PACKET_OPEN_RE = re.compile(r"<packet>\s*", re.IGNORECASE)


def parse_reply_packets(reply: object) -> list[Packet] | None:
    """Structured Packets a Resident wrote as ``<packet>{...}</packet>`` blocks.

    JSON owns the content boundary, including tag literals inside strings.
    A complete final object may omit only its closing tag at end of reply.
    Invalid or ambiguous content returns ``None``; never dispatch a partial
    selection of the blocks in a reply.
    """

    text = str(reply or "").strip()
    decoder = json.JSONDecoder()
    packets: list[Packet] = []
    position = 0
    try:
        while opening := _REPLY_PACKET_OPEN_RE.search(text, position):
            if len(packets) >= MAX_PACKETS:
                return None
            value, end = decoder.raw_decode(text, opening.end())
            if not isinstance(value, dict):
                return None
            tail = text[end:].lstrip()
            if tail and not tail.lower().startswith("</packet>"):
                return None
            packets.append(normalize_packet(value))
            position = len(text) - len(tail) + len("</packet>") if tail else len(text)
    except (ValueError, TypeError):
        return None
    keys = [packet.key for packet in packets]
    return packets if packets and len(set(keys)) == len(keys) and not any(packet.events for packet in packets) else None

