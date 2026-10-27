"""Resident domain responsibilities: record."""
from __future__ import annotations

import os

from . import (
    identity_contract,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_execution_work,
    resident_lineage,
)
from management_portal import agent_context_selection
from pathlib import Path
from typing import Any, Mapping
import copy
from management_portal.execution import slash_commands
import re

from . import resident_domain_common


def _new_workspace_id(existing_ids: set[str] | None = None) -> str:
    occupied = {str(value or "").strip() for value in existing_ids or set()}
    return identity_contract.new_id("workspace", occupied)

REF_ITEM_CHAR_LIMIT = int(os.getenv("RESIDENT_AGENT_REF_ITEM_CHAR_LIMIT", "30000"))

REF_TOTAL_CHAR_LIMIT = int(os.getenv("RESIDENT_AGENT_REF_TOTAL_CHAR_LIMIT", "80000"))

TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def clean_token_usage(value: object) -> dict[str, int]:
    from . import resident_lifecycle_domain
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key in TOKEN_USAGE_KEYS:
        number = resident_lifecycle_domain.nonnegative_int(value.get(key), 0)
        if number > 0:
            out[key] = number
    return out


def record_resident_id(record: dict[str, Any]) -> str:
    return str(record.get("resident_id") or "").strip()


_BACKEND_SESSION_STATES = frozenset({
    "active",
    "creating",
    "deleted",
    "failed",
    "pending_create",
})


def _backend_session_state(
    stored_state: object,
    *,
    backend_session_id: object,
) -> str:
    """Return backend-session lifecycle state independent of Resident work state."""

    state = str(stored_state or "").strip().lower()
    if state in _BACKEND_SESSION_STATES:
        return state
    return "active" if str(backend_session_id or "").strip() else "pending_create"


def record_backend_session_state(
    record: Mapping[str, object],
    *,
    provider_binding: Mapping[str, object] | None = None,
) -> str:
    """Project session state without permitting Scheduler lifecycle values."""

    backend_session_id = (
        resident_backend.record_session_id(record)
        if provider_binding is None
        else resident_backend.session_id(
            provider_binding.get("backend_session_id")
        )
    )
    return _backend_session_state(
        record.get("session_state"),
        backend_session_id=backend_session_id,
    )


def apply_resident_resource_identity(record: dict[str, Any]) -> None:
    resident_id = str(record.get("resident_id") or "").strip()
    if resident_id:
        identity_contract.validate_id(resident_id, "resident")
    else:
        resident_id = resident_domain_common.new_resident_id()
        record["resident_id"] = resident_id
    lineage_id = str(record.get("lineage_id") or "").strip()
    if lineage_id:
        identity_contract.validate_id(lineage_id, "lineage")
    else:
        lineage_id = resident_domain_common.new_lineage_id()
        record["lineage_id"] = lineage_id


def resident_resource_identity_signature(record: dict[str, Any]) -> dict[str, Any]:
    """Return only fields projected to ResourceService, excluding runtime state."""
    bindings = sorted(
        (
            str(item.get("workspace_id") or "").strip(),
            str(item.get("path") or "").strip(),
        )
        for item in record.get("workspace_bindings") or []
        if isinstance(item, dict)
        and (
            str(item.get("workspace_id") or "").strip()
            or str(item.get("path") or "").strip()
        )
    )
    return {
        "resident_id": str(record.get("resident_id") or "").strip(),
        "lineage_id": str(record.get("lineage_id") or "").strip(),
        "title": str(record.get("title") or ""),
        "description": str(record.get("description") or ""),
        "project_id": str(record.get("project_id") or "").strip(),
        "principal_hierarchy": copy.deepcopy(record.get("principal_hierarchy")),
        "personal_owner_id": str(record.get("personal_owner_id") or "").strip(),
        "backend": str(record.get("backend") or "codex").strip(),
        "owned_workspace_id": str(record.get("owned_workspace_id") or "").strip(),
        "workspace_bindings": bindings,
        "created_at": str(record.get("created_at") or "").strip(),
        "deleted_at": str(record.get("deleted_at") or "").strip(),
    }


def canonical_derived_resident_record(
    anchor: dict[str, Any],
    resident: dict[str, Any],
) -> dict[str, Any]:
    """Build one complete Resident created by a derivation operation."""

    payload = dict(resident)
    if (payload.get("principal_hierarchy") or {}).get("level") in {"project", "lineage"}:
        payload.pop("principal_hierarchy", None)

    for field in (
        "personal_owner_id",
        "project_id",
        "backend",
        "lineage_title",
        "lineage_project_id",
        "workspace_root",
        "workspace_path",
        "workspace_paths",
    ):
        if field not in payload and field in anchor:
            payload[field] = copy.deepcopy(anchor[field])
    payload.update({
        "schema": resident_catalog.RESIDENT_RECORD_SCHEMA,
        "resident_id": resident_lineage.resident_id(resident),
        "lineage_id": resident_lineage.lineage_id(anchor),
        "folder_schema": resident_domain_common.LINEAGE_FOLDER_SCHEMA_VERSION,
        "title": str(resident.get("title") or resident_lineage.resident_id(resident)),
        "session_state": record_backend_session_state(resident),
        # A derived Resident is usable at once; only registration waits for its resources.
        "registration_state": "ready",
    })
    return payload


def sync_lineage_member_credentials(
    lineage: dict[str, Any],
    *,
    previous_lineage: dict[str, Any] | None = None,
) -> None:
    """Project Lineage membership to Resident credentials."""

    lineage_id = str(lineage.get("lineage_id") or "").strip()
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        raise ValueError("formal member projection requires a canonical lineage_id")
    resident_ids = list(dict.fromkeys(
        str(node.get("id") or "").strip()
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
        and identity_contract.is_valid_id(
            str(node.get("id") or "").strip(),
            "resident",
        )
    ))
    previous_resident_ids = {
        str(node.get("id") or "").strip()
        for node in (previous_lineage or {}).get("nodes") or []
        if isinstance(node, dict)
        and identity_contract.is_valid_id(
            str(node.get("id") or "").strip(),
            "resident",
        )
    }
    added_resident_ids = set(resident_ids).difference(previous_resident_ids)
    removed_resident_ids = previous_resident_ids.difference(resident_ids)
    resident_ids_to_read = added_resident_ids | removed_resident_ids
    if not resident_ids_to_read:
        return
    records = {
        str(item.get("resident_id") or "").strip(): item
        for item in resident_catalog.get_catalog().get_many(
            sorted(resident_ids_to_read)
        )
        if isinstance(item, dict)
    }
    from . import resident_hatg_credentials

    for resident_id in sorted(added_resident_ids):
        record = records.get(resident_id)
        # New-Lineage registration persists the graph before its first
        # Resident fact. _save_resident_record() completes this same projection
        # immediately afterwards once the catalog entry exists.
        if record is None:
            continue
        if str(record.get("lineage_id") or "").strip() != lineage_id:
            if (lineage.get("principal_hierarchy") or {}).get("level") == "project":
                # Structural participation does not change the execution credential's home.
                continue
            raise ValueError(
                f"HATG Resident belongs to another Lineage: {resident_id}"
            )
        resident_hatg_credentials.ensure_resident_credential(
            resident_id,
            lineage_id,
        )


def resident_folder_path(record: dict[str, Any]) -> Path:
    from . import resident_workspace_domain
    workspace = resident_workspace_domain.resident_workspace_path(record)
    resident_id = record_resident_id(record)
    if not resident_id:
        raise ValueError("resident_id missing")
    return workspace / "residents" / resident_id


def resident_owned_workspace_path(
    record: dict[str, Any],
    resident_id: str = "",
) -> Path:
    return resident_catalog.owned_workspace_path(record, resident_id)


def set_resident_owned_workspace_binding(record: dict[str, Any], *, resident_id: str = "") -> dict[str, Any]:
    """Prepare one owned Workspace and only explicit additional relationships."""
    from .resident_workspace_storage import apply_binding_projection, canonical_path
    if record.get("deleted_at"):
        raise KeyError("resident not found")
    target_id = resident_id or record_resident_id(record)
    path = canonical_path(resident_owned_workspace_path(record, target_id))
    previous = record.get("owned_workspace") or {}
    # A Fork owns a new Workspace, while a move preserves the original ID.
    own_id = (record.get("owned_workspace_id") if target_id == record_resident_id(record) else "")
    if previous.get("path") and Path(previous["path"]).parent.name != target_id:
        own_id = ""
    own_id = own_id or _new_workspace_id()
    bindings = [copy.deepcopy(item) for item in record.get("workspace_bindings") or []
                if item["workspace_id"] != own_id and canonical_path(item["path"]) != path]
    record.update(owned_workspace_id=own_id,
                  owned_workspace={"workspace_id": own_id, "path": path},
                  workspace_bindings=bindings)
    apply_binding_projection(record)
    return record


def require_resident_owned_workspace_binding(
    record: dict[str, Any],
) -> dict[str, Any]:
    """Return the canonical private Workspace Binding without changing it."""

    if record.get("deleted_at"):
        raise KeyError("resident not found")
    if not resident_catalog.default_cwd_path(record):
        raise ValueError(
            "Resident default Workspace Binding does not match current contract"
        )
    return dict(record["owned_workspace"])


def validated_resident_record_payload(
    record: dict[str, Any],
) -> dict[str, Any]:
    payload = copy.deepcopy(dict(record))
    payload.pop("intake_state", None)
    payload["schema"] = resident_catalog.RESIDENT_RECORD_SCHEMA
    resident_catalog.get_catalog().validate_record(payload)
    return payload


def resident_instructions_path(record: dict[str, Any]) -> Path:
    return resident_folder_path(record) / resident_domain_common.RESIDENT_INSTRUCTIONS_FILE


def sessions_path(record: dict[str, Any]) -> Path:
    return resident_folder_path(record) / "runtime"


def session_token_usage_payload(record: dict[str, Any]) -> dict[str, Any]:
    usage = clean_token_usage(record.get("last_turn_usage"))
    return {
        "summary": {
            "last_turn_usage": usage,
            "updated_at": str(record.get("last_turn_usage_at") or ""),
        }
    }


def last_user_message_text(resident_id: str) -> str:
    from . import resident_workspace_domain
    message = resident_conversation_stream.get_repository().latest_message(
        resident_id,
        role="user",
    )
    return resident_workspace_domain.protocol_message_text(message) if isinstance(message, dict) else ""


def resident_goal_payload(
    record: dict[str, Any],
    *,
    job: dict[str, Any] | None = None,
    last_user_message: str = "",
) -> dict[str, Any]:
    active_job = job if isinstance(job, dict) else {}
    current_job = str(active_job.get("raw_message") or active_job.get("effective_message") or active_job.get("message") or "").strip()
    goal_command = slash_commands.parse_goal_command(current_job)
    job_outcome = resident_execution_work.outcome(active_job)
    if goal_command and (
        resident_execution_work.is_active(active_job)
        or job_outcome in {"cancelled"}
    ):
        action = str(goal_command.get("action") or "")
        if action in {"set", "resume", "observe"}:
            return {
                **(
                    {"objective": str(goal_command.get("objective") or "")}
                    if goal_command.get("objective")
                    else {}
                ),
                "status": (
                    "active"
                    if resident_execution_work.is_active(active_job)
                    else "paused"
                ),
            }
    return {
        "resident_id": str(record.get("resident_id") or ""),
        "title": str(record.get("title") or record.get("resident_id") or ""),
        "state": record_backend_session_state(record),
        "current_job": "" if current_job.startswith("/") else current_job,
        "last_user_message": str(last_user_message or ""),
    }


def _runtime_error_code(message: str) -> str:
    from management_portal.execution.provider_failures import is_usage_limit_failure

    text = str(message or "").casefold()
    if is_usage_limit_failure(message):
        return "provider_usage_limit"
    if (
        "failed to load configuration" in text
        or "failed to parse configuration" in text
        or "failed to read configuration" in text
    ):
        return "configuration"
    if "refresh token" in text or "token_invalidated" in text or "access token" in text:
        return "auth"
    if "rate limit" in text or "429" in text:
        return "rate_limit"
    if "canceling statement due to lock timeout" in text:
        return "database_lock_timeout"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "permission" in text or "sandbox" in text or "operation not permitted" in text:
        return "permission"
    if "network" in text or "connection" in text or "dns" in text:
        return "network"
    return "unknown"


def resident_backend_failure_message(record: dict[str, Any], raw_error: str) -> str:
    return str(raw_error or "").strip()


def runtime_error_payload(
    raw_error: str,
    *,
    code: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import resident_conversation_projection_domain
    raw = str(raw_error or "").strip()
    if not raw:
        return {}
    from management_portal.execution.provider_failures import is_usage_limit_failure

    supplied_code = str(code or "").strip().lower()
    if is_usage_limit_failure(raw, code=supplied_code):
        supplied_code = "provider_usage_limit"
    payload = {
        "message": resident_conversation_projection_domain.truncate_text(raw, 1600),
        "code": supplied_code or _runtime_error_code(raw),
        "source": "execution_result",
        "raw_error": resident_conversation_projection_domain.truncate_text(raw, 1600),
    }
    if isinstance(details, dict) and details:
        payload["details"] = dict(details)
    return payload


def runtime_failure_summary(
    raw_error: str,
    runtime_error: dict[str, Any] | None = None,
) -> str:
    from . import resident_conversation_projection_domain
    details = runtime_error if isinstance(runtime_error, dict) else {}
    message = str(details.get("message") or raw_error or "").strip()
    folded = message.casefold()
    code = str(details.get("code") or _runtime_error_code(message)).strip().lower()
    if "app-server" in folded and any(
        marker in folded
        for marker in ("connection", "closed", "disconnected", "lost", "unavailable")
    ):
        return "Codex app-server 连接中断"
    if "execution ended with status cancelled" in folded or "execution was cancelled" in folded:
        return "执行已取消"
    if "stopped by" in folded or "execution stopped" in folded:
        return "执行被停止"
    summaries = {
        "auth": "模型服务认证失败",
        "configuration": "Codex 配置加载失败",
        "rate_limit": "模型服务达到速率限制",
        "timeout": "执行超时",
        "database_lock_timeout": "数据库锁等待超时",
        "permission": "执行权限不足",
        "network": "模型后端连接中断",
        "provider_turn_interrupted": "模型后端中断了本轮执行",
        "provider_usage_limit": "当前模型账号额度不足",
    }
    if code in summaries:
        return summaries[code]
    compact = re.sub(r"\s+", " ", message).strip()
    return resident_conversation_projection_domain.truncate_text(compact or "执行意外结束", 240)


def resolve_context_refs(
    selection: object,
    actor: str = "",
    content: object = "",
) -> dict[str, Any]:
    return agent_context_selection.resolve(
        selection,
        actor=actor,
        content=content,
    )


def attached_references_text(context_refs: dict[str, Any]) -> str:
    return agent_context_selection.prompt_references(
        context_refs,
        item_char_limit=REF_ITEM_CHAR_LIMIT,
        total_char_limit=REF_TOTAL_CHAR_LIMIT,
    )
