"""Resident domain responsibilities: intake."""
from __future__ import annotations

from . import (
    lineage_message_events,
    family_facade,
    identity_contract,
    invariant_locks,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_execution_provider,
    resident_held_infos,
    resident_input_scope,
    resident_intake_admission,
    resident_execution_work,
    resident_lineage,
    resident_message_contract,
    resident_state,
    resident_record_domain,
)
from .domain_ports import execution_port, resident_lineage_host_port
from .resident_access import (
    ensure_resident_visible_to_actor,
    resident_deliverable_to_actor as _resident_deliverable_to_actor,
    resident_visible_to_actor as _resident_visible_to_actor,
)
from contextlib import ExitStack
from datetime import datetime, timezone
from management_common.actor_identity import canonical_actor_id
from management_common.skill_paths import normalize_additional_skills
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service.resident_execution_work import execution_status
from management_portal.portal_access_control import get_portal_access_store
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
import copy
import hashlib
import logging
from management_portal.execution import slash_commands
import re
import secrets
import sys
import time

from . import resident_domain_common


MAX_RUNTIME_CONTEXT_CHARS = 120_000


def start_resident_continuation(record: dict, continuation_id: str) -> None:
    from . import resident_execution_domain
    resident_execution_work.start(
        record,
        continuation_id,
        resident_execution_domain.run_resident_execution,
    )


def finish_resident_continuation(
    resident_id: str,
) -> None:
    """Release one waiter and re-wake work persisted during its shutdown."""
    from . import resident_execution_domain, resident_record_domain

    should_wake = False
    with resident_record_domain.state_lock(resident_id):
        record = resident_execution_domain.resident_record_for_execution(resident_id)
        record_for_key = record or {
            "resident_id": resident_id,
        }
        if not resident_execution_work.finish_current_thread(record_for_key):
            return
        if not record:
            return
        job = resident_execution_work.read(record)
        # Persisted work remains resumable after the current waiter exits.
        should_wake = (
            resident_execution_work.is_active(job)
            and not resident_execution_work.is_execution_held(job)
            and not resident_execution_work.completion_applied(job)
            and bool(str(job.get("execution_id") or ""))
        )
    if should_wake:
        from management_portal.society_service.lifecycle import (
            notify_execution_binding,
        )

        notify_execution_binding(resident_id)

def acknowledge_execution(job: dict[str, Any]) -> bool:
    execution_id = resident_execution_work.submitted_execution_id(job)
    if not execution_id:
        return True
    actor = canonical_actor_id(job.get("actor") or "")
    if not actor:
        return False
    service = execution_port.service()
    for attempt in range(5):
        try:
            try:
                service.acknowledge(execution_id, actor=actor)
            except KeyError:
                pass  # An already-pruned execution has no remaining acknowledgement work.
            job["execution_acknowledged"] = True
            resident_execution_work.acknowledge_execution(job)
            return True
        except Exception as exc:
            if execution_port.is_unavailable(exc):
                if attempt >= 4:
                    print(
                        "Resident Execution acknowledgement deferred: "
                        f"{execution_id}: {exc}",
                        file=sys.stderr,
                    )
                    return False
                time.sleep(0.1 * (2 ** attempt))
                continue
            print(
                "Resident Execution acknowledgement failed: "
                f"{execution_id}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return False
    return False


def _active_resident_execution(record: dict) -> dict[str, Any]:
    job = resident_execution_work.read(record)
    return job if resident_execution_work.is_active(job) else {}


def _queue_item_matches(item: dict[str, Any], queue_item_id: str) -> bool:
    target = str(queue_item_id or "").strip()
    if not target:
        return False
    return target == str(item.get("user_message_id") or "")


def promote_queue_after_failed_execution(
    job: dict[str, Any],
    resident_id: str,
    now: str,
    error_message: str,
    runtime_error: dict[str, Any],
) -> bool:
    queue = resident_execution_work.waiting_input_queue(job)
    if not queue:
        return False
    failed_execution_id = str(job.get("execution_id") or "")
    if not str(job.get("failure_message_persisted_at") or ""):
        raise RuntimeError("Failed job queue promotion requires a persisted failure message")
    failed_message_id = str(job.get("user_message_id") or "").strip()
    if failed_message_id:
        # Its failure notice answers it in the trace.
        resident_conversation_stream.get_repository().set_input_state(
            resident_id,
            failed_message_id,
            "failed",
            error=runtime_error,
            updated_at=now,
        )
    next_job = queue.pop(0)
    resident_execution_work.promote_queue_item(job, next_job, queue, now)
    job["previous_execution_failure"] = {
        "execution_id": failed_execution_id,
        "error": error_message[:1000],
        "runtime_error": runtime_error,
        "failed_at": now,
    }
    return True


def build_message(
    role: str,
    content: str,
    author: str = "",
    *,
    message_id: str = "",
    created_at: str = "",
    reply_to_message_id: str = "",
    content_parts: list[dict[str, Any]] | None = None,
    usage: Mapping[str, Any] | None = None,
    provider_response: Mapping[str, Any] | None = None,
    execution_id: str = "",
) -> dict[str, Any]:
    from . import resident_domain_common
    if role not in {"user", "agent", "system"}:
        raise ValueError("Resident Message role must be user, agent, or system")
    identity = resident_message_contract.message_identity(message_id)
    item: dict[str, Any] = {
        "message_id": identity,
        "role": role,
        "content_parts": copy.deepcopy(content_parts)
        if content_parts is not None
        else [{"type": "text", "text": content}],
        "created_at": str(created_at or resident_domain_common.now_iso()),
    }
    if role == "system":
        from management_common.principal import message_origin

        item["author_id"] = "portal"
        item["origin"] = message_origin(
            "portal", initiated_by=author if str(author or "").casefold() != "portal" else "",
        )
    elif str(author or "").strip():
        from management_common.principal import message_origin

        item["author_id"] = str(author).strip()
        item["origin"] = message_origin(author)
    if role != "user" and str(reply_to_message_id or "").strip():
        item["reply_to_message_id"] = str(reply_to_message_id).strip()
    if isinstance(usage, Mapping) and usage:
        item["usage"] = copy.deepcopy(dict(usage))
    if isinstance(provider_response, Mapping) and provider_response:
        item["provider_response"] = copy.deepcopy(dict(provider_response))
    if execution_id:
        item["execution_id"] = str(execution_id)
    return item


def _resident_user_message(
    resident_id: str,
    content: str,
    actor: str,
    *,
    message_id: str,
    protocol_input: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the one durable user Message used by every delivery path."""

    item = build_message(
        "user",
        content,
        actor,
        message_id=message_id,
    )
    from management_common.operation_identity import current_operation_identity
    from management_common.principal import message_origin, message_quote

    identity = current_operation_identity()
    if identity is not None:
        if identity.principal.id != actor:
            raise PermissionError("Resident input sender disagrees with authenticated operation")
        item["origin"] = message_origin(actor, initiated_by=identity.initiated_by)
    if not isinstance(protocol_input, Mapping):
        return item
    supplied_id = str(protocol_input.get("message_id") or "").strip()
    if supplied_id and supplied_id != message_id:
        raise ValueError("Resident input message_id changed during delivery")
    supplied_role = str(protocol_input.get("role") or "user").strip().casefold()
    if supplied_role != "user":
        raise ValueError("DeliverMessage accepts user messages only")
    parts = protocol_input.get("content_parts")
    if not isinstance(parts, list) or not parts:
        raise ValueError("message.content_parts required")
    item["content_parts"] = copy.deepcopy(parts)
    # Delivery has checked the quote and filled in its author and excerpt (message_quotes.accept).
    quote = message_quote(protocol_input)
    if quote:
        item["quote"] = quote
    existing = resident_conversation_stream.get_repository().message(
        resident_id,
        message_id,
    )
    if existing is not None:
        item["created_at"] = str(existing.get("created_at") or item["created_at"])
        if "origin" not in existing:
            item.pop("origin", None)
    return item


def existing_resident_delivery(
    resident_id: str,
    message: Mapping[str, Any],
    *,
    bound_job: Mapping[str, Any] | None = None,
    require_execution_binding: bool = False,
) -> bool:
    """Validate an idempotent retry and reject incomplete intake commits."""
    from . import resident_lifecycle_domain, resident_record_domain

    repository = resident_conversation_stream.get_repository()
    message_id = str(message.get("message_id") or "").strip()
    existing = repository.message(resident_id, message_id)
    if existing is not None:
        candidate = dict(message)
        candidate["created_at"] = str(existing.get("created_at") or "")
        repository.validate_message_identity(resident_id, candidate)
    else:
        try:
            previous_input_state = repository.input_state(resident_id, message_id)
        except KeyError:
            previous_input_state = {}
        if str(previous_input_state.get("state") or "") in {
            "completed", "failed", "cancelled",
        }:
            return True
        job = dict(bound_job or {})
        queued = resident_execution_work.input_location(resident_id, message_id, job)
        if queued is None:
            return False
        stored = queued.get("input_message")
        if not isinstance(stored, Mapping):
            return False
        for field in ("message_id", "role", "author_id", "content_parts"):
            if stored.get(field) != message.get(field):
                raise resident_message_contract.MessageIdentityConflict("Resident input changed during idempotent retry")
        queued_message = copy.deepcopy(dict(stored))

        def repair_queued_input() -> None:
            resident_lifecycle_domain.accept_resident_user_message(resident_id, queued_message)
            record = resident_record_domain.find_resident(resident_id)
            if record:
                resident_execution_work.project_latest_conversation_state(record, job)

        invariant_locks.defer_until_state_unlock(repair_queued_input)
        return True
    if not require_execution_binding:
        return True
    try:
        input_state = repository.input_state(resident_id, message_id)
    except KeyError:
        input_state = {}
    if str(input_state.get("state") or "") in {"completed", "failed", "cancelled"}:
        return True
    job = dict(bound_job or {})
    return resident_execution_work.input_location(resident_id, message_id, job) is not None


def persist_failed_execution_message(
    resident_id: str,
    job: dict[str, Any],
    error_message: str,
    runtime_error: dict[str, Any],
    *,
    failed_at: str = "",
) -> dict[str, Any]:
    from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain
    message_id = resident_execution_work.failed_message_id(job, failed_at=failed_at)
    repository = resident_conversation_stream.get_repository()
    existing = repository.message(resident_id, message_id)
    if existing is not None:
        job["failure_message_id"] = message_id
        job["failure_message_persisted_at"] = str(
            existing.get("created_at") or failed_at or resident_domain_common.now_iso()
        )
        return repository.public_message(resident_id, message_id)
    details = dict(runtime_error) if isinstance(runtime_error, dict) else {}
    if not details:
        details = resident_record_domain.runtime_error_payload(error_message)
    summary = resident_record_domain.runtime_failure_summary(error_message, details)
    item = build_message(
        "system",
        summary,
        message_id=message_id,
        reply_to_message_id=str(job.get("user_message_id") or ""),
        created_at=str(
            failed_at
            or job.get("finished_at")
            or job.get("updated_at")
            or resident_domain_common.now_iso()
        ),
        # The failed turn's Execution: the notice shows what that turn used.
        execution_id=resident_execution_work.submitted_execution_id(job),
    )
    persisted = resident_lifecycle_domain.append_conversation_message(
        resident_id,
        item,
    )
    job["failure_message_id"] = message_id
    job["failure_message_persisted_at"] = str(item.get("created_at") or resident_domain_common.now_iso())
    return persisted


def _portal_command_message(
    result: dict[str, Any],
    actor: str,
    *,
    reply_to_message_id: str = "",
) -> dict[str, Any]:
    reply_to = str(reply_to_message_id or "").strip()
    return build_message(
        "system",
        str(result.get("content") or ""),
        message_id=(
            resident_message_contract.derived_message_identity(
                "portal-command-reply",
                reply_to,
                str(result.get("title") or "Portal command"),
            )
            if reply_to
            else ""
        ),
        reply_to_message_id=reply_to,
    )


def _persist_synchronous_command_exchange(
    resident_id: str,
    actor: str,
    content: str,
    message_id: str,
    result: dict[str, Any],
    *,
    user_message: Mapping[str, Any] | None = None,
) -> None:
    from . import resident_lifecycle_domain
    persisted_user_message = resident_lifecycle_domain.persist_resident_user_message(
        resident_id,
        dict(user_message) if isinstance(user_message, Mapping) else build_message(
            "user", content, actor, message_id=message_id
        ),
    )
    input_message_id = str(persisted_user_message.get("message_id") or message_id)
    resident_lifecycle_domain.append_conversation_message(
        resident_id,
        _portal_command_message(
            result,
            actor,
            reply_to_message_id=input_message_id,
        ),
    )
    resident_conversation_stream.get_repository().set_input_state(
        resident_id,
        input_message_id,
        "completed",
    )


def compact_portal_command_message(
    result: dict[str, Any],
    actor: str,
    *,
    reply_to_message_id: str = "",
) -> dict[str, Any]:
    return _portal_command_message(
        result,
        actor,
        reply_to_message_id=reply_to_message_id,
    )


def _queue_resident_compaction(
    record: dict[str, Any],
    actor_id: str,
    message_id: str,
    *,
    user_message: Mapping[str, Any] | None = None,
) -> None:
    from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain
    now = resident_domain_common.now_iso()
    resident_id = resident_domain_common.record_resident_id(record)
    session_id = resident_backend.record_session_id(record)
    running = _active_resident_execution(record)
    user_item = (
        dict(user_message)
        if isinstance(user_message, Mapping)
        else build_message("user", "/compact", actor_id, message_id=message_id)
    )
    input_message_id = str(user_item.get("message_id") or message_id)
    item = resident_execution_work.new_compact_item(
        actor_id,
        session_id,
        "Resident Session",
        now,
        input_message_id,
    )
    item["input_message"] = copy.deepcopy(user_item)
    if running:
        queue = resident_execution_work.normalized_queue(running)
        item.pop("execution_id", None)
        queue.append(item)
        running["queue"] = queue
        running["updated_at"] = now
        resident_execution_work.write(record, running)
        resident_lifecycle_domain.mark_resident_input_queued(resident_id, user_item)
        record["updated_at"] = now
        resident_record_domain.save_resident_record(record)
        return
    job = {
        **item,
        "started_at": now,
        "updated_at": now,
        "error": "",
        "queue": [],
    }
    resident_execution_work.write(record, job)
    record["session_state"] = resident_record_domain.record_backend_session_state(record)
    record["updated_at"] = now
    resident_record_domain.save_resident_record(record)


def _context_refs_include_image(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            text = str(item).lower() if isinstance(item, str) else ""
            if name in {"mime", "mime_type", "content_type"} and text.startswith("image/"):
                return True
            if name in {"path", "file_path", "name", "filename", "url"} and any(
                text.split("?", 1)[0].endswith(suffix) for suffix in resident_domain_common.RESIDENT_IMAGE_SUFFIXES
            ):
                return True
            if _context_refs_include_image(item):
                return True
    elif isinstance(value, list):
        return any(_context_refs_include_image(item) for item in value)
    return False


def _ensure_backend_accepts_context_refs(record: dict[str, Any], context_refs: object) -> None:
    if not resident_execution_provider.provider_for(record).capabilities.supports_media and _context_refs_include_image(context_refs):
        raise ValueError("Claude Code + DeepSeek 当前不支持图片输入；请改用文字或文本文件")


def normalized_runtime_context(value: object) -> str:
    runtime_context = str(value or "").strip()
    if len(runtime_context) > MAX_RUNTIME_CONTEXT_CHARS:
        raise ValueError(f"runtime_context 最多 {MAX_RUNTIME_CONTEXT_CHARS} 个字符")
    return runtime_context


def _send_synchronous_provider_message(
    record: dict[str, Any],
    actor_id: str,
    message: str,
    *,
    message_id: str,
    user_message: Mapping[str, Any],
) -> dict:
    """Record a deterministic provider invocation as one Agent Execution."""
    from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain
    provider = resident_execution_provider.provider_for(record)
    resident_id = resident_domain_common.record_resident_id(record)

    # Keep the Resident registry lock out of the Lineage mutation boundary.
    # Resident registration takes Lineage -> Resident locks, so the reverse
    # order here could deadlock with a concurrent node creation.

    from uuid import uuid4
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution import execution_inputs
    execution_id = "execution-" + uuid4().hex[:24]
    store = get_agent_data_store()
    started_at = resident_domain_common.now_iso()
    with store.database.transaction():
        store.begin_execution(execution_id=execution_id, agent_id=resident_id,
                              actor_ref=actor_id, provider="portal", started_at=started_at)
        execution_inputs.attach(store, execution_id, [{
            "message_id": message_id, "role": "primary", "accepted_at": started_at,
        }])
    try:
        outcome = provider.deliver(
            resident_execution_provider.DeliveryRequest(
                lineage_id=str(record.get("lineage_id") or ""),
                resident_id=str(record.get("resident_id") or ""),
                message=message,
                actor_id=actor_id,
                resident=record,
                delivery=user_message,
            )
        )
        reply = outcome.reply.strip()
        if not reply:
            raise RuntimeError("Synchronous ExecutionProvider did not produce a reply")

        with resident_record_domain.state_lock(resident_id):
            current = resident_record_domain.find_resident(str(record.get("resident_id") or ""))
            if not current:
                raise KeyError("resident not found")
            if not _resident_deliverable_to_actor(current, actor_id):
                raise KeyError("resident not found")
            if not resident_execution_provider.provider_for(
                current
            ).capabilities.synchronous_delivery:
                raise ValueError("Resident 已不再使用同步 ExecutionProvider")

            user_item = dict(user_message)
            resident_lifecycle_domain.append_conversation_message(
                resident_domain_common.record_resident_id(current),
                {**build_message(
                    "agent",
                    reply,
                    message_id=resident_message_contract.derived_message_identity(
                        "portal-resident-reply",
                        str(user_item.get("message_id") or ""),
                        "final",
                    ),
                    reply_to_message_id=str(user_item.get("message_id") or ""),
                ), "execution_id": execution_id},
            )
            store.finish_execution(execution_id, status="completed", finished_at=resident_domain_common.now_iso())
            resident_conversation_stream.get_repository().set_input_state(
                resident_domain_common.record_resident_id(current),
                str(user_item.get("message_id") or ""),
                "completed",
            )
            current["session_state"] = "active"
            current["last_error"] = ""
            current["updated_at"] = resident_domain_common.now_iso()
            resident_record_domain.save_resident_record(current)

    except Exception:
        if (store.execution(execution_id) or {}).get("status") not in {"completed", "failed", "cancelled"}:
            store.finish_execution(execution_id, status="failed", finished_at=resident_domain_common.now_iso())
        raise

    return _resident_message_delivery_result(
        str(record.get("resident_id") or ""),
        message_id,
    )


def _resident_message_cwd(
    record: dict[str, Any],
    working_directory: object,
) -> Path:
    from . import resident_record_domain, resident_runtime_domain
    raw = str(working_directory or "").strip()
    default = resident_runtime_domain.resident_working_directory(record)
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Resident 工作目录必须是已存在的绝对目录")
    resolved = path.resolve(strict=False)
    allowed_roots = {
        Path(str(item.get("path") or "")).expanduser().resolve(strict=False)
        for item in resident_record_domain.resident_workspace_roots(record)
        if str(item.get("path") or "").strip()
    }
    if resolved not in allowed_roots:
        raise PermissionError("Resident 工作目录必须是已绑定的 Workspace")
    return resolved


def _resident_message_delivery_result(
    resident_id: str = "",
    message_id: str = "",
) -> dict[str, Any]:
    repository = resident_conversation_stream.get_repository()
    clean_message_id = str(message_id or "").strip()
    if not clean_message_id:
        raise ValueError("Resident delivery acknowledgement requires message_id")
    projection = repository.current_projection(resident_id)
    stored_message = repository.message(resident_id, clean_message_id)
    result = {
        "resident_id": str(resident_id or "").strip(),
        "message_id": clean_message_id,
        "state": dict(projection.get("state") or {}),
        "queue": copy.deepcopy(dict(projection.get("queue") or {})),
        "cursor": int(projection.get("cursor") or 0),
    }
    if stored_message is not None:
        # Admission may move the input between Mailbox and Chat while the receipt is built.
        # The first read already decoded the public message; do not race a second lookup.
        result["message"] = stored_message
    try:
        result["input_state"] = repository.input_state(
            resident_id,
            clean_message_id,
        )
    except KeyError:
        pass
    return result


def _accepted_delivery(resident_id: str, message_id: str, **fields: Any) -> dict[str, Any]:
    """A durable receipt is independent of an eventually published chat snapshot."""
    return {"resident_id": resident_id, "message_id": message_id, "accepted": True, **fields}


def _save_admission_observations(record: Mapping[str, Any]) -> None:
    """Admission changes only these observations, never identity or Workspace."""
    from .resident_catalog import get_catalog
    from . import resident_record_domain
    def write(connection):
        connection.execute("UPDATE residents SET updated_at=%s WHERE resident_id=%s",
                           (record["updated_at"], record["resident_id"]))
        connection.execute("UPDATE resident_sessions SET session_state=%s WHERE resident_id=%s",
                           (resident_record_domain.record_backend_session_state(record), record["resident_id"]))
        if "last_error" in record:
            connection.execute("UPDATE resident_observation_cache SET last_error=%s WHERE resident_id=%s",
                               (record["last_error"], record["resident_id"]))
    get_catalog().state_store.database.write(write)


def resident_operation_result(
    resident_id: str,
    **fields: Any,
) -> dict[str, Any]:
    projection = resident_conversation_stream.get_repository().current_projection(
        resident_id
    )
    return {
        "resident_id": str(resident_id or "").strip(),
        "state": dict(projection.get("state") or {}),
        "queue": copy.deepcopy(dict(projection.get("queue") or {})),
        "cursor": int(projection.get("cursor") or 0),
        **fields,
    }


def resident_input_control_id(resident_id: object, message_id: object) -> str:
    identity = f"{str(resident_id or '').strip()}:{str(message_id or '').strip()}"
    return f"control-{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"


def _submit_active_turn_input(
    record: dict[str, Any],
    actor_id: str,
    *,
    control_key: str,
    turn_input: list[dict[str, Any]],
    source: str,
    expected_execution_id: str,
) -> dict[str, Any]:
    """Submit one idempotent control to the Resident's exact active execution."""
    from . import resident_domain_common, resident_record_domain

    resident_id = resident_domain_common.record_resident_id(record)
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id) or record
        job = resident_execution_work.read(record)
        if (
            resident_execution_work.submitted_execution_id(job) != expected_execution_id
            or not resident_execution_work.is_active(job)
            or str(job.get("stop_requested_at") or "").strip()
            or str(job.get("execution_terminal_status") or "").strip()
            in {"completed", "failed", "cancelled"}
        ):
            return {
                "requested": False,
                "status": "inactive",
                "resident_id": resident_id,
            }
        normalized_input = [
            dict(part)
            for part in turn_input
            if isinstance(part, dict)
        ]
        if not normalized_input:
            raise ValueError("active turn input required")
        normalized_control_key = str(control_key or "").strip()
        if not normalized_control_key:
            raise ValueError("active turn control_key required")
        control_id = resident_input_control_id(resident_id, normalized_control_key)
        execution_id = resident_execution_work.submitted_execution_id(job)

        if not execution_id:
            now = resident_domain_common.now_iso()
            pending_inputs = [
                dict(item)
                for item in job.get("pending_active_turn_inputs") or []
                if isinstance(item, dict)
                and str(item.get("control_id") or "").strip() != control_id
            ]
            pending_inputs.append({
                "message_id": normalized_control_key,
                "control_id": control_id,
                "actor": actor_id,
                "input": normalized_input,
                "source": str(source or "").strip(),
                "created_at": now,
            })
            job["pending_active_turn_inputs"] = pending_inputs
            job["updated_at"] = now
            resident_execution_work.write(record, job)
            record["updated_at"] = now
            resident_record_domain.save_resident_record(record)
            return {
                "requested": True,
                "status": "pending",
                "resident_id": resident_id,
                "execution_id": "",
                "control_id": control_id,
                "source": str(source or "").strip(),
                "pending_execution_binding": True,
            }

    if execution_id:
        delivery = execution_port.service().append_input(
            execution_id,
            actor=actor_id,
            input=normalized_input,
            control_id=control_id,
            source_segment_id=execution_port.input_source_segment_id(resident_id, normalized_control_key),
        )
        status = str(delivery.get("status") or "").strip()
        if status not in {"delivered", "pending"}:
            raise RuntimeError(
                str(delivery.get("error") or "无法把输入插入当前 Codex turn")
            )
        return {
            "requested": True,
            "status": status,
            "resident_id": resident_id,
            "execution_id": execution_id,
            "control_id": control_id,
            "source": str(source or "").strip(),
        }


def submit_active_turn_settings(
    record: dict[str, Any],
    actor_id: str,
    *,
    control_key: str,
    service_tier: str,
    source: str,
) -> dict[str, Any]:
    """Publish a Provider-native speed update to the exact active turn."""
    from . import resident_domain_common, resident_record_domain


    resident_id = resident_domain_common.record_resident_id(record)
    if not str(control_key or "").strip():
        raise ValueError("active turn settings control_key required")
    control_id = resident_input_control_id(resident_id, f"turn-settings:{control_key}")
    with resident_record_domain.state_lock(resident_id):
        current_record = resident_record_domain.find_resident(resident_id) or record
        job = resident_execution_work.read(current_record)
        if (not resident_execution_work.is_active(job) or job.get("stop_requested_at")
                or job.get("execution_terminal_status") in {"completed", "failed", "cancelled"}):
            return {"requested": False, "status": "inactive", "resident_id": resident_id}
        execution_id = resident_execution_work.submitted_execution_id(job)
        expected_execution_id = str(job.get("execution_id") or "")
        item = {"control_id": control_id, "actor": actor_id, "service_tier": str(service_tier or ""),
                "source": source, "created_at": resident_domain_common.now_iso()}
        job["pending_active_turn_settings"] = [item]
        job["updated_at"] = item["created_at"]
        resident_execution_work.write(current_record, job)
    result = {"requested": True, "status": "pending", "resident_id": resident_id,
              "execution_id": execution_id, "control_id": control_id, "source": source}
    if not execution_id:
        result["pending_execution_binding"] = True
        return result

    def deliver():
        try:
            delivery = execution_port.service().update_turn_settings(
                execution_id, actor=actor_id, service_tier=item["service_tier"], control_id=control_id,
            )
            status = str(delivery.get("status") or "")
            if status not in {"delivered", "pending"}:
                raise RuntimeError(str(delivery.get("error") or "无法更新当前 Codex turn 的 Speed"))
        except Exception as exc:
            result.update(status="failed", error=str(exc))
            return
        with resident_record_domain.state_lock(resident_id):
            current_record = resident_record_domain.find_resident(resident_id) or {}
            current = resident_execution_work.read(current_record) if current_record else {}
            if str(current.get("execution_id") or "") != expected_execution_id or resident_execution_work.submitted_execution_id(current) != execution_id:
                return
            remaining = [value for value in current.get("pending_active_turn_settings") or [] if value != item]
            if remaining:
                current["pending_active_turn_settings"] = remaining
            else:
                current.pop("pending_active_turn_settings", None)
            resident_execution_work.write(current_record, current)
        result["status"] = status
    invariant_locks.defer_until_state_unlock(deliver)
    return result


def sync_resident_quota_overrides(residents: list[dict[str, Any]] | None = None) -> bool:
    if residents is None:
        from . import resident_catalog, resident_storage
        catalog = resident_catalog.get_catalog()
        with catalog.state_store.database.read() as connection:
            ids = resident_storage.quota_override_resident_ids(connection)
        residents = catalog.get_many(ids)
    policies = [policy for record in residents if (policy := resident_record_domain.resident_quota_override(record))]
    if not policies:
        return True
    try:
        execution_port.configure_binding_quota_overrides(policies)
        return True
    except Exception as error:
        print(f"Resident quota override sync failed: {type(error).__name__}", file=sys.stderr)
        return False


def commit_resident_intake_state(
    record: dict[str, Any], next_state: str, *,
    commit_configuration: bool = False,
) -> dict[str, Any]:
    from . import resident_domain_common, resident_record_domain
    now = resident_domain_common.now_iso()
    target_id = resident_domain_common.record_resident_id(record)
    repository = resident_conversation_stream.get_repository()
    current_state = repository.published_state(target_id)
    previous_state = "closed" if record.get("intake_open") is False else "open"
    if next_state == resident_state.INTAKE_OPEN:
        job = resident_execution_work.read_control(target_id).get("state") or {}
        if resident_execution_work.is_active(job) and resident_execution_work.is_intake_held(job):
            record["session_state"] = resident_record_domain.record_backend_session_state(record)
    record["updated_at"] = now
    record["intake_transition_id"] = secrets.token_hex(16)
    record["intake_open"] = next_state == resident_state.INTAKE_OPEN
    resident_record_domain.save_resident_record(record, commit_configuration=commit_configuration, commit_intake=True)
    repository.set_state(target_id, {**current_state, "intake_state": next_state})
    return {
        "resident_id": target_id, "state_domain": resident_state.INTAKE_DOMAIN,
        "field": resident_state.INTAKE_FIELD, "previous_state": previous_state,
        "state": next_state, "intake_state": next_state,
        "changed": previous_state != next_state, "updated_at": now,
    }


def _execution_account_routes(
    accounts: Iterable[Mapping[str, Any]],
) -> dict[tuple[str, str], str]:
    routes: dict[tuple[str, str], str] = {}
    for account in accounts:
        account_id = str(account.get("account_id") or "").strip()
        provider = str(account.get("provider") or "").strip()
        route_id = str(account.get("backend_route_id") or "").strip()
        if not account_id or not provider:
            raise ValueError("Execution account catalog entry is incomplete")
        key = (provider, route_id)
        previous = routes.get(key)
        if previous and previous != account_id:
            raise ValueError("Execution account routes must be unique per provider")
        routes[key] = account_id
        routes.setdefault((account_id, ""), account_id)
    return routes


def _resident_quota_account_resolver(
    accounts: Iterable[Mapping[str, Any]],
) -> Callable[[Mapping[str, Any], Mapping[str, Any]], str]:
    """Resolve account ownership once per distinct unbound Resident route."""

    account_routes = _execution_account_routes(accounts)
    resolved_defaults: dict[tuple[str, str], str] = {}

    def resolve(
        record: Mapping[str, Any],
        binding: Mapping[str, Any],
    ) -> str:
        provider = resident_backend.execution_provider(record)
        # A fresh release deliberately has no source Model API route. Such a
        # Resident is unassigned, not an error in every account's read-only view.
        if not provider:
            return ""
        route_id = resident_backend.execution_account_route(record, binding)
        if route_id or (provider, route_id) in account_routes:
            return str(account_routes.get((provider, route_id)) or "")

        configuration = record.get("backend_binding") or {}
        provider_profile = str(
            record.get("provider_profile")
            or configuration.get("provider_profile")
            or ""
        ).strip()
        selector = (provider, provider_profile)
        if selector not in resolved_defaults:
            account = execution_port.service().resolve_account(
                provider,
                runtime_options={
                    "provider_profile": provider_profile,
                },
            )
            account_id = str(account.get("account_id") or "").strip()
            resolved_route = str(account.get("backend_route_id") or "").strip()
            if (
                account_id
                and account_routes.get((provider, resolved_route)) != account_id
            ):
                raise ValueError(
                    "Resolved Execution account is absent from the account catalog"
                )
            resolved_defaults[selector] = account_id
        return resolved_defaults[selector]

    return resolve


def _require_execution_account(
    account_id: str,
    accounts: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    for account in accounts:
        if account["account_id"] == account_id:
            return dict(account)
    raise ValueError("Unknown Execution account")


@resident_lineage.access_snapshot_scope()
def get_account_resident_summary(account_id: str, actor: str) -> dict[str, Any]:
    from . import resident_domain_common
    operator = canonical_actor_id(actor)
    if not operator:
        raise PermissionError("需要登录后查看账号节点详情")
    account_id = str(account_id or "").strip()
    if not account_id:
        raise ValueError("account_id required")
    accounts = execution_port.service().accounts().get("accounts", [])
    _require_execution_account(account_id, accounts)
    resolve_account = _resident_quota_account_resolver(accounts)
    # Only the Residents the caller can read (GetResident) are counted, whoever the caller is.
    residents = [
        record for record in resident_lineage.all()
        if isinstance(record, dict) and _resident_visible_to_actor(record, operator)
    ]
    bindings = resident_backend.session_routes_for_bindings([
        resident_backend.execution_binding_id(record) for record in residents
    ])
    selected = [
        record for record in residents
        if resolve_account(
            record,
            bindings.get(resident_backend.execution_binding_id(record)) or {},
        ) == account_id
    ]
    snapshots = resident_conversation_stream.get_repository().state_snapshots([
        resident_domain_common.record_resident_id(record) for record in selected
    ])
    intake = {"open": 0, "closed": 0, "unknown": 0}
    # The page opens or closes these with OpenIntake resident_ids.
    intake_ids: dict[str, list[str]] = {"open": [], "closed": []}
    work = {"idle": 0, "queued": 0, "running": 0, "stopping": 0, "unknown": 0}
    latest_input = {"failed": 0, "cancelled": 0}
    queued_messages = no_session = exempt = 0
    for record in selected:
        snapshot = snapshots.get(resident_domain_common.record_resident_id(record)) or {}
        state = snapshot.get("state") or {}
        intake_state = state.get("intake_state", "unknown")
        if intake_state == "closed":
            intake["closed"] += 1
        else:
            intake[intake_state if intake_state == "open" else "unknown"] += 1
        if intake_state in intake_ids:
            intake_ids[intake_state].append(resident_domain_common.record_resident_id(record))
        work_state = state.get("work_state", "unknown")
        work[work_state if work_state in work else "unknown"] += 1
        queued_messages += max(0, int(state.get("queued_count") or 0))
        last_state = (snapshot.get("latest_input_state") or {}).get("state")
        if last_state in latest_input:
            latest_input[last_state] += 1
        binding = bindings.get(resident_backend.execution_binding_id(record)) or {}
        no_session += int(not binding.get("backend_session_id"))
        exempt += int(resident_record_domain.resident_quota_override(record).get("enabled") is True)
    quota = next(
        (
            item.get("quota_reserve")
            for item in accounts
            if item["account_id"] == account_id
        ),
        None,
    ) or {"status": "unknown"}
    return {
        "account_id": account_id, "observed_at": resident_domain_common.now_iso(),
        "scope": "visible", "total": len(selected),
        "intake": intake, "open_resident_ids": intake_ids["open"], "closed_resident_ids": intake_ids["closed"],
        "work": work, "latest_input": latest_input,
        "queued_messages": queued_messages, "without_session": no_session,
        "quota_exempt": exempt, "quota": quota,
    }


def _commit_manual_resident_intake_state(
    record: dict[str, Any], next_state: str, *, allow_quota_override: bool,
) -> dict[str, Any]:
    """Commit a human choice under the caller's Resident lock and authorization."""
    policy = resident_record_domain.resident_quota_override(record)
    commit_configuration = bool(
        allow_quota_override or (next_state == resident_state.INTAKE_CLOSED and policy)
    )
    if commit_configuration:
        record["quota_reserve_override"] = {
            "binding_id": resident_backend.execution_binding_id(record),
            "enabled": next_state == resident_state.INTAKE_OPEN,
            "revision": int(policy.get("revision") or 0) + 1,
        }
    return commit_resident_intake_state(
        record, next_state, commit_configuration=commit_configuration,
    )


def _change_resident_intake_state(
    resident_id: str,
    actor: str,
    intake_state: str,
    *,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    from . import resident_record_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后切换 Agent 接单状态")
    next_state = str(intake_state or "").strip().lower()
    if next_state not in resident_state.INTAKE_VALUES:
        raise ValueError("intake_state 必须是 open 或 closed")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident_canonical(resident_id)
        if not record:
            raise KeyError("resident not found")
        ensure_resident_visible_to_actor(record, actor_id)
        human_actor = not actor_id.startswith("resident_") and not get_portal_access_store().is_service_actor(actor_id)
        result = _commit_manual_resident_intake_state(
            record, next_state, allow_quota_override=human_actor and allow_quota_override,
        )
    result["quota_override_pending"] = not sync_resident_quota_overrides([record])
    if next_state == resident_state.INTAKE_OPEN:
        from .lifecycle import notify_execution_binding
        notify_execution_binding(resident_id)
    return result

def transition_resident_state(
    resident_id: str,
    actor: str,
    *,
    state_domain: str = resident_state.INTAKE_DOMAIN,
    transition: str,
    allow_quota_override: bool = True,
) -> dict[str, Any]:
    from . import resident_record_domain
    domain = str(state_domain or "").strip().lower() or resident_state.INTAKE_DOMAIN
    if domain != resident_state.INTAKE_DOMAIN:
        raise ValueError(f"Resident state module 不拥有状态域：{domain}")
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    result = _change_resident_intake_state(
        target.resident_id,
        actor,
        resident_state.intake_state_for_transition(transition),
        allow_quota_override=allow_quota_override,
    )
    result["resident_id"] = target.resident_id
    return dict(result)


@resident_lineage.access_snapshot_scope()
def persist_resident_input(
    resident_id: str,
    actor: str,
    content: str,
    context_refs: object = None,
    working_directory: str = "",
    runtime_context: str = "",
    additional_skills: object = (),
    activation_policy: str = "enqueue_and_trigger",
    lineage_id: str = "",
    input_media: object = None,
    message_id: str = "",
    protocol_input: Mapping[str, Any] | None = None,
    business_ref: str = "",
) -> dict:
    from . import (
        resident_directory_domain,
        resident_domain_common,
        resident_lifecycle_domain,
        resident_record_domain,
    )
    from . import message_quotes
    from .outward_messages import speaking_for
    from . import (
        resident_media_domain,
        resident_record_domain,
        resident_registration_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
    )
    def _save_resident_record(record):
        if record.get("default_cwd"):
            return resident_record_domain.save_resident_record(record)
        return _save_admission_observations(record)
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后发送")
    message = str(content or "")
    if not message.strip():
        raise ValueError("content required")
    message_id = resident_message_contract.message_identity(message_id)
    activation = resident_message_contract.activation_policy(activation_policy)
    isolated_input = resident_input_scope.permits(resident_id, actor_id, message_id)
    if isolated_input:
        if activation == "inject_active_turn":
            raise ValueError("Isolated input cannot join another active turn")
        import hashlib
        profile = resident_input_scope.get(resident_id, message_id)["profile"]
        if hashlib.sha256(message.encode()).hexdigest() != profile["input_sha256"]:
            raise ValueError("Resident input differs from its authorized invocation")
        if context_refs or input_media or working_directory or runtime_context or additional_skills:
            raise ValueError("Isolated input context must be contained in the authorized message")
    resolved_runtime_context = normalized_runtime_context(runtime_context)
    resolved_additional_skills = normalize_additional_skills(additional_skills)
    # A Family Router delivery carries the facade message it relays; the
    # facade message authorizes delivery to members of that Family.
    relay_source = family_facade.validate_relay(business_ref, actor_id) if business_ref else None
    delegated_relay = family_facade.relay_delegates(relay_source, resident_id)
    # A relay that carries its facade message unchanged reads that message's
    # attachments and references with its sender's access, as the facade
    # checked them on arrival; anything else is read with the relayer's own.
    relayed_sender = family_facade.relay_sender(relay_source, protocol_input) if delegated_relay else ""
    # A Family's message out goes as its facade, but its attachments and
    # references are the member's: they are read with the member's own access.
    speaking_member = speaking_for(actor_id)
    attachment_actor = relayed_sender or speaking_member or actor_id
    # A request may quote one message of this Conversation; an info carries
    # only what it says.  The quote is checked and filled in once, here.
    requested_quote = protocol_input.get("quote") if isinstance(protocol_input, Mapping) else None
    if requested_quote not in (None, {}) and (activation == "attach_to_next" or isolated_input):
        raise ValueError("info 消息和授权调用的输入不带引用")
    quote_record = resident_record_domain.find_resident_for_admission(resident_id) or {"resident_id": resident_id}
    if requested_quote in (None, {}) and relay_source is None and family_facade.is_facade(quote_record):
        from .resident_questions import answered_set

        # An answer to a member's questions shown on a facade quotes them, so
        # it goes straight back to that member (family_facade.quoted_member).
        asked_in = answered_set(protocol_input)
        if asked_in and message_quotes.quote_of(quote_record, asked_in):
            requested_quote = {"message_id": asked_in}
    quote = message_quotes.accept(
        quote_record, requested_quote, reader=speaking_member or actor_id, message_id=message_id,
        relay_source=relay_source if delegated_relay else None,
    )
    if isinstance(protocol_input, Mapping) and (quote or "quote" in protocol_input):
        protocol_input = {**{key: value for key, value in protocol_input.items() if key != "quote"},
                          **({"quote": quote} if quote else {})}

    deferred_delivery = False

    def resolved_delivery_result() -> dict[str, Any]:
        if activation == "inject_active_turn":
            # Recheck under the same queue lock used by promotion. A turn may
            # finish while context is prepared; the accepted input stays queued.
            with resident_record_domain.state_lock(resident_id):
                current_record = resident_record_domain.find_resident_for_admission(resident_id)
                current = resident_execution_work.read(current_record) if current_record else {}
                if resident_execution_work.is_active(current) and any(
                    _queue_item_matches(item, message_id)
                    for item in resident_execution_work.normalized_queue(current)
                ):
                    inject = True
                else:
                    inject = False
            if inject:
                return {
                    **_inject_queued_input_into_active_turn(resident_id, actor_id, message_id, delivered=True),
                    "activation_policy": activation,
                }
        result = (_accepted_delivery(resident_id, message_id) if deferred_delivery else
                  _resident_message_delivery_result(resident_id=resident_id, message_id=message_id))
        if relay_source is not None:
            # The member now holds the relayed message; close the facade copy.
            family_facade.complete_relay(relay_source, resident_id, message_id)
        if activation != "enqueue_and_trigger":
            result = dict(result)
            result["activation_policy"] = activation
            if deferred_delivery or (result.get("input_state") or {}).get("state") in {"queued", "running"}:
                result["injected"] = False
                result["delivery_status"] = "next_turn"
        return result

    def delivery_result() -> dict[str, Any]:
        resident_intake_admission.committed(resident_id, message_id)
        # A Job write projects its durable input only after every outer state
        # scope releases. Identity is available immediately, including to
        # adapters that copy this receipt; only projections wait for unlock.
        if invariant_locks.lock_held():
            deferred_result: dict[str, Any] = {
                "resident_id": resident_id,
                "message_id": message_id,
            }
            if activation != "enqueue_and_trigger":
                deferred_result["activation_policy"] = activation
            invariant_locks.defer_until_state_unlock(
                lambda: deferred_result.update(
                    resolved_delivery_result()
                )
            )
            return deferred_result
        return resolved_delivery_result()

    # File attachments/explicit cwd require the execution environment. Plain
    # messages need only persisted identity/configuration; resolve paths later.
    has_media = bool(input_media or resident_media_domain.RESIDENT_IMAGE_MARKER_RE.search(message))
    environment = (resident_record_domain.find_resident(resident_id) if has_media or working_directory or message.lstrip().startswith("/")
                   or resident_execution_provider.provider_for(quote_record).capabilities.synchronous_delivery else None)
    synchronous_record: dict[str, Any] = {}
    synchronous_message: dict[str, Any] = {}
    input_record: dict[str, Any] = {}
    with resident_intake_admission.scope(resident_id, message_id), resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident_for_admission(resident_id)
        if not record:
            raise KeyError("resident not found")
        if not isolated_input and not delegated_relay and not _resident_deliverable_to_actor(record, actor_id):
            raise KeyError("resident not found")
        if record.get("deleted_at"):
            raise ValueError("该 Resident 已删除，不能接收消息")
        from .resident_questions import prepare_question_answer

        question_answer = prepare_question_answer(
            resident_domain_common.record_resident_id(record), protocol_input,
            resident_conversation_stream.get_repository(),
            facade=record if family_facade.is_facade(record) else None,
            relay_source=relay_source if relayed_sender else None,
        )
        if question_answer is not None:
            if activation != "enqueue_and_trigger":
                raise ValueError("问题回答需要 enqueue_and_trigger")
            message, protocol_input = question_answer
            if quote:
                protocol_input = {**protocol_input, "quote": quote}
            context_refs = None
        if family_facade.is_facade(record):
            # A Family facade never runs a turn: its requests wait for the
            # Family Router, and an info goes to the Family's steward at once.
            if activation not in {"enqueue_and_trigger", "attach_to_next"} or isolated_input:
                raise ValueError("Family 门面只接收普通消息和 info 消息")
            if activation == "attach_to_next" and (context_refs or input_media or runtime_context or additional_skills):
                raise ValueError("info 消息只包含消息内容")
            # A request may carry whatever a message to any Resident may; the
            # sender's right to each attachment and reference is checked here,
            # and the member the Router chooses reads them (decision_44cf587c1721).
            if input_media:
                resident_media_domain.resident_input_media(
                    input_media, actor=speaking_member or actor_id, resident_id=resident_domain_common.record_resident_id(record), content=message,
                    workspace_roots=resident_runtime_domain.resident_media_workspace_roots(environment or record),
                )
            if context_refs:
                resident_record_domain.resolve_context_refs(context_refs, speaking_member or actor_id, message)
            facade_item = _resident_user_message(
                resident_domain_common.record_resident_id(record), message, actor_id,
                message_id=message_id, protocol_input=protocol_input,
            )
            if business_ref:
                facade_item["business_ref"] = str(business_ref).strip()[:500]
            facade_message = copy.deepcopy(facade_item)
            if activation == "attach_to_next":
                if not existing_resident_delivery(resident_domain_common.record_resident_id(record), facade_item):
                    # Held only to be shown with the facade's next request.
                    resident_held_infos.hold(record, facade_item)

                    def accept_info() -> None:
                        # Handed to the steward on arrival; the facade's trace gets it
                        # with the facade's next request (resident_held_infos.release_with).
                        resident_lifecycle_domain.accept_resident_user_message(resident_id, facade_message)
                        resident_conversation_stream.get_repository().set_input_state(
                            resident_id, str(facade_message.get("message_id") or ""), "completed",
                        )

                    invariant_locks.defer_until_state_unlock(accept_info)
                # A retried delivery forwards again; the steward's copy has a fixed ID.
                invariant_locks.defer_until_state_unlock(
                    lambda: family_facade.forward_info(record, facade_message, actor_id)
                )
                return delivery_result()
            if existing_resident_delivery(resident_domain_common.record_resident_id(record), facade_item):
                from . import family_recovery
                invariant_locks.defer_until_state_unlock(
                    lambda: family_recovery.handle(record, str(facade_message.get("message_id") or ""))
                )
                return delivery_result()

            def accept_for_router() -> None:
                from . import family_recovery
                resident_lifecycle_domain.accept_resident_user_message(resident_id, facade_message)
                family_recovery.register(record, facade_message)
                resident_conversation_stream.get_repository().set_input_state(
                    resident_id, str(facade_message.get("message_id") or ""), "queued",
                )
                # Infos that came before this request are shown with it.
                resident_held_infos.release_with(record, str(facade_message.get("message_id") or ""))
                # Its sender sees it at once; the Router still chooses a member.
                family_facade.show_request(resident_id, str(facade_message.get("message_id") or ""))
                family_recovery.handle(record, str(facade_message.get("message_id") or ""))

            invariant_locks.defer_until_state_unlock(accept_for_router)
            invariant_locks.defer_until_state_unlock(
                lambda: lineage_message_events.wake_watchers(record, str(facade_message.get("message_id") or ""))
            )
            return delivery_result()
        if activation == "attach_to_next":
            # An info message only waits for the next request; it never starts a turn.
            if isolated_input:
                raise ValueError("授权调用的输入不能作为 info 消息")
            if context_refs or input_media or runtime_context or additional_skills:
                raise ValueError("info 消息只包含消息内容")
            info_item = _resident_user_message(
                resident_domain_common.record_resident_id(record), message, actor_id,
                message_id=message_id, protocol_input=protocol_input,
            )
            if business_ref:
                info_item["business_ref"] = str(business_ref).strip()[:500]
            if existing_resident_delivery(resident_domain_common.record_resident_id(record), info_item):
                return delivery_result()
            resident_held_infos.hold(record, info_item)
            held_message = copy.deepcopy(info_item)

            def accept_info() -> None:
                resident_lifecycle_domain.accept_resident_user_message(resident_id, held_message)
                resident_conversation_stream.get_repository().set_input_state(
                    resident_id, str(held_message.get("message_id") or ""), "queued",
                )

            invariant_locks.defer_until_state_unlock(accept_info)
            invariant_locks.defer_until_state_unlock(
                lambda: lineage_message_events.wake_watchers(record, str(held_message.get("message_id") or ""))
            )
            return delivery_result()
        provider = resident_execution_provider.provider_for(record)
        capabilities = provider.capabilities
        if activation == "inject_active_turn":
            if not capabilities.supports_active_turn_input:
                raise ValueError("该 Resident 不支持注入当前工作，请使用正常发送")
            current = resident_execution_work.read(record)
            user_item = _resident_user_message(
                resident_id, message, actor_id,
                message_id=message_id, protocol_input=protocol_input,
            )
            if business_ref:
                user_item["business_ref"] = str(business_ref).strip()[:500]
            if existing_resident_delivery(
                resident_id, user_item, bound_job=current, require_execution_binding=True,
            ):
                return delivery_result()
            if (
                not resident_execution_work.is_active(current)
                or resident_execution_work.is_execution_held(current)
                or not resident_execution_work.submitted_execution_id(current)
                or current.get("stop_requested_at")
                or current.get("execution_terminal_status") in {"completed", "failed", "cancelled"}
                or resident_execution_work.is_compact(current)
            ):
                raise ValueError("当前 Resident 没有可注入的活动工作，请使用正常发送")
        if context_refs and not capabilities.supports_context:
            raise ValueError("该 Resident ExecutionProvider 不接受 Context 引用")
        if resolved_runtime_context and not capabilities.supports_context:
            raise ValueError("该 Resident ExecutionProvider 不接受 runtime context")
        if resolved_additional_skills and not capabilities.supports_skills:
            raise ValueError("该 Resident ExecutionProvider 不加载执行层 Skill")
        if input_media is not None and not isinstance(input_media, (list, tuple)):
            raise ValueError("input_media must be an array")
        if isinstance(input_media, (list, tuple)):
            for media in input_media:
                media_type = str(
                    (media.get("type") or media.get("media_type") or media.get("kind") or "image")
                    if isinstance(media, dict) else "image"
                ).strip().casefold()
                if media_type == "file" and not capabilities.local_tools:
                    raise ValueError("该 Resident ExecutionProvider 不读取本地文件附件")
                if media_type != "file" and not capabilities.supports_media:
                    raise ValueError("该 Resident ExecutionProvider 不读取图片输入")
        if provider.capabilities.synchronous_delivery:
            record = environment or record
            synchronous_message = _resident_user_message(
                resident_domain_common.record_resident_id(record),
                message,
                actor_id,
                message_id=message_id,
                protocol_input=protocol_input,
            )
            if business_ref:
                synchronous_message["business_ref"] = str(business_ref).strip()[:500]
            if existing_resident_delivery(
                resident_domain_common.record_resident_id(record),
                synchronous_message,
            ):
                return delivery_result()
            synchronous_message = resident_lifecycle_domain.persist_resident_user_message(
                resident_domain_common.record_resident_id(record),
                synchronous_message,
            )
            if business_ref:
                synchronous_message["business_ref"] = str(business_ref).strip()[:500]
            resident_conversation_stream.get_repository().set_input_state(
                resident_domain_common.record_resident_id(record),
                message_id,
                "running",
            )
            record["session_state"] = "running"
            record["updated_at"] = resident_domain_common.now_iso()
            _save_resident_record(record)
            synchronous_record = copy.deepcopy(record)
            resident_intake_admission.committed(resident_id, message_id)
        else:
            input_record = copy.deepcopy(record)
    if synchronous_record:
        try:
            _send_synchronous_provider_message(
                synchronous_record,
                actor_id,
                message_quotes.with_quote(message, quote),
                message_id=message_id,
                user_message=synchronous_message,
            )
        except Exception as exc:
            with resident_record_domain.state_lock(resident_id):
                resident_conversation_stream.get_repository().set_input_state(
                    resident_id,
                    message_id,
                    "failed",
                    error=resident_record_domain.runtime_error_payload(str(exc)),
                )
                current = resident_record_domain.find_resident_for_admission(resident_id)
                if current:
                    current["session_state"] = "active"
                    current["last_error"] = str(exc)
                    current["updated_at"] = resident_domain_common.now_iso()
                    _save_resident_record(current)
            raise
        return delivery_result()

    deferred_delivery = not message.lstrip().startswith("/")
    message_cwd = _resident_message_cwd(environment, working_directory) if environment is not None else None
    _ensure_backend_accepts_context_refs(input_record, context_refs)
    resolved_refs = resident_record_domain.resolve_context_refs(context_refs, attachment_actor, message)
    media_roots = resident_runtime_domain.resident_media_workspace_roots(environment) if has_media else ()
    facade_record = resident_record_domain.find_resident(relay_source[0]) if relayed_sender and input_media else None
    if facade_record:
        # The facade accepted attached paths within its own Workspaces.
        media_roots = (*media_roots, *resident_runtime_domain.resident_media_workspace_roots(facade_record))
    resolved_attachments = resident_media_domain.resident_input_media(
        input_media,
        actor=attachment_actor,
        resident_id=resident_domain_common.record_resident_id(input_record),
        content=message,
        workspace_roots=media_roots,
    )
    if resolved_attachments:
        environment = environment or resident_record_domain.find_resident(resident_id)
        resolved_attachments = resident_media_domain.stage_resident_input_images(
            resolved_attachments, resident_record_domain.sessions_path(environment or input_record), message_id,
        )
    resolved_input_media = [
        item for item in resolved_attachments if item["media_type"] == "image"
    ]
    provider_input = message_quotes.with_quote(
        resident_media_domain.resident_message_with_file_references(message, resolved_attachments), quote,
    )
    goal_command = slash_commands.parse_goal_command(message)
    last_user_message = (
        resident_record_domain.last_user_message_text(resident_id)
        if goal_command.get("action") == "get"
        else ""
    )

    with resident_intake_admission.scope(resident_id, message_id), resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident_for_admission(resident_id)
        if not record:
            raise KeyError("resident not found")
        if not isolated_input and not delegated_relay and not _resident_deliverable_to_actor(record, actor_id):
            raise KeyError("resident not found")

        if environment is not None:
            # Keep current admission facts, adding only the prepared filesystem view.
            for field in ("default_cwd", "workspace_path", "workspace_root", "workspace_bindings", "workspace_paths", "owned_workspace"):
                if field in environment:
                    record[field] = environment[field]
        runtime_settings = resident_runtime_domain.read_resident_runtime_settings(record)
        scope_label = "Resident Session"
        slash_result = {}
        goal_payload = (
            resident_record_domain.resident_goal_payload(
                record,
                job=resident_execution_work.read(record),
                last_user_message=last_user_message,
            )
            if goal_command.get("action") == "get"
            else {}
        )
        # Every agent Provider answers the slash commands it declares, with or without a session.
        if activation == "enqueue_and_trigger" and not isolated_input and (
            resident_backend.execution_backend(record) in AGENT_PROVIDERS
        ):
            slash_result = resident_session_backend_domain.handle_resident_native_command(
                record,
                message,
                runtime_settings,
                session_id=resident_backend.record_session_id(record),
                cwd=str(message_cwd),
                actor=actor_id,
                can_update_runtime=True,
                can_manage_permissions=True,
                scope_label=scope_label,
                updated_at=resident_domain_common.now_iso(),
                token_usage=resident_record_domain.session_token_usage_payload(record),
                goal=goal_payload,
            )
        if slash_result.get("native_compact"):
            command_message = _resident_user_message(
                resident_domain_common.record_resident_id(record), message, actor_id, message_id=message_id,
                protocol_input=protocol_input,
            )
            if existing_resident_delivery(
                resident_domain_common.record_resident_id(record),
                command_message,
                bound_job=resident_execution_work.read(record),
                require_execution_binding=True,
            ):
                return delivery_result()
            _queue_resident_compaction(
                record, actor_id, message_id, user_message=command_message,
            )
            return delivery_result()
        if activation == "enqueue_and_trigger" and not isolated_input and not slash_result.get("handled") and not slash_result.get("native_goal"):
            slash_result = slash_commands.handle_slash_command(
                message,
                runtime_settings,
                scope_label=scope_label,
                token_usage=resident_record_domain.session_token_usage_payload(record),
                backend=resident_backend.execution_backend(record),
            )
        if slash_result.get("handled"):
            command_message = _resident_user_message(
                resident_domain_common.record_resident_id(record), message, actor_id, message_id=message_id,
                protocol_input=protocol_input,
            )
            if existing_resident_delivery(resident_domain_common.record_resident_id(record), command_message):
                return delivery_result()
            if slash_result.get("updates"):
                resident_runtime_domain.write_resident_runtime_settings(record, slash_result["updates"])
            command_payload = slash_result.get("command_payload")
            if (
                isinstance(command_payload, dict)
                and command_payload.get("type") == "codex_runtime_control"
                and command_payload.get("command") == "fast"
            ):
                slash_result["active_turn_settings_update"] = (
                    submit_active_turn_settings(
                        record,
                        actor_id,
                        control_key=f"slash-fast:{message_id}",
                        service_tier=str(
                            command_payload.get("service_tier") or ""
                        ),
                        source="slash_fast",
                    )
                )
            _persist_synchronous_command_exchange(
                resident_domain_common.record_resident_id(record),
                actor_id,
                message,
                message_id,
                slash_result,
                user_message=command_message,
            )
            record["updated_at"] = resident_domain_common.now_iso()
            _save_resident_record(record)
            return delivery_result()

        from . import resident_initialization
        initialization_blocked = resident_initialization.blocked(record)
        running = _active_resident_execution(record)
        if initialization_blocked:
            running = resident_execution_work.read(record)
            running['queue'] = resident_initialization.waiting_inputs(record, running)
            if not running.get('operation'):
                running = {'queue': running['queue'], 'operation': 'context_fork_create',
                           'outcome': 'failed', 'error': str(record.get('last_error') or '等待初始化恢复')}
        if (
            not resident_backend.record_session_id(record)
            and not running
        ):
            job = resident_registration_domain.queue_bootstrap_resident_session(
                record,
                message=provider_input,
                actor_id=actor_id,
                message_id=message_id,
                context_refs=resolved_refs,
                input_media=resolved_input_media,
                working_directory=message_cwd,
                runtime_context=resolved_runtime_context,
                activation_policy=activation,
                business_ref=business_ref,
                lineage_id=lineage_id,

                delivery_message_id=message_id if deferred_delivery else "",
                user_message=_resident_user_message(
                    resident_domain_common.record_resident_id(record), message, actor_id,
                    message_id=message_id,
                    protocol_input=protocol_input,
                ),
            )
            record["session_state"] = resident_record_domain.record_backend_session_state(record)
            record["last_error"] = ""
            record["updated_at"] = resident_domain_common.now_iso()
            _save_resident_record(record)
            return delivery_result()
        now = resident_domain_common.now_iso()
        intake_state = resident_lifecycle_domain.resident_intake_state(record)
        user_item = _resident_user_message(
            resident_domain_common.record_resident_id(record),
            message,
            actor_id,
            message_id=message_id,
            protocol_input=protocol_input,
        )
        if business_ref:
            user_item["business_ref"] = str(business_ref).strip()[:500]
        if existing_resident_delivery(
            resident_domain_common.record_resident_id(record),
            user_item,
            bound_job=running,
            require_execution_binding=True,
        ):
            return delivery_result()
        provider_message = resident_media_domain.provider_message_with_native_media(
            record,
            provider_input,
            resolved_input_media,
        )

        prompt_seed = {
            "user_message_id": str(user_item.get("message_id") or ""),
            "raw_message": message,
            "message": provider_message,
            "effective_message": provider_message,
            "actor": actor_id,
            "context_refs": resolved_refs,
            "input_media": resolved_input_media,
            "runtime_context": resolved_runtime_context,
            "additional_skills": resolved_additional_skills,
            **resident_directory_domain.resident_session_prompt_controls(runtime_settings),
        }
        prompt_cwd = str(message_cwd) if message_cwd is not None else ""
        queue_item = resident_execution_work.new_item(
            user_item,
            message,
            provider_message,
            now,
            resolved_refs,
            prompt_cwd=prompt_cwd,
            input_media=resolved_input_media,
        )
        queue_item.update(prompt_seed)
        queue_item["actor"] = actor_id
        queue_item["activation_policy"] = activation
        if lineage_id:
            queue_item["lineage_id"] = str(lineage_id).strip()
        if business_ref:
            queue_item["business_ref"] = str(business_ref).strip()[:500]
        persist = (
            resident_lifecycle_domain.persist_resident_user_message
            if not running and not initialization_blocked and intake_state == resident_state.INTAKE_OPEN
            and activation == "enqueue_and_trigger"
            else resident_lifecycle_domain.accept_resident_user_message
        )
        if not deferred_delivery:
            invariant_locks.defer_until_state_unlock(lambda: persist(resident_id, user_item))
            invariant_locks.defer_until_state_unlock(
                lambda: lineage_message_events.wake_watchers(record, str(user_item.get("message_id") or ""))
            )
        if running:
            running[resident_state.INTAKE_FIELD] = intake_state
            queue = resident_execution_work.normalized_queue(running)
            queue_item.pop("execution_id", None)
            queue.append(queue_item)
            running["queue"] = queue
            running["updated_at"] = now
            if deferred_delivery:
                resident_execution_work.admit(record, running, user_item)
            else:
                resident_execution_work.write(record, running)
            record["updated_at"] = now
            _save_resident_record(record)
            return delivery_result()

        job = {
            **queue_item,
            "execution_id": queue_item["execution_id"],
            "started_at": now,
            "updated_at": now,
            "error": "",
            "queue": [],
        }
        job[resident_state.INTAKE_FIELD] = intake_state
        if deferred_delivery:
            resident_execution_work.admit(record, job, user_item)
        else:
            resident_execution_work.write(record, job)
        record["session_state"] = (
            "queued"
            if (
                intake_state == resident_state.INTAKE_CLOSED
            )
            else "running"
        )
        record["updated_at"] = now
        _save_resident_record(record)

        return delivery_result()


def reset_resident_context(resident_id: str, actor: str) -> dict:
    """Commit a context reset and durably schedule its external cleanup."""
    from . import resident_domain_common, resident_lifecycle_domain, resident_record_domain

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后清空上下文")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if resident_execution_work.is_active(resident_execution_work.read(record)):
            raise ValueError("当前仍有任务正在排队或处理，停止或完成后再清空上下文")
        operations = resident_record_domain.lifecycle_operations()
        if operations.pending(resident_id):
            raise ValueError("Resident 生命周期操作正在完成，请稍后重试")
        now = resident_domain_common.now_iso()
        next_record = copy.deepcopy(record)
        next_record.update({"session_state": "ready", "last_error": "", "updated_at": now})
        # Provider sessions that ran before this time hold the cleared context:
        # a restart or a Fork must not hand any of it to a new session.
        next_record["context_reset_at"] = datetime.now(timezone.utc).isoformat(
            timespec="milliseconds").replace("+00:00", "Z")
        for key in (
            "last_runtime_error", "last_turn_usage", "last_turn_usage_at",
            "last_model_context_window", "session_created_via",
        ):
            next_record.pop(key, None)
        resident_record_domain.validated_resident_record_payload(next_record)
        archive_stamp = re.sub(r"[^0-9A-Za-z]+", "", now) + "_" + secrets.token_hex(6)
        repository = resident_conversation_stream.get_repository()
        steps = [{
            "kind": "context_archive", "resident_id": resident_id,
            "archived_at": now, "message_ids": repository.context_reset_boundary(resident_id),
            # The clear is also marked in the Resident's trace, after what it
            # held now (nothing runs during a clear), whatever arrives later.
            "reset_at": next_record["context_reset_at"], "actor": actor_id,
            "trace_position": repository.agent_data.trace_length(resident_id),
        }, resident_lifecycle_domain.resident_release_step(record, actor_id)]
        def commit() -> str:
            resident_record_domain.save_resident_record(next_record)
            resident_execution_work.clear(record)
            return operations.enqueue(resident_id, steps)
        operation_id = resident_catalog.get_catalog().state_store.transaction(commit)
    return resident_operation_result(
        resident_id, reset=True, cleanup_pending=True, operation_id=operation_id,
    )


def resident_execution_stopped_event(
    actor: str,
    job: dict[str, Any],
    *,
    queue_continues: bool = False,
    backend_label: str = "Codex",
) -> dict[str, Any]:
    from . import resident_domain_common
    content = (
        f"{actor} 已停止当前 {backend_label} 运行。"
        if actor else f"当前 {backend_label} 运行已取消。"
    )
    if queue_continues:
        content += "下一条排队消息将继续执行。"
    turn = str(job.get("user_message_id") or job.get("execution_id") or "")
    expected_execution_id = str(job.get("execution_id") or "")
    return build_message(
        "system",
        content,
        # One notice per stopped turn: a message put back in the Mailbox can be
        # stopped again in a later Job (decision_b5d349190743).
        message_id=resident_message_contract.derived_message_identity(
            "resident-turn-cancelled", *((turn, expected_execution_id) if expected_execution_id and expected_execution_id != turn else (turn,)),
        ),
        reply_to_message_id=str(job.get("user_message_id") or ""),
        created_at=str(
            job.get("stop_requested_at")
            or job.get("stopped_at")
            or job.get("finished_at")
            or resident_domain_common.now_iso()
        ),
        # A stopped turn still used what it ran: the notice names its
        # Execution, whose usage the Chat shows on it.
        execution_id=resident_execution_work.submitted_execution_id(job),
    )


def cancel_resident_execution_for_target(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    continue_queue: bool = True,
) -> dict:
    from . import resident_domain_common, resident_execution_domain, resident_record_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后停止")

    # Authorization needs canonical identity, not a Workspace projection. The
    # exact input is rechecked under the command lock and in the DB commit.
    from .state_store import get_lineage_data_store
    record = resident_record_domain.find_resident_for_admission(resident_id)
    if not record:
        raise KeyError("resident not found")
    held_continuation = ""
    with resident_record_domain.state_lock(resident_id):
        control = dict(resident_execution_work.read_control(resident_id).get("state") or {})
        if str(control.get("user_message_id") or "") != message_id:
            raise ValueError("message is not the active Resident input")
        if resident_execution_work.is_self_stop_request(record, control):
            raise RuntimeError("当前 Resident job 不能停止自己；请从 Portal 外部停止，或等待本轮运行结束")
        if continue_queue and not resident_execution_work.is_execution_held(control) and control.get("operation") != "context_fork_create":
            accepted = get_lineage_data_store().request_resident_execution_stop(
                resident_id, str(control.get("execution_id") or ""), message_id, actor_id, resident_domain_common.now_iso(),
            )
            if accepted:
                from .lifecycle import notify_execution_cancellation
                from .resident_delivery_work import wake
                notify_execution_cancellation(resident_execution_work.submitted_execution_id(control), str(control.get("actor") or ""))
                invariant_locks.defer_until_state_unlock(lambda: wake(resident_id))
            return {"resident_id": resident_id, "message_id": message_id,
                    "cancelled": False, "cancellation_requested": accepted,
                    "already_terminal": not accepted, "backend_cancelled": False}
    # Withdrawing held inputs/clearing their queue also needs immutable input
    # materialization. Prepare its Workspace before reacquiring the Job lock.
    record = resident_record_domain.find_resident(resident_id)
    with resident_record_domain.state_lock(resident_id):
        if not record:
            raise KeyError("resident not found")
        job = resident_execution_work.read(record)
        if str(job.get("user_message_id") or "") != message_id:
            raise ValueError("message is not the active Resident input")
        if not resident_execution_work.is_active(job):
            return resident_operation_result(
                resident_id,
                cancellation_requested=False,
                already_terminal=True,
            )
        if resident_execution_work.is_self_stop_request(record, job):
            raise RuntimeError(
                "当前 Resident job 不能停止自己；请从 Portal 外部停止，或等待本轮运行结束"
            )
        current = job
        cleared_queue: list[dict[str, Any]] = []
        now = resident_domain_common.now_iso()
        if not continue_queue:
            cleared_queue = resident_execution_work.normalized_queue(current)
        current.setdefault("stop_requested_by", actor_id)
        current.setdefault("stop_requested_at", now)
        current["updated_at"] = now
        if resident_execution_work.is_execution_held(current) or (
            current.get("operation") == "context_fork_create"
            and not resident_execution_work.submitted_execution_id(current) and not current.get("continuation_id")
        ):
            if not current.get("continuation_id"):
                current.update(resident_execution_work.continuation_fields(record))
            held_continuation = str(current["continuation_id"])
        if not continue_queue:
            _withdraw_waiting_inputs(record, current, [item["user_message_id"] for item in cleared_queue], now)
        else:
            resident_execution_work.write(record, current)

    if held_continuation:
        resident_execution_domain.apply_cancelled_continuation(resident_id, held_continuation)
    return resident_operation_result(
        resident_id,
        cancelled=bool(held_continuation),
        cancellation_requested=True,
        cleared_queue_count=len(cleared_queue),
        backend_cancelled=False,
    )


def request_resident_execution_cancellation(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    continue_queue: bool = True,
) -> dict[str, Any]:
    """Submit cancellation for one exact Resident input."""
    from . import resident_record_domain

    message_id = str(message_id or "").strip()
    if not message_id:
        raise ValueError("message_id required")

    target = resident_record_domain.resolve_resident_admission_target(
        resident_id,
        actor,
        require_visible=True,
    )
    result = cancel_resident_execution_for_target(
        target.resident_id,
        actor,
        message_id,
        continue_queue=continue_queue,
    )
    return dict(result)


def _running_turn_prompt(record: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """An active turn already has context; inject only the new input."""
    from . import resident_registration_domain
    prompt_job = dict(item)
    prompt_job["message"] = str(item.get("effective_message") or item.get("raw_message") or item.get("message") or "")
    prompt = resident_registration_domain.build_resident_prompt(dict(record), prompt_job)
    return {**item, "message": prompt, "actual_sent_prompt": prompt}


def _queued_active_turn_input(selected: Mapping[str, Any]) -> list[dict[str, Any]]:
    text = str(
        selected.get("actual_sent_prompt")
        or selected.get("effective_message")
        or selected.get("raw_message")
        or selected.get("message")
        or ""
    ).strip()
    if not text:
        raise ValueError("排队消息没有可插入的内容")
    turn_input: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for media in selected.get("input_media") or []:
        if not isinstance(media, dict) or media.get("media_type") != "image":
            continue
        source = str(media.get("source") or "").strip()
        if not source:
            continue
        if media.get("source_type") == "local_path":
            turn_input.append({"type": "localImage", "path": source})
        elif media.get("source_type") == "url":
            turn_input.append({"type": "image", "url": source})
    return turn_input


def _inject_queued_input_into_active_turn(
    resident_id: str, actor: str, queue_item_id: str, *, delivered: bool = False,
) -> dict:
    """Durably request injection; prompt preparation and provider IO belong to recovery.

    ``delivered`` is a delivery injecting the input it has just queued: the
    sender needs only to be able to deliver to the Resident, not to read it.
    """
    from . import resident_domain_common, resident_record_domain
    actor_id = canonical_actor_id(actor)
    message_id = str(queue_item_id or "").strip()
    if not actor_id:
        raise ValueError("需要登录后操作队列")
    if not message_id:
        raise ValueError("queue_item_id required")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident_for_admission(resident_id)
        if not record:
            raise KeyError("resident not found")
        if not delivered:
            ensure_resident_visible_to_actor(record, actor_id)
        elif not _resident_deliverable_to_actor(record, actor_id):
            raise KeyError("resident not found")
        job = resident_execution_work.read(record)
        existing = next((item for item in resident_execution_work.send_now_inputs(job)
                         if _queue_item_matches(item, message_id)), None)
        if existing:
            from .resident_delivery_work import wake
            invariant_locks.defer_until_state_unlock(lambda: wake(resident_id))
            return _accepted_delivery(resident_id, message_id, injected=False,
                                      already_injected=True, delivery_status="pending")
        held = resident_execution_work.is_execution_held(job)
        is_head = str(job.get("user_message_id") or "") == message_id
        if is_head and not held:
            return _accepted_delivery(resident_id, message_id, injected=True, already_injected=True)
        queue = resident_execution_work.normalized_queue(job)
        selected = job if is_head and held else next(
            (item for item in queue if _queue_item_matches(item, message_id)), None)
        repository = resident_conversation_stream.get_repository()
        try:
            state = repository.input_state(resident_id, message_id)
        except KeyError:
            state = {}
        if state.get("state") == "completed":
            if selected is not None:
                job["queue"] = [item for item in queue if not _queue_item_matches(item, message_id)]
                resident_execution_work.write(record, job, delivery_message_id=message_id)
            return _accepted_delivery(resident_id, message_id, injected=True, already_injected=True)
        if selected is None:
            raise KeyError("queue item not found")
        if not resident_execution_work.is_active(job):
            raise ValueError("当前 Resident 没有活动 turn，不能插入")
        if not resident_execution_provider.provider_for(record).capabilities.supports_active_turn_input:
            raise ValueError("该 Resident 的执行后端不能在运行中接收新消息；消息保留在队列中按顺序执行")
        if held or resident_execution_work.completion_applied(job) or job.get("stop_requested_at"):
            # A facade's state snapshot can precede completion or a manual
            # intake change. Keep the same input for normal queue admission;
            # never append to a stopping turn or bypass the intake gate.
            if not is_head:
                next_turn = {**selected, "activation_policy": "enqueue_and_trigger"}
                next_turn.pop("active_turn_input", None)
                job["queue"] = [next_turn, *(
                    item for item in queue if not _queue_item_matches(item, message_id))]
            job["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, job, delivery_message_id=message_id)
            return _accepted_delivery(resident_id, message_id, injected=False,
                                      scheduled_next_turn=True, delivery_status="next_turn")
        selected = dict(selected)
        selected["activation_policy"] = "enqueue_and_trigger"
        selected["active_turn_input"] = {
            "control_id": resident_input_control_id(resident_id, message_id), "actor": actor_id,
        }
        job["queue"] = [item for item in queue if not _queue_item_matches(item, message_id)]
        job["send_now_inputs"] = [*resident_execution_work.send_now_inputs(job), selected]
        job["updated_at"] = resident_domain_common.now_iso()
        resident_execution_work.write(record, job, delivery_message_id=message_id)
    return _accepted_delivery(resident_id, message_id, injected=False, delivery_status="pending")


def reconcile_queued_turn_inputs(
    resident_id: str, expected_execution_id: str, *, turn_finished: bool = False,
) -> bool:
    """Resolve Send now receipts before queued inputs start another turn.

    Queue retry policy belongs to Resident; delivery receipts to Execution.
    Receipt RPCs are unlocked; commits revalidate the exact job and execution.
    An unavailable receipt must never be guessed to mean failed delivery.
    """
    from . import resident_domain_common, resident_record_domain
    # Every Execution snapshot asks this; nearly all find nothing pending.
    # Read the committed control unlocked; each commit below revalidates.
    job = dict(resident_execution_work.read_control(resident_id).get("state") or {})
    if str(job.get("execution_id") or "") != expected_execution_id:
        return False
    execution_id = resident_execution_work.submitted_execution_id(job)
    pending = resident_execution_work.send_now_inputs(job)
    if not pending:
        return True
    if not execution_id and not turn_finished:
        return False
    environment = resident_record_domain.find_resident(resident_id) or {}
    service = execution_port.service() if execution_id else None
    resolved = True
    for item in pending:
        control = item["active_turn_input"]
        message_id = str(item["user_message_id"])
        try:
            # Without a persisted binding, Send now only records local intent;
            # it cannot append to a provider. A finished runner will not bind
            # that intent later (including failure during execution startup).
            receipt = (service.input_control(control["control_id"], agent_id=resident_id)
                       if service is not None else {"status": "failed"})
            if receipt is None:
                # Recover an intent committed before the remote append.
                try:
                    prepared = _queued_active_turn_input(_running_turn_prompt(environment, item))
                    # Preparation can overlap Stop, withdrawal or a turn change.
                    # Revalidate the captured control before dispatching to the
                    # exact Execution; never discover a newer turn here.
                    with resident_record_domain.state_lock(resident_id):
                        current_record = resident_record_domain.find_resident_for_admission(resident_id) or {}
                        current = resident_execution_work.read(current_record) if current_record else {}
                        if (str(current.get("execution_id") or "") != expected_execution_id
                                or resident_execution_work.submitted_execution_id(current) != execution_id):
                            return False
                        if not any(value.get("active_turn_input") == control for value in resident_execution_work.send_now_inputs(current)):
                            continue
                        if current.get("stop_requested_at"):
                            raise ValueError("Current turn is stopping")
                    receipt = service.append_input(
                        execution_id, actor=control["actor"],
                        input=prepared,
                        control_id=control["control_id"],
                        source_segment_id=execution_port.input_source_segment_id(resident_id, message_id),
                    )
                except ValueError:
                    # Refused, so never delivered: it goes to the next turn.
                    receipt = {"status": "failed"}
        except Exception:
            resolved = False
            continue
        if receipt.get("status") not in {"delivered", "failed"}:
            resolved = False
            continue
        # Post-commit effects include media cleanup and need the execution
        # workspace. Hydrate it outside the state lock; admission records are
        # deliberately identity-only. The locked read below still owns the
        # exact-turn and control checks before committing the receipt.
        if not environment:
            return False
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident_for_admission(resident_id) or {}
            current = resident_execution_work.read(record) if record else {}
            if (str(current.get("execution_id") or "") != expected_execution_id
                    or resident_execution_work.submitted_execution_id(current) != execution_id):
                return False
            dispatched = resident_execution_work.send_now_inputs(current)
            index = next((i for i, candidate in enumerate(dispatched)
                          if candidate.get("user_message_id") == message_id
                          and candidate.get("active_turn_input") == control), -1)
            if index < 0:
                continue
            now = resident_domain_common.now_iso()
            if receipt["status"] == "delivered":
                delivered_item = {**dispatched[index], "dispatched_at": now,
                                  "delivery_mode": "inject_active_turn"}
                resident_execution_work.materialize_conversation_input(record, delivered_item, activated_at=now)
                resident_conversation_stream.get_repository().set_input_state(
                    resident_id, message_id, "completed", updated_at=now,
                )
                dispatched.pop(index)
            else:
                next_turn = dispatched.pop(index)
                next_turn.pop("active_turn_input", None)
                current["queue"] = [next_turn, *resident_execution_work.normalized_queue(current)]
            if dispatched:
                current["send_now_inputs"] = dispatched
            else:
                current.pop("send_now_inputs", None)
            current["pending_active_turn_inputs"] = [
                value for value in current.get("pending_active_turn_inputs") or []
                if value.get("control_id") != control["control_id"]
            ]
            if not current["pending_active_turn_inputs"]:
                current.pop("pending_active_turn_inputs", None)
            current["updated_at"] = now
            resident_execution_work.write({**environment, **record}, current)
    return resolved


def _withdraw_waiting_inputs(record, job, message_ids, now):
    """Under the Resident lock, end queued inputs and their groups atomically."""
    from . import resident_catalog, resident_mailbox
    from . import resident_record_domain

    ids = set(message_ids)
    queue = resident_execution_work.normalized_queue(job)
    removed = [item for item in queue if item["user_message_id"] in ids]
    repository = resident_conversation_stream.get_repository()
    def commit():
        for item in removed:
            message_id = item["user_message_id"]
            resident_mailbox.finish_group(record, message_id, "cancelled")
            repository.set_input_state(record["resident_id"], message_id, "cancelled", updated_at=now)
        job["queue"] = [item for item in queue if item["user_message_id"] not in ids]
        job["updated_at"] = now
        resident_execution_work.write(record, job)
        record["updated_at"] = now
        resident_record_domain.save_resident_record(record)
    resident_catalog.get_catalog().state_store.transaction(commit)
    return removed


def _cancel_resident_queue_item(resident_id: str, actor: str, queue_item_id: str) -> dict:
    from . import resident_domain_common, resident_record_domain
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后操作队列")
    target_item_id = str(queue_item_id or "").strip()
    if not target_item_id:
        raise ValueError("queue_item_id required")
    now = resident_domain_common.now_iso()
    # A waiting info is not in the queue; withdrawing it ends it before any
    # request carries it (decision_b5d349190743).
    info_record = resident_record_domain.find_resident(resident_id)
    if info_record and not family_facade.is_facade(info_record) and resident_held_infos.withdraw(
        info_record, target_item_id, actor_id, manager=True,
    ) is not None:
        conversation_resident_id = str(info_record.get("resident_id") or resident_id)
        resident_conversation_stream.get_repository().set_input_state(
            conversation_resident_id, target_item_id, "cancelled",
            error={"code": "info_withdrawn", "message": "info 已撤回，不再随请求送达"}, updated_at=now,
        )
        return resident_operation_result(conversation_resident_id, message_id=target_item_id, cancelled=True)
    if info_record and family_facade.is_facade(info_record):
        # A facade has no queue: a request no member has yet waits as a queued input.
        family_facade.withdraw_request(info_record, target_item_id, actor_id, manager=True)
        return resident_operation_result(str(info_record.get("resident_id") or resident_id),
                                          message_id=target_item_id, cancelled=True)
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        execution = resident_execution_work.read(record)
        held_head = (resident_execution_work.is_execution_held(execution)
                     and _queue_item_matches(execution, target_item_id))
        if held_head:
            message_id = str(execution["user_message_id"])
        else:
            selected = next((item for item in resident_execution_work.normalized_queue(execution)
                             if _queue_item_matches(item, target_item_id)), None)
            if selected is None:
                raise KeyError("queue item not found")
            message_id = str(selected["user_message_id"])
            _withdraw_waiting_inputs(record, execution, [message_id], now)
    if held_head:
        return dict(cancel_resident_execution_for_target(
            resident_id, actor_id, message_id, continue_queue=True,
        ))
    return resident_operation_result(resident_id, message_id=message_id, cancelled=True)


def _reordered_ids(
    ids: list[str],
    *,
    message_id: str,
    before_message_id: str,
    after_message_id: str,
    order: list[str] | None,
) -> list[str]:
    """The waiting order after one relative move or a complete permutation (decision_b5d349190743)."""

    if order is not None:
        wanted = [str(item or "").strip() for item in order]
        if len(set(wanted)) != len(wanted) or sorted(wanted) != sorted(ids):
            raise ValueError("order 必须恰好包含当前全部等待消息 ID，不能重复、遗漏或多出")
        return wanted
    anchor = str(before_message_id or after_message_id or "").strip()
    if bool(before_message_id) == bool(after_message_id):
        raise ValueError("调序需要 before_message_id 与 after_message_id 中的一个")
    if message_id not in ids or anchor not in ids:
        raise ValueError("要移动的消息或参照消息不在等待队列中")
    if anchor == message_id:
        raise ValueError("参照消息不能是要移动的消息本身")
    rest = [item for item in ids if item != message_id]
    position = rest.index(anchor) + (0 if before_message_id else 1)
    return rest[:position] + [message_id] + rest[position:]


def reorder_resident_queue(
    resident_id: str,
    actor: str,
    *,
    message_id: str = "",
    before_message_id: str = "",
    after_message_id: str = "",
    order: list[str] | None = None,
) -> dict[str, Any]:
    """Change only the waiting order; times, authors and contents stay as they were."""

    from . import resident_domain_common, resident_record_domain

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后操作队列")
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    now = resident_domain_common.now_iso()
    with resident_record_domain.state_lock(target.resident_id):
        record = resident_record_domain.find_resident(target.resident_id)
        if not record:
            raise KeyError("resident not found")
        job = resident_execution_work.read(record)
        queue = resident_execution_work.normalized_queue(job)
        if not queue:
            raise ValueError("等待队列为空，没有可以调序的消息")
        ids = [str(item.get("user_message_id") or "") for item in queue]
        wanted = _reordered_ids(
            ids,
            message_id=str(message_id or "").strip(),
            before_message_id=str(before_message_id or "").strip(),
            after_message_id=str(after_message_id or "").strip(),
            order=order,
        )
        if wanted != ids:
            by_id = {str(item.get("user_message_id") or ""): item for item in queue}
            job["queue"] = [by_id[item] for item in wanted]
            job["updated_at"] = now
            resident_execution_work.write(record, job)
            record["session_state"] = resident_record_domain.record_backend_session_state(record)
            record["updated_at"] = now
            resident_record_domain.save_resident_record(record)
    return resident_operation_result(target.resident_id, reordered=wanted != ids, order=wanted)


def inject_resident_queue_input_into_active_turn(
    resident_id: str,
    actor: str,
    queue_item_id: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_admission_target(
        resident_id,
        actor,
        require_visible=True,
    )
    return _inject_queued_input_into_active_turn(
        target.resident_id,
        actor,
        queue_item_id,
    )


def clear_resident_queued_inputs(resident_id: str, actor: str) -> dict[str, Any]:
    from . import resident_domain_common, resident_record_domain

    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    resident_id = target.resident_id
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        current = resident_execution_work.read(record)
        message_ids = [item["user_message_id"] for item in resident_execution_work.normalized_queue(current)]
        held_head = (str(current.get("user_message_id") or "")
                     if resident_execution_work.is_execution_held(current) else "")
        if message_ids:
            _withdraw_waiting_inputs(record, current, message_ids, resident_domain_common.now_iso())
    # Already admitted Executions belong to Stop, not ClearMailbox. A held head
    # has not been admitted; cancellation revalidates its input identity.
    errors = []
    if held_head:
        try:
            cancel_resident_execution_for_target(resident_id, canonical_actor_id(actor), held_head, continue_queue=False)
            message_ids.append(held_head)
        except (KeyError, ValueError, PermissionError, RuntimeError) as error:
            errors.append({"message_id": held_head, "error": str(error)})
    return resident_operation_result(
        resident_id, cleared_count=len(message_ids), cleared_message_ids=message_ids,
        skipped_message_ids=[], errors=errors,
    )


def cancel_resident_queued_input(
    resident_id: str,
    actor: str,
    queue_item_id: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    result = _cancel_resident_queue_item(
        target.resident_id,
        actor,
        queue_item_id,
    )
    return dict(result)


def rename_resident_title(
    resident_id: str,
    actor: str,
    title: str,
) -> dict[str, Any]:
    from . import resident_record_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )

    def commit() -> dict[str, Any]:
        result = _rename_resident_title_record(target.resident_id, actor, title)
        resident_lineage_host_port.commit_resident_title_projection(
            target.resident_id,
            str(result.get("title") or ""),
            actor,
        )
        return dict(result)

    with invariant_locks.lineage_scope(
        resident_lineage.lineage_id(target.record), operation="resident.rename",
    ), resident_record_domain.state_lock(resident_id):
        return resident_catalog.get_catalog().state_store.transaction(commit)


def _set_resident_title_locked(
    record: dict[str, Any],
    title: str,
    *,
    origin: str,
) -> dict[str, Any]:
    from . import (
        resident_domain_common,
        resident_lifecycle_domain,
        resident_record_domain,
        resident_workspace_domain,
    )
    resident_lifecycle_domain.require_no_pending_lifecycle(str(record.get("resident_id") or ""))
    previous_workspace_path = str(record.get("workspace_path") or "")
    record["title"] = title
    record["title_origin"] = origin
    record["updated_at"] = resident_domain_common.now_iso()
    resident_record_domain.apply_resident_resource_identity(record)
    resident_record_domain.require_resident_owned_workspace_binding(record)
    resident_workspace_domain.ensure_resident_workspace(record)
    resident_workspace_domain.refresh_resident_workspace_document(record)
    resident_record_domain.save_resident_record(record)
    return record


def adopt_generated_resident_title(
    resident_id: str,
    actor: str,
    title: str,
) -> bool:
    """Adopt a Lineage title while the exact Resident still has its generated name."""
    from . import resident_conversation_projection_domain, resident_record_domain

    actor_id = canonical_actor_id(actor)
    next_title = str(title or "").strip()
    if not actor_id:
        raise ValueError("需要登录后同步 Resident 名称")
    if not next_title:
        raise ValueError("title required")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if str(record.get("deleted_at") or "").strip():
            raise KeyError("resident not found")
        if not resident_conversation_projection_domain.derived_resident_title_is_generated(record):
            return False
        _set_resident_title_locked(
            record,
            next_title,
            origin="lineage",
        )
    return True


def _rename_resident_title_record(resident_id: str, actor: str, title: str) -> dict:
    """Rename a Resident without changing its owners, workspace, or lineage."""
    from . import resident_record_domain
    actor_id = canonical_actor_id(actor)
    next_title = str(title or "").strip()
    if not actor_id:
        raise ValueError("需要登录后重命名 Resident")
    if not next_title:
        raise ValueError("title required")
    with resident_record_domain.state_lock(resident_id):
        record = resident_record_domain.find_resident(resident_id)
        if not record:
            raise KeyError("resident not found")
        if (
            str(record.get("title") or "") == next_title
            and str(record.get("title_origin") or "") == "custom"
        ):
            return record
        if (record.get("family") or {}).get("role") == "facade":
            from . import family_storage
            retitled = resident_catalog.get_catalog().state_store.database.write(
                lambda connection: family_storage.rename_facade(connection, resident_id, next_title)
            )
            _publish_family_retitle(retitled)
        previous_title = str(record.get("title") or "")
        renamed = _set_resident_title_locked(
            record,
            next_title,
            origin="custom",
        )
        _announce_family_retitle(resident_id, previous_title, str(renamed.get("title") or ""), actor_id)
        return renamed


def _publish_family_retitle(resource_ids: list[str]) -> None:
    """Once the rename commits, the Families' descriptors carry the new title."""

    if not resource_ids:
        return

    def publish() -> None:
        from .resource_client import sync_families

        try:
            sync_families(resource_ids)
        except Exception:  # The reconciliation worker republishes from the domain.
            logging.getLogger(__name__).exception("Family descriptor publication for %s failed", resource_ids)

    invariant_locks.defer_until_state_unlock(publish)


def _announce_family_retitle(resident_id: str, previous: str, title: str, actor: str) -> None:
    """Once the rename commits, a Family member's steward hears its new title."""

    if not title or title == previous:
        return

    def announce() -> None:
        from .family_familize import announce_member_retitle

        try:
            announce_member_retitle(resident_id, previous, title, actor)
        except Exception:  # The rename stands; a lost note must not report it as failed.
            logging.getLogger(__name__).exception("Family retitle note for %s failed", resident_id)

    invariant_locks.defer_until_state_unlock(announce)


def rename_resident_identity(resident_id: str, actor: str, title: str) -> dict:
    """Rename Resident-owned state while the caller owns the graph transition."""

    return _rename_resident_title_record(resident_id, actor, title)


def move_resident_identities(
    source_id: str, target_id: str, resident_ids: list[str], actor: str,
    commit_graphs: Callable[[dict[str, dict[str, Any]]], None],
) -> list[dict[str, Any]]:
    """Publish a batch membership transition and workspace intents in one transaction.

    The Lineage owner holds both graph scopes; this owner takes sorted Resident
    scopes and supplies exact new identity projections to the graph callback.
    """
    from .resident_projection import _resident_lineage_node_projection
    from . import resident_record_domain, resident_workspace_domain
    with ExitStack() as stack:
        for resident_id in sorted(resident_ids):
            stack.enter_context(resident_record_domain.state_lock(resident_id))
        prepared = []
        for resident_id in sorted(resident_ids):
            record = copy.deepcopy(resident_record_domain.find_resident(resident_id))
            if not record or resident_lineage.lineage_id(record) != source_id:
                raise ValueError("Resident membership changed; refresh and retry")
            if resident_record_domain.lifecycle_operations().pending(resident_id):
                raise ValueError("Resident 生命周期操作正在完成，请稍后重试")
            previous_path = str(record.get("workspace_path") or "")
            transfer = resident_workspace_domain.prepare_resident_lineage_move(record, target_id, actor)
            resident_record_domain.set_resident_owned_workspace_binding(record)
            prepared.append((record, transfer, previous_path))

        def commit():
            from .lineage_definition_scope import check_before_membership_write
            check_before_membership_write(resident_catalog.get_catalog().state_store, source_id)
            projections = {}
            operations = []
            for record, transfer, previous_path in prepared:
                resident_id = record["resident_id"]
                resident_record_domain.apply_resident_resource_identity(record)
                resident_record_domain.save_resident_record(record)
                projections[resident_id] = _resident_lineage_node_projection(record)
                operation_id = resident_workspace_domain.enqueue_resident_lineage_move(record, transfer, source_id, previous_path)
                operations.append({"resident_id": resident_id, "operation_id": operation_id})
            commit_graphs(projections)
            return operations

        return resident_catalog.get_catalog().state_store.membership_transaction(commit)


def update_resident(
    resident_id: str,
    actor: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from . import resident_record_domain, resident_workspace_domain
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )

    def commit() -> dict[str, Any]:
        previous_title = str(target.record.get("title") or "")
        result = resident_workspace_domain.update_resident_record(target.resident_id, payload, actor)
        next_title = str(result.get("title") or "")
        if next_title != previous_title:
            resident_lineage_host_port.commit_resident_title_projection(
                target.resident_id,
                next_title,
                actor,
            )
            _announce_family_retitle(target.resident_id, previous_title, next_title, actor)
        canonical = dict(result)
        canonical["resident_id"] = target.resident_id
        return canonical

    graph_ids = [resident_lineage.lineage_id(target.record)]
    destination_lineage = str(payload.get("target_lineage_id") or "").strip()
    if destination_lineage:
        identity_contract.validate_id(destination_lineage, "lineage")
        graph_ids.append(destination_lineage)
    with invariant_locks.lineage_scopes(
        graph_ids, operation="resident.update",
    ), resident_record_domain.state_lock(resident_id):
        current = resident_record_domain.find_resident(target.resident_id)
        if not current or resident_lineage.lineage_id(current) != graph_ids[0]:
            raise ValueError("Resident membership changed; retry the update")
        return resident_catalog.get_catalog().state_store.transaction(commit)
