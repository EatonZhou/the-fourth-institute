"""Call, result and error formats shared by every HATG ISA entry and owner.

The formats follow decision_898b8911a2ff: a call carries only the instruction
name, its arguments and two optional controls.  The caller's identity and the
entry kind come from the entry, never from the call body.
"""
from __future__ import annotations
from management_common.format_tags import schema_matches

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from management_common.operation_errors import OperationError, failure_payload


INSTRUCTION_SCHEMA = "tfi.hatg-instruction.v2"
RESULT_SCHEMA = "tfi.hatg-instruction-result.v2"
INSTRUCTION_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{1,79}$")
MAX_IDEMPOTENCY_KEY = 240
ENTRY_KINDS = frozenset({"web", "cli", "harness", "lineage", "system"})
CALL_FIELDS = frozenset(
    {"schema", "instruction", "arguments", "idempotency_key", "expected_version"}
)

# Error code -> HTTP status used by the web entry (decision_898b8911a2ff).
ERROR_STATUS = {
    "instruction.unknown": 400,
    "instruction.invalid_arguments": 400,
    "instruction.forbidden": 403,
    "instruction.not_found": 404,
    "instruction.version_conflict": 409,
    "instruction.idempotency_conflict": 409,
    "instruction.in_progress": 409,
    "instruction.conflict": 409,
    "instruction.owner_unavailable": 503,
    "instruction.projection_pending": 503,
    "instruction.failed": 500,
}


class InstructionError(OperationError, ValueError):
    """A failed instruction; ``code`` is the stable machine-readable reason."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        details: Mapping[str, Any] | None = None,
        retryable: bool = False,
        commit_state: str = "not_committed",
    ) -> None:
        code = str(code or "instruction.failed")
        super().__init__(
            message,
            code=code,
            http_status=int(http_status or ERROR_STATUS.get(code, 400)),
            retryable=retryable,
            commit_state=commit_state,
            details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = self.failure_payload()
        payload.pop("http_status", None)
        return payload


def invalid(message: str, **details: Any) -> InstructionError:
    return InstructionError("instruction.invalid_arguments", message, details=details)


def not_found(message: str, **details: Any) -> InstructionError:
    return InstructionError("instruction.not_found", message, details=details)


def _vocabulary_code(code: str, http_status: int, retryable: bool) -> str:
    """The v2 error code for an owner-specific code outside the fixed vocabulary."""

    if code.endswith(("version_conflict", "revision_conflict")):
        return "instruction.version_conflict"
    if http_status in (401, 403):
        return "instruction.forbidden"
    if http_status in (404, 410):
        return "instruction.not_found"
    if http_status == 409:
        return "instruction.conflict"
    if http_status == 429 or (500 <= http_status < 600 and retryable):
        return "instruction.owner_unavailable"
    if 400 <= http_status < 500:
        return "instruction.invalid_arguments"
    return "instruction.failed"


def normalize_error(exc: BaseException) -> InstructionError:
    """Map a domain exception to the instruction error vocabulary."""

    code_by_exception = (
        (PermissionError, "instruction.forbidden"),
        (FileNotFoundError, "instruction.not_found"),
        (KeyError, "instruction.not_found"),
        (FileExistsError, "instruction.conflict"),
        (ValueError, "instruction.invalid_arguments"),
    )
    if isinstance(exc, OperationError):
        failure = exc.failure_payload()
        code = str(failure["code"])
        mapped = {
            "operation.forbidden": "instruction.forbidden",
            "operation.not_found": "instruction.not_found",
            "operation.file_not_found": "instruction.not_found",
            "operation.conflict": "instruction.conflict",
            "operation.invalid": "instruction.invalid_arguments",
            "operation.failed": "instruction.failed",
            "operation.timeout": "instruction.failed",
            "lineage.unavailable": "instruction.owner_unavailable",
            "resource.unavailable": "instruction.owner_unavailable",
            "lineage.busy": "instruction.owner_unavailable",
            "projection.pending": "instruction.projection_pending",
        }.get(code, code)
        details = dict(failure["details"] or {})
        if mapped not in ERROR_STATUS:
            mapped = _vocabulary_code(code, int(failure["http_status"] or 500), bool(failure["retryable"]))
        if mapped != code:
            details.setdefault("reason", code)
        if isinstance(exc, InstructionError) and mapped == code:
            return exc
        return InstructionError(
            mapped,
            failure["message"],
            http_status=ERROR_STATUS[mapped],
            details=details,
            retryable=failure["retryable"],
            commit_state=failure["commit_state"],
        )
    declared_code = getattr(exc, "code", None)
    if isinstance(declared_code, str) and declared_code in ERROR_STATUS:
        return InstructionError(declared_code, str(exc) or declared_code)
    for exception_type, code in code_by_exception:
        if isinstance(exc, exception_type):
            message = str(exc).strip("'") if isinstance(exc, KeyError) else str(exc)
            return InstructionError(code, message or code)
    if isinstance(exc, TimeoutError):
        return InstructionError(
            "instruction.failed", str(exc) or "timeout", retryable=True, commit_state="unknown"
        )
    failure = failure_payload(exc)
    return InstructionError(
        "instruction.failed", failure["message"] or type(exc).__name__, commit_state="unknown"
    )


def _validate_expected_version(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        if not value:
            raise invalid("expected_version 映射不能为空")
        result = {}
        for key, token in value.items():
            if not isinstance(key, str) or not key.strip():
                raise invalid("expected_version 的键必须是对象 ID")
            result[key.strip()] = _validate_expected_version(token)
        return result
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise invalid("expected_version 必须是版本令牌（整数或字符串）或以对象 ID 为键的映射")
    if isinstance(value, str) and not value.strip():
        raise invalid("expected_version 不能为空字符串")
    return value


@dataclass(frozen=True, slots=True)
class InstructionCall:
    instruction: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    expected_version: Any = None

    def __post_init__(self) -> None:
        name = str(self.instruction or "").strip()
        if not INSTRUCTION_NAME_RE.fullmatch(name):
            raise InstructionError(
                "instruction.unknown", "instruction 必须是 2–80 位 UpperCamelCase 指令名"
            )
        if not isinstance(self.arguments, Mapping):
            raise invalid("arguments 必须是对象")
        if any(not isinstance(key, str) or not key.strip() for key in self.arguments):
            raise invalid("arguments 的字段名必须是非空字符串")
        key = str(self.idempotency_key or "").strip()
        if len(key) > MAX_IDEMPOTENCY_KEY:
            raise invalid("idempotency_key 不能超过 240 个字符")
        object.__setattr__(self, "instruction", name)
        object.__setattr__(self, "arguments", copy.deepcopy(dict(self.arguments)))
        object.__setattr__(self, "idempotency_key", key)
        object.__setattr__(
            self, "expected_version", _validate_expected_version(self.expected_version)
        )

    @classmethod
    def from_payload(cls, payload: Any) -> "InstructionCall":
        if not isinstance(payload, Mapping):
            raise invalid("调用必须是 JSON 对象")
        unknown = sorted(set(payload) - CALL_FIELDS)
        if unknown:
            raise invalid("调用包含未定义字段：" + "、".join(unknown), fields=unknown)
        schema = payload.get("schema")
        if schema not in (None, "") and not schema_matches(schema, "tfi.hatg-instruction.v2"):
            raise invalid(f"不支持的调用格式：{schema}")
        arguments = payload.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping):
            raise invalid("arguments 必须是对象")
        key = payload.get("idempotency_key") or ""
        if not isinstance(key, str):
            raise invalid("idempotency_key 必须是字符串")
        return cls(
            instruction=str(payload.get("instruction") or ""),
            arguments=arguments,
            idempotency_key=key,
            expected_version=payload.get("expected_version"),
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": INSTRUCTION_SCHEMA,
            "instruction": self.instruction,
            "arguments": copy.deepcopy(dict(self.arguments)),
        }
        if self.idempotency_key:
            payload["idempotency_key"] = self.idempotency_key
        if self.expected_version is not None:
            payload["expected_version"] = copy.deepcopy(self.expected_version)
        return payload


@dataclass(frozen=True, slots=True)
class InstructionContext:
    """Who is calling and through which entry; set by the entry, never the call."""

    caller: str
    entry: str
    trigger_path_id: str = ""
    # The Resident execution a call belongs to, so resource content it changes
    # is recorded with that execution (the owner confirms the association).
    execution_id: str = ""
    resource_lifecycle_id: str = ""
    # Set only by the trusted Lineage runtime, never by instruction arguments.
    recovery_key: str = ""

    def __post_init__(self) -> None:
        caller = str(self.caller or "").strip()
        entry = str(self.entry or "").strip().lower()
        if not caller:
            raise InstructionError(
                "instruction.forbidden", "调用缺少经过认证的调用者", http_status=401
            )
        if entry not in ENTRY_KINDS:
            raise invalid(f"未知入口种类：{entry or '(empty)'}")
        object.__setattr__(self, "caller", caller)
        object.__setattr__(self, "entry", entry)
        object.__setattr__(self, "trigger_path_id", str(self.trigger_path_id or "").strip())
        execution_id = str(self.execution_id or "").strip()
        object.__setattr__(self, "execution_id", execution_id if len(execution_id) <= 128 else "")
        recovery_key = str(self.recovery_key or "").strip()
        if recovery_key and (entry != "lineage" or len(recovery_key) > 240):
            raise invalid("恢复依赖仅由 Lineage 运行入口登记")
        object.__setattr__(self, "recovery_key", recovery_key)

    def to_payload(self) -> dict[str, str]:
        payload = {"caller": self.caller, "entry": self.entry}
        if self.trigger_path_id:
            payload["trigger_path_id"] = self.trigger_path_id
        if self.execution_id:
            payload["execution_id"] = self.execution_id
        if self.resource_lifecycle_id:
            payload["resource_lifecycle_id"] = self.resource_lifecycle_id
        if self.recovery_key:
            payload["recovery_key"] = self.recovery_key
        return payload

    @classmethod
    def from_payload(cls, payload: Any) -> "InstructionContext":
        if not isinstance(payload, Mapping):
            raise invalid("调用上下文必须是对象")
        return cls(
            caller=str(payload.get("caller") or ""),
            entry=str(payload.get("entry") or ""),
            trigger_path_id=str(payload.get("trigger_path_id") or ""),
            execution_id=str(payload.get("execution_id") or ""),
            resource_lifecycle_id=str(payload.get("resource_lifecycle_id") or ""),
            recovery_key=str(payload.get("recovery_key") or ""),
        )

    @property
    def is_resident(self) -> bool:
        return self.caller.startswith("resident_")


@dataclass(frozen=True, slots=True)
class InstructionResult:
    instruction_id: str
    instruction: str
    status: str
    result: Any = None
    error: Mapping[str, Any] | None = None
    replayed: bool = False

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": RESULT_SCHEMA,
            "instruction_id": self.instruction_id,
            "instruction": self.instruction,
            "status": self.status,
            "replayed": bool(self.replayed),
        }
        if self.status == "committed":
            payload["result"] = copy.deepcopy(self.result)
        else:
            payload["error"] = copy.deepcopy(dict(self.error or {}))
        return payload

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "InstructionResult":
        return cls(
            instruction_id=str(payload.get("instruction_id") or ""),
            instruction=str(payload.get("instruction") or ""),
            status=str(payload.get("status") or ""),
            result=copy.deepcopy(payload.get("result")),
            error=copy.deepcopy(payload.get("error")),
            replayed=bool(payload.get("replayed")),
        )

    def raise_for_error(self) -> Any:
        if self.status == "committed":
            return self.result
        error = dict(self.error or {})
        details = copy.deepcopy(dict(error.get("details") or {})) if isinstance(error.get("details"), Mapping) else {}
        # Keep the outer receipt separate from an owner's original/in-progress
        # instruction_id. An entry receipt does not identify an owner execution.
        details["instruction_result"] = {
            "instruction": self.instruction,
            "instruction_id": self.instruction_id,
            "replayed": self.replayed,
        }
        raise InstructionError(
            str(error.get("code") or "instruction.failed"),
            str(error.get("message") or "指令执行失败"),
            details=details,
            retryable=bool(error.get("retryable")),
            commit_state=str(error.get("commit_state") or "unknown"),
        )


def failed_result(instruction_id: str, instruction: str, error: InstructionError) -> InstructionResult:
    return InstructionResult(
        instruction_id=instruction_id,
        instruction=instruction,
        status="failed",
        error=error.to_dict(),
    )
