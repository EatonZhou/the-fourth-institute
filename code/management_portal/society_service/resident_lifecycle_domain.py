"""Resident domain responsibilities: lifecycle."""
from __future__ import annotations

from . import (
    invariant_locks,
    resident_backend,
    resident_catalog,
    resident_conversation_stream,
    resident_lineage,
    resident_record_domain,
    resident_state,
)
from .domain_ports import execution_port, resident_lineage_host_port
from .resident_lifecycle import LifecycleStepPending
from contextlib import contextmanager
from management_portal.execution.contracts import ExecutionRequest
from pathlib import Path
from typing import Any, Mapping
import copy

from . import resident_domain_common


def resident_aggregate_lineage_id(resident_id: object) -> str:
    """Resolve graph membership for operations that also change topology."""

    clean_id = str(resident_id or "").strip()
    if not clean_id:
        raise ValueError("resident_id is required for Resident mutation locking")
    try:
        return resident_catalog.get_catalog().target(clean_id).lineage_id
    except KeyError as exc:
        raise KeyError(
            f"Resident mutation locking requires a registered Resident: {clean_id}"
        ) from exc


@contextmanager
def resident_state_scope(
    resident_id: object,
    operation: str = "",
):
    """Attach Resident request diagnostics without selecting a mutation lock."""

    with invariant_locks.diagnostic_scope(
        f"resident:{str(resident_id or '').strip()}",
        operation,
    ):
        yield


def resident_intake_state(record: Mapping[str, Any]) -> str:
    return resident_state.INTAKE_CLOSED if record.get("intake_open") is False else resident_state.INTAKE_OPEN


def resident_has_pending_work(resident_id: object) -> bool:
    if resident_record_domain.lifecycle_operations().pending(str(resident_id or "")):
        return True
    state = resident_conversation_stream.get_repository().current_state(str(resident_id or ""))
    return str(state.get("work_state") or "idle") in {
        "queued",
        "running",
        "stopping",
    }


def require_no_pending_lifecycle(resident_id: str) -> None:
    """Folder writes wait until a pending move has relocated the Resident folder."""

    if resident_record_domain.lifecycle_operations().pending(resident_id):
        raise ValueError("Resident 生命周期操作正在完成，请稍后编辑")


def _resident_turn_running(resident_id: str) -> bool:
    state = resident_conversation_stream.get_repository().current_state(resident_id)
    return str(state.get("work_state") or "idle") in {"running", "stopping"}


def ready_resident_lifecycle_operations() -> list[str]:
    return resident_record_domain.lifecycle_operations().ready()


def resident_lifecycle_health() -> dict[str, Any]:
    return resident_record_domain.lifecycle_operations().health()


def observe_committed_resident_transfer(operation_id: str, resident_id: str) -> dict[str, Any]:
    """Internal Lineage port: caller has verified receipt and both read grants."""
    return resident_record_domain.lifecycle_operations().observe_committed_transfer(operation_id, resident_id)


def resident_release_step(record: dict[str, Any], actor: str) -> dict[str, Any]:
    return {
        "kind": "release", "record": {
            key: copy.deepcopy(record[key]) for key in (
                "resident_id", "lineage_id", "backend", "backend_route_id",
                "workspace_path", "owned_workspace_id", "owned_workspace", "default_cwd",
            ) if key in record
        }, "actor": actor,
        "binding": resident_backend.session_record(record),
    }


def resume_resident_lifecycle_operation(operation_id: str) -> bool:
    from . import resident_execution_domain, resident_record_domain
    resident_id = resident_record_domain.lifecycle_operations().run(operation_id, apply_resident_lifecycle_step)
    if resident_id and resident_record_domain.find_resident(resident_id):
        resident_execution_domain.resume_resident_execution(resident_id)
    return bool(resident_id)


def apply_resident_lifecycle_step(operation_id: str, step: dict[str, Any]) -> None:
    from . import (
        resident_record_domain,
        resident_restart_domain,
        resident_runtime_domain,
        resident_session_backend_domain,
        resident_workspace_domain,
    )
    kind = step["kind"]
    record = dict(step.get("record") or {})
    if kind == "registration_abort":
        _compensate_resident_registration(operation_id, step)
    elif kind == "session_restart":
        resident_restart_domain.resume_resident_session_restart(step)
    elif kind == "execution_ack":
        try:
            execution_port.service().acknowledge(step["execution_id"], actor=step["actor"])
        except KeyError:
            # An acknowledged receipt may have been pruned before a crashed
            # lifecycle owner saved its cursor. There is nothing to recreate.
            pass
    elif kind == "context_archive":
        repository = resident_conversation_stream.get_repository()
        # The journal holds the exact content cutoff captured for this clear.
        repository.agent_data.record_context_reset(
            step["resident_id"], reset_at=step["reset_at"],
            actor_ref=str(step.get("actor") or ""),
            trace_position=step["trace_position"],
        )
        repository.archive_context(
            step["resident_id"], archived_at=step["archived_at"], message_ids=step["message_ids"],
        )
    elif kind == "remove":
        from .resident_workspace_retirement import remove_retired_folder
        remove_retired_folder(step["source"])
    elif kind == "archive":
        # Clearing context moves the run folder into the Resident's context archive.
        source, target = Path(step["source"]), Path(step["target"])
        if source.exists():
            if target.exists():
                raise FileExistsError(f"Archive target already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            # Atomic metadata rename; never overwrite a partially copied tree.
            source.rename(target)
    elif kind == "release":
        expected = step["binding"]
        binding = resident_backend.session_record(record, expected_binding=expected)
        if binding:
            resident_session_backend_domain.run_backend_session_operation(
                backend=resident_backend.execution_provider(record),
                operation="release", actor=step["actor"],
                cwd=resident_runtime_domain.resident_working_directory(record),
                backend_route_id=str(binding.get("backend_route_id") or ""),
                binding_id=resident_backend.execution_binding_id(record),
                runtime_options={"session_key": resident_runtime_domain.resident_execution_session_key(record), "delete_binding_on_release": True},
                operation_id=operation_id,
            )
        resident_backend.delete_session_identity(
            record, expected_provider_session_ref=str(expected.get("provider_session_ref") or ""),
        )
    elif kind == "forget_deleted":
        # What other stores keep for deleted Residents goes with them.
        from management_portal.workflow_bindings_store import get_workflow_binding_store

        execution_port.forget_binding_quota_overrides(list(step["binding_ids"]))
        get_workflow_binding_store().forget_residents(list(step["resident_ids"]))
    elif kind == "lineage_tombstone":
        resident_lineage_host_port.project_lineage_deletion(step)
    elif kind == "lineage_folder_unbind":
        resident_lineage_host_port.unbind_deleted_lineage_folder(step)
    elif kind == "resident_detach":
        resident_lineage_host_port.finalize_deleted_resident_detach(step["lineage_id"])
    elif kind == "workspace_document":
        with resident_record_domain.state_lock(step["resident_id"]):
            current = resident_record_domain.find_resident(step["resident_id"])
            if current:
                resident_workspace_domain.refresh_resident_workspace_document(current)
                from .state_store import get_lineage_data_store
                get_lineage_data_store().enqueue_resident_resource_projection(step["resident_id"], current)
    elif kind in {"workspace_move", "workspace_finish"}:
        from . import resident_workspace_transfer

        transfer_id = operation_id.rsplit(":", 1)[0]
        if kind == "workspace_move":
            # A turn that is already running keeps working in the old folder;
            # the folder moves when it ends. Queued work waits for this move.
            if _resident_turn_running(Path(step["source"]).name):
                raise LifecycleStepPending()
            resident_workspace_transfer.relocate(transfer_id, step["source"], step["target"])
        else:
            current = resident_record_domain.find_resident(step["resident_id"])
            if not current or resident_lineage.lineage_id(current) != step["lineage_id"]:
                raise RuntimeError("Resident membership changed during workspace transfer")
            resident_workspace_domain.ensure_resident_workspace(current)
            resident_workspace_domain.refresh_resident_workspace_document(current)
            resident_workspace_transfer.finish(transfer_id, step["source"], step["target"])
    else:
        raise ValueError(f"Unknown Resident lifecycle effect: {kind}")


def append_conversation_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    """Persist one complete message through the conversation stream authority."""
    from management_common.principal import message_origin

    message = copy.deepcopy(message)
    if message.get("role") == "agent":
        from management_common.async_questions import decode_question_content

        message["content_parts"] = decode_question_content(
            message.get("content_parts") or [], resident_id, message["message_id"],
        )
    if "origin" not in message and message.get("role") in {"agent", "system"}:
        sender = resident_id if message["role"] == "agent" else "portal"
        message["origin"] = message_origin(sender)
        message["author_id"] = sender
    return resident_conversation_stream.get_repository().append_message(
        resident_id,
        message,
    )


def mark_resident_input_queued(
    resident_id: str,
    message: Mapping[str, Any],
) -> None:
    resident_conversation_stream.get_repository().set_input_state(
        resident_id,
        str(message.get("message_id") or ""),
        "queued",
    )


def resident_user_message_count(resident_id: object) -> int:
    key = str(resident_id or "").strip()
    if not key:
        return 0
    return resident_conversation_stream.get_repository().user_message_counts([key]).get(key, 0)


def persist_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().commit_user_message(
        resident_id,
        message,
    )


def accept_resident_user_message(
    resident_id: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    return resident_conversation_stream.get_repository().accept_user_message(
        resident_id,
        message,
    )


def _compensate_resident_registration(operation_id: str, step: dict[str, Any]) -> None:
    from . import resident_domain_common, resident_record_domain
    record = step["record"]
    resident_id = resident_domain_common.record_resident_id(record)
    if resident_record_domain.find_resident_canonical(resident_id) is not None:
        return
    request_payload = step.get("execution_request")
    if request_payload:
        request = ExecutionRequest.from_payload(request_payload)
        service = execution_port.service()
        try:
            service.resume(
                request.execution_id, actor=step["actor"],
                correlation_id="backend-session:fork", service_type="agent_session",
                provider=resident_backend.execution_provider(record),
            )
        except KeyError:
            # The reservation precedes dispatch. Re-submit only this immutable
            # request: a lost acceptance reply must never allocate a new Fork.
            service.submit(request)
        snapshot = service.wait(
            request.execution_id, actor=step["actor"],
            timeout_seconds=resident_domain_common.CODEX_COMPACT_TIMEOUT_SECONDS + 90,
        )
        result = snapshot.get("result") or {}
        if (
            snapshot.get("status") not in {"completed", "failed", "cancelled"}
            or result.get("status") != snapshot.get("status")
            or result.get("execution_id", request.execution_id) != request.execution_id
        ):
            raise RuntimeError("Registration execution has no matching terminal result")
        # Keep the result unacknowledged until cleanup has a durable cursor.
    if resident_record_domain.find_resident_canonical(resident_id) is not None:
        return
    apply_resident_lifecycle_step(
        operation_id, resident_release_step(record, step["actor"]),
    )
    if step.get("source"):
        apply_resident_lifecycle_step(operation_id, {
            "kind": "archive", "source": step["source"], "target": step["target"],
        })
