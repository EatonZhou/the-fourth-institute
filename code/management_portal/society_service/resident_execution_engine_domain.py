"""Resident domain responsibilities: execution engine.

Submit one Execution, bind or resume it, wait for it while projecting its
events, and record the result."""
from __future__ import annotations
from dataclasses import replace
from typing import Any, Callable, Mapping
import copy
import hashlib
import sys
from management_portal.execution import event_projection as execution_events
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.harness_settings import normalize_agent_harness_settings
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from management_portal.society_service.resident_execution_work import bind_execution, execution_status
from . import (
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_work,
    resident_input_queue_domain,
    resident_lifecycle_domain,
    resident_record_domain,
    resident_restart_handoff,
    resident_runtime_domain,
)
from .domain_ports import execution_port

RESIDENT_PROJECTION_QUARANTINE_LIMIT = 64


def runtime_activity_for_execution(
    resident_id: str,
    job: dict[str, Any] | None,
    *,
    transcript_projection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project transient Execution activity without mixing it into Chat."""

    active_job = job if isinstance(job, dict) else {}
    execution_id = resident_execution_work.submitted_execution_id(active_job)
    if not execution_id:
        return {
            "live": bool(active_job),
            "execution_id": execution_id,
            "details": [],
        }
    projection = (
        dict(transcript_projection)
        if isinstance(transcript_projection, Mapping)
        else execution_events.execution_projection_from_execution(
            execution_id,
            runtime_limit=execution_events.DEFAULT_RUNTIME_DETAIL_LIMIT,
            message_limit=0,
        )
    )
    return {
        "live": True,
        "execution_id": execution_id,
        "details": [
            {
                "activity_id": str(item.get("id") or ""),
                "resident_id": resident_id,
                "message_id": str(active_job.get("user_message_id") or ""),
                "kind": str(item.get("kind") or "activity"),
                "status": (
                    "running"
                    if str(item.get("status") or "").strip().casefold()
                    in {"running", "in_progress", "inprogress"}
                    else "failed"
                    if str(item.get("status") or "").strip().casefold()
                    in {"failed", "error"}
                    else "completed"
                ),
                "title": str(item.get("title") or ""),
                "content": str(item.get("content") or ""),
                "started_at": str(item.get("created_at") or ""),
                "updated_at": str(item.get("created_at") or ""),
                **(
                    {"phase": str(item.get("phase") or "")}
                    if str(item.get("phase") or "").strip()
                    else {}
                ),
                # A running subagent's card, and the Execution it runs in
                # (the Chat stops one run there).
                **(
                    {"native_plan": copy.deepcopy(item["native_plan"]),
                     "execution_id": str(item.get("execution_id") or execution_id)}
                    if isinstance(item.get("native_plan"), dict)
                    else {}
                ),
                **(
                    {"subagent": item["subagent"], "execution_id": str(item.get("execution_id") or execution_id)}
                    if isinstance(item.get("subagent"), dict)
                    else {}
                ),
            }
            for item in projection.get("runtime_details") or []
            if isinstance(item, dict)
            and str(item.get("id") or "").strip()
        ],
    }


def resident_execution_request_backend(record: dict[str, Any], job: Mapping[str, Any] | None = None) -> str:
    return resident_backend.execution_provider(record)


def turn_allows_subagents(record: Mapping[str, object], job: Mapping[str, object], settings: Mapping[str, object]) -> bool:
    """The turn's subagent switch: the Resident's setting, where its Provider can start subagents."""

    definition = AGENT_PROVIDERS.get(resident_backend.execution_backend(record))
    value = bool(job.get("allow_subagents", settings.get("allow_subagents", False)))
    return definition is not None and definition.allows_subagents({"allow_subagents": value})


def resident_execution_runtime_options(
    record: dict[str, Any],
    job: dict[str, Any],
    *,
    is_owner: bool,
) -> dict[str, Any]:
    from . import resident_turn_scope

    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    job["_assembly_runtime_settings"] = copy.deepcopy(settings)
    scope = resident_turn_scope.filesystem_scope(
        record, job, cwd=str(resident_runtime_domain.resident_working_directory(record)),
        resident_folder=str(resident_record_domain.resident_folder_path(record)), bindings=resident_record_domain.resident_workspace_roots(record),
    )
    job["filesystem_scope"] = scope
    job["authorization_prompt"] = resident_turn_scope.context_prompt(scope, job, str(resident_record_domain.resident_folder_path(record)))
    options = {
        "runtime_settings": settings,
        "authorization_scope": scope,
        "session_key": resident_backend.execution_binding_id(record),
        "runtime_root": str(resident_record_domain.sessions_path(record) / "backend"),
        "title": str(record.get("title") or "Portal Resident"),
        "is_owner": bool(is_owner),
        "add_dirs": resident_domain_common.dedupe_strings([str(resident_record_domain.resident_workspace_path(record)), *resident_record_domain.workspace_paths(record)]),
        "execution_id": str(job.get("execution_id") or ""),
        "resident_environment": resident_runtime_domain.resident_cli_environment(record),
        "allow_subagents": turn_allows_subagents(record, job, settings),
        "client_user_message_id": str(job.get("user_message_id") or ""),
    }
    if "agent_harness" in settings:
        options["agent_harness"] = normalize_agent_harness_settings(settings["agent_harness"])
    options["resident_assembly"] = {"settings_revision": resident_runtime_domain.resident_settings_revision(record, settings)}
    return options


def resident_record_for_execution(resident_id: str) -> dict[str, Any] | None:
    """Read canonical identity for a bound execution, without external effects.

    Polling an already-bound job needs neither resource resolution nor
    bootstrap finalization. Both may perform slow external work and prevent
    a concurrent Stop from acquiring this Resident's short state scope.
    """
    try:
        return resident_catalog.get_catalog().get(resident_id)
    except KeyError:
        return None


def read_resident_execution_control(resident_id: str) -> dict[str, Any]:
    return dict(resident_execution_work.read_control(resident_id).get("state") or {})


def _execution_matches_input(
    job: Mapping[str, Any],
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    same_job = (
        bool(str(expected_execution_id or "").strip())
        and str(job.get("execution_id") or "") == str(expected_execution_id or "")
    )
    same_input = (
        bool(str(user_message_id or "").strip())
        and str(job.get("user_message_id") or "")
        == str(user_message_id or "")
    )
    return same_job or same_input


def _resident_execution_still_current(
    resident_id: str,
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    # A read-only answer: it can be stale the moment it returns whether or
    # not the state scope was held, so it reads the committed control alone.
    current = read_resident_execution_control(resident_id)
    return (
        _execution_matches_input(
            current,
            expected_execution_id,
            user_message_id,
        )
        and resident_execution_work.is_active(current)
        and not str(current.get("stop_requested_at") or "").strip()
    )


def resident_execution_cancellation_requested(
    resident_id: str,
    expected_execution_id: str,
    user_message_id: str = "",
) -> bool:
    current = read_resident_execution_control(resident_id)
    return bool(
        _execution_matches_input(
            current,
            expected_execution_id,
            user_message_id,
        )
        and resident_execution_work.is_active(current)
        and str(current.get("stop_requested_at") or "").strip()
    )


def _record_resident_execution(
    record: dict[str, Any],
    job: dict[str, Any],
    execution_id: str,
) -> None:
    resident_id = resident_domain_common.record_resident_id(record)
    with resident_record_domain.state_lock(resident_id):
        current_record = resident_record_domain.find_resident(resident_id) or {}
        if not current_record:
            return
        current = resident_execution_work.read(current_record)
        if not _execution_matches_input(
            current,
            str(job.get("execution_id") or ""),
            str(job.get("user_message_id") or ""),
        ):
            return
        bind_execution(
            current,
            execution_id=execution_id,
            now=resident_domain_common.now_iso(),
        )
        resident_execution_work.write(current_record, current)


def _flush_pending_active_turn_controls(
    service: object,
    resident_id: str,
    execution_id: str,
) -> None:
    while True:
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            if not record:
                return
            current = resident_execution_work.read(record)
            if (
                str(current.get("execution_id") or "") != execution_id
                or not resident_execution_work.is_active(current)
                or str(current.get("stop_requested_at") or "").strip()
                or str(current.get("execution_terminal_status") or "").strip()
                in {"completed", "failed", "cancelled"}
            ):
                return
            pending = [
                copy.deepcopy(candidate)
                for candidate in current.get("pending_active_turn_inputs") or []
                if isinstance(candidate, dict)
            ]
            if not pending:
                break
            item = pending[0]
            message_id = str(item.get("message_id") or "").strip()
            turn_input = item.get("input")
            if not message_id or not isinstance(turn_input, list) or not turn_input:
                return
            expected_execution_id = str(current.get("execution_id") or "")
        try:
            delivery = service.append_input(
                execution_id,
                actor=str(item.get("actor") or ""),
                input=[
                    dict(part)
                    for part in turn_input
                    if isinstance(part, dict)
                ],
                control_id=str(item.get("control_id") or "")
                or resident_input_queue_domain.resident_input_control_id(resident_id, message_id),
                source_segment_id=execution_port.input_source_segment_id(resident_id, message_id),
            )
        except Exception as error:
            print(
                f"Resident {resident_id} turn input {message_id} not appended to Execution {execution_id};"
                f" it stays pending: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            return
        if str(delivery.get("status") or "") not in {
            "delivered",
            "pending",
        }:
            return
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            current = resident_execution_work.read(record) if record else {}
            if str(current.get("execution_id") or "") != expected_execution_id or resident_execution_work.submitted_execution_id(current) != execution_id:
                return
            remaining = [value for value in current.get("pending_active_turn_inputs") or [] if value != item]
            if remaining:
                current["pending_active_turn_inputs"] = remaining
            else:
                current.pop("pending_active_turn_inputs", None)
            current["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, current)

    while True:
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            if not record:
                return
            current = resident_execution_work.read(record)
            if (
                str(current.get("execution_id") or "") != execution_id
                or not resident_execution_work.is_active(current)
                or str(current.get("stop_requested_at") or "").strip()
                or str(current.get("execution_terminal_status") or "").strip()
                in {"completed", "failed", "cancelled"}
            ):
                return
            pending_settings = [
                copy.deepcopy(candidate)
                for candidate in current.get("pending_active_turn_settings") or []
                if isinstance(candidate, dict)
            ]
            if not pending_settings:
                return
            item = pending_settings[0]
            expected_execution_id = str(current.get("execution_id") or "")
        try:
            delivery = service.update_turn_settings(
                execution_id,
                actor=str(item.get("actor") or ""),
                service_tier=str(item.get("service_tier") or ""),
                control_id=str(item.get("control_id") or ""),
            )
        except Exception as error:
            print(
                f"Resident {resident_id} turn settings not sent to Execution {execution_id};"
                f" they stay pending: {type(error).__name__}: {error}",
                file=sys.stderr,
            )
            return
        if str(delivery.get("status") or "") not in {
            "delivered",
            "pending",
        }:
            return
        with resident_record_domain.state_lock(resident_id):
            record = resident_record_domain.find_resident(resident_id) or {}
            current = resident_execution_work.read(record) if record else {}
            if str(current.get("execution_id") or "") != expected_execution_id or resident_execution_work.submitted_execution_id(current) != execution_id:
                return
            remaining_settings = [value for value in current.get("pending_active_turn_settings") or [] if value != item]
            if remaining_settings:
                current["pending_active_turn_settings"] = remaining_settings
            else:
                current.pop("pending_active_turn_settings", None)
            current["updated_at"] = resident_domain_common.now_iso()
            resident_execution_work.write(record, current)


def durable_resident_execution_request(
    record: dict[str, Any],
    job: dict[str, Any],
    request_factory: Callable[[], ExecutionRequest],
) -> ExecutionRequest:
    """Recover the durable request before constructing a new submission."""

    stored_payload = job.get("execution_request")
    if isinstance(stored_payload, dict):
        durable_request = ExecutionRequest.from_payload(stored_payload)
        expected_id = resident_execution_work.execution_id(job)
        if expected_id and durable_request.execution_id != expected_id:
            raise RuntimeError("Resident execution request id does not match its job")
    else:
        planned_id = resident_execution_work.ensure_execution_id(job)
        durable_request = replace(request_factory(), execution_id=planned_id).validate()
        options = dict(durable_request.runtime_options)
        options["resident_assembly"] = {**dict(options.get("resident_assembly") or {}),
            "prompt_parts": [{"id": part.name, "timing": part.timing, "placement": part.placement,
                              "content_digest": hashlib.sha256(part.content.encode()).hexdigest(),
                              "content_location": {"execution_id": planned_id, "field": "system_instructions.parts", "part_id": part.name},
                              "source": "resident.md" if part.name == "resident_md" else part.name}
                             for part in durable_request.system_instructions.parts],
            "skills": copy.deepcopy(job.get("_assembly_skill_observations") or [])}
        durable_request = replace(durable_request, runtime_options=options)
    planned_id = durable_request.execution_id
    payload = durable_request.to_payload()
    resident_id = resident_domain_common.record_resident_id(record)
    with resident_record_domain.state_lock(resident_id):
        current_record = resident_record_domain.find_resident(resident_id) or {}
        if not current_record:
            raise RuntimeError("Resident disappeared before Execution submission")
        current = resident_execution_work.read(current_record)
        if str(current.get("execution_id") or "") != str(job.get("execution_id") or ""):
            raise RuntimeError("Resident job changed before Execution submission")
        if (
            str(current.get("stop_requested_at") or "").strip()
            and not resident_execution_work.submitted_execution_id(current)
        ):
            raise resident_domain_common.ResidentTurnCancelled(
                "Resident turn was stopped before Execution submission"
            )
        existing_payload = current.get("execution_request")
        if isinstance(existing_payload, dict):
            durable_request = ExecutionRequest.from_payload(existing_payload)
            payload = durable_request.to_payload()
        else:
            current["execution_id"] = planned_id
            current["execution_request"] = payload
            resident_execution_work.write(current_record, current)
    job["execution_id"] = durable_request.execution_id
    job["execution_request"] = payload
    return durable_request


def _project_resident_execution_snapshot(
    resident_id: str,

    execution_id: str,
    snapshot: dict[str, Any],
    *,
    activity_projection: execution_events.ExecutionActivityProjection | None = None,
) -> None:
    projected_snapshot = dict(snapshot)
    if resident_restart_handoff.receipt(projected_snapshot):
        return
    # Most snapshots change nothing in the Job. Read and compare without the
    # state scope; take it only to write, re-checking ownership inside.
    current_record = resident_record_for_execution(resident_id) or {}
    if not current_record:
        return
    current = read_resident_execution_control(resident_id)
    if (
        str(current.get("execution_id") or "") != str(execution_id or "")
    ):
        return
    incoming_status = execution_status(projected_snapshot)
    terminal_status = str(
        current.get("execution_terminal_status") or ""
    ).strip().lower()
    changed = current.pop("conversation_projection_error", None) is not None
    if incoming_status in {"completed", "failed", "cancelled"}:
        terminal_status = incoming_status
        if current.get("execution_terminal_status") != terminal_status:
            current["execution_terminal_status"] = terminal_status
            changed = True
    elif terminal_status in {"completed", "failed", "cancelled"}:
        projected_snapshot["status"] = terminal_status
        projected_snapshot.pop("scheduler", None)
    if changed:
        with resident_record_domain.state_lock(resident_id):
            complete_job = resident_execution_work.read(current_record)
            if (
                str(complete_job.get("execution_id") or "") != str(execution_id or "")
            ):
                return
            complete_job.pop("conversation_projection_error", None)
            if terminal_status:
                complete_job["execution_terminal_status"] = terminal_status
            resident_execution_work.write(current_record, complete_job)
        current = complete_job
    exact_resident_id = resident_domain_common.record_resident_id(current_record)
    if not resident_execution_work.is_active(current):
        resident_conversation_stream.get_repository().set_activities(
            exact_resident_id,
            [],
        )
        return
    transcript_projection = (
        activity_projection.read()
        if activity_projection is not None else
        execution_events.execution_projection_from_execution(
            execution_id,
            runtime_limit=execution_events.DEFAULT_RUNTIME_DETAIL_LIMIT,
            message_limit=0,
        )
    )
    # The append-only Execution activity stream is the one ordered queue for
    # visible output. Do not also deliver from scheduler event callbacks.
    persist_execution_display_messages(
        exact_resident_id,

        execution_id,
        [
            dict(item)
            for item in transcript_projection.get("outputs") or []
            if isinstance(item, Mapping)
        ],
    )
    if activity_projection is not None:
        activity_projection.acknowledge()
    # The ownership fence is the committed revision: publish from one read,
    # then re-project if a Job transition committed in between.
    observed_revision = 0
    for attempt in range(2):
        observed = resident_execution_work.read_control(resident_id)
        observed_revision = int(observed.get("revision") or 0)
        current = dict(observed.get("state") or {})
        if (
            str(current.get("execution_id") or "") != str(execution_id or "")
            or resident_execution_work.submitted_execution_id(current) != str(execution_id or "")
            or not resident_execution_work.is_active(current)
            or resident_execution_work.completion_applied(current)
        ):
            return
        publish_snapshot = dict(projected_snapshot)
        terminal_status = str(current.get("execution_terminal_status") or "")
        if terminal_status in {"completed", "failed", "cancelled"}:
            publish_snapshot["status"] = terminal_status
            publish_snapshot.pop("scheduler", None)

        details = []
        if execution_status(publish_snapshot) not in {"completed", "failed", "cancelled"}:
            activity = runtime_activity_for_execution(
                exact_resident_id,
                current,
                transcript_projection=transcript_projection,
            )
            details = activity.get("details") if isinstance(activity.get("details"), list) else []
        resident_execution_work.publish_conversation_state(
            current_record,
            current,
            snapshot=publish_snapshot,
            control_only=True,
        )
        resident_conversation_stream.get_repository().set_activities(
            exact_resident_id,
            details,
        )
        if resident_execution_work.read_revision(resident_id) == observed_revision:
            break
        after = resident_execution_work.read_control(resident_id)
        latest = dict(after.get("state") or {})
        if (
            str(latest.get("execution_id") or "") != str(execution_id or "")
            or resident_execution_work.submitted_execution_id(latest) != str(execution_id or "")
        ):
            resident_execution_work.project_latest_conversation_state(
                current_record,
                latest or None,
            )
            resident_conversation_stream.get_repository().set_activities(
                exact_resident_id,
                [],
            )
            break
        if attempt:
            break
    resident_execution_work.notify_changed(current_record)


def _record_resident_projection_error(
    resident_id: str,

    execution_id: str,
    error: Exception,
    *,
    event_sequence: int = 0,
) -> dict[str, Any]:
    """Record a projection fault without changing the Execution terminal state."""

    message_id = str(
        getattr(error, "_resident_projection_message_id", "") or ""
    ).strip()
    recorded_at = resident_domain_common.now_iso()
    payload = {
        "message": resident_domain_common.truncate_text(f"{type(error).__name__}: {error}", 1600),
        "execution_id": str(execution_id or ""),
        "event_sequence": max(0, int(event_sequence or 0)),
        "recorded_at": recorded_at,
        **({"message_id": message_id} if message_id else {}),
    }
    try:
        record = resident_record_for_execution(resident_id) or {}
        if not record:
            return payload
        with resident_record_domain.state_lock(resident_id):
            resident_execution_work.record_conversation_projection_error(
                record,

                execution_id=execution_id,
                payload=payload,
                quarantine_limit=RESIDENT_PROJECTION_QUARANTINE_LIMIT,
            )
    except Exception as record_error:
        print(
            "Resident conversation projection error could not be recorded: "
            f"{type(record_error).__name__}: {record_error}",
            file=sys.stderr,
        )
    print(
        "Resident conversation projection failed without terminalizing Execution: "
        f"{payload['message']}",
        file=sys.stderr,
    )
    return payload


def _resident_execution_event_cursor(
    resident_id: str,

    execution_id: str,
) -> int:
    # The cursor only moves forward, so a read that races an advance returns
    # an earlier cursor and replays events the consumer already deduplicates.
    job = read_resident_execution_control(resident_id)
    if (
        str(job.get("execution_id") or "") != str(execution_id or "")
    ):
        return 0
    return resident_domain_common.nonnegative_int(job.get("execution_event_cursor"), 0)


def _advance_resident_execution_event_cursor(
    resident_id: str,

    execution_id: str,
    sequence: int,
) -> None:
    next_cursor = max(0, int(sequence or 0))
    if not next_cursor:
        return
    # One conditional UPDATE: it moves only this Execution's row and only
    # forward, so it needs no Resident state scope.
    resident_execution_work.advance_execution_event_cursor(
        {"resident_id": resident_id},

        execution_id=execution_id,
        sequence=next_cursor,
    )


def persist_execution_display_messages(
    resident_id: str,

    execution_id: str,
    outputs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from management_common.async_questions import question_message_parts

    display_messages: list[dict[str, Any]] = []
    repository = resident_conversation_stream.get_repository()
    for raw_output in outputs:
        output = dict(raw_output)
        canonical_final = (
            output.get("message")
            if output.get("canonical_final") and isinstance(output.get("message"), Mapping)
            else None
        )
        message_id = str(output.get("output_id") or "").strip()
        content = str(output.get("text") or "").strip()
        questions = output.get("questions") or []
        native_parts = output.get("content_parts") or []
        if not message_id or (canonical_final is None and not content and not questions and not native_parts):
            continue
        is_final = str(output.get("phase") or "").strip().casefold() in {
            "final",
            "final_answer",
        }
        if is_final and not questions and canonical_final is None:
            continue
        if is_final:
            content = ""

        existing = repository.message(resident_id, message_id)
        # Capture the reply fence from the committed control. Authorization
        # below re-checks it under the Job lock before anything is written.
        job = read_resident_execution_control(resident_id)
        if (
            str(job.get("execution_id") or "") != str(execution_id or "")
        ):
            break
        stored_quarantine = job.get("conversation_projection_quarantine")
        quarantined_message_ids = (
            {
                str(value or "").strip()
                for value in stored_quarantine.get("message_ids") or []
                if str(value or "").strip()
            }
            if isinstance(stored_quarantine, Mapping)
            and str(stored_quarantine.get("execution_id") or "")
            == str(execution_id or "")
            else set()
        )
        if message_id in quarantined_message_ids:
            continue
        record = {"resident_id": job["resident_id"]}
        exact_resident_id = resident_domain_common.record_resident_id(record)
        reply_to_message_id = str(job.get("user_message_id") or "")

        if canonical_final is not None:
            message = copy.deepcopy(dict(canonical_final))
        elif output.get("role") == "system" and isinstance(output.get("message"), Mapping):
            # The Harness produced this input; the Conversation consumer
            # places it in the trace before publishing its event.
            message = copy.deepcopy(dict(output["message"]))
        else:
            # Replayed events carry an observation time, not a new creation time.
            message = resident_input_queue_domain.build_message(
                "agent",
                content,
                message_id=message_id,
                created_at=str((existing or output).get("created_at") or ""),
                reply_to_message_id=reply_to_message_id,
                execution_id=execution_id,
                content_parts=repository.execution_content_parts(
                    exact_resident_id, execution_id, native_parts,
                ) if native_parts else question_message_parts(
                    content, questions, exact_resident_id, message_id,
                ),
            )

        # Persist authorization before crossing into AgentData/Conversation.
        # This intent is the logical commit point for a later idempotent append.
        with resident_record_domain.state_lock(resident_id):
            current = read_resident_execution_control(resident_id)
            if (
                str(current.get("execution_id") or "") != str(execution_id or "")
                or str(current.get("user_message_id") or "") != reply_to_message_id
                or (canonical_final is not None and (
                    str(current.get("actor") or "") != str(output.get("actor") or "")
                    or str(current.get("stop_requested_at") or "").strip()
                ))
            ):
                break
            try:
                authorized = resident_execution_work.authorize_conversation_projection(
                    record,

                    execution_id=execution_id,
                    message=message,
                )
            except KeyError:
                # The Resident was deleted while its Job row remained.
                break
            if not authorized:
                continue
        try:
            persisted = resident_lifecycle_domain.append_conversation_message(exact_resident_id, message)
        except Exception as error:
            setattr(error, "_resident_projection_message_id", message_id)
            raise
        with resident_record_domain.state_lock(resident_id):
            try:
                resident_execution_work.complete_conversation_projection(
                    record,

                    execution_id=execution_id,
                    message_id=message_id,
                )
            except KeyError:
                pass
        display_messages.append(persisted)
    return display_messages


def wait_for_resident_execution(
    service: object,
    *,
    execution_id: str,
    actor: str,
    resident_id: str,

    user_message_id: str = "",
    persist_messages: bool = True,
    timeout_seconds: float | None = None,
    cancellation_already_requested: bool = False,
) -> dict[str, Any]:
    wait_timeout = (
        max(30.0, float(timeout_seconds))
        if timeout_seconds is not None
        else None
    )
    projection_errors: list[dict[str, Any]] = []
    activity_projection = execution_events.ExecutionActivityProjection(execution_id)

    def record_projection_error(error: Exception, *, event_sequence: int = 0) -> None:
        projection_errors.append(
            _record_resident_projection_error(
                resident_id,

                execution_id,
                error,
                event_sequence=event_sequence,
            )
        )

    def project_execution_events(events: list[dict[str, Any]]) -> None:
        # The checkpoint is forward-only, so one advance to the batch's last
        # sequence records the same progress as one advance per event.
        sequence = max((int(event.get("sequence") or 0) for event in events), default=0)
        try:
            _advance_resident_execution_event_cursor(
                resident_id,

                execution_id,
                sequence,
            )
        except Exception as error:
            record_projection_error(error, event_sequence=sequence)

    def project_execution_state(snapshot: dict[str, Any]) -> bool:
        try:
            # A peer Worker can commit a receipt without this Scheduler seeing
            # its transient control event. Reconcile durable receipts on state
            # changes, including the initial snapshot after reconnect.
            resident_input_queue_domain.reconcile_queued_turn_inputs(resident_id, execution_id)
            _project_resident_execution_snapshot(
                resident_id,

                execution_id,
                snapshot,
                activity_projection=activity_projection,
            )
            return True
        except Exception as error:
            record_projection_error(error)
            return False

    snapshot = service.wait(
        execution_id,
        actor=actor,
        after_sequence=_resident_execution_event_cursor(
            resident_id,

            execution_id,
        ),
        timeout_seconds=wait_timeout,
        cancellation_requested=(
            None
            if cancellation_already_requested
            else lambda: not _resident_execution_still_current(
                resident_id,
                execution_id,
                user_message_id,
            )
        ),
        on_events=project_execution_events,
        on_snapshot=project_execution_state,
    )
    if resident_restart_handoff.receipt(snapshot):
        raise resident_restart_handoff.ExecutionHandedOff()
    if persist_messages and execution_status(snapshot) in {"completed", "failed", "cancelled"}:
        from .resident_resource_changes import display_outputs
        try:
            resource_outputs = display_outputs(
                resident_conversation_stream.get_repository(), resident_id, execution_id,
            )
            if resource_outputs:
                persist_execution_display_messages(
                    resident_id,  execution_id, resource_outputs,
                )
        except Exception as error:
            record_projection_error(error)
    snapshot["projection_errors"] = list(projection_errors)
    return snapshot


def bind_or_resume_resident_execution(
    service: object,
    request: object,
    record: dict[str, Any],
    job: dict[str, Any],
) -> str:
    resident_catalog.ensure_resident_agent(record, actor=str(job.get("actor") or ""))
    execution_id = resident_execution_work.submitted_execution_id(job)
    actor = str(getattr(request, "actor", "") or job.get("actor") or "")
    correlation_id = str(job.get("execution_id") or "")
    service_type = str(
        getattr(request, "service_type", "") or "agent_session"
    )
    provider = str(
        getattr(request, "provider", "")
        or resident_execution_request_backend(record, job)
    )
    if execution_id:
        service.resume(
            execution_id,
            actor=actor,
            correlation_id=correlation_id,
            service_type=service_type,
            provider=provider,
        )
        _flush_pending_active_turn_controls(
            service,
            resident_domain_common.record_resident_id(record),
            execution_id,
        )
        return execution_id
    if resident_execution_cancellation_requested(
        resident_domain_common.record_resident_id(record),
        correlation_id,
        str(job.get("user_message_id") or ""),
    ):
        raise resident_domain_common.ResidentTurnCancelled(
            "Resident turn was stopped before Execution submission"
        )
    submitted = service.submit(request)
    execution_id = str(
        submitted.get("execution_id") or request.execution_id
    )
    _record_resident_execution(
        record,
        job,
        execution_id,
    )
    bind_execution(
        job,
        execution_id=execution_id,
        now=resident_domain_common.now_iso(),
    )
    _flush_pending_active_turn_controls(
        service,
        resident_domain_common.record_resident_id(record),
        execution_id,
    )
    return execution_id
