"""Resident domain responsibilities: lifecycle steps.

The lifecycle step table: each journaled step kind maps to its handler."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
from management_portal.execution.contracts import ExecutionRequest
from . import (
    resident_backend,
    resident_conversation_stream,
    resident_domain_common,
    resident_execution_domain,
    resident_lifecycle_domain,
    resident_lineage,
    resident_record_domain,
    resident_restart_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resident_workspace_domain,
)
from .domain_ports import execution_port, resident_lineage_host_port
from .resident_lifecycle import LifecycleStepPending


def resume_resident_lifecycle_operation(operation_id: str) -> bool:
    resident_id = resident_record_domain.lifecycle_operations().run(operation_id, apply_resident_lifecycle_step)
    if resident_id and resident_record_domain.find_resident(resident_id):
        resident_execution_domain.resume_resident_execution(resident_id)
    return bool(resident_id)


def _execution_ack(operation_id: str, step: dict[str, Any]) -> None:
    try:
        execution_port.service().acknowledge(step["execution_id"], actor=step["actor"])
    except KeyError:
        # An acknowledged receipt may have been pruned before a crashed
        # lifecycle owner saved its cursor. There is nothing to recreate.
        pass


def _session_restart(operation_id: str, step: dict[str, Any]) -> None:
    resident_restart_domain.resume_resident_session_restart(step)


def _context_archive(operation_id: str, step: dict[str, Any]) -> None:
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


def _remove(operation_id: str, step: dict[str, Any]) -> None:
    from .resident_workspace_retirement import remove_retired_folder
    remove_retired_folder(step["source"])


def _archive(operation_id: str, step: dict[str, Any]) -> None:
    # Clearing context moves the run folder into the Resident's context archive.
    source, target = Path(step["source"]), Path(step["target"])
    if source.exists():
        if target.exists():
            raise FileExistsError(f"Archive target already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic metadata rename; never overwrite a partially copied tree.
        source.rename(target)


def _release(operation_id: str, step: dict[str, Any]) -> None:
    record = dict(step.get("record") or {})
    expected = step["binding"]
    binding = resident_backend.session_record(record, expected_binding=expected)
    if binding:
        resident_session_backend_domain.run_backend_session_operation(
            backend=resident_backend.execution_provider(record),
            operation="release", actor=step["actor"],
            cwd=resident_runtime_domain.resident_working_directory(record),
            backend_route_id=str(binding.get("backend_route_id") or ""),
            binding_id=resident_backend.execution_binding_id(record),
            runtime_options={"session_key": resident_execution_session_key(record), "delete_binding_on_release": True},
            operation_id=operation_id,
        )
    resident_backend.delete_session_identity(
        record, expected_provider_session_ref=str(expected.get("provider_session_ref") or ""),
    )


def _forget_deleted(operation_id: str, step: dict[str, Any]) -> None:
    # What other stores keep for deleted Residents goes with them.
    from management_portal.workflow_bindings_store import get_workflow_binding_store

    execution_port.forget_binding_quota_overrides(list(step["binding_ids"]))
    get_workflow_binding_store().forget_residents(list(step["resident_ids"]))


def _lineage_tombstone(operation_id: str, step: dict[str, Any]) -> None:
    resident_lineage_host_port.project_lineage_deletion(step)


def _lineage_folder_unbind(operation_id: str, step: dict[str, Any]) -> None:
    resident_lineage_host_port.unbind_deleted_lineage_folder(step)


def _resident_detach(operation_id: str, step: dict[str, Any]) -> None:
    resident_lineage_host_port.finalize_deleted_resident_detach(step["lineage_id"])


def _workspace_document(operation_id: str, step: dict[str, Any]) -> None:
    with resident_record_domain.state_lock(step["resident_id"]):
        current = resident_record_domain.find_resident(step["resident_id"])
        if current:
            resident_workspace_domain.refresh_resident_workspace_document(current)
            from .state_store import get_lineage_data_store
            get_lineage_data_store().enqueue_resident_resource_projection(step["resident_id"], current)


def _workspace_move(operation_id: str, step: dict[str, Any]) -> None:
    from . import resident_workspace_transfer
    # A turn that is already running keeps working in the old folder;
    # the folder moves when it ends. Queued work waits for this move.
    if resident_lifecycle_domain.resident_turn_running(Path(step["source"]).name):
        raise LifecycleStepPending()
    resident_workspace_transfer.relocate(operation_id.rsplit(":", 1)[0], step["source"], step["target"])


def _workspace_finish(operation_id: str, step: dict[str, Any]) -> None:
    from . import resident_workspace_transfer
    current = resident_record_domain.find_resident(step["resident_id"])
    if not current or resident_lineage.lineage_id(current) != step["lineage_id"]:
        raise RuntimeError("Resident membership changed during workspace transfer")
    resident_workspace_domain.ensure_resident_workspace(current)
    resident_workspace_domain.refresh_resident_workspace_document(current)
    resident_workspace_transfer.finish(operation_id.rsplit(":", 1)[0], step["source"], step["target"])



def _compensate_resident_registration(operation_id: str, step: dict[str, Any]) -> None:
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
        operation_id, resident_lifecycle_domain.resident_release_step(record, step["actor"]),
    )
    if step.get("source"):
        apply_resident_lifecycle_step(operation_id, {
            "kind": "archive", "source": step["source"], "target": step["target"],
        })


def resident_execution_session_key(record: dict[str, Any]) -> str:
    return f"resident:{resident_domain_common.record_resident_id(record)}"


# Step kind -> handler(operation_id, step). The kinds are stored in the journal; keep them.
STEPS: dict[str, Callable[[str, dict[str, Any]], None]] = {
    "registration_abort": _compensate_resident_registration,
    "session_restart": _session_restart,
    "execution_ack": _execution_ack,
    "context_archive": _context_archive,
    "remove": _remove,
    "archive": _archive,
    "release": _release,
    "forget_deleted": _forget_deleted,
    "lineage_tombstone": _lineage_tombstone,
    "lineage_folder_unbind": _lineage_folder_unbind,
    "resident_detach": _resident_detach,
    "workspace_document": _workspace_document,
    "workspace_move": _workspace_move,
    "workspace_finish": _workspace_finish,
}


def apply_resident_lifecycle_step(operation_id: str, step: dict[str, Any]) -> None:
    handler = STEPS.get(step["kind"])
    if handler is None:
        raise ValueError(f"Unknown Resident lifecycle effect: {step['kind']}")
    handler(operation_id, step)
