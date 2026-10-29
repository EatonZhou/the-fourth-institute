from __future__ import annotations

import copy
import secrets
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import nullcontext
from typing import Any
from management_common.product_environment import read_product_env
from management_common.clock import utc_now_iso
from management_portal.execution.contracts import TERMINAL_STATUSES



from . import (
    identity_contract,
    invariant_locks,
    resident_backend,
    resident_conversation_stream,
    resident_input_checkpoints,
    resident_lineage,
    resident_state,
)
from . import resident_message_contract
from .domain_ports import execution_port, resident_domain_port
from .state_store import get_lineage_data_store


SCHEMA = "tfi.resident-execution.v1"
SCHEMA_VERSION = 1
TERMINAL_OUTCOMES = TERMINAL_STATUSES
INVALID_ACTIVATION_REASON = "activation_policy.invalid"
FOLDER_SCHEMA = "lineage-folder-v3"
PROMPT_BUILDER_VERSION = "resident-prompt-v4-runtime-context"
CONVERSATION_PROJECTION_INTENTS = "conversation_projection_intents"
CONVERSATION_PROJECTION_INTENT_LIMIT = 64

_THREADS: dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.RLock()


def key(
    record: Mapping[str, object],
) -> str:
    exact_resident_id = resident_lineage.resident_id(record)
    identity_contract.validate_id(exact_resident_id, "resident")
    return f"resident:{exact_resident_id}"


def outcome(job: Mapping[str, object]) -> str:
    return str(job.get("outcome") or "").strip().lower()


def is_active(job: Mapping[str, object]) -> bool:
    return bool(str(job.get("execution_id") or "").strip()) and not (
        outcome(job) in TERMINAL_OUTCOMES
        or str(job.get("finished_at") or "").strip()
    )


def submitted_execution_id(job: Mapping[str, object]) -> str:
    return str(job.get("execution_id") or "").strip() if job.get("submitted_at") else ""


def request_actor(job: Mapping[str, object]) -> str:
    from management_common.principal import actor_principal

    actor = str(job.get("actor") or "").strip()
    if not actor:
        raise PermissionError("Resident execution has no verified input principal; explicit reissue is required")
    return actor_principal(actor).id


def execution_id(job: Mapping[str, object]) -> str:
    return str(job.get("execution_id") or "").strip()


def ensure_execution_id(job: dict[str, Any]) -> str:
    planned = execution_id(job)
    if not planned:
        planned = f"execution-{secrets.token_hex(12)}"
        job["execution_id"] = planned
    return planned


def is_intake_held(job: Mapping[str, object]) -> bool:
    return (
        str(job.get(resident_state.INTAKE_FIELD) or "")
        == resident_state.INTAKE_CLOSED
        and not submitted_execution_id(job)
        and not job.get("intake_admitted_at")
    )


def is_execution_held(job: Mapping[str, object]) -> bool:
    return (
        not submitted_execution_id(job)
        and (
            is_intake_held(job)
            or str(job.get("activation_policy") or "enqueue_and_trigger")
            not in resident_message_contract.ACTIVATION_POLICIES
        )
    )


def can_preserve_for_session_restart(job: Mapping[str, object]) -> bool:
    """A concrete original input can migrate until Execution admission."""
    return bool(job.get("execution_id") and job.get("user_message_id")
                and (is_execution_held(job) or not submitted_execution_id(job)))


def has_completion_continuation(job: Mapping[str, object]) -> bool:
    return bool(
        is_active(job)
        and str(job.get("continuation_id") or "").strip()
    )


def completion_applied(job: Mapping[str, object]) -> bool:
    applied_execution_id = str(
        job.get("completion_applied_execution_id") or ""
    ).strip()
    return bool(
        str(job.get("completion_applied_at") or "").strip()
        and applied_execution_id
        and applied_execution_id == submitted_execution_id(job)
    )


def matches_continuation(
    job: Mapping[str, object],
    continuation_id: str,
) -> bool:
    return (
        str(job.get("continuation_id") or "")
        == str(continuation_id or "")
        and has_completion_continuation(job)
    )


def new_compact_item(
    actor: str,
    session_id: str,
    scope_label: str,
    timestamp: str,
    user_message_id: str,
) -> dict[str, Any]:
    return {
        "execution_id": f"execution-{secrets.token_hex(12)}",
        "user_message_id": str(user_message_id or "").strip(),
        "operation": "backend_compact",
        "raw_message": "/compact",
        "effective_message": "/compact",
        "message": "/compact",
        "actor": actor,
        "context_refs": {},
        "compact_session_id": resident_backend.session_id(session_id),
        "compact_scope_label": scope_label,
        "enqueued_at": timestamp,
    }


def is_compact(job: Mapping[str, object]) -> bool:
    return str(job.get("operation") or "") in {
        "backend_compact",
    }


def new_item(
    user_item: Mapping[str, object],
    raw_message: str,
    effective_message: str,
    timestamp: str,
    context_refs: Mapping[str, object] | None = None,
    prompt_cwd: str = "",
    input_media: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    user_message_id = str(user_item.get("message_id") or "").strip()
    if not user_message_id:
        raise ValueError("Resident job requires message.message_id")
    return {
        "execution_id": f"execution-{secrets.token_hex(12)}",
        "user_message_id": user_message_id,
        "input_message": copy.deepcopy(dict(user_item)),
        "raw_message": raw_message,
        "effective_message": effective_message,
        "message": str(effective_message or raw_message),
        "prompt_cwd": prompt_cwd,
        "context_refs": dict(context_refs or {}),
        "input_media": list(input_media or []),
        "enqueued_at": timestamp,
    }


def identity_matches(job: Mapping[str, object], expected_execution_id: object) -> bool:
    expected = str(expected_execution_id or "").strip()
    return bool(expected) and expected == str(job.get("execution_id") or "").strip()


def is_self_stop_request(
    record: Mapping[str, object],
    job: Mapping[str, object],
) -> bool:
    exact_resident_id = resident_lineage.resident_id(record)
    return (
        bool(exact_resident_id)
        and read_product_env("PORTAL_RESIDENT_ID", "").strip()
        == exact_resident_id
        and identity_matches(
            job,
            read_product_env("PORTAL_RESIDENT_EXECUTION_ID", ""),
        )
    )








def continuation_fields(
    record: Mapping[str, object],
    *,
    timestamp: str = "",
) -> dict[str, Any]:
    return {
        "continuation_id": f"resident-continuation:{secrets.token_hex(12)}",
        "continuation_started_at": str(timestamp or utc_now_iso()),
    }


def read(
    record: Mapping[str, object],
) -> dict[str, Any]:
    exact_resident_id = resident_lineage.resident_id(record)
    identity_contract.validate_id(exact_resident_id, "resident")
    return get_lineage_data_store().resident_execution_record(
        exact_resident_id
    )


def pending_records() -> list[dict[str, Any]]:
    """Find delivery work from its durable owner, including pre-submit work."""
    from . import resident_catalog
    with get_lineage_data_store().database.read() as connection:
        ids = [row["resident_id"] for row in connection.execute(
            "SELECT resident_id FROM resident_execution_work ORDER BY resident_id"
        ).fetchall()]
    return resident_catalog.get_catalog().get_many(ids)


def read_many(
    records: Iterable[Mapping[str, object]],
) -> dict[str, dict[str, Any]]:
    resident_ids: list[str] = []
    for record in records:
        exact_resident_id = resident_lineage.resident_id(record)
        identity_contract.validate_id(exact_resident_id, "resident")
        resident_ids.append(exact_resident_id)
    return get_lineage_data_store().resident_execution_records(resident_ids)


def read_control(resident_id: str) -> dict[str, Any]:
    identity_contract.validate_id(resident_id, "resident")
    return get_lineage_data_store().resident_execution_control(resident_id)


def read_revision(resident_id: str) -> int:
    identity_contract.validate_id(resident_id, "resident")
    return get_lineage_data_store().resident_execution_revision(resident_id)


def acknowledge_execution(job):
    resident_id = str(job.get('resident_id') or '')
    if resident_id:
        get_lineage_data_store().acknowledge_resident_execution(resident_id,submitted_execution_id(job))


def notify_changed(record: Mapping[str, object]) -> None:
    from .changes import resident_changed

    resident_changed(record.get("resident_id"), scope="runtime")


def _conversation_queue_item(
    repository: Any,
    resident_id: str,
    item: Mapping[str, object],
) -> dict[str, Any]:
    preview = item.get("conversation_queue_preview")
    if isinstance(preview, Mapping):
        content_preview = str(preview.get("content_preview") or "")
        media_parts = preview.get("media_parts")
        if not isinstance(media_parts, list):
            stored_message = repository.public_message(
                resident_id,
                str(preview.get("message_id") or ""),
            )
            media_parts = [
                copy.deepcopy(dict(part))
                for part in stored_message.get("content_parts") or []
                if isinstance(part, Mapping)
                and str(part.get("type") or "") in {"image", "file"}
            ]
        return {
            **{
                key: str(preview.get(key) or "")
                for key in ("message_id", "author_id", "content_preview", "enqueued_at")
            },
            "content_parts": [
                *(
                    [{"type": "text", "text": content_preview}]
                    if content_preview
                    else []
                ),
                *[
                    copy.deepcopy(dict(part))
                    for part in media_parts
                    if isinstance(part, Mapping)
                    and str(part.get("type") or "") in {"image", "file"}
                ],
            ] if isinstance(media_parts, list) else (
                [{"type": "text", "text": content_preview}]
                if content_preview
                else []
            ),
        }
    message_id = str(item.get("user_message_id") or "")
    stored_input = item.get("input_message")
    message = (
        copy.deepcopy(dict(stored_input))
        if isinstance(stored_input, Mapping)
        else repository.public_message(resident_id, message_id)
    )
    content_preview = "\n".join(
        str(part.get("text") or "")
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping) and str(part.get("type") or "") == "text"
    ).strip()
    media_parts = [
        copy.deepcopy(dict(part))
        for part in message.get("content_parts") or []
        if isinstance(part, Mapping)
        and str(part.get("type") or "") in {"image", "file"}
    ]
    return {
        "message_id": message_id,
        "author_id": str(message.get("author_id") or ""),
        "content_preview": content_preview[:500],
        "content_parts": [
            *(
                [{"type": "text", "text": content_preview[:500]}]
                if content_preview
                else []
            ),
            *media_parts,
        ],
        "enqueued_at": str(item.get("enqueued_at") or message.get("created_at") or ""),
    }


def public_queue(
    repository: Any,
    resident_id: str,
    job: Mapping[str, object],
) -> dict[str, Any]:
    """The Resident's waiting queue as its Chat shows it, from its Job as read.

    The Job's queue is its Mailbox's queue rows; a held head waits in front.
    """

    return {
        "resident_id": resident_id,
        "items": [
            _conversation_queue_item(repository, resident_id, item)
            for item in public_waiting_queue(job)
        ],
    }


def conversation_state(
    job: Mapping[str, object] | None,
    *,
    intake_state: str,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    active_job = dict(job or {})
    queue = normalized_queue(active_job)
    waiting_queue = public_waiting_queue(active_job)
    execution_state = (
        public_execution_state(active_job, snapshot=snapshot)
        if isinstance(snapshot, Mapping) and active_job
        else {}
    )
    # Provider completion precedes Resident result delivery. Only the durable
    # Resident job may end the public lifecycle, after its reply/error is saved.
    if (
        not active_job
        or not is_active(active_job)
        or completion_applied(active_job)
    ):
        return {
            "work_state": "queued" if waiting_queue else "idle",
            "active_message_id": "",
            "queued_count": len(waiting_queue),
            "intake_state": resident_state.normalize_intake_state(intake_state),
        }
    if is_execution_held(active_job):
        return {
            "work_state": "queued",
            "active_message_id": "",
            "queued_count": len(waiting_queue),
            "intake_state": resident_state.normalize_intake_state(intake_state),
        }
    elif not submitted_execution_id(active_job):
        execution_state = {"status": "submitting", "queue_reason": ""}
    elif not execution_state:
        # A binding is a submission receipt, not evidence that a worker began.
        execution_state = {"status": "submitting", "queue_reason": ""}
    status = str(execution_state.get("status") or "").lower()
    state = {
        "work_state": (
            "stopping"
            if (
                bool(str(active_job.get("stop_requested_at") or "").strip())
                or bool(execution_state.get("cancel_requested"))
            )
            else
            "queued"
            if status in {"held", "submitting", "queued"}
            else "running"
        ),
        "active_message_id": str(active_job.get("user_message_id") or ""),
        "queued_count": len(queue),
        "intake_state": resident_state.normalize_intake_state(intake_state),
    }
    queue_reason = str(execution_state.get("queue_reason") or "").lower()
    if status in {"submitting", "claimed"} or (
        status == "queued" and queue_reason == "ready"
    ):
        state["work_phase"] = "starting_session"
    elif status == "queued" and queue_reason == "capacity":
        state["work_phase"] = "waiting_capacity"
    elif status == "queued" and queue_reason == "dispatch_interval":
        state["work_phase"] = "waiting_dispatch"
    elif status == "queued" and queue_reason == "quota_reserve":
        state["work_phase"] = "waiting_quota"
    elif (
        state["work_state"] == "running"
        and active_job.get("operation") == "context_fork_create"
        and not state["active_message_id"]
    ):
        # Native fork materializes a Session without delivering an input. Keep
        # that phase visible after the worker starts, until completion applies.
        state["work_phase"] = "starting_session"
    return state


def publish_conversation_state(
    record: Mapping[str, object],
    job: Mapping[str, object] | None,
    *,
    snapshot: Mapping[str, Any] | None = None,
    control_only: bool = False,
) -> None:
    from .resident_conversation_stream import get_repository

    resident_id = resident_lineage.resident_id(record)
    repository = get_repository()
    current_state = repository.published_state(resident_id)
    projected_state = conversation_state(
        job,
        intake_state="closed" if record.get("intake_open") is False else "open",
        snapshot=snapshot,
    )
    if (
        job
        and is_active(job)
        and not execution_state_projection(snapshot)
        and submitted_execution_id(job)
        and str(current_state.get("active_message_id") or "")
        == str(projected_state.get("active_message_id") or "")
        and str(current_state.get("work_state") or "") in {"queued", "running", "stopping"}
        and str(projected_state.get("work_state") or "") != "stopping"
    ):
        projected_state = {
            **current_state,
            "queued_count": projected_state.get("queued_count", 0),
            "intake_state": projected_state.get("intake_state", "open"),
        }
    # These public phases are committed only after input materialization below.
    # Reuse that durable activation checkpoint for the same input: metadata,
    # running snapshots and Stop must not re-read/recommit its immutable body.
    # Capacity/dispatch waiting and a different input still need activation.
    input_already_materialized = bool(
        current_state.get("active_message_id")
        and current_state.get("active_message_id")
        == projected_state.get("active_message_id")
        and (
            current_state.get("work_state") == "running"
            or current_state.get("work_phase") == "starting_session"
        )
    )
    if (
        job
        and is_active(job)
        and not is_execution_held(job)
        and not input_already_materialized
        and (
            str(projected_state.get("work_phase") or "") == "starting_session"
            or str(projected_state.get("work_state") or "") == "running"
        )
    ):
        materialized_job = read(record) if control_only else job
        if (
            _projection_identity(materialized_job) != _projection_identity(job)
            or materialized_job.get("user_message_id") != job.get("user_message_id")
        ):
            return
        materialize_conversation_input(record, materialized_job)
    repository.set_state(resident_id, projected_state)
    if (
        projected_state["work_state"] == "idle"
        or str(projected_state.get("active_message_id") or "")
        != str(current_state.get("active_message_id") or "")
    ):
        repository.set_activities(resident_id, [])
    internal_queue = (
        normalized_queue(dict(job or {})) if job and is_active(job) else []
    )
    repository.publish_queue(resident_id)
    candidates = [dict(job or {})] if job else []
    candidates.extend(internal_queue)
    for candidate in candidates:
        message_id = str(candidate.get("user_message_id") or "").strip()
        if not message_id:
            continue
        candidate_outcome = outcome(candidate)
        is_current = candidate is candidates[0]
        if (
            candidate_outcome == "completed"
            or completion_applied(candidate)
        ):
            input_state = "completed"
        elif candidate_outcome in {"failed"}:
            input_state = "failed"
        elif candidate_outcome == "cancelled":
            input_state = "cancelled"
        elif is_current and job and is_active(job):
            input_state = (
                "queued"
                if (
                    is_execution_held(job)
                    or str(projected_state.get("work_phase") or "")
                    in {"waiting_capacity", "waiting_dispatch", "waiting_quota"}
                )
                else "running"
            )
        else:
            input_state = "queued"
        error = {}
        if input_state == "failed":
            error = {
                "code": str(
                    ((candidate.get("runtime_error") or {}).get("code") or "execution_failed")
                    if isinstance(candidate.get("runtime_error"), Mapping)
                    else "execution_failed"
                ),
                "message": str(candidate.get("error") or "Resident execution failed"),
            }
        repository.set_input_state(
            resident_id,
            message_id,
            input_state,
            error=error,
        )


def materialize_conversation_input(
    record: Mapping[str, object],
    job: Mapping[str, object],
    *,
    activated_at: str = "",
) -> dict[str, Any] | None:
    """Commit the active input at its execution position in Conversation."""

    raw_message = job.get("input_message")
    if not isinstance(raw_message, Mapping):
        return None
    message = copy.deepcopy(dict(raw_message))
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        return None
    from .resident_conversation_stream import get_repository

    resident_id = resident_lineage.resident_id(record)
    repository = get_repository()
    existing = repository.message(resident_id, message_id)
    if existing:
        message = {
            **copy.deepcopy(dict(existing)),
            **message,
        }
    activation_time = str(
        activated_at
        or job.get("dispatched_at")
        or job.get("started_at")
        or job.get("updated_at")
        or utc_now_iso()
    )
    persisted = repository.commit_user_message(
        resident_id,
        message,
        activated_at=activation_time,
    )
    repository.set_input_state(
        resident_id,
        message_id,
        "running",
        updated_at=activation_time,
    )
    return persisted


def _projection_identity(value: Mapping[str, object]) -> str:
    return str(value.get("execution_id") or "")


def project_latest_conversation_state(
    record: Mapping[str, object],
    committed_job: Mapping[str, object] | None,
    *,
    committed_revision: int | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> None:
    """Project outside the Job lock and repair a concurrent revision once observed."""

    expected_identity = _projection_identity(committed_job or {})
    preferred_snapshot = copy.deepcopy(dict(snapshot)) if isinstance(snapshot, Mapping) else None
    for attempt in range(3):
        observed = read_control(resident_lineage.resident_id(record))
        current = dict(observed.get("state") or {})
        current_identity = _projection_identity(current)
        publish_conversation_state(
            record,
            current or None,
            control_only=True,
            snapshot=(
                preferred_snapshot
                if (
                    current_identity == expected_identity
                    and (
                        committed_revision is None
                        or int(observed.get("revision") or 0)
                        == int(committed_revision)
                    )
                )
                else None
            ),
        )
        if not current.get("execution_id") and committed_job and outcome(committed_job) in TERMINAL_STATUSES:
            message_id = str(committed_job.get("user_message_id") or "")
            if message_id:
                from .resident_conversation_stream import get_repository
                get_repository().publish_archived_input_state(resident_lineage.resident_id(record), message_id)
        if read_revision(resident_lineage.resident_id(record)) == int(observed.get("revision") or 0):
            return
        # A newer execution commit owns the final public state. Re-read and
        # repair it without holding the Resident state lock across projection IO.
        if attempt == 2:
            return


def _post_execution_commit(
    record: Mapping[str, object],
    committed_job: Mapping[str, object] | None,
    *,
    committed_revision: int | None = None,
    snapshot: Mapping[str, Any] | None = None,
) -> None:
    first_error: BaseException | None = None

    def run_effect(effect: Callable[[], None]) -> None:
        nonlocal first_error
        try:
            effect()
        except BaseException as error:
            if first_error is None:
                first_error = error

    # The committed, exact Execution must receive Stop before potentially slow
    # Conversation projection IO. This effect runs after the Resident state unlock;
    # its failure must not suppress the other committed effects.
    if committed_job and is_active(committed_job) and committed_job.get("stop_requested_at"):
        from .lifecycle import notify_execution_cancellation

        run_effect(lambda: notify_execution_cancellation(
            submitted_execution_id(committed_job),
            str(committed_job.get("actor") or ""),
        ))

    run_effect(lambda: project_latest_conversation_state(
        record,
        committed_job,
        committed_revision=committed_revision,
        snapshot=snapshot,
    ))

    # Notification and execution control are independent effects of the
    # committed execution state. A broken Conversation projection must not
    # prevent cancellation or binding from being delivered.
    run_effect(lambda: notify_changed(record))
    if committed_job and is_active(committed_job):
        from .lifecycle import notify_execution_binding
        if not is_execution_held(committed_job):
            run_effect(lambda: notify_execution_binding(
                resident_lineage.resident_id(record)
            ))
    run_effect(lambda: resident_domain_port.cleanup_expired_input_images(dict(record)))
    if first_error is not None:
        raise first_error


def authorize_conversation_projection(
    record: Mapping[str, object],
    *,
    
    execution_id: str,
    message: Mapping[str, Any],
) -> bool:
    """Persist one message projection authorization under the caller's Job lock."""

    job = read(record)
    if _projection_identity(job) != str(execution_id or ""):
        return False
    message_id = str(message.get("message_id") or "").strip()
    if not message_id:
        raise ValueError("Conversation projection requires message_id")
    quarantine = job.get("conversation_projection_quarantine")
    if (
        isinstance(quarantine, Mapping)
        and str(quarantine.get("execution_id") or "") == str(execution_id or "")
        and message_id in {
            str(value or "").strip()
            for value in quarantine.get("message_ids") or []
        }
    ):
        return False
    stored_intents = job.get(CONVERSATION_PROJECTION_INTENTS)
    intents = (
        copy.deepcopy(dict(stored_intents))
        if isinstance(stored_intents, Mapping)
        else {}
    )
    if message_id not in intents and len(intents) >= CONVERSATION_PROJECTION_INTENT_LIMIT:
        raise RuntimeError("Resident Conversation projection intent limit exceeded")
    intents[message_id] = {
        "execution_id": str(execution_id or ""),
        "message": copy.deepcopy(dict(message)),
    }
    job[CONVERSATION_PROJECTION_INTENTS] = intents
    get_lineage_data_store().put_resident_execution_record(
        resident_lineage.resident_id(record),
        job,
    )
    return True


def complete_conversation_projection(
    record: Mapping[str, object],
    *,
    
    execution_id: str,
    message_id: str,
) -> None:
    """Acknowledge an idempotently applied message projection."""

    job = read(record)
    stored_intents = job.get(CONVERSATION_PROJECTION_INTENTS)
    if not isinstance(stored_intents, Mapping) or message_id not in stored_intents:
        return
    intents = copy.deepcopy(dict(stored_intents))
    selected = intents.get(message_id)
    if not isinstance(selected, Mapping) or (
        str(selected.get("execution_id") or "") != str(execution_id or "")
    ):
        return
    intents.pop(message_id, None)
    if intents:
        job[CONVERSATION_PROJECTION_INTENTS] = intents
    else:
        job.pop(CONVERSATION_PROJECTION_INTENTS, None)
    get_lineage_data_store().put_resident_execution_record(
        resident_lineage.resident_id(record),
        job,
    )


def record_conversation_projection_error(
    record: Mapping[str, object],
    *,
    
    execution_id: str,
    payload: Mapping[str, Any],
    quarantine_limit: int,
) -> bool:
    """Record projection bookkeeping without republishing runtime state."""

    job = read(record)
    error_payload = copy.deepcopy(dict(payload))
    message_id = str(error_payload.get("message_id") or "").strip()
    intent_removed = False
    stored_intents = job.get(CONVERSATION_PROJECTION_INTENTS)
    if isinstance(stored_intents, Mapping) and message_id in stored_intents:
        intents = copy.deepcopy(dict(stored_intents))
        selected = intents.get(message_id)
        if isinstance(selected, Mapping) and (
            str(selected.get("execution_id") or "") == str(execution_id or "")
        ):
            intents.pop(message_id, None)
            intent_removed = True
            if intents:
                job[CONVERSATION_PROJECTION_INTENTS] = intents
            else:
                job.pop(CONVERSATION_PROJECTION_INTENTS, None)
    if _projection_identity(job) != str(execution_id or ""):
        if intent_removed:
            get_lineage_data_store().put_resident_execution_record(
                resident_lineage.resident_id(record),
                job,
            )
        return False
    job["conversation_projection_error"] = error_payload
    if message_id:
        stored_quarantine = job.get("conversation_projection_quarantine")
        quarantine = (
            dict(stored_quarantine)
            if isinstance(stored_quarantine, Mapping)
            and str(stored_quarantine.get("execution_id") or "")
            == str(execution_id or "")
            else {"execution_id": str(execution_id or ""), "message_ids": []}
        )
        message_ids = [
            str(value or "").strip()
            for value in quarantine.get("message_ids") or []
            if str(value or "").strip()
        ]
        if message_id not in message_ids:
            message_ids.append(message_id)
        quarantine.update({
            "message_ids": message_ids[-max(1, int(quarantine_limit)):],
            "last_event_sequence": int(error_payload.get("event_sequence") or 0),
            "updated_at": str(error_payload.get("recorded_at") or ""),
        })
        job["conversation_projection_quarantine"] = quarantine
    get_lineage_data_store().put_resident_execution_record(
        resident_lineage.resident_id(record),
        job,
    )
    return True


def retain_input_checkpoint(record: Mapping[str, Any], job: Mapping[str, Any], carried: list[Mapping[str, Any]]) -> None:
    """Retain the input needed for requeue and attach its infos to the Execution.

    Only inputs that someone sent are checkpoints: a /compact, a Fork's preparation,
    a project creation and an isolated authorized call are not.
    """

    from . import resident_input_scope

    message_id = str(job.get("user_message_id") or "").strip()
    if not message_id or str(job.get("operation") or "") or is_compact(job):
        return
    lineage_id, resident_id = resident_input_checkpoints.address(record)
    if not lineage_id or resident_input_scope.get(resident_id, message_id):
        return
    expected_execution_id = str(job.get("execution_id") or "")
    durable = read(record)
    # Stored preparation keeps the item as it was queued; the argument may
    # carry a prompt rebuilt for this delivery.
    source = durable if expected_execution_id and str(durable.get("execution_id") or "") == expected_execution_id else job
    repository = resident_conversation_stream.get_repository()
    completed: set[str] = set()
    for entry in resident_input_checkpoints.checkpoints(record):
        if "item" not in entry:
            continue
        try:
            if repository.input_state(resident_id, entry.get("message_id"))["state"] == "completed":
                completed.add(str(entry.get("message_id") or ""))
        except KeyError:
            completed.add(str(entry.get("message_id") or ""))
    entry = {
        "message_id": message_id,
        "execution_id": expected_execution_id,
        "kind": "group" if source.get("mailbox_members") else "request",
        "started_at": utc_now_iso(),
        "infos": [
            {"message_id": str(item.get("message_id") or ""), "author_id": str(item.get("author_id") or ""),
             "held_at": str(item.get("held_at") or "")}
            for item in carried if item.get("message_id")
        ],
        "item": resident_input_checkpoints.queue_item(source),
    }
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.execution_inputs import attach
    if carried:
        attach(get_agent_data_store(), expected_execution_id, [
            {"message_id":item["message_id"],"role":"info","accepted_at":item.get("held_at") or entry["started_at"]}
            for item in carried if item.get("message_id")
        ])
    store = get_lineage_data_store()
    with store.resident_mailbox_progress.transaction(lineage_id, resident_id) as state:
        kept = [item for item in state.get("checkpoints") or [] if isinstance(item, dict)]
        existing = next((item for item in kept if item.get("message_id") == message_id
                         and item.get("execution_id") == expected_execution_id), None)
        if existing is not None:
            # Reservation captured the original input before any projection or
            # prompt work. Later preparation may attach infos, not replace it.
            if carried:
                existing["infos"] = entry["infos"]
            return
        kept = [item for item in kept if item.get("message_id") != message_id]
        for item in kept:
            if item.get("message_id") in completed:
                item.pop("item", None)
        state["checkpoints"] = [*kept, entry][-resident_input_checkpoints.MAX_CHECKPOINTS:]


def write(
    record: Mapping[str, object],
    job: dict[str, Any],
    *,
    snapshot: Mapping[str, Any] | None = None,
    delivery_message_id: str = "",
) -> None:
    previous = read(record)
    previous_intents = previous.get(CONVERSATION_PROJECTION_INTENTS)
    # Projection authorization is owned exclusively by the narrow helpers
    # above. General Job transitions preserve the committed set exactly, so a
    # stale caller cannot remove an active intent or resurrect an acknowledged
    # one from an older in-memory Job copy.
    job.pop(CONVERSATION_PROJECTION_INTENTS, None)
    if isinstance(previous_intents, Mapping) and previous_intents:
        job[CONVERSATION_PROJECTION_INTENTS] = copy.deepcopy(
            dict(previous_intents)
        )
    job.pop("status", None)
    job.pop("queue_reason", None)
    if is_active(job):
        if not is_execution_held(job) and not submitted_execution_id(job):
            ensure_execution_id(job)
            # Passing the manual gate starts this input before Session / prompt
            # preparation. Closing intake later only holds subsequent inputs.
            job.setdefault("intake_admitted_at", utc_now_iso())
    if job.get("execution_id"):
        from . import resident_catalog
        from management_portal.execution.agent_data_store import get_agent_data_store
        resident_catalog.ensure_resident_agent(record, actor=request_actor(job))
        get_agent_data_store()
    # Admission also allocates a Conversation cursor. Use the same short
    # commit scope as streaming replies so their events cannot take one cursor.
    with (invariant_locks.resident_conversation_lock(resident_lineage.resident_id(record))
          if delivery_message_id else nullcontext()), get_lineage_data_store().database.transaction():
        committed_revision, committed_state = get_lineage_data_store().put_resident_execution_record(
            resident_lineage.resident_id(record),
            job,
            return_state=True,
            delivery_message_id=delivery_message_id,
        )
        if is_active(job) and execution_id(job) != execution_id(previous):
            # Preserve accepted preparation before projection or prompt work
            # can fail and release it. The existing checkpoint owns retries.
            retain_input_checkpoint(record, committed_state, [])
    job.clear()
    job.update(committed_state)
    if delivery_message_id:
        # The durable outbox owns the slow effects. A missed wake is recovered
        # by the coordinator scan; the caller only waits for the commit.
        from .lifecycle import wake_execution_binding
        invariant_locks.defer_until_state_unlock(lambda: wake_execution_binding(resident_lineage.resident_id(record)))
        return
    committed_record = copy.deepcopy(dict(record))
    committed_job = copy.deepcopy(job)
    committed_snapshot = (
        copy.deepcopy(dict(snapshot))
        if isinstance(snapshot, Mapping)
        else None
    )
    invariant_locks.defer_until_state_unlock(
        lambda: _post_execution_commit(
            committed_record,
            committed_job,
            committed_revision=committed_revision,
            snapshot=committed_snapshot,
        )
    )


def admit(record: Mapping[str, object], job: dict[str, Any], message: Mapping[str, Any]) -> None:
    """Commit immutable input, execution/queue state and publication intent together."""
    from .resident_conversation_stream import get_repository
    resident_id = resident_lineage.resident_id(record)
    def commit():
        get_repository().accept_user_message(resident_id, message)
        write(record, job, delivery_message_id=str(message["message_id"]))
    with invariant_locks.resident_conversation_lock(resident_id):
        get_lineage_data_store().transaction(commit)


def advance_execution_event_cursor(
    record: Mapping[str, object],
    *,
    
    execution_id: str,
    sequence: int,
) -> None:
    """Checkpoint consumed events with one forward-only conditional UPDATE.

    This changes recovery bookkeeping only: it must not materialize inputs,
    republish Conversation state, or wake execution binding/cancellation.
    Those effects remain owned by actual job/state transitions through write().
    """
    next_cursor = max(0, int(sequence or 0))
    if not next_cursor:
        return
    get_lineage_data_store().advance_resident_execution_cursor(
        resident_lineage.resident_id(record), execution_id, next_cursor,
    )


def clear(
    record: Mapping[str, object],
) -> None:
    get_lineage_data_store().delete_resident_execution_record(
        resident_lineage.resident_id(record)
    )
    committed_record = copy.deepcopy(dict(record))
    invariant_locks.defer_until_state_unlock(
        lambda: _post_execution_commit(committed_record, None)
    )


def execution_snapshot(
    job: Mapping[str, object],
    *,
    include_result: bool = False,
) -> dict[str, Any]:
    bound_execution_id = submitted_execution_id(job)
    if not bound_execution_id:
        return {}
    try:
        return execution_port.service().status(
            bound_execution_id,
            actor=str(job.get("actor") or ""),
            include_events=False,
            include_result=include_result,
        )
    except KeyError:
        # Execution records are disposable operational state. Resident state
        # remains readable after an acknowledged record has been pruned.
        return {}


def public_execution_state(
    job: Mapping[str, object],
    *,
    snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Scheduler-owned state exposed by a Resident binding."""

    if not job:
        return {}
    if is_execution_held(job):
        return {
            "status": "held",
            "queue_reason": (
                resident_state.INTAKE_CLOSED_QUEUE_REASON
                if is_intake_held(job)
                else INVALID_ACTIVATION_REASON
            ),
        }
    bound_execution_id = submitted_execution_id(job)
    if not bound_execution_id:
        return {"status": "submitting", "queue_reason": ""}
    resolved = (
        dict(snapshot)
        if isinstance(snapshot, Mapping)
        else execution_snapshot(job)
    )
    return execution_state_projection(resolved)


def _canonical_queue_input_message(
    item: Mapping[str, object],
) -> dict[str, Any]:
    """Restore the queue identity contract from its scheduling authority."""

    stored = item.get("input_message")
    message = (
        copy.deepcopy(dict(stored))
        if isinstance(stored, Mapping)
        else {}
    )
    message_id = str(item.get("user_message_id") or "").strip()
    if message_id:
        message["message_id"] = message_id
    return message


def input_items(job: Mapping[str, object]):
    """Inputs owned by the turn, Send now intents and waiting groups."""
    def members(item):
        yield item
        for member in item.get("mailbox_members") or []:
            yield from members(member)
    yield from members(job)
    for item in [*(job.get("queue") or []), *send_now_inputs(job)]:
        yield from members(item)


def input_location(resident_id: str, message_id: str, job: Mapping[str, object]):
    """Return the durable owner of an input, including held background info."""
    owned = job if is_active(job) else {key: value for key, value in job.items() if key not in {"user_message_id", "mailbox_members"}}
    for item in input_items(owned):
        if str(item.get("user_message_id") or "") == message_id:
            return item
    from .state_store import get_lineage_data_store
    entry = get_lineage_data_store().mailbox_entry(resident_id, message_id)
    if entry and entry["place"] == "info":
        return {"user_message_id": message_id, "input_message": entry["message"]}
    return None


def normalized_queue(
    job: Mapping[str, object],
    queue: object | None = None,
) -> list[dict[str, Any]]:
    raw_queue = job.get("queue") if queue is None else queue
    if not isinstance(raw_queue, list):
        return []
    current_message_id = str(job.get("user_message_id") or "").strip()
    normalized: list[dict[str, Any]] = []
    seen_message_ids = {str(item.get("user_message_id") or "") for item in input_items({**job, "queue": []})}
    seen_message_ids.discard("")
    for item in raw_queue:
        if not isinstance(item, dict):
            raise ValueError("Resident Queue items must be objects")
        item_message_id = str(item.get("user_message_id") or "").strip()
        if not item_message_id:
            raise ValueError("Resident Queue item requires user_message_id")
        if item_message_id == current_message_id:
            raise ValueError("Resident Queue contains the active message")
        for owned in input_items({"mailbox_members": [item]}):
            owned_id = str(owned.get("user_message_id") or "")
            if not owned_id:
                continue
            if owned_id in seen_message_ids:
                raise ValueError("Resident Queue contains a duplicate message_id")
            seen_message_ids.add(owned_id)
        normalized_item = copy.deepcopy(item)
        normalized_item["input_message"] = (
            _canonical_queue_input_message(normalized_item)
        )
        normalized.append(normalized_item)
    return normalized




def send_now_inputs(job: Mapping[str, object]) -> list[dict[str, Any]]:
    return [copy.deepcopy(item) for item in job.get("send_now_inputs") or []
            if isinstance(item, dict)]


def waiting_input_queue(job: Mapping[str, object]) -> list[dict[str, Any]]:
    return normalized_queue(job)


def public_waiting_queue(job: Mapping[str, object]) -> list[dict[str, Any]]:
    """Return inputs that are publicly waiting rather than executing.

    A held first input remains the internal durable job head so that reopening
    intake or a later triggering message can resume it without re-enqueueing.
    Publicly it is still a Queue item: it has not passed the intake gate yet.
    An admitted input preparing its Session already belongs to the active turn.
    """

    queue = normalized_queue(job)
    if is_active(job) and is_execution_held(job):
        return [copy.deepcopy(dict(job)), *queue]
    return queue




# Fields that belong to one queued input: promoting the next input replaces
# them, so a stale business reference or Mailbox group
# of the previous turn never travels with the next one.
QUEUE_ITEM_FIELDS = (
    "business_ref",
    "additional_skills",
    "allow_subagents",
    "mailbox_members",
    "conversation_queue_preview",
)


def promote_queue_item(
    job: dict[str, Any],
    item: Mapping[str, object],
    queue: list[dict[str, Any]],
    timestamp: str,
    *,
    retain_continuation: bool = False,
) -> dict[str, Any]:
    intake_state = resident_state.normalize_intake_state(
        job.get(resident_state.INTAKE_FIELD)
    )
    intake_closed = intake_state == resident_state.INTAKE_CLOSED
    retain_local_continuation = (
        retain_continuation
        and not intake_closed
    )
    from .resident_execution_storage import queue_input
    contract = {key: copy.deepcopy(job[key]) for key in (
        "schema", "schema_version", "resident_id", "lineage_id", "backend", "folder_schema",
    ) if key in job}
    if retain_local_continuation:
        contract.update({key: job[key] for key in ("continuation_id", "continuation_started_at") if key in job})
    item = queue_input(item)
    job.clear()
    job.update(contract)
    job.update(item)
    next_activation = str(
        item.get("activation_policy") or "enqueue_and_trigger"
    )
    job.update({
        "execution_id": str(item.get("execution_id") or f"execution-{secrets.token_hex(12)}"),
        "user_message_id": str(item.get("user_message_id") or ""),
        "input_message": _canonical_queue_input_message(item),
        "raw_message": str(item.get("raw_message") or ""),
        "message": str(item.get("message") or ""),
        "effective_message": str(
            item.get("effective_message") or item.get("message") or ""
        ),
        "prompt_cwd": str(item.get("prompt_cwd") or ""),
        "actor": str(item.get("actor") or ""),
        "context_refs": (
            dict(item.get("context_refs"))
            if isinstance(item.get("context_refs"), Mapping)
            else {}
        ),
        "input_media": (
            list(item.get("input_media"))
            if isinstance(item.get("input_media"), list)
            else []
        ),
        "activation_policy": next_activation,
        "started_at": timestamp,
        "dispatched_at": timestamp,
        "updated_at": timestamp,
        "error": "",
        "queue": queue,
        resident_state.INTAKE_FIELD: (
            resident_state.INTAKE_CLOSED
            if intake_closed
            else resident_state.INTAKE_OPEN
        ),
    })
    for field in ("actual_sent_prompt", "prompt_built_at", "prompt_builder_version"):
        if field in item:
            job[field] = item[field]
    next_execution_id = str(item.get("execution_id") or "")
    if next_execution_id:
        bind_execution(job, execution_id=next_execution_id, now=timestamp)
    for field in ("operation", "compact_session_id", "compact_scope_label"):
        value = item.get(field)
        if value not in (None, ""):
            job[field] = str(value)
    for field in QUEUE_ITEM_FIELDS:
        job.pop(field, None)
        if item.get(field) not in (None, ""):
            job[field] = copy.deepcopy(item[field])
    job["queue"] = normalized_queue(job, queue)
    return job


def failed_message_id(
    job: Mapping[str, object],
    *,
    failed_at: str = "",
) -> str:
    del failed_at
    persisted = str(job.get("failure_message_id") or "").strip()
    if persisted:
        return persisted
    identity = "|".join(
        str(value or "").strip()
        for value in (
            job.get("execution_id") or job.get("user_message_id") or "unknown",
            submitted_execution_id(job),
        )
    )
    return resident_message_contract.derived_message_identity(
        "resident-runtime-error",
        identity,
    )


def start(
    record: Mapping[str, object],
    continuation_id: str,
    runner: Callable[[str, str], None],
) -> None:
    resident_id = resident_lineage.resident_id(record)

    def run_exclusively() -> None:
        with invariant_locks.resident_continuation_lock(resident_id) as acquired:
            if acquired:
                runner(resident_id, str(continuation_id or ""))

    thread = threading.Thread(
        target=run_exclusively,
        name=f"resident-completion-{resident_id}",
        daemon=True,
    )
    with _THREADS_LOCK:
        active = _THREADS.get(key(record))
        if active is not None and active.is_alive():
            return
        _THREADS[key(record)] = thread
    thread.start()


def thread(record: Mapping[str, object]) -> threading.Thread | None:
    with _THREADS_LOCK:
        return _THREADS.get(key(record))


def track_current_thread(
    record: Mapping[str, object],
) -> None:
    with _THREADS_LOCK:
        _THREADS[key(record)] = threading.current_thread()


def finish_current_thread(
    record: Mapping[str, object],
) -> bool:
    thread_key = key(record)
    with _THREADS_LOCK:
        if _THREADS.get(thread_key) is not threading.current_thread():
            return False
        _THREADS.pop(thread_key, None)
    return True


def active_continuation_count() -> int:
    with _THREADS_LOCK:
        stale = [key for key, thread in _THREADS.items() if not thread.is_alive()]
        for thread_key in stale:
            _THREADS.pop(thread_key, None)
        return sum(thread.is_alive() for thread in _THREADS.values())


def continuation_running(record: Mapping[str, object]) -> bool:
    active_thread = thread(record)
    return bool(active_thread and active_thread.is_alive())


def continuation_needed(
    record: Mapping[str, object],
    job: Mapping[str, object],
) -> bool:
    if (
        not is_active(job)
        or is_execution_held(job)
    ):
        return False
    if not str(job.get("execution_id") or "") or continuation_running(record):
        return False
    # Shared Dev/Release recovery cannot infer ownership from this process's
    # thread registry. Avoid preparing/writing another waiter's active job.
    # start() still acquires ownership for the full runner lifetime: this short
    # availability probe is not a reservation across the dispatch boundary.
    with invariant_locks.resident_continuation_lock(
        resident_lineage.resident_id(record)
    ) as available:
        return available


ACTIVE_EXECUTION_STATUSES = frozenset({"queued", "claimed", "running"})





def bind_execution(job: dict[str, Any], *, execution_id: str, now: str) -> None:
    if str(job.get("execution_id") or "") != execution_id:
        raise ValueError("Execution submission cannot replace the reserved identity")
    job["submitted_at"] = job.get("submitted_at") or now
    job["updated_at"] = now


def execution_status(snapshot: Mapping[str, Any] | None) -> str:
    return str((snapshot or {}).get("status") or "").strip().lower()


def execution_state_projection(
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Expose Scheduler state without creating a second state machine."""

    status = execution_status(snapshot)
    if status not in (
        ACTIVE_EXECUTION_STATUSES | TERMINAL_STATUSES
    ):
        return {}
    scheduler = (
        snapshot.get("scheduler")
        if isinstance((snapshot or {}).get("scheduler"), Mapping)
        else {}
    )
    projection: dict[str, Any] = {"status": status}
    if bool(scheduler.get("cancel_requested")):
        projection["cancel_requested"] = True
    if status != "queued":
        projection["queue_reason"] = ""
        return projection
    projection.update(
        {
            "queue_reason": str(
                scheduler.get("queue_reason") or "ready"
            ).strip().lower(),
            "queue_position": int(
                scheduler.get("queue_position") or 0
            ),
            "capacity_key": str(
                scheduler.get("capacity_key") or ""
            ),
            "capacity_limit": int(
                scheduler.get("capacity_limit") or 0
            ),
            "active_capacity_count": int(
                scheduler.get("active_capacity_count") or 0
            ),
            "dispatch_interval_remaining_seconds": max(
                0.0,
                float(
                    scheduler.get(
                        "dispatch_interval_remaining_seconds"
                    )
                    or 0.0
                ),
            ),
        }
    )
    return projection
