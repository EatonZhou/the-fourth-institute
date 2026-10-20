from __future__ import annotations

from management_common.format_tags import schema_matches

from datetime import datetime, timezone

import re
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlsplit

from management_common.principal import OperationIdentity, actor_principal, execution_identity


EXECUTION_ID_RE = re.compile(r"^execution-[0-9a-f]{24}$")
PROVIDER_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
BINDING_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
MAX_SYSTEM_INSTRUCTIONS_CHARS = 500_000
MAX_INPUT_MEDIA_ITEMS = 20
MAX_INPUT_MEDIA_SOURCE_CHARS = 16_384
MAX_INPUT_MEDIA_NAME_CHARS = 512
MAX_INPUT_MEDIA_MIME_CHARS = 255
EXECUTION_SCHEMA = "tfi.execution-request.v8"
EXECUTION_SCHEMA_VERSION = 8
EXECUTION_INPUT_CONTROL_SCHEMA = "tfi.execution-input-control.v1"
EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA = "tfi.execution-turn-settings-control.v1"
EXECUTION_INPUT_CONTROL_ID_RE = re.compile(r"^control-[0-9a-f]{24}$")
MAX_EXECUTION_INPUT_CONTROL_ITEMS = 16
MAX_EXECUTION_INPUT_CONTROL_TEXT_CHARS = 200_000
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
EXECUTION_SERVICE_TYPES = frozenset({"agent_session", "chat_completion"})
from .provider_catalog import AGENT_PROVIDERS
AGENT_SESSION_OPERATIONS = frozenset({"create", "turn", "fork", "compact", "release"})
GENERATION_OPERATIONS = frozenset({"generate"})
# When Portal-added input reaches a Provider session: on the session's first
# injection only; on that and again after every compaction of its context; or
# on every turn.
CONTEXT_INJECTION_POLICIES = frozenset({"session_start", "session_start_and_after_compaction", "every_turn"})
# Where it goes: the Provider's system location (developer instructions,
# session or process system prompt), or before / after the turn's prompt.
INJECTION_PLACEMENTS = frozenset({"system", "prompt_prefix", "prompt_suffix"})
INPUT_MEDIA_TYPES = frozenset({"image"})
INPUT_MEDIA_SOURCE_TYPES = frozenset({"local_path", "url"})


class ExecutionSchedulerStatus(TypedDict, total=False):
    """Scheduler-owned fields exposed in an execution snapshot."""

    capability: str
    priority: int
    correlation_id: str
    status: str
    owner_worker_id: str
    lease_expires_at: float
    cancel_requested: bool
    attempts: int
    acknowledged_at: str
    created_at: str
    updated_at: str
    queue_reason: str
    queue_position: int
    capacity_key: str
    capacity_limit: int
    active_capacity_count: int
    last_dispatched_at: float
    next_dispatch_at: float
    dispatch_interval_seconds: float
    dispatch_interval_remaining_seconds: float
    retry_after_remaining_seconds: float


class ExecutionSnapshot(TypedDict):
    """Public status item returned by status and recent queries."""

    execution_id: str
    actor: str
    status: str
    request: dict[str, Any]
    events: list[dict[str, Any]]
    result: dict[str, Any]
    error: str
    scheduler: ExecutionSchedulerStatus


class ExecutionServiceHealth(TypedDict):
    """Stable top-level health response from ExecutionService."""

    ok: bool
    schema: str
    instance: str
    build_id: str
    scheduler: dict[str, Any]
    notifications: dict[str, Any]
    agent_data: dict[str, Any]


class ExecutionControlSignal(RuntimeError):
    """Worker-owned control flow that optional features must never suppress."""


class ExecutionRecoveryDeferred(ExecutionControlSignal):
    """The execution remains recoverable and should return to the queue."""

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float = 0,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))


@dataclass(frozen=True)
class InputMedia:
    """Provider-neutral media attached to one execution input."""

    media_type: str
    source_type: str
    source: str
    mime_type: str = ""
    name: str = ""

    def validate(self) -> "InputMedia":
        if self.media_type not in INPUT_MEDIA_TYPES:
            raise ValueError("unsupported execution input media type")
        if self.source_type not in INPUT_MEDIA_SOURCE_TYPES:
            raise ValueError("unsupported execution input media source type")
        if not self.source or len(self.source) > MAX_INPUT_MEDIA_SOURCE_CHARS:
            raise ValueError("invalid execution input media source")
        if "\x00" in self.source:
            raise ValueError("invalid execution input media source")
        if self.source_type == "local_path":
            if not Path(self.source).is_absolute():
                raise ValueError("execution input media path must be absolute")
        else:
            parsed = urlsplit(self.source)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("execution input media URL must use HTTP(S)")
        if len(self.mime_type) > MAX_INPUT_MEDIA_MIME_CHARS:
            raise ValueError("execution input media MIME type is too long")
        if self.mime_type and not self.mime_type.casefold().startswith(
            f"{self.media_type}/"
        ):
            raise ValueError("execution input media MIME type does not match")
        if len(self.name) > MAX_INPUT_MEDIA_NAME_CHARS:
            raise ValueError("execution input media name is too long")
        return self

    def to_payload(self) -> dict[str, str]:
        self.validate()
        return {
            "media_type": self.media_type,
            "source_type": self.source_type,
            "source": self.source,
            "mime_type": self.mime_type,
            "name": self.name,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "InputMedia":
        if isinstance(payload, cls):
            return payload.validate()
        if not isinstance(payload, dict):
            raise ValueError("execution input media must be an object")
        return cls(
            media_type=str(payload.get("media_type") or ""),
            source_type=str(payload.get("source_type") or ""),
            source=str(payload.get("source") or ""),
            mime_type=str(payload.get("mime_type") or ""),
            name=str(payload.get("name") or ""),
        ).validate()


@dataclass(frozen=True)
class InjectionPart:
    """One piece of caller-materialized input with its own timing and place."""

    name: str
    content: str
    timing: str = "session_start"
    placement: str = "system"

    def validate(self) -> "InjectionPart":
        if not self.name.strip():
            raise ValueError("injection part requires a name")
        if self.timing not in CONTEXT_INJECTION_POLICIES:
            raise ValueError(f"invalid injection timing for {self.name}: {self.timing}")
        if self.placement not in INJECTION_PLACEMENTS:
            raise ValueError(f"invalid injection placement for {self.name}: {self.placement}")
        if not self.content:
            raise ValueError(f"injection part {self.name} is empty")
        return self

    @classmethod
    def from_payload(cls, payload: object) -> "InjectionPart":
        value = payload if isinstance(payload, dict) else {}
        return cls(
            name=str(value.get("name") or ""),
            content=str(value.get("content") or ""),
            timing=str(value.get("timing") or "session_start"),
            placement=str(value.get("placement") or "system"),
        ).validate()


@dataclass(frozen=True)
class SystemInstructions:
    """Caller-materialized parts, each with its own timing and placement."""

    parts: tuple[InjectionPart, ...] = ()

    def validate(self) -> "SystemInstructions":
        for part in self.parts:
            part.validate()
        if len({part.name for part in self.parts}) != len(self.parts):
            raise ValueError("injection part names must be unique")
        size = sum(len(part.content) for part in self.parts)
        if size > MAX_SYSTEM_INSTRUCTIONS_CHARS:
            raise ValueError("system instructions are too long")
        return self

    @classmethod
    def from_payload(cls, payload: object) -> "SystemInstructions":
        value = payload if isinstance(payload, dict) else {}
        raw_parts = value.get("parts") or ()
        if not isinstance(raw_parts, (list, tuple)):
            raise ValueError("system instruction parts must be a list")
        return cls(
            parts=tuple(InjectionPart.from_payload(item) for item in raw_parts),
        ).validate()


@dataclass(frozen=True)
class SessionOptions:
    mode: str = "stateless"
    binding_id: str = ""
    backend_route_id: str = ""
    context_source_binding_id: str = ""

    def validate(self) -> "SessionOptions":
        if self.mode not in {"stateless", "persistent"}:
            raise ValueError("invalid execution session mode")
        if self.binding_id and not BINDING_RE.fullmatch(self.binding_id):
            raise ValueError("invalid execution binding id")
        if (
            self.context_source_binding_id
            and not BINDING_RE.fullmatch(self.context_source_binding_id)
        ):
            raise ValueError("invalid context source binding id")
        if self.mode == "stateless" and (
            self.binding_id
            or self.context_source_binding_id
        ):
            raise ValueError(
                "stateless execution cannot declare session identities"
            )
        if self.mode == "persistent" and not self.binding_id:
            raise ValueError(
                "persistent execution requires a binding id"
            )
        if (
            self.backend_route_id
            and not PROVIDER_RE.fullmatch(self.backend_route_id)
        ):
            raise ValueError("invalid backend route id")
        return self

    @classmethod
    def from_payload(cls, payload: object) -> "SessionOptions":
        value = payload if isinstance(payload, dict) else {}
        unknown = set(value) - {
            "mode",
            "binding_id",
            "backend_route_id",
            "context_source_binding_id",
        }
        if unknown:
            raise ValueError(
                "unsupported execution session fields: "
                + ", ".join(sorted(unknown))
            )
        return cls(
            mode=str(value.get("mode") or "stateless"),
            binding_id=str(value.get("binding_id") or ""),
            backend_route_id=str(value.get("backend_route_id") or ""),
            context_source_binding_id=str(
                value.get("context_source_binding_id") or ""
            ),
        ).validate()


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    actor: str
    prompt: str
    service_type: str
    provider: str
    operation: str
    agent_id: str = ""
    priority: int = 0
    correlation_id: str = ""
    cwd: str = ""
    permission_profile: str = "default"
    input_media: tuple[InputMedia, ...] = ()
    runtime_options: dict[str, Any] = field(default_factory=dict)
    session: SessionOptions = field(default_factory=SessionOptions)
    system_instructions: SystemInstructions = field(
        default_factory=SystemInstructions
    )
    metadata: dict[str, Any] = field(default_factory=dict)
    schema: str = EXECUTION_SCHEMA
    schema_version: int = EXECUTION_SCHEMA_VERSION

    @property
    def identity(self) -> OperationIdentity:
        return execution_identity(self.agent_id, self.actor)

    @classmethod
    def new(
        cls,
        *,
        actor: str,
        prompt: str,
        service_type: str,
        provider: str,
        operation: str,
        **values: Any,
    ) -> "ExecutionRequest":
        execution_id = f"execution-{secrets.token_hex(12)}"
        session = values.get("session")
        binding_id = (
            str(getattr(session, "binding_id", "") or "").strip()
            if session is not None
            else ""
        )
        agent_id = str(values.pop("agent_id", "") or binding_id or execution_id)
        return cls(
            execution_id=execution_id,
            actor=actor,
            prompt=prompt,
            service_type=service_type,
            provider=provider,
            operation=operation,
            agent_id=agent_id,
            **values,
        ).validate()

    def validate(self) -> "ExecutionRequest":
        if not schema_matches(self.schema, "tfi.execution-request.v8"):
            raise ValueError("unsupported execution request schema")
        if self.schema_version != EXECUTION_SCHEMA_VERSION:
            raise ValueError("unsupported execution request schema version")
        if not EXECUTION_ID_RE.fullmatch(self.execution_id):
            raise ValueError("invalid execution id")
        if not self.actor.strip() or len(self.actor) > 120:
            raise ValueError("invalid execution actor")
        actor_principal(self.actor)
        if not self.agent_id.strip() or len(self.agent_id) > 240 or "\x00" in self.agent_id:
            raise ValueError("invalid Agent identity")
        # Provider/Harness owns model input capacity; an arbitrary character
        # ceiling here can reject an authorized catalog before AI execution.
        if not self.prompt.strip():
            raise ValueError("execution prompt must not be empty")
        if self.service_type not in EXECUTION_SERVICE_TYPES:
            raise ValueError("invalid AI execution service type")
        if not PROVIDER_RE.fullmatch(self.provider):
            raise ValueError("invalid AI provider")
        if not -1_000_000 <= int(self.priority) <= 1_000_000:
            raise ValueError("execution priority is outside the supported range")
        if len(self.correlation_id) > 240:
            raise ValueError("execution correlation id is too long")
        if len(self.cwd) > 4096:
            raise ValueError("execution cwd is too long")
        if len(self.permission_profile) > 120:
            raise ValueError("execution permission profile is too long")
        if not isinstance(self.input_media, tuple):
            raise ValueError("execution input media must be a tuple")
        if len(self.input_media) > MAX_INPUT_MEDIA_ITEMS:
            raise ValueError("too many execution input media items")
        for item in self.input_media:
            if not isinstance(item, InputMedia):
                raise ValueError("invalid execution input media item")
            item.validate()
        if not isinstance(self.runtime_options, dict):
            raise ValueError("execution runtime options must be an object")
        if not isinstance(self.metadata, dict):
            raise ValueError("execution metadata must be an object")
        self.session.validate()
        self.system_instructions.validate()
        if self.service_type == "agent_session":
            definition = AGENT_PROVIDERS.get(self.provider)
            if definition is None:
                raise ValueError("invalid Agent Backend Provider")
            if self.operation not in AGENT_SESSION_OPERATIONS:
                raise ValueError("invalid Agent Session operation")
            if self.operation not in definition.operations:
                raise ValueError(
                    f"{self.provider} does not support {self.operation}"
                )
            if self.session.backend_route_id and not AGENT_PROVIDERS[self.provider].accepts_routes:
                raise ValueError(
                    "backend route selection is not supported by this adapter"
                )
            context_sources = int(bool(self.session.context_source_binding_id))
            if self.operation == "fork":
                if self.session.mode != "persistent" or context_sources != 1:
                    raise ValueError(
                        "Agent Session fork requires one context source "
                        "and a persistent destination"
                    )
            elif context_sources:
                raise ValueError(
                    "context sources are only valid for Agent Session fork"
                )
            elif self.operation == "create" and self.session.mode != "persistent":
                raise ValueError(
                    "Agent Session create requires a persistent session"
                )
            elif self.operation in {"compact", "release"} and (
                self.session.mode != "persistent"
            ):
                raise ValueError(
                    f"Agent Session {self.operation} requires a persistent session"
                )
        elif self.service_type == "chat_completion":
            if self.operation not in GENERATION_OPERATIONS:
                raise ValueError("invalid Chat Completion operation")
            if self.session.mode != "stateless":
                raise ValueError("Chat Completion requires a stateless session")
        return self

    @property
    def capability(self) -> str:
        if self.service_type == "agent_session":
            return f"{self.service_type}:{self.provider}"
        return self.service_type

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        payload = asdict(self)
        payload["identity"] = self.identity.to_dict()
        payload["input_media"] = [
            item.to_payload() for item in self.input_media
        ]
        payload["system_instructions"]["parts"] = list(payload["system_instructions"]["parts"])
        return payload

    @classmethod
    def from_payload(cls, payload: object) -> "ExecutionRequest":
        if not isinstance(payload, dict):
            raise ValueError("execution request must be an object")
        schema = str(payload.get("schema") or "")
        schema_version = int(payload.get("schema_version") or 0)
        if not schema_matches(schema, "tfi.execution-request.v8") or schema_version != EXECUTION_SCHEMA_VERSION:
            raise ValueError("invalid execution request schema")
        runtime_options = payload.get("runtime_options")
        metadata = payload.get("metadata")
        raw_input_media = payload.get("input_media")
        if isinstance(raw_input_media, (list, tuple)):
            input_media = tuple(
                InputMedia.from_payload(item) for item in raw_input_media
            )
        else:
            raise ValueError("execution input media must be an array")
        request = cls(
            execution_id=str(payload.get("execution_id") or ""),
            actor=str(payload.get("actor") or ""),
            prompt=str(payload.get("prompt") or ""),
            service_type=str(payload.get("service_type") or ""),
            provider=str(payload.get("provider") or ""),
            operation=str(payload.get("operation") or ""),
            agent_id=str(payload.get("agent_id") or ""),
            priority=int(payload.get("priority") or 0),
            correlation_id=str(payload.get("correlation_id") or ""),
            cwd=str(payload.get("cwd") or ""),
            permission_profile=str(payload.get("permission_profile") or "default"),
            input_media=input_media,
            runtime_options=dict(runtime_options) if isinstance(runtime_options, dict) else {},
            session=SessionOptions.from_payload(payload.get("session")),
            system_instructions=SystemInstructions.from_payload(
                payload.get("system_instructions")
            ),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            schema=schema,
            schema_version=schema_version,
        )
        if "identity" not in payload:
            raise ValueError("execution identity required")
        if OperationIdentity.from_dict(payload["identity"]) != request.identity:
            raise ValueError("execution identity disagrees with request coordinates")
        return request.validate()




@dataclass(frozen=True)
class ExecutionInputControl:
    """Control input for one active Agent Session turn, including tool replies."""

    control_id: str
    execution_id: str
    actor: str
    input: tuple[dict[str, Any], ...]
    expected_turn_id: str = ""
    source_segment_id: str = ""
    schema: str = EXECUTION_INPUT_CONTROL_SCHEMA

    @classmethod
    def new(
        cls,
        *,
        execution_id: str,
        actor: str,
        input: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        expected_turn_id: str = "",
        control_id: str = "",
        source_segment_id: str = "",
    ) -> "ExecutionInputControl":
        return cls(
            control_id=str(control_id or f"control-{secrets.token_hex(12)}"),
            execution_id=str(execution_id or ""),
            actor=str(actor or ""),
            input=tuple(dict(item) for item in input),
            expected_turn_id=str(expected_turn_id or ""),
            source_segment_id=str(source_segment_id or ""),
        ).validate()


    def validate(self) -> "ExecutionInputControl":
        if not schema_matches(self.schema, "tfi.execution-input-control.v1"):
            raise ValueError("unsupported execution input control schema")
        if not EXECUTION_INPUT_CONTROL_ID_RE.fullmatch(self.control_id):
            raise ValueError("invalid execution input control id")
        if not EXECUTION_ID_RE.fullmatch(self.execution_id):
            raise ValueError("invalid execution id")
        if not self.actor.strip() or len(self.actor) > 120:
            raise ValueError("invalid execution input control actor")
        actor_principal(self.actor)
        if not 1 <= len(self.input) <= MAX_EXECUTION_INPUT_CONTROL_ITEMS:
            raise ValueError("execution input control must contain input")
        text_chars = 0
        for item in self.input:
            if not isinstance(item, dict):
                raise ValueError("execution input control item must be an object")
            item_type = str(item.get("type") or "")
            if item_type == "text":
                text = str(item.get("text") or "")
                if not text:
                    raise ValueError("execution input control text is empty")
                text_chars += len(text)
            elif item_type == "localImage":
                if not str(item.get("path") or "").strip():
                    raise ValueError("execution input control image path is empty")
            elif item_type == "image":
                if not str(item.get("url") or "").strip():
                    raise ValueError("execution input control image url is empty")
            elif item_type == "user_input_response":
                from management_common.provider_user_input import validate_response

                if len(self.input) != 1 or not self.expected_turn_id:
                    raise ValueError("user input response must target exactly one active turn")
                validate_response(item)
            elif item_type == "subagent_stop":
                # Stop one subagent run the turn's model started; the turn goes on.
                tool_use_id = item.get("tool_use_id")
                if len(self.input) != 1 or not isinstance(tool_use_id, str) or not 0 < len(tool_use_id.strip()) <= 200:
                    raise ValueError("subagent stop must name exactly one subagent run")
            else:
                raise ValueError("unsupported execution input control item")
        if text_chars > MAX_EXECUTION_INPUT_CONTROL_TEXT_CHARS:
            raise ValueError("execution input control text is too long")
        if len(self.expected_turn_id) > 512:
            raise ValueError("expected turn id is too long")
        if self.source_segment_id and (not self.source_segment_id.startswith("segment_") or len(self.source_segment_id) > 160):
            raise ValueError("invalid input source Segment")
        return self

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "control_id": self.control_id,
            "execution_id": self.execution_id,
            "actor": self.actor,
            "input": [dict(item) for item in self.input],
            "expected_turn_id": self.expected_turn_id,
            **({"source_segment_id": self.source_segment_id} if self.source_segment_id else {}),
        }

    @classmethod
    def from_payload(cls, payload: object) -> "ExecutionInputControl":
        value = payload if isinstance(payload, dict) else {}
        raw_input = value.get("input")
        if not isinstance(raw_input, list):
            raise ValueError("execution input control input must be an array")
        return cls(
            schema=str(value.get("schema") or ""),
            control_id=str(value.get("control_id") or ""),
            execution_id=str(value.get("execution_id") or ""),
            actor=str(value.get("actor") or ""),
            input=tuple(
                dict(item) if isinstance(item, dict) else item
                for item in raw_input
            ),
            expected_turn_id=str(value.get("expected_turn_id") or ""),
            source_segment_id=str(value.get("source_segment_id") or ""),
        ).validate()


@dataclass(frozen=True)
class ExecutionTurnSettingsControl:
    """Provider-native settings update for one active Agent Session turn."""

    control_id: str
    execution_id: str
    actor: str
    settings: dict[str, Any]
    expected_turn_id: str = ""
    schema: str = EXECUTION_TURN_SETTINGS_CONTROL_SCHEMA

    @classmethod
    def new(
        cls,
        *,
        execution_id: str,
        actor: str,
        settings: dict[str, Any],
        expected_turn_id: str = "",
        control_id: str = "",
    ) -> "ExecutionTurnSettingsControl":
        return cls(
            control_id=str(control_id or f"control-{secrets.token_hex(12)}"),
            execution_id=str(execution_id or ""),
            actor=str(actor or ""),
            settings=dict(settings),
            expected_turn_id=str(expected_turn_id or ""),
        ).validate()

    def validate(self) -> "ExecutionTurnSettingsControl":
        if not schema_matches(self.schema, "tfi.execution-turn-settings-control.v1"):
            raise ValueError("unsupported execution turn settings control schema")
        if not EXECUTION_INPUT_CONTROL_ID_RE.fullmatch(self.control_id):
            raise ValueError("invalid execution turn settings control id")
        if not EXECUTION_ID_RE.fullmatch(self.execution_id):
            raise ValueError("invalid execution id")
        if not self.actor.strip() or len(self.actor) > 120:
            raise ValueError("invalid execution turn settings control actor")
        actor_principal(self.actor)
        if set(self.settings) != {"service_tier"}:
            raise ValueError("turn settings control only supports service_tier")
        service_tier = self.settings.get("service_tier")
        if not isinstance(service_tier, str) or service_tier not in {"", "priority"}:
            raise ValueError("invalid turn service tier")
        if len(self.expected_turn_id) > 512:
            raise ValueError("expected turn id is too long")
        return self

    def to_payload(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema": self.schema,
            "control_id": self.control_id,
            "execution_id": self.execution_id,
            "actor": self.actor,
            "action": "update_settings",
            "settings": dict(self.settings),
            "expected_turn_id": self.expected_turn_id,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "ExecutionTurnSettingsControl":
        value = payload if isinstance(payload, dict) else {}
        settings = value.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("execution turn settings must be an object")
        return cls(
            schema=str(value.get("schema") or ""),
            control_id=str(value.get("control_id") or ""),
            execution_id=str(value.get("execution_id") or ""),
            actor=str(value.get("actor") or ""),
            settings=dict(settings),
            expected_turn_id=str(value.get("expected_turn_id") or ""),
        ).validate()


@dataclass(frozen=True)
class ProviderInputBinding:
    """Provider-accepted input identity carried by an Execution event."""

    provider: str
    turn_id: str
    backend_session_id: str = ""
    backend_route_id: str = ""
    provider_message_id: str = ""

    def validate(self) -> "ProviderInputBinding":
        if not PROVIDER_RE.fullmatch(self.provider):
            raise ValueError("invalid input binding provider")
        if not self.turn_id.strip() or len(self.turn_id) > 512:
            raise ValueError("invalid input binding turn id")
        if len(self.backend_session_id) > 512:
            raise ValueError("input binding session id is too long")
        if (
            self.backend_route_id
            and not PROVIDER_RE.fullmatch(self.backend_route_id)
        ):
            raise ValueError("invalid input binding backend route")
        if len(self.provider_message_id) > 512:
            raise ValueError("input binding provider message id is too long")
        return self

    def to_payload(self) -> dict[str, str]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    backend_session_id: str = ""
    backend_route_id: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    error_code: str = ""
    error_details: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("invalid execution result status")
        if len(self.error_code) > 120:
            raise ValueError("execution error code is too long")
        return asdict(self)

    @classmethod
    def from_payload(cls, result: object) -> "ExecutionResult":
        if not isinstance(result, dict):
            raise ValueError("execution result must be an object")
        parsed = cls(
            execution_id=str(result.get("execution_id") or ""),
            status=str(result.get("status") or ""),
            output=(
                dict(result.get("output"))
                if isinstance(result.get("output"), dict)
                else {}
            ),
            backend_session_id=str(
                result.get("backend_session_id") or ""
            ),
            backend_route_id=str(
                result.get("backend_route_id") or ""
            ),
            usage=(
                dict(result.get("usage"))
                if isinstance(result.get("usage"), dict)
                else {}
            ),
            error=str(result.get("error") or ""),
            error_code=str(result.get("error_code") or ""),
            error_details=(
                dict(result.get("error_details"))
                if isinstance(result.get("error_details"), dict)
                else {}
            ),
        )
        parsed.to_payload()
        return parsed


TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def execution_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def usage_total(usage: dict[str, Any], *, include_cached_input: bool) -> int:
    """Use the reported total, excluding cached input only when requested."""
    def nonnegative(key):
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    total = (nonnegative("total_tokens") if usage.get("total_tokens") is not None
             else nonnegative("input_tokens") + nonnegative("output_tokens"))
    return total if include_cached_input else max(0, total - nonnegative("cached_input_tokens"))
