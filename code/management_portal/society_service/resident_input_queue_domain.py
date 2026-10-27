"""Resident domain responsibilities: input queue.

Queued input state: messages, execution acknowledgement, the queue after a
failed execution, queued input joined into the running turn, and the
settings the running turn takes."""
from __future__ import annotations
from typing import Any, Mapping
import copy
import hashlib
import secrets
import sys
import time
from management_common.actor_identity import canonical_actor_id
from . import (
    invariant_locks,
    resident_catalog,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_provider,
    resident_execution_work,
    resident_lifecycle_domain,
    resident_message_contract,
    resident_prompt_domain,
    resident_record_domain,
    resident_state,
)
from .domain_ports import execution_port
from .resident_access import (
    ensure_resident_visible_to_actor,
    resident_deliverable_to_actor as _resident_deliverable_to_actor,
)


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


def active_resident_execution(record: dict) -> dict[str, Any]:
    job = resident_execution_work.read(record)
    return job if resident_execution_work.is_active(job) else {}


def queue_item_matches(item: dict[str, Any], queue_item_id: str) -> bool:
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


def resident_user_message(
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


def persist_failed_execution_message(
    resident_id: str,
    job: dict[str, Any],
    error_message: str,
    runtime_error: dict[str, Any],
    *,
    failed_at: str = "",
) -> dict[str, Any]:
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


def portal_command_message(
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


def compact_portal_command_message(
    result: dict[str, Any],
    actor: str,
    *,
    reply_to_message_id: str = "",
) -> dict[str, Any]:
    return portal_command_message(
        result,
        actor,
        reply_to_message_id=reply_to_message_id,
    )


def accepted_delivery(resident_id: str, message_id: str, **fields: Any) -> dict[str, Any]:
    """A durable receipt is independent of an eventually published chat snapshot."""
    return {"resident_id": resident_id, "message_id": message_id, "accepted": True, **fields}


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


def resident_execution_stopped_event(
    actor: str,
    job: dict[str, Any],
    *,
    queue_continues: bool = False,
    backend_label: str = "Codex",
) -> dict[str, Any]:
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


def _running_turn_prompt(record: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, Any]:
    """An active turn already has context; inject only the new input."""
    prompt_job = dict(item)
    prompt_job["message"] = str(item.get("effective_message") or item.get("raw_message") or item.get("message") or "")
    prompt = resident_prompt_domain.build_resident_prompt(dict(record), prompt_job)
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


def inject_queued_input_into_active_turn(
    resident_id: str, actor: str, queue_item_id: str, *, delivered: bool = False,
) -> dict:
    """Durably request injection; prompt preparation and provider IO belong to recovery.

    ``delivered`` is a delivery injecting the input it has just queued: the
    sender needs only to be able to deliver to the Resident, not to read it.
    """
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
                         if queue_item_matches(item, message_id)), None)
        if existing:
            from .lifecycle import wake_execution_binding
            invariant_locks.defer_until_state_unlock(lambda: wake_execution_binding(resident_id))
            return accepted_delivery(resident_id, message_id, injected=False,
                                      already_injected=True, delivery_status="pending")
        held = resident_execution_work.is_execution_held(job)
        is_head = str(job.get("user_message_id") or "") == message_id
        if is_head and not held:
            return accepted_delivery(resident_id, message_id, injected=True, already_injected=True)
        queue = resident_execution_work.normalized_queue(job)
        selected = job if is_head and held else next(
            (item for item in queue if queue_item_matches(item, message_id)), None)
        repository = resident_conversation_stream.get_repository()
        try:
            state = repository.input_state(resident_id, message_id)
        except KeyError:
            state = {}
        if state.get("state") == "completed":
            if selected is not None:
                job["queue"] = [item for item in queue if not queue_item_matches(item, message_id)]
                resident_execution_work.write(record, job, delivery_message_id=message_id)
            return accepted_delivery(resident_id, message_id, injected=True, already_injected=True)
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
                    item for item in queue if not queue_item_matches(item, message_id))]
            job["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, job, delivery_message_id=message_id)
            return accepted_delivery(resident_id, message_id, injected=False,
                                      scheduled_next_turn=True, delivery_status="next_turn")
        selected = dict(selected)
        selected["activation_policy"] = "enqueue_and_trigger"
        selected["active_turn_input"] = {
            "control_id": resident_input_control_id(resident_id, message_id), "actor": actor_id,
        }
        job["queue"] = [item for item in queue if not queue_item_matches(item, message_id)]
        job["send_now_inputs"] = [*resident_execution_work.send_now_inputs(job), selected]
        job["updated_at"] = resident_domain_common.now_iso()
        resident_execution_work.write(record, job, delivery_message_id=message_id)
    return accepted_delivery(resident_id, message_id, injected=False, delivery_status="pending")


def reconcile_queued_turn_inputs(
    resident_id: str, expected_execution_id: str, *, turn_finished: bool = False,
) -> bool:
    """Resolve Send now receipts before queued inputs start another turn.

    Queue retry policy belongs to Resident; delivery receipts to Execution.
    Receipt RPCs are unlocked; commits revalidate the exact job and execution.
    An unavailable receipt must never be guessed to mean failed delivery.
    """
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


def withdraw_waiting_inputs(record, job, message_ids, now):
    """Under the Resident lock, end queued inputs and their groups atomically."""
    from . import resident_catalog, resident_mailbox

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
