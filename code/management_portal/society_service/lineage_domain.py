"""The Lineage domain's composition root and runtime entry points.

It hosts the Program ports (``lineage_program_services`` reads their callbacks from this module),
dispatches Program Events, consumes Resident terminal facts, polls and reconciles system Events,
and serves Program State Cells, pending deliveries and human notifications.  It also carries the
service-level hooks: the diagnostic scope of each domain call, lock health, projection retries
and the principal operations the RPC table forwards.  The Lineage operations live in the
``lineage_*`` modules it builds on, with ``lineage_state`` as their shared core.
"""

from __future__ import annotations

import functools
import hashlib
import sys
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from management_common.projection_receipts import project_committed

from .lineage_program_queries import _compiled_program
from . import lineage_event_reconciliation
from . import lineage_program_services
from . import (
    lineage_program_execution,
    lineage_program_polling,
    lineage_program_terminal_reconciliation,
    lineage_program_event_dispatch,
    lineage_program_identity,
)
from .lineage_program_contracts import (
    _program_invoke_wire_execution_configs,
    _program_event_source_contract_fingerprint,
)
from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_program_backlog
from .lineage_access_policy import actor_can_manage_lineage as _actor_can_manage_lineage
from .lineage_view_projection import node_resident_id as _node_resident_id
from . import invariant_locks, lineage_program_runtime, lineage_primitives, resident_state, resident_protocol
from . import lineage_aggregate, resident_work_origin
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state

# lineage_program_services reads these Program host callbacks from this module.
from .lineage_state import _lineage_scopes, _now_iso, _persist_lineage_runtime_state, _scheduled_time_reached
from .lineage_reads import _lineage_node_session_key, _lineage_snapshot_for_read, get_lineage
from .lineage_program_edits import _retire_exhausted_event_sources
from .lineage_resident_controls import _send_node_message


@contextmanager
def lineage_state_scope(
    lineage_id: str,
    operation: str = "",
) -> Iterator[None]:
    """Attach request diagnostics without selecting a mutation lock."""

    with invariant_locks.diagnostic_scope(lineage_id, operation):
        yield


def _lineage_scoped(function: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(function)
    def scoped(lineage_id: str, *args: Any, **kwargs: Any) -> Any:
        with lineage_state_scope(lineage_id, function.__name__):
            return function(lineage_id, *args, **kwargs)

    return scoped


def state_lock_health() -> dict[str, Any]:
    return invariant_locks.health()


def _lineage_runtime_actor(lineage: dict[str, Any]) -> str:
    """Resolve the current Lineage Leader for new unattended work."""
    from .lineage_automation_identity import runtime_actor
    return runtime_actor(lineage)


def retry_lineage_projections(
    failures: list[dict[str, Any]], actor: str
) -> None:
    from . import resource_client

    for failure in failures:
        if failure.get("projection") == "lineage.system_workflow_switches":
            from . import system_workflow_switches
            system_workflow_switches.project_after_commit(
                str(failure.get("lineage_id") or ""),
                known_keys=tuple(failure.get("known_keys") or ()),
            )
            continue
        if failure.get("projection") == "project.principal":
            provision_project_principal(str(failure.get("project_id") or ""), actor)
            continue
        if failure.get("projection") == "resident.resource_bundle":
            resident_domain.confirm_resident_registration(
                str(failure.get("resident_id") or ""), actor,
            )
            continue
        if failure.get("projection") == "resident.resource_tombstone":
            resident_domain.confirm_resident_deletion_projection(
                str(failure.get("resident_id") or "")
            )
            continue
        if failure.get("projection") != "lineage.resource_identity":
            raise ValueError("未知 Lineage 投影")
        lineage_id = str(failure.get("lineage_id") or "")
        lineage_aggregate.ensure_manager(lineage_aggregate.read_lineage(lineage_id, actor=actor), actor)
        project_committed(
            failure,
            # Execution receipts keep references only, so the revision may be
            # gone; then the retry covers whatever is committed now.
            lambda lineage_id=lineage_id, revision=failure.get("committed_revision"): (
                lineage_state._publish_lineage_resource_projection(
                    lineage_id, None if revision is None else int(revision), actor=actor,
                )
            ),
        )


def list_principal_hierarchy(actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import navigation

    return navigation(actor)


def ensure_personal_ama(username: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import ensure_personal_ama as ensure

    return ensure(username, actor)


def delete_personal_ama(username: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import delete_personal_ama as delete

    return delete(username, actor)


def ensure_personal_assistant(username: str, ama_id: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import ensure_personal_assistant as ensure
    return ensure(username, ama_id, actor)


def provision_project_principal(project_id: str, actor: str) -> dict[str, Any]:
    from .principal_hierarchy_domain import ensure_project
    return ensure_project(project_id, actor)


def update_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Update one runtime State Cell without advancing graph revision; ``expected_value`` makes it a compare-and-set."""

    actor_id = _safe_actor(actor)
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    from . import lineage_graph_state

    guard = {"expected_value": payload["expected_value"]} if "expected_value" in payload else {}
    return lineage_graph_state.update_cell(
        lineage_id,
        payload.get("key"),
        payload.get("value"),
        **guard,
    )


def get_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Read the bounded runtime State snapshot or one exact Cell."""

    actor_id = _safe_actor(actor)
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_state._ensure_visible(lineage, actor_id)
    from . import lineage_graph_state

    if payload.get("key") not in (None, ""):
        return lineage_graph_state.read_cell(
            lineage_id,
            payload.get("key"),
        )
    return lineage_graph_state.read_state(lineage_id)


def delete_lineage_graph_state(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Delete one runtime State Cell without advancing graph revision."""

    actor_id = _safe_actor(actor)
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_aggregate.ensure_manager(lineage, actor_id)
    from . import lineage_graph_state

    arguments: list[Any] = [
        lineage_id,
        payload.get("key"),
    ]
    if "expected_value" in payload:
        arguments.append(payload.get("expected_value"))
    return lineage_graph_state.delete_cell(*arguments)


def create_lineage_human_notification(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Store a durable notification; default recipients follow the initiator."""

    actor_id = _safe_actor(actor)
    recipients = [str(item) for item in payload.get("recipients") or [] if str(item or "").strip()]
    if lineage_id:
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
        lineage_state._ensure_visible(lineage, actor_id)
        if not recipients:
            recipients = resident_work_origin.notification_recipients(actor_id)
    if not recipients:
        raise ValueError("通知需要明确的 recipients 或可解析的人类发起人")
    from . import lineage_human_notifications

    return lineage_human_notifications.create_notification(
        lineage_id,
        title=payload.get("title") or "通知",
        message=payload.get("message"),
        recipients=recipients,
        severity=payload.get("severity") or "info",
        trigger_path_id=payload.get("trigger_path_id") or "",
        created_by=actor_id,
        notification_id=payload.get("notification_id"),
    )


def _completed_message_evidence(
    node: dict[str, Any],
    message_id: str,
    actor: str,
) -> dict[str, Any]:
    resident_id = _node_resident_id(node)
    return resident_protocol.get_completed_message_evidence(
        resident_id,
        actor,
        message_id,
    )


def _resident_input_reconciliation_state(
    node: dict[str, Any],
    message_id: str,
    actor: str,
) -> dict[str, Any]:
    """Resolve one exact Resident input without an evidence/idle TOCTOU."""

    return resident_protocol.get_input_reconciliation_state(
        _node_resident_id(node),
        actor,
        message_id,
    )


def _node_has_active_execution(node: dict[str, Any], actor: str) -> bool:
    """Read whether the canonical Resident is currently executing work.

    Backlog selection and completion scans ask this for every pending route or
    watched member while holding the Lineage's execution authority, so it reads
    only the work state. Callers have already checked that the actor manages
    the Lineage this node belongs to.
    """
    resident_id = _node_resident_id(node)
    states = resident_protocol.read_work_states([resident_id])
    if resident_id not in states:
        raise KeyError(resident_id)
    return states[resident_id] != "idle"


def _register_program_agent_invocation(
    lineage_id: str,
    target_id: str,
    message_id: str,
    event: lineage_primitives.LineageEvent,
    source_wire_id: str,
    actor: str,
) -> dict[str, Any]:
    """Commit the authoritative HATG Run link after delivery acknowledgement."""

    trigger_path_id = lineage_program_identity.event_path_id(lineage_id, event)
    event_envelope = event.to_dict()
    event_envelope["payload"] = {
        **dict(event_envelope.get("payload") or {}),
        "trigger_path_id": trigger_path_id,
    }
    invocation = lineage_program_runtime.register_resident_handoff(
        lineage_id,
        message_id=message_id,
        resident_id=target_id,
        trigger_path_id=trigger_path_id,
        event=event_envelope,
        source_wire_id=source_wire_id,
    )
    return invocation


def _pending_event_has_registered_invocation(
    lineage_id: str,
    wire_id: str,
    target_id: str,
    pending: dict[str, Any],
) -> bool:
    event = pending.get("event") if isinstance(pending.get("event"), dict) else {}
    event_id = str(event.get("id") or "")
    if not event_id or not target_id:
        return False
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    trigger_path_id = str(payload.get("trigger_path_id") or "") or lineage_program_identity.trigger_path_id(lineage_id, event_id)
    return any(
        str(invocation.get("resident_id") or "") == target_id
        and str(invocation.get("source_wire_id") or "") == wire_id
        and str((invocation.get("event") or {}).get("id") or "") == event_id
        for invocation in lineage_program_runtime.path_resident_inputs(lineage_id, trigger_path_id)
        if isinstance(invocation, dict)
        and isinstance(invocation.get("event"), dict)
    )


@_lineage_scoped
def dispatch_lineage_program_event(
    lineage_id: str,
    event_source_id: str,
    event: lineage_primitives.LineageEvent | dict[str, Any],
    actor: str,
    *,
    cursor: str,
    expected_event_source_contract: str = "",
    event_source_override: dict[str, Any] | None = None,
    selected_wire_ids: set[str] | None = None,
    preclaimed_wire_ids: set[str] | None = None,
    bypass_unavailable_policy: bool = False,
    invocation_arguments: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return lineage_program_event_dispatch.dispatch_program_event(
        lineage_id, event_source_id, event, actor,
        cursor=cursor,
        expected_event_source_contract=expected_event_source_contract,
        event_source_override=event_source_override,
        selected_wire_ids=selected_wire_ids,
        preclaimed_wire_ids=preclaimed_wire_ids,
        bypass_unavailable_policy=bypass_unavailable_policy,
        invocation_arguments=invocation_arguments,
        authority=_program_event_authority(),
    )


def dispatch_resident_terminal_observers(
    resident_id: str,
    message_id: str,
    *,
    terminal_status: str = "completed",
    completed_at: str = "",
    exclude_lineage_ids: set[str] | None = None,
) -> dict[str, Any]:
    return lineage_program_terminal_reconciliation.dispatch_resident_terminal_observers(
        resident_id,
        message_id,
        terminal_status=terminal_status,
        completed_at=completed_at,
        exclude_lineage_ids=exclude_lineage_ids,
        ports=_program_terminal_ports(),
    )


def _notify_program_resident_invocation_terminal(
    lineage_id: str,
    resident_id: str,
    message_id: str,
    invocations: list[dict[str, Any]],
    *,
    status: str,
) -> bool:
    """Notify from terminal facts; node state is read from the invocation ledger."""

    if not invocations:
        return False
    latest = invocations[-1]
    terminal_status = _text(status, 40) or "failed"
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage_state(lineage_id)
        node = next(
            (
                item
                for item in lineage.get("nodes") or []
                if isinstance(item, dict)
                and str(item.get("id") or "") == resident_id
            ),
            None,
        )
        if node is None:
            return False
        current_last_run = (
            node.get("last_execution")
            if isinstance(node.get("last_execution"), dict)
            else {}
        )
        current_message_id = str(current_last_run.get("message_id") or "")
        if current_message_id and current_message_id != message_id:
            # A newer queued invocation owns the visible node projection.
            return False
    return True


def _resume_program_execution_principal(
    lineage_id: str,
    event: lineage_primitives.LineageEvent,
    executor_resident_id: str,
) -> str:
    from management_common.principal import OperationIdentity

    if not event.identity:
        raise PermissionError("Pending invocation has no verified identity; explicit reissue is required")
    identity = OperationIdentity.from_dict(event.identity)
    lineage_aggregate.ensure_manager(lineage_aggregate.read_lineage_state(lineage_id), identity.principal.id)
    return lineage_state.authorize_lineage_resident_principal(lineage_id, executor_resident_id)


def emit_program_manual_event(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """TriggerEvent: one manual Event through a source or one of its wires."""

    return lineage_program_backlog.emit_manual_event(lineage_id, payload, actor, authority=_program_control_authority())


def observe_trigger_path(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    from . import trigger_path_observation
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_state._ensure_visible(lineage, _safe_actor(actor))
    return {"lineage_id": lineage_id, **trigger_path_observation.observe(lineage_id, payload)}


def get_pending_deliveries(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_state._ensure_visible(lineage, _safe_actor(actor))
        wire_id = str(payload.get("wire_id") or "")
        contracts = _program_invoke_wire_execution_configs(
            _compiled_program(lineage),
        )
        if wire_id not in contracts:
            raise KeyError("Invoke 连线已不存在")
        return {
            "wire_id": wire_id,
            "deliveries": lineage_program_runtime.pending_wire_deliveries(
                lineage_id, wire_id,
            ),
            "runtime": lineage_program_runtime.event_wire_runtime_view(
                lineage_id, {wire_id: contracts[wire_id]["config"]},
            )[wire_id],
        }


def _mutate_pending_delivery(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    delete: bool,
) -> dict[str, Any]:
    with lineage_state_scope(lineage_id, "mutate_pending_delivery"):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(lineage, _safe_actor(actor))
        wire_id = str(payload.get("wire_id") or "")
        contracts = _program_invoke_wire_execution_configs(
            _compiled_program(lineage),
        )
        if wire_id not in contracts:
            raise KeyError("Invoke 连线已不存在")
        result = lineage_program_runtime.update_pending_wire_delivery(
            lineage_id,
            wire_id,
            str(payload.get("delivery_id") or ""),
            prompt=payload.get("prompt", ""),
            delete=delete,
        )
        return {**result, **get_pending_deliveries(lineage_id, payload, actor)}


def update_pending_delivery(
    lineage_id: str, payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    return _mutate_pending_delivery(lineage_id, payload, actor, delete=False)


def delete_pending_delivery(
    lineage_id: str, payload: dict[str, Any], actor: str,
) -> dict[str, Any]:
    return _mutate_pending_delivery(lineage_id, payload, actor, delete=True)


def _role_file_signature(
    lineage_id: str,
    target_node: dict[str, Any],
    relative_path: str,
) -> str:
    root = lineage_state._node_workspace_path(lineage_id, target_node)
    try:
        path = lineage_primitives.resolve_workspace_relative_path(
            root,
            relative_path,
        )
    except ValueError as exc:
        raise ValueError("文件 Event 路径越出了目标 Workspace") from exc
    try:
        stat = path.stat()
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"
    return f"sha256:{digest.hexdigest()}:{stat.st_size}"


def _program_resident_state_value(
    lineage: dict[str, Any],
    node: dict[str, Any],
    state_domain: str,
    actor: str,
    state_cache: dict[str, str],
) -> str:
    """Read one state through the module that owns that state domain."""

    if state_domain != resident_state.INTAKE_DOMAIN:
        raise ValueError("未知的 Resident 状态域")
    resident_id = _node_resident_id(node)
    if not resident_id:
        raise ValueError("Resident 节点缺少 resident_id")
    cache_key = resident_id
    if cache_key not in state_cache:
        state_view = resident_protocol.get_resident_state(
            resident_id, actor,
        )["state"]
        state_cache[cache_key] = str(
            state_view.get("intake_state") or "open"
        )
    return state_cache[cache_key]


def _read_resident_activity_snapshot(
    resident_ids: list[str] | None,
) -> dict[str, Any]:
    """Read Resident work state through its single owning subsystem."""

    snapshot = resident_protocol.get_resident_activity_snapshot(resident_ids)
    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "Resident activity resolver returned an invalid snapshot"
        )
    residents = (
        snapshot.get("residents")
        if isinstance(snapshot.get("residents"), dict)
        else {}
    )
    return {
        **snapshot,
        "resident_ids": sorted(
            str(value)
            for value in snapshot.get("resident_ids") or residents
            if str(value)
        ),
        "active_resident_ids": sorted(
            str(value)
            for value in snapshot.get("active_resident_ids") or []
            if str(value)
        ),
        "residents": residents,
    }


@_lineage_scoped
def _reconcile_lineage_program_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return lineage_event_reconciliation.reconcile_lineage_program_system_events(
        lineage_id,
        actor,
        resident_activity_snapshot=resident_activity_snapshot,
        ports=lineage_program_services.event_reconcile_ports(sys.modules[__name__]),
    )


def reconcile_lineage_system_events(
    lineage_id: str,
    actor: str,
    *,
    resident_activity_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recognize Program-owned time, file, state, and activity Events."""

    actor_id = _safe_actor(actor)
    program_result = _reconcile_lineage_program_system_events(
        lineage_id,
        actor_id,
        resident_activity_snapshot=resident_activity_snapshot,
    )
    backlog = lineage_program_backlog.drain_event_backlog(
        lineage_id, actor_id, authority=_program_control_authority(),
    )
    return {
        "lineage_id": lineage_id,
        "baseline_count": int(
            program_result.get("baseline_count") or 0
        ),
        "event_count": int(program_result.get("event_count") or 0),
        "events": list(program_result.get("events") or []),
        "program_event_backlog": backlog,
        "retry_after_seconds": int(
            backlog.get("retry_after_seconds") or 0
        ),
        "next_wake_at": str(program_result.get("next_wake_at") or ""),
    }


def poll_all_lineage_system_events() -> dict[str, Any]:
    return lineage_program_polling.poll_all_system_events(ports=_program_polling_ports())


def recover_pending_program_work_as_service() -> dict[str, Any]:
    return lineage_program_polling.poll_all_system_events(
        ports=_program_polling_ports(), recovery_only=True,
    )


@_lineage_scoped
def reconcile_lineage_system_events_as_service(
    lineage_id: str,
) -> dict[str, Any]:
    return lineage_program_polling.reconcile_system_events_as_service(
        lineage_id, ports=_program_polling_ports(),
    )


@_lineage_scoped
def dispatch_message_arrival(
    lineage_id: str,
    resident_id: str,
    message_id: str,
) -> dict[str, Any]:
    """Produce ``resident.message_received`` for one accepted message now (decision_44cf587c1721)."""

    return lineage_program_polling.dispatch_arrival_as_service(
        lineage_id, resident_id, message_id,
        ports=_program_polling_ports(),
        dispatch=dispatch_lineage_program_event,
        contract=_program_event_source_contract_fingerprint,
    )


def handle_resident_message_terminal(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    terminal_status: str = "completed",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Serialize one Resident terminal with Lineage runtime consumption."""

    from . import resource_client

    result = _handle_resident_message_terminal(
        resident_id, actor, message_id, completed_at=completed_at, lineage_id=lineage_id,
        terminal_status=terminal_status, program_event_source_ids=program_event_source_ids,
    )
    requested = str(lineage_id or "").strip()
    lineage_ids = [requested] if requested else sorted(lineage_aggregate.lineage_catalog_store().lineage_ids_for_resident(resident_id))
    # An explorer may have added or dismissed candidates in this turn.
    from .templates import explore

    explore.sync_after_turn(lineage_ids, resident_id, actor)
    return result


def _handle_resident_message_terminal(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    terminal_status: str = "completed",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    def matching_lineage_ids() -> set[str]:
        return set(lineage_aggregate.lineage_catalog_store().lineage_ids_for_resident(resident_id))

    requested_lineage_id = str(lineage_id or "").strip()
    if requested_lineage_id:
        with lineage_aggregate.execution_authority(requested_lineage_id):
            with lineage_aggregate.lineage_scope(requested_lineage_id):
                return lineage_program_terminal_reconciliation.handle_resident_message_terminal_locked(
                    resident_id,
                    actor,
                    message_id,
                    completed_at=completed_at,
                    lineage_id=requested_lineage_id,
                    terminal_status=terminal_status,
                    program_event_source_ids=program_event_source_ids,
                    ports=_program_terminal_ports(),
                )

    # A callback without lineage_id may fan out to every HATG containing the
    # Resident view. Acquire only those Lineage authorities and aggregate locks
    # in canonical order, then verify the committed membership snapshot again.
    while True:
        authority_ids = matching_lineage_ids()
        with lineage_aggregate.execution_authorities(authority_ids):
            with _lineage_scopes(
                authority_ids,
                operation="resident.message_terminal",
            ):
                if matching_lineage_ids() != authority_ids:
                    continue
                return lineage_program_terminal_reconciliation.handle_resident_message_terminal_locked(
                    resident_id,
                    actor,
                    message_id,
                    completed_at=completed_at,
                    lineage_id="",
                    terminal_status=terminal_status,
                    program_event_source_ids=program_event_source_ids,
                    ports=_program_terminal_ports(),
                )


def handle_resident_message_completion(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    program_event_source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Record the completed terminal fact for one Resident work turn."""

    return handle_resident_message_terminal(
        resident_id,
        actor,
        message_id,
        completed_at=completed_at,
        lineage_id=lineage_id,
        terminal_status="completed",
        program_event_source_ids=program_event_source_ids,
    )


def reconcile_lineage_runtime(
    lineage_id: str, actor: str, *, recovery_only: bool = False, drain: bool = True,
) -> dict[str, Any]:
    return lineage_program_terminal_reconciliation.reconcile_runtime_with_authority(
        lineage_id,
        actor,
        ports=_program_terminal_ports(),
        recovery_only=recovery_only,
        drain=drain,
    )


def _program_execution_ports() -> lineage_program_execution.ProgramExecutionPorts:
    return lineage_program_services.execution_ports(sys.modules[__name__])


def _program_polling_ports() -> lineage_program_polling.ProgramPollingPorts:
    return lineage_program_services.polling_ports(sys.modules[__name__])


def _program_terminal_ports() -> lineage_program_terminal_reconciliation.ProgramTerminalPorts:
    return lineage_program_services.terminal_ports(sys.modules[__name__])


def _program_event_authority() -> lineage_program_event_dispatch.ProgramEventAuthority:
    return lineage_program_services.event_authority(sys.modules[__name__])


def _program_control_authority() -> lineage_program_backlog.ProgramControlAuthority:
    return lineage_program_services.control_authority(sys.modules[__name__])
