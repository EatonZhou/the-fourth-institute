"""Restart-safe Program terminal observation and repair."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, ContextManager, Iterable, Protocol
from management_common.database_time import instant
from management_common.principal import OperationIdentity
from management_common.service_shutdown import shutdown_requested

from .lineage_program_contracts import _program_event_source_contract_fingerprint
from . import trigger_paths
from . import (
    lineage_catalog, lineage_program_event_scope, lineage_program_observations,
    lineage_program_resident_inputs, lineage_program_execution, lineage_primitives,
)
from .lineage_program_queries import _compiled_program, _program_runtime_resident_ids
from .lineage_values import _safe_actor, _text, _clone_json

LOGGER = logging.getLogger(__name__)


class ProgramTerminalState(Protocol):
    def lineage_scope(self, lineage_id: str, *, operation: str = "") -> ContextManager[None]: ...
    def read_lineage(self, lineage_id: str, *, actor: str) -> dict[str, Any]: ...
    def read_lineage_state(self, lineage_id: str) -> dict[str, Any]: ...
    def ensure_manager(self, lineage: dict[str, Any], actor: str) -> None: ...
    def persist_lineage_runtime_state(self, previous: dict[str, Any], lineage: dict[str, Any], reason: str) -> None: ...
    def now_iso(self) -> str: ...


class ProgramTerminalResident(Protocol):
    def node_has_active_execution(self, node: dict[str, Any], actor: str) -> bool: ...
    def latest_completed_message_reference(self, resident_id: str, actor: str) -> dict[str, Any]: ...
    def node_resident_id(self, node: dict[str, Any]) -> str: ...
    def handle_resident_message_completion(self, resident_id: str, actor: str, message_id: str, **options: Any) -> dict[str, Any]: ...
    def resident_input_reconciliation_state(self, node: dict[str, Any], message_id: str, actor: str) -> dict[str, Any]: ...
    def handle_resident_message_terminal(self, resident_id: str, actor: str, message_id: str, **options: Any) -> dict[str, Any]: ...
    def notify_program_resident_invocation_terminal(self, lineage_id: str, resident_id: str, message_id: str, invocations: list[dict[str, Any]], *, status: str) -> bool: ...


class ProgramTerminalFlow(Protocol):
    def drain_program_event_wire_backlog(self, lineage_id: str, actor: str, *, max_events: int = 32) -> dict[str, Any]: ...
    def program_execution_ports(self) -> lineage_program_execution.ProgramExecutionPorts: ...
    def dispatch_lineage_program_event(self, lineage_id: str, event_source_id: str, event: lineage_primitives.LineageEvent | dict[str, Any], actor: str, **options: Any) -> dict[str, Any]: ...


class ProgramTerminalDirectory(Protocol):
    def actor_can_manage_lineage(self, lineage: dict[str, Any], actor: str) -> bool: ...
    def lineage_scopes(self, lineage_ids: Iterable[str], *, operation: str = "") -> ContextManager[None]: ...
    def lineage_ids_for_resident(self, resident_id: str) -> list[str]: ...
    def lineage_catalog_store(self) -> lineage_catalog.LineageCatalog: ...
    def lineage_execution_authority(self, lineage_id: str) -> ContextManager[None]: ...
    def lineage_runtime_actor(self, lineage: dict[str, Any]) -> str: ...


@dataclass(frozen=True)
class ProgramTerminalPorts:
    state: ProgramTerminalState
    resident: ProgramTerminalResident
    flow: ProgramTerminalFlow
    directory: ProgramTerminalDirectory


def reconcile_runtime_with_authority(
    lineage_id: str,
    actor: str,
    *,
    ports: ProgramTerminalPorts,
    recovery_only: bool = False,
    drain: bool = True,
) -> dict[str, Any]:
    """Read recovery evidence outside admission authority; settle each input inside it.

    Recovery observes nothing new, so without ``drain`` it only settles the
    caller's handoffs.
    """
    recovered = _reconcile_resident_handoffs(lineage_id, actor, ports=ports, recovery_only=recovery_only)
    if recovered["stopped"] or (recovery_only and not drain):
        return {"program_completion_baseline_count": 0,
                "created_count": recovered["program_created_count"],
                "program_event_backlog": {}, **recovered}
    # Provider/database evidence must not occupy the Lineage admission lock.
    # Recheck the definition before consuming evidence.
    observations = _read_completion_evidence(lineage_id, actor, ports=ports) if not recovery_only else None
    result = _reconcile_observations_with_authority(
        lineage_id, actor, ports=ports, recovery_only=recovery_only, observations=observations,
    )
    for key in ("checked_node_count", "recovered_handoff_count", "program_created_count",
                "operator_result_count", "removed_absorption_count"):
        result[key] += recovered[key]
    result["created_count"] = result["program_created_count"]
    result.setdefault("recovery_failures", []).extend(recovered["recovery_failures"])
    return result


def _observation_definition(lineage: dict[str, Any]) -> dict[str, Any]:
    return {"program": lineage.get("program") or {},
            "members": sorted(str(n.get("id") or "") for n in lineage.get("nodes") or [])}


def _read_completion_evidence(lineage_id: str, actor: str, *, ports: ProgramTerminalPorts) -> dict[str, Any]:
    lineage = ports.state.read_lineage(lineage_id, actor=actor)
    ports.state.ensure_manager(lineage, _safe_actor(actor))
    subjects = {subject for _, watched, _, subscribers in lineage_program_event_scope.resident_completion_subscriptions(
        _compiled_program(lineage), _lineage_member_ids(lineage)) if subscribers for subject in watched}
    evidence = {}
    for node in lineage.get("nodes") or []:
        if shutdown_requested():
            break
        identifier = str(node.get("id") or "")
        if identifier not in subjects or ports.resident.node_has_active_execution(node, actor):
            continue
        try:
            evidence[identifier] = ports.resident.latest_completed_message_reference(
                ports.resident.node_resident_id(node), actor)
        except (KeyError, PermissionError, ValueError):
            continue
    return {"definition": _clone_json(_observation_definition(lineage)), "evidence": evidence}


# An input that finished longer ago than this missed its Run, like a Run past
# residents.all_finished's window. Recovery only settles its handoff: Events and
# outputs that late would start work nobody waits for any more.
LATE_HANDOFF_AGE = timedelta(hours=24)


def _input_receipt(resident_id: str, message_id: str, *, member: bool) -> dict[str, Any]:
    from .lineage_recovery_policy import deleted_resident
    from .resident_conversation_stream import get_repository
    try:
        return get_repository().input_state(resident_id, message_id)
    except KeyError:
        if member:
            return {}
        if not deleted_resident(resident_id):
            raise
        # Deleting the Resident removed its conversation; this input never finishes.
        return {"state": "cancelled", "updated_at": ""}


def _finished_before(input_state: dict[str, Any], cutoff: datetime) -> bool:
    if str(input_state.get("state") or "") not in {"completed", "failed", "cancelled"}:
        return False
    return bool(input_state.get("updated_at")) and instant(input_state["updated_at"]) < cutoff


def _runs_no_turns(resident_id: str) -> bool:
    """A Family facade relays its input to a member and never runs a turn itself."""
    from . import family_facade, resident_record_domain
    return family_facade.is_facade(resident_record_domain.find_resident(resident_id))


def _reconcile_resident_handoffs(
    lineage_id: str, actor: str, *, ports: ProgramTerminalPorts, recovery_only: bool,
) -> dict[str, Any]:
    from . import lineage_instruction_context

    pending = lineage_program_resident_inputs.pending_resident_handoffs(lineage_id)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for invocation in pending:
        key = (str(invocation.get("resident_id") or ""), str(invocation.get("message_id") or ""))
        groups.setdefault(key, []).append(invocation)
    result: dict[str, Any] = {
        "stopped": False, "checked_node_count": 0, "recovered_handoff_count": 0,
        "program_created_count": 0, "operator_result_count": 0, "removed_absorption_count": 0,
        "recovery_failures": [],
    }
    # With nothing to settle there is nothing to authorize; the drain that
    # follows checks the caller itself.
    if not groups:
        return result
    lineage = ports.state.read_lineage(lineage_id, actor=actor)
    ports.state.ensure_manager(lineage, _safe_actor(actor))
    nodes = {str(node.get("id") or ""): node for node in lineage.get("nodes") or []}
    for (resident_id, message_id), expected in groups.items():
        if shutdown_requested():
            result["stopped"] = True
            break
        try:
            saved_event = lineage_primitives.normalize_event(expected[0].get("event") or {})
            if not saved_event.identity:
                raise PermissionError("Pending Resident input has no verified identity; explicit reissue is required")
            saved_identity = OperationIdentity.from_dict(saved_event.identity)
            original = lineage_instruction_context.current_event_identity()
            if recovery_only and original is not None and saved_identity != original:
                continue
            invocation_actor = saved_identity.principal.id
            with lineage_instruction_context.event_scope(saved_event, invocation_actor):
                if not message_id:
                    continue
                node = nodes.get(resident_id)
                input_state = _input_receipt(resident_id, message_id, member=bool(node))
                late = _finished_before(input_state, datetime.now(timezone.utc) - LATE_HANDOFF_AGE)
                # A facade's input finishes when the Router relays it, with no
                # turn of its own to find in the Chat.
                by_receipt = late or (bool(node) and _runs_no_turns(resident_id))
                if node and not by_receipt:
                    state = ports.resident.resident_input_reconciliation_state(node, message_id, invocation_actor)
                else:
                    # Membership does not own an already dispatched input, and
                    # a late input's turn may be gone from the cleared Chat.
                    state = {"status": input_state.get("state"),
                             "completed_at": input_state.get("updated_at", "")}
                status = str(state.get("status") or "").lower()
                status = {"success": "completed", "succeeded": "completed",
                          "canceled": "cancelled", "interrupted": "cancelled"}.get(status, status)
                if status not in {"completed", "failed", "cancelled"}:
                    continue
                with ports.directory.lineage_execution_authority(lineage_id):
                    # Compare the exact accepted leaves, including identity, execution
                    # links and status. Deletion, replacement or another reconciler
                    # invalidates this observation; never settle its successor.
                    current = trigger_paths.resident_inputs(
                        lineage_id, resident_id=resident_id, message_id=message_id, pending=True,
                    )
                    if current != expected:
                        continue
                    current_lineage = ports.state.read_lineage(lineage_id, actor=actor)
                    ports.state.ensure_manager(current_lineage, _safe_actor(actor))
                    current_node = next((item for item in current_lineage.get("nodes") or []
                                         if str(item.get("id") or "") == resident_id), None)
                    if bool(current_node) != bool(node):
                        continue
                    completed_at = str(state.get("completed_at") or (state.get("evidence") or {}).get("completed_at") or "")
                    if current_node and not by_receipt:
                        settled = ports.resident.handle_resident_message_terminal(
                            resident_id, invocation_actor, message_id, completed_at=completed_at,
                            lineage_id=lineage_id, terminal_status=status,
                        )
                        for key in ("program_created_count", "operator_result_count", "removed_absorption_count"):
                            result[key] += int(settled.get(key) or 0)
                    else:
                        finished = lineage_program_resident_inputs.settle_resident_handoffs(
                            lineage_id, resident_id=resident_id, message_id=message_id,
                            status=status, completed_at=completed_at,
                            output={"resident_id": resident_id, "message_id": message_id,
                                    "reply_message_ids": list(input_state.get("reply_message_ids") or [])},
                        )
                        settle_program_paths(lineage_id, {item["trigger_path_id"] for item in finished if item.get("trigger_path_id")})
                    result["checked_node_count"] += 1
                    result["recovered_handoff_count"] += 1
        except (KeyError, PermissionError, ValueError) as exc:
            result["recovery_failures"].append({"message_id": message_id, "error": str(exc)})
    return result


@dataclass
class _CompletionWatch:
    """The completion subscriptions of a Lineage and the evidence read for its watched nodes."""

    # subject -> [(source, watched-Resident scope)]; the scope is empty
    # unless the source watches several Residents.
    sources_by_subject: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    subscribers_by_source: dict[str, list[str]] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    source_nodes: list[dict[str, Any]] = field(default_factory=list)


def _completion_watch(
    lineage_id: str,
    actor: str,
    actor_id: str,
    observations: dict[str, Any] | None,
    *,
    ports: ProgramTerminalPorts,
) -> _CompletionWatch:
    """Read, under the Lineage's authority, which sources watch which nodes' completions.

    Evidence read before counts only while the definition it was read for is current.
    """

    watch = _CompletionWatch()
    with ports.directory.lineage_execution_authority(lineage_id), ports.state.lineage_scope(lineage_id):
        lineage = ports.state.read_lineage(lineage_id, actor=actor)
        ports.state.ensure_manager(lineage, actor_id)
        program_plan = _compiled_program(lineage)
        for event_source_id, watched, many, subscriber_ids in (
            lineage_program_event_scope.resident_completion_subscriptions(program_plan, _lineage_member_ids(lineage))
        ):
            if not watched or not subscriber_ids:
                continue
            for subject_id in watched:
                watch.sources_by_subject.setdefault(
                    subject_id, []
                ).append((event_source_id, subject_id if many else ""))
            watch.subscribers_by_source[event_source_id] = subscriber_ids
        source_ids = set(watch.sources_by_subject)
        watch.evidence = ((observations or {}).get("evidence") or {}) if (
            (observations or {}).get("definition") == _observation_definition(lineage)
        ) else {}
        watch.source_nodes = [
            dict(node)
            for node in lineage.get("nodes") or []
            if (
                isinstance(node, dict)
                and str(node.get("id") or "") in source_ids
                and str(node.get("id") or "") in watch.evidence
            )
        ]
    return watch


@dataclass
class _ObservationCounts:
    created_count: int = 0
    operator_result_count: int = 0
    checked_count: int = 0
    completion_baseline_count: int = 0
    recovered_handoff_count: int = 0
    removed_absorption_count: int = 0


def _observe_node_completion(
    lineage_id: str,
    actor: str,
    node_id: str,
    completion: dict[str, Any],
    watch: _CompletionWatch,
    observations: dict[str, Any] | None,
    counts: _ObservationCounts,
    *,
    ports: ProgramTerminalPorts,
) -> list[tuple[str, str, dict[str, Any]]] | None:
    """Compare a node's latest completion with each watching source's cursor, under the Lineage's authority.

    A first observation sets the source's baseline; the sources that saw a new
    completion are returned.  None when the definition changed since the
    evidence was read: the pass stops.
    """

    event_source_ids = sorted(
        watch.sources_by_subject.get(node_id) or []
    )
    completion_message_id = str(completion.get("message_id") or "")
    completion_at = str(completion.get("completed_at") or "")
    completion_locator = f"resident:{node_id}"
    changed_sources: list[tuple[str, str, dict[str, Any]]] = []
    with ports.directory.lineage_execution_authority(lineage_id):
        if _observation_definition(ports.state.read_lineage(lineage_id, actor=actor)) != (observations or {}).get('definition'):
            return None
        for event_source_id, scope in event_source_ids:
            observed = lineage_program_observations.observe_resident_completion(
                lineage_id,
                event_source_id,
                locator=completion_locator,
                message_id=completion_message_id,
                completed_at=completion_at,
                subject=scope,
            )
            if observed.get("status") == "baseline":
                counts.completion_baseline_count += 1
                lineage_program_observations.establish_resident_completion_subscription_baseline(
                    lineage_id, event_source_id, locator=completion_locator,
                    subject_resident_id=node_id, message_id=completion_message_id,
                    completed_at=completion_at,
                    subscriber_ids=watch.subscribers_by_source.get(event_source_id, []),
                    per_resident=bool(scope),
                )
            elif observed.get("status") == "changed":
                changed_sources.append((event_source_id, scope, observed))
    return changed_sources


def _recognize_completion(
    lineage_id: str,
    actor: str,
    actor_id: str,
    node: dict[str, Any],
    completion: dict[str, Any],
    changed_sources: list[tuple[str, str, dict[str, Any]]],
    observations: dict[str, Any] | None,
    counts: _ObservationCounts,
    *,
    ports: ProgramTerminalPorts,
) -> None:
    """Report the completion to the sources that saw it; advance the cursor of each source that took it."""

    node_id = str(node.get("id") or "")
    completion_message_id = str(completion.get("message_id") or "")
    completion_at = str(completion.get("completed_at") or "")
    completion_locator = f"resident:{node_id}"
    counts.checked_count += 1
    result = ports.resident.handle_resident_message_completion(
        ports.resident.node_resident_id(node),
        actor_id,
        completion_message_id,
        completed_at=completion_at,
        lineage_id=lineage_id,
        program_event_source_ids={
            event_source_id
            for event_source_id, _scope, _observed in changed_sources
        },
    )
    recognized_source_ids = {
        str(item.get("event_source_id") or "")
        for item in result.get("program_events") or []
        if isinstance(item, dict)
    }
    with ports.directory.lineage_execution_authority(lineage_id):
        if _observation_definition(ports.state.read_lineage(lineage_id, actor=actor)) == (observations or {}).get('definition'):
            for event_source_id, scope, observed in changed_sources:
                if event_source_id not in recognized_source_ids:
                    continue
                lineage_program_observations.commit_resident_completion(
                    lineage_id, event_source_id, locator=completion_locator,
                    previous_message_id=str(observed.get("previous_message_id") or ""),
                    message_id=completion_message_id, completed_at=completion_at, subject=scope,
                )
    counts.created_count += int(result.get("program_created_count") or 0)
    counts.operator_result_count += int(
        result.get("operator_result_count") or 0
    )
    counts.removed_absorption_count += int(
        result.get("removed_absorption_count") or 0
    )


def _reconcile_observations_with_authority(
    lineage_id: str,
    actor: str,
    *,
    ports: ProgramTerminalPorts,
    recovery_only: bool = False,
    observations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Recover Program completion observations and one-shot absorption."""

    actor_id = _safe_actor(actor)
    # Recovery observes no completions; it only drains what is already queued.
    watch = (
        _completion_watch(lineage_id, actor, actor_id, observations, ports=ports)
        if not recovery_only
        else _CompletionWatch()
    )
    counts = _ObservationCounts()
    # A stopping owner leaves the rest of this repair pass to the next start;
    # every step below is observed and committed by message ID, so the pass
    # resumes where it stopped.
    stopped = False
    for node in watch.source_nodes:
        if shutdown_requested():
            stopped = True
            break
        node_id = str(node.get("id") or "")
        completion = watch.evidence[node_id]
        if not isinstance(completion, dict):
            completion = {}
        changed_sources = _observe_node_completion(
            lineage_id, actor, node_id, completion, watch, observations, counts, ports=ports,
        )
        if changed_sources is None:
            break
        if not str(completion.get("message_id") or ""):
            continue
        if not changed_sources:
            continue
        _recognize_completion(
            lineage_id, actor, actor_id, node, completion, changed_sources, observations, counts, ports=ports,
        )

    recovery_failures: list[dict[str, str]] = []

    if stopped:
        return {
            "stopped": True,
            "checked_node_count": counts.checked_count,
            "recovered_handoff_count": counts.recovered_handoff_count,
            "program_completion_baseline_count": counts.completion_baseline_count,
            "removed_absorption_count": counts.removed_absorption_count,
            "created_count": counts.created_count,
            "program_created_count": counts.created_count,
            "operator_result_count": counts.operator_result_count,
            "program_event_backlog": {},
        }
    if not recovery_only:
        counts.removed_absorption_count += reconcile_terminal_absorption_edges(
            lineage_id,
            actor_id,
            ports=ports,
        )
    program_event_backlog = ports.flow.drain_program_event_wire_backlog(
        lineage_id,
        actor_id,
    )
    return {
        "stopped": False,
        "checked_node_count": counts.checked_count,
        "recovered_handoff_count": counts.recovered_handoff_count,
        "program_completion_baseline_count": counts.completion_baseline_count,
        "removed_absorption_count": counts.removed_absorption_count,
        "created_count": counts.created_count,
        "program_created_count": counts.created_count,
        "operator_result_count": counts.operator_result_count,
        "program_event_backlog": program_event_backlog,
        "recovery_failures": recovery_failures,
    }



def settle_program_paths(
    lineage_id: str,
    trigger_path_ids: Iterable[str],
) -> list[str]:
    """Mark runs finalized after all resulting routes have settled."""

    finished: list[str] = []
    for trigger_path_id in sorted({str(value) for value in trigger_path_ids if str(value)}):
        if not lineage_program_resident_inputs.settle_trigger_path(
            lineage_id, trigger_path_id
        ):
            continue
        finished.append(trigger_path_id)
    return finished


def remove_terminal_absorption_edges(
    lineage: dict[str, Any],
    target_node_ids: set[str],
    target_message_id: str,
) -> int:
    """Remove one-shot absorption relations once their target turn is over."""

    if not target_node_ids or not target_message_id:
        return 0
    retained_edges: list[dict[str, Any]] = []
    removed_count = 0
    for edge in lineage.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        absorption = (
            edge.get("absorption")
            if isinstance(edge.get("absorption"), dict)
            else {}
        )
        if (
            str(edge.get("type") or "") == "absorption"
            and str(edge.get("target") or "") in target_node_ids
            and str(absorption.get("target_message_id") or "") == target_message_id
        ):
            removed_count += 1
            continue
        retained_edges.append(edge)
    if removed_count:
        lineage["edges"] = retained_edges
    return removed_count


def reconcile_terminal_absorption_edges(
    lineage_id: str,
    actor: str,
    *,
    ports: ProgramTerminalPorts,
) -> int:
    """Remove stale one-shot edges whose exact target job is already over."""
    _lineage_scope = ports.state.lineage_scope
    _read_lineage = ports.state.read_lineage
    _ensure_manager = ports.state.ensure_manager
    _resident_input_reconciliation_state = ports.resident.resident_input_reconciliation_state
    _persist_lineage_runtime_state = ports.state.persist_lineage_runtime_state

    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor)
        nodes_by_id = {
            str(node.get("id") or ""): dict(node)
            for node in lineage.get("nodes") or []
            if isinstance(node, dict)
        }
        candidates = [
            (
                nodes_by_id.get(str(edge.get("target") or ""), {}),
                str((edge.get("absorption") or {}).get("target_message_id") or ""),
            )
            for edge in lineage.get("edges") or []
            if (
                isinstance(edge, dict)
                and str(edge.get("type") or "") == "absorption"
                and str((edge.get("absorption") or {}).get("target_message_id") or "")
            )
        ]

    terminal_targets_by_job: dict[str, set[str]] = {}
    for target_node, target_message_id in candidates:
        if not target_node:
            continue
        try:
            state = _resident_input_reconciliation_state(
                target_node,
                target_message_id,
                actor,
            )
        except (KeyError, PermissionError, ValueError):
            continue
        if str(state.get("status") or "") not in {"completed", "cancelled"}:
            continue
        terminal_targets_by_job.setdefault(target_message_id, set()).add(
            str(target_node.get("id") or "")
        )
    if not terminal_targets_by_job:
        return 0

    with _lineage_scope(lineage_id):
        previous = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(previous, actor)
        lineage = _clone_json(previous)
        removed_count = sum(
            remove_terminal_absorption_edges(
                lineage,
                target_node_ids,
                target_message_id,
            )
            for target_message_id, target_node_ids
            in terminal_targets_by_job.items()
        )
        if removed_count:
            _persist_lineage_runtime_state(
                previous,
                lineage,
                "节点吸收终态已协调",
            )
        return removed_count


def _mark_sources_idle(
    lineage_ids: list[str],
    exact_resident_id: str,
    source_message_id: str,
    source_completed_at: str,
    actor: str,
    actor_id: str,
    *,
    ports: ProgramTerminalPorts,
) -> tuple[list[tuple[str, str, dict[str, Any]]], int, int]:
    """Set the Resident's nodes idle and drop the one-shot absorptions of the finished turn.

    Returns the (Lineage, node, node snapshot) of every matched node, how many
    nodes matched and how many absorption edges were removed.
    """

    matched_sources: list[tuple[str, str, dict[str, Any]]] = []
    matched_node_count = 0
    removed_absorption_count = 0
    with ports.directory.lineage_scopes(
        lineage_ids,
        operation="resident.message_completed",
    ):
        for current_lineage_id in lineage_ids:
            try:
                lineage = ports.state.read_lineage(current_lineage_id, actor=actor)
            except KeyError:
                continue
            if not ports.directory.actor_can_manage_lineage(lineage, actor_id):
                continue
            sources = [
                node
                for node in lineage.get("nodes") or []
                if (
                    isinstance(node, dict)
                    and ports.resident.node_resident_id(node) == exact_resident_id
                )
            ]
            if not sources:
                continue
            matched_node_count += len(sources)
            previous = _clone_json(lineage)
            source_node_ids = {
                str(node.get("id") or "") for node in sources
            }
            changed = False
            for source in sources:
                if str(source.get("status") or "idle") != "idle":
                    source["status"] = "idle"
                    source["updated_at"] = source_completed_at
                    changed = True
            removed_for_lineage = remove_terminal_absorption_edges(
                lineage,
                source_node_ids,
                source_message_id,
            )
            if removed_for_lineage:
                removed_absorption_count += removed_for_lineage
                changed = True
            for source in sources:
                matched_sources.append(
                    (
                        current_lineage_id,
                        str(source.get("id") or ""),
                        dict(source),
                    )
                )
            if changed:
                ports.state.persist_lineage_runtime_state(
                    previous,
                    lineage,
                    "节点完成事件",
                )
    return matched_sources, matched_node_count, removed_absorption_count


@dataclass
class _CompletedWork:
    """What one finished turn completed: operator and Resident invocations, paths and Events."""

    operator_results: list[dict[str, Any]] = field(default_factory=list)
    agent_results: list[dict[str, Any]] = field(default_factory=list)
    completed_path_ids_by_lineage: dict[str, set[str]] = field(default_factory=dict)
    program_event_results: list[dict[str, Any]] = field(default_factory=list)


def _complete_node_turn(
    work: _CompletedWork,
    current_lineage_id: str,
    source_node_id: str,
    source_node: dict[str, Any],
    source_message_id: str,
    actor_id: str,
    source_completed_at: str,
    program_event_source_ids: set[str] | None,
    *,
    ports: ProgramTerminalPorts,
) -> None:
    """Complete one node's invocation by the turn, then report the turn to the Event Sources watching it."""

    # A turn a Packet Operator asked for belongs to that operator; any
    # other turn is an ordinary Resident invocation.
    completed_operator = lineage_program_execution.complete_operator_invocation(
        current_lineage_id,
        source_node_id,
        source_message_id,
        actor_id,
        ports=ports.flow.program_execution_ports(),
    )
    if completed_operator:
        work.operator_results.append(completed_operator)
        if completed_operator.get("trigger_path_id"):
            work.completed_path_ids_by_lineage.setdefault(current_lineage_id, set()).add(
                str(completed_operator["trigger_path_id"])
            )
    else:
        completed_agent = lineage_program_execution.complete_agent_invocations(
            current_lineage_id,
            source_node_id,
            source_node,
            source_message_id,
            actor_id,
            source_completed_at,
            ports=ports.flow.program_execution_ports(),
        )
        if completed_agent:
            work.agent_results.append(completed_agent)
            work.completed_path_ids_by_lineage.setdefault(
                current_lineage_id, set()
            ).update(
                str(item.get("trigger_path_id") or "")
                for item in completed_agent.get("invocations") or []
                if str(item.get("trigger_path_id") or "")
            )

    work.program_event_results.extend(
        dispatch_program_resident_work_finished(
            current_lineage_id,
            source_node_id,
            source_message_id,
            actor_id,
            completed_at=source_completed_at,
            event_source_ids=program_event_source_ids,
            ports=ports,
        )
    )


def _settle_paths_by_lineage(path_ids_by_lineage: dict[str, set[str]]) -> list[str]:
    """Settle each Lineage's touched trigger paths, Lineages in order; the paths that finished."""

    return [
        trigger_path_id
        for current_lineage_id, trigger_path_ids in sorted(
            path_ids_by_lineage.items()
        )
        for trigger_path_id in settle_program_paths(
            current_lineage_id,
            trigger_path_ids,
        )
    ]


def _drain_backlogs(lineage_ids: Iterable[str], actor_id: str, *, ports: ProgramTerminalPorts) -> dict[str, Any]:
    """Deliver what waited on the now-free Resident, Lineage by Lineage in order."""

    return {
        current_lineage_id: ports.flow.drain_program_event_wire_backlog(
            current_lineage_id,
            actor_id,
        )
        for current_lineage_id in sorted(set(lineage_ids))
    }


def handle_node_message_completed(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    program_event_source_ids: set[str] | None = None,
    ports: ProgramTerminalPorts,
) -> dict[str, Any]:
    """Translate a Resident idle transition into Program-owned Events."""

    actor_id = _safe_actor(actor)
    exact_resident_id = _text(resident_id, 180)
    source_message_id = _text(message_id, 180)
    source_completed_at = _text(completed_at, 80) or ports.state.now_iso()
    if not exact_resident_id or not source_message_id:
        raise ValueError("节点完成事件缺少 Resident 或源 message_id")

    lineage_ids = (
        [lineage_id]
        if lineage_id
        else ports.directory.lineage_ids_for_resident(exact_resident_id)
    )
    matched_sources, matched_node_count, removed_absorption_count = _mark_sources_idle(
        lineage_ids, exact_resident_id, source_message_id, source_completed_at, actor, actor_id, ports=ports,
    )
    work = _CompletedWork()
    for current_lineage_id, source_node_id, source_node in matched_sources:
        _complete_node_turn(
            work, current_lineage_id, source_node_id, source_node, source_message_id, actor_id,
            source_completed_at, program_event_source_ids, ports=ports,
        )

    program_created_count = sum(
        int(item.get("matched_count") or 0)
        for item in work.program_event_results
    )
    program_event_backlog = _drain_backlogs((item[0] for item in matched_sources), actor_id, ports=ports)
    settled_trigger_path_ids = _settle_paths_by_lineage(work.completed_path_ids_by_lineage)
    return {
        "matched_node_count": matched_node_count,
        "removed_absorption_count": removed_absorption_count,
        "created_count": program_created_count,
        "program_created_count": program_created_count,
        "operator_result_count": len(work.operator_results),
        "agent_result_count": len(work.agent_results),
        "program_events": work.program_event_results,
        "operator_results": work.operator_results,
        "agent_results": work.agent_results,
        "settled_trigger_path_ids": settled_trigger_path_ids,
        "program_event_backlog": program_event_backlog,
    }


_TERMINAL_STATUS_ALIASES = {
    "success": "completed",
    "succeeded": "completed",
    "canceled": "cancelled",
    "interrupted": "cancelled",
}


def _terminal_status(terminal_status: object) -> str:
    """A terminal status in Program terms; spellings of other systems map to it."""

    status = _text(terminal_status, 24).lower() or "completed"
    return _TERMINAL_STATUS_ALIASES.get(status, status)


@dataclass
class _TerminalMatch:
    """The nodes one failed or cancelled turn belongs to, by (Lineage, node)."""

    matched_node_count: int = 0
    removed_absorption_count: int = 0
    operator_nodes: set[tuple[str, str]] = field(default_factory=set)
    terminal_nodes: set[tuple[str, str]] = field(default_factory=set)


def _match_terminal_nodes(
    lineage_ids: list[str],
    exact_resident_id: str,
    source_message_id: str,
    status: str,
    actor: str,
    actor_id: str,
    *,
    ports: ProgramTerminalPorts,
) -> _TerminalMatch:
    """Find the Resident's nodes; a cancelled turn also stops the absorptions it was the target of."""

    match = _TerminalMatch()
    with ports.directory.lineage_scopes(
        lineage_ids,
        operation="resident.message_terminal",
    ):
        for current_lineage_id in lineage_ids:
            try:
                lineage = ports.state.read_lineage(current_lineage_id, actor=actor)
            except KeyError:
                continue
            if not ports.directory.actor_can_manage_lineage(lineage, actor_id):
                continue
            runtime_resident_ids = _program_runtime_resident_ids(lineage)
            matched_source_node_ids: set[str] = set()
            for node in lineage.get("nodes") or []:
                if (
                    not isinstance(node, dict)
                    or ports.resident.node_resident_id(node) != exact_resident_id
                ):
                    continue
                match.matched_node_count += 1
                node_id = str(node.get("id") or "")
                matched_source_node_ids.add(node_id)
                match.terminal_nodes.add((current_lineage_id, node_id))
                if node_id in runtime_resident_ids:
                    match.operator_nodes.add(
                        (current_lineage_id, node_id)
                    )
            if matched_source_node_ids and status == "cancelled":
                previous = _clone_json(lineage)
                removed_for_lineage = remove_terminal_absorption_edges(
                    lineage,
                    matched_source_node_ids,
                    source_message_id,
                )
                if removed_for_lineage:
                    match.removed_absorption_count += removed_for_lineage
                    ports.state.persist_lineage_runtime_state(
                        previous,
                        lineage,
                        "节点吸收已停止",
                    )
    return match


def _settle_terminal_handoffs(
    terminal_nodes: set[tuple[str, str]],
    source_message_id: str,
    status: str,
    terminal_at: str,
    *,
    ports: ProgramTerminalPorts,
) -> tuple[int, dict[str, set[str]]]:
    """Settle the Program's handoffs to each node and tell their senders; (count, paths by Lineage)."""

    ordinary_terminal_count = 0
    terminal_path_ids_by_lineage: dict[str, set[str]] = {}
    for current_lineage_id, source_node_id in sorted(
        terminal_nodes
    ):
        invocations = lineage_program_resident_inputs.settle_resident_handoffs(
            current_lineage_id,
            message_id=source_message_id,
            resident_id=source_node_id,
            status=status,
            completed_at=terminal_at,
        )
        if not invocations:
            continue
        terminal_path_ids_by_lineage.setdefault(
            current_lineage_id, set()
        ).update(
            str(item.get("trigger_path_id") or "")
            for item in invocations
            if str(item.get("trigger_path_id") or "")
        )
        ordinary_terminal_count += len(invocations)
        ports.resident.notify_program_resident_invocation_terminal(
            current_lineage_id,
            source_node_id,
            source_message_id,
            invocations,
            status=status,
        )
    return ordinary_terminal_count, terminal_path_ids_by_lineage


def _complete_terminal_operators(
    operator_nodes: set[tuple[str, str]],
    source_message_id: str,
    actor_id: str,
    status: str,
    *,
    ports: ProgramTerminalPorts,
) -> list[dict[str, Any]]:
    """End the Packet Operator invocations the turn was running."""

    operator_results: list[dict[str, Any]] = []
    for current_lineage_id, source_node_id in sorted(
        operator_nodes
    ):
        completed_operator = lineage_program_execution.complete_operator_invocation(
            current_lineage_id,
            source_node_id,
            source_message_id,
            actor_id,
            terminal_status=status,
            ports=ports.flow.program_execution_ports(),
        )
        if completed_operator:
            operator_results.append(completed_operator)
    return operator_results


def handle_resident_message_terminal_locked(
    resident_id: str,
    actor: str,
    message_id: str,
    *,
    completed_at: str = "",
    lineage_id: str = "",
    terminal_status: str = "completed",
    program_event_source_ids: set[str] | None = None,
    ports: ProgramTerminalPorts,
) -> dict[str, Any]:
    """Consume one durable Resident terminal fact in Program runtime."""

    status = _terminal_status(terminal_status)
    if status not in {"completed", "failed", "cancelled"}:
        raise ValueError("Resident terminal_status 无效")
    if status == "completed":
        return handle_node_message_completed(
            resident_id,
            actor,
            message_id,
            completed_at=completed_at,
            lineage_id=lineage_id,
            program_event_source_ids=program_event_source_ids,
            ports=ports,
        )

    actor_id = _safe_actor(actor)
    exact_resident_id = _text(resident_id, 180)
    source_message_id = _text(message_id, 180)
    if not exact_resident_id or not source_message_id:
        raise ValueError("节点终态事件缺少 Resident 或源 message_id")

    lineage_ids = (
        [lineage_id]
        if lineage_id
        else ports.directory.lineage_ids_for_resident(exact_resident_id)
    )
    match = _match_terminal_nodes(
        lineage_ids, exact_resident_id, source_message_id, status, actor, actor_id, ports=ports,
    )
    terminal_at = _text(completed_at, 80) or ports.state.now_iso()
    ordinary_terminal_count, terminal_path_ids_by_lineage = _settle_terminal_handoffs(
        match.terminal_nodes, source_message_id, status, terminal_at, ports=ports,
    )
    operator_results = _complete_terminal_operators(
        match.operator_nodes, source_message_id, actor_id, status, ports=ports,
    )
    terminal_event_type = (
        "resident.work_failed"
        if status == "failed"
        else "resident.work_cancelled"
    )
    program_events: list[dict[str, Any]] = []
    for current_lineage_id, source_node_id in sorted(
        match.terminal_nodes
    ):
        program_events.extend(
            dispatch_program_resident_terminal_event(
                current_lineage_id,
                source_node_id,
                source_message_id,
                actor_id,
                event_type=terminal_event_type,
                completed_at=terminal_at,
                ports=ports,
            )
        )

    settled_trigger_path_ids = _settle_paths_by_lineage(terminal_path_ids_by_lineage)
    # The Resident is free again whatever the outcome: Events that waited for
    # it on a latest or queue wire go now, as after a completed turn.
    program_event_backlog = _drain_backlogs(
        (lineage_id for lineage_id, _node_id in match.terminal_nodes), actor_id, ports=ports,
    )
    return {
        "terminal_status": status,
        "matched_node_count": match.matched_node_count,
        "removed_absorption_count": match.removed_absorption_count,
        "created_count": 0,
        "operator_result_count": len(operator_results),
        "agent_result_count": ordinary_terminal_count,
        "program_events": program_events,
        "operator_results": operator_results,
        "settled_trigger_path_ids": settled_trigger_path_ids,
        "program_event_backlog": program_event_backlog,
    }


def _watching_sources(
    plan: dict[str, Any],
    event_type: str,
    subjects: dict[str, Any],
    selected_source_ids: set[str] | None,
    *,
    resident_id: str = "",
    member_ids: Iterable[str] = (),
) -> list[tuple[str, str, str]]:
    """(source, contract, subject) for every observed subject a terminal fact is about.

    A source that watches several Residents appears once per subject it
    watches among ``subjects``; each gets its own Event.  A source scoped to
    the Lineage (``member_ids``) or to all of Portal observes only the
    Resident that did the work, ``resident_id``: a Family facade it relayed
    for is a member of the same scope and is not reported twice.
    """

    members = frozenset(member_ids)
    matches: set[tuple[str, str, str]] = set()
    for event_source_id, source in (plan.get("event_sources") or {}).items():
        if not (
            isinstance(source, dict)
            and str(source.get("event_type") or "") == event_type
            and (selected_source_ids is None or str(event_source_id) in selected_source_ids)
        ):
            continue
        contract = _program_event_source_contract_fingerprint(source)
        if lineage_program_event_scope.event_source_resident_scope(source) == "watched":
            observed = [
                subject_id for subject_id in lineage_program_event_scope.event_source_subject_ids(source)
                if subject_id in subjects
            ]
        else:
            observed = [resident_id] if (
                resident_id in subjects
                and lineage_program_event_scope.event_source_covers(source, resident_id, members)
            ) else []
        matches.update((str(event_source_id), contract, subject_id) for subject_id in observed)
    return sorted(matches)


def _lineage_member_ids(lineage: dict[str, Any]) -> set[str]:
    """The Residents of a Lineage, as a scoped Event Source sees them."""

    return {
        str(node.get("id") or "")
        for node in lineage.get("nodes") or []
        if isinstance(node, dict)
        and lineage_primitives.RESIDENT_ID_RE.fullmatch(str(node.get("id") or ""))
    }


def _self_caused_sources(
    plan: dict[str, Any],
    lineage_id: str,
    event_sources: list[tuple[str, str, str]],
    resident_id: str,
    message_id: str,
) -> set[str]:
    """Scoped sources whose own Event delivered this work, directly or through an Effect.

    The Program's invocation ledger names the wire each delivery came
    through; a wire leaving a scoped source means the work is that source's
    own, and observing it again would loop.
    """

    scoped = {
        event_source_id for event_source_id, _contract, _subject in event_sources
        if lineage_program_event_scope.event_source_resident_scope(
            (plan.get("event_sources") or {}).get(event_source_id)
        ) != "watched"
    }
    if not scoped:
        return set()
    invocation = lineage_program_resident_inputs.resident_handoff(lineage_id, resident_id, message_id)
    delivered_through = {str((invocation or {}).get("source_wire_id") or "")}
    delivered_through.discard("")
    if not delivered_through:
        return set()
    return {
        event_source_id for event_source_id in scoped
        if delivered_through & {
            str(route.get("wire_id") or "")
            for route in ((plan.get("event_sources") or {}).get(event_source_id) or {}).get("subscriber_routes") or []
            if isinstance(route, dict)
        }
    }


def _self_caused_result(event_source_id: str) -> dict[str, Any]:
    return {
        "event_source_id": event_source_id,
        "matched_count": 0,
        "duplicate": False,
        "self_caused": True,
        "results": [],
    }


def _terminal_subjects(source_resident_id: str, source_message_id: str) -> dict[str, tuple[str, dict[str, Any]]]:
    """Subjects one Resident terminal is reported for: the Resident, and a Family facade it relayed for.

    A member finishing a message relayed from a facade also finishes the
    facade's message, so Event Sources watching the facade observe the
    member's work (decision_44cf587c1721).  Each subject maps to its own
    message ID and extra payload.
    """

    from . import family_facade

    subjects: dict[str, tuple[str, dict[str, Any]]] = {source_resident_id: (source_message_id, {})}
    try:
        # Nested Families: every facade up the chain finishes with the member.
        relays = family_facade.relay_sources_of(source_resident_id, source_message_id)
    except Exception:  # noqa: BLE001 - a relay lookup never blocks the member's own Events
        LOGGER.exception(
            "Relay lookup for %s message %s failed; its facades' Event Sources miss this terminal",
            source_resident_id, source_message_id,
        )
        relays = []
    for facade_id, facade_message_id in relays:
        subjects.setdefault(facade_id, (facade_message_id, {
            "member_resident_id": source_resident_id,
            "member_message_id": source_message_id,
        }))
    return subjects



def _terminal_dispatcher(
    lineage: dict[str, Any],
    actor: str,
    ports: ProgramTerminalPorts,
) -> tuple[str, dict[str, Any] | None]:
    """Keep a trusted request initiator; unattended observations use the current Leader."""
    from management_common.principal import actor_principal
    from .lineage_instruction_context import current_event_identity

    original = current_event_identity()
    if original is not None and original.principal.id == actor:
        return actor, original.to_dict()

    if actor_principal(actor).kind in {"user", "resident"}:
        return actor, OperationIdentity.for_actor(actor).to_dict()
    principal = ports.directory.lineage_runtime_actor(lineage)
    return principal, OperationIdentity.for_actor(principal).to_dict()


def dispatch_program_resident_work_finished(
    lineage_id: str,
    source_resident_id: str,
    source_message_id: str,
    actor: str,
    *,
    completed_at: str = "",
    event_source_ids: set[str] | None = None,
    ports: ProgramTerminalPorts,
) -> list[dict[str, Any]]:
    """Route one successful Resident terminal fact to matching Event Sources."""
    _lineage_scope = ports.state.lineage_scope
    _read_lineage = ports.state.read_lineage
    _ensure_manager = ports.state.ensure_manager
    dispatch_lineage_program_event = ports.flow.dispatch_lineage_program_event

    selected_source_ids = (
        {str(item) for item in event_source_ids if str(item)}
        if event_source_ids is not None
        else None
    )
    subjects = _terminal_subjects(source_resident_id, source_message_id)
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor)
        plan = _compiled_program(lineage)
        dispatcher, identity = _terminal_dispatcher(lineage, actor, ports)
        event_sources = _watching_sources(
            plan, "resident.work_finished", subjects, selected_source_ids,
            resident_id=source_resident_id, member_ids=_lineage_member_ids(lineage),
        )
    self_caused = _self_caused_sources(
        plan, lineage_id, event_sources, source_resident_id, source_message_id,
    )

    results: list[dict[str, Any]] = [
        _self_caused_result(event_source_id) for event_source_id in sorted(self_caused)
    ]
    for event_source_id, source_contract, subject_id in event_sources:
        if event_source_id in self_caused:
            continue
        message_id, extra = subjects[subject_id]
        correlation = (
            f"{event_source_id}:{subject_id}:{message_id}"
        )
        event = lineage_primitives.make_event(
            "resident.work_finished",
            subject_resident_id=subject_id,
            occurred_at=completed_at,
            payload={
                "message_id": message_id,
                "execution_id": trigger_paths.execution_cause(source_resident_id, source_message_id)["id"],
                "cause": trigger_paths.execution_cause(source_resident_id, source_message_id),
                **extra,
            },
            correlation=correlation,
            identity=identity,
        )
        results.append(
            dispatch_lineage_program_event(
                lineage_id,
                event_source_id,
                event,
                dispatcher,
                cursor=f"message:{subject_id}:{message_id}",
                expected_event_source_contract=source_contract,
            )
        )
    return results



def dispatch_program_resident_terminal_event(
    lineage_id: str,
    source_resident_id: str,
    source_message_id: str,
    actor: str,
    *,
    event_type: str,
    completed_at: str = "",
    event_source_ids: set[str] | None = None,
    ports: ProgramTerminalPorts,
) -> list[dict[str, Any]]:
    """Route one failed or cancelled terminal fact to matching sources."""
    _lineage_scope = ports.state.lineage_scope
    _read_lineage = ports.state.read_lineage
    _ensure_manager = ports.state.ensure_manager
    dispatch_lineage_program_event = ports.flow.dispatch_lineage_program_event

    if event_type not in {
        "resident.work_failed",
        "resident.work_cancelled",
    }:
        raise ValueError("Resident terminal Event type 无效")
    selected_source_ids = (
        {str(item) for item in event_source_ids if str(item)}
        if event_source_ids is not None
        else None
    )
    subjects = _terminal_subjects(source_resident_id, source_message_id)
    with _lineage_scope(lineage_id):
        lineage = _read_lineage(lineage_id, actor=actor)
        _ensure_manager(lineage, actor)
        plan = _compiled_program(lineage)
        dispatcher, identity = _terminal_dispatcher(lineage, actor, ports)
        event_sources = _watching_sources(
            plan, event_type, subjects, selected_source_ids,
            resident_id=source_resident_id, member_ids=_lineage_member_ids(lineage),
        )
    self_caused = _self_caused_sources(
        plan, lineage_id, event_sources, source_resident_id, source_message_id,
    )
    results: list[dict[str, Any]] = [
        _self_caused_result(event_source_id) for event_source_id in sorted(self_caused)
    ]
    for event_source_id, source_contract, subject_id in event_sources:
        if event_source_id in self_caused:
            continue
        message_id, extra = subjects[subject_id]
        event = lineage_primitives.make_event(
            event_type,
            subject_resident_id=subject_id,
            occurred_at=completed_at,
            payload={
                "message_id": message_id,
                "execution_id": trigger_paths.execution_cause(source_resident_id, source_message_id)["id"],
                "cause": trigger_paths.execution_cause(source_resident_id, source_message_id),
                **extra,
            },
            correlation=(
                f"{event_source_id}:{subject_id}:{message_id}"
            ),
            identity=identity,
        )
        results.append(
            dispatch_lineage_program_event(
                lineage_id,
                event_source_id,
                event,
                dispatcher,
                cursor=(
                    f"message-terminal:{event_type}:"
                    f"{subject_id}:{message_id}"
                ),
                expected_event_source_contract=source_contract,
            )
        )
    return results


# The Event type each terminal status is observed as.
_TERMINAL_EVENT_TYPES = {
    "completed": "resident.work_finished",
    "failed": "resident.work_failed",
    "cancelled": "resident.work_cancelled",
}


def _dispatch_to_observer(
    observer_lineage_id: str,
    indexed_source_ids: Iterable[str],
    exact_resident_id: str,
    source_message_id: str,
    status: str,
    event_type: str,
    occurred_at: str,
    *,
    ports: ProgramTerminalPorts,
) -> dict[str, Any] | None:
    """Dispatch the terminal to one observing Lineage's current sources, under its Leader; None when none observes it."""

    with ports.state.lineage_scope(observer_lineage_id):
        observer = ports.state.read_lineage_state(observer_lineage_id)
        observer_actor = ports.directory.lineage_runtime_actor(observer)
        plan = _compiled_program(observer)
        current_source_ids = {
            str(event_source_id)
            for event_source_id, source in (
                plan.get("event_sources") or {}
            ).items()
            if (
                isinstance(source, dict)
                and str(source.get("event_type") or "")
                == event_type
                and lineage_program_event_scope.event_source_covers(
                    source,
                    exact_resident_id,
                    _lineage_member_ids(observer),
                )
            )
        }
        selected_source_ids = current_source_ids & set(
            indexed_source_ids
        )
    if not selected_source_ids:
        return None
    from .lineage_instruction_context import automatic_scope
    with automatic_scope(observer_actor):
        if status == "completed":
            results = dispatch_program_resident_work_finished(
                observer_lineage_id,
                exact_resident_id,
                source_message_id,
                observer_actor,
                completed_at=occurred_at,
                event_source_ids=selected_source_ids,
                ports=ports,
            )
        else:
            results = dispatch_program_resident_terminal_event(
                observer_lineage_id,
                exact_resident_id,
                source_message_id,
                observer_actor,
                event_type=event_type,
                completed_at=occurred_at,
                event_source_ids=selected_source_ids,
                ports=ports,
            )
    return {
        "lineage_id": observer_lineage_id,
        "event_source_ids": sorted(selected_source_ids),
        "results": results,
    }


def dispatch_resident_terminal_observers(
    resident_id: str,
    message_id: str,
    *,
    terminal_status: str = "completed",
    completed_at: str = "",
    exclude_lineage_ids: set[str] | None = None,
    ports: ProgramTerminalPorts,
) -> dict[str, Any]:
    """Fan one Resident terminal fact out to cross-Lineage observers.

    The catalog owns the reverse observer index, so this hot path touches only
    Lineages that actually watch the exact Resident and terminal type.  Each
    observer executes under its current Leader identity; downstream Effects retain
    their normal target-Lineage authorization checks.
    """

    exact_resident_id = _text(resident_id, 80).lower()
    source_message_id = _text(message_id, 180)
    if (
        not lineage_primitives.RESIDENT_ID_RE.fullmatch(exact_resident_id)
        or not source_message_id
    ):
        raise ValueError("Resident terminal observer fact is incomplete")
    status = _terminal_status(terminal_status)
    event_type = _TERMINAL_EVENT_TYPES.get(status, "")
    if not event_type:
        raise ValueError("Resident terminal_status 无效")

    excluded = (
        {
            str(item or "").strip()
            for item in exclude_lineage_ids
            if str(item or "").strip()
        }
        if exclude_lineage_ids is not None
        else set(ports.directory.lineage_ids_for_resident(exact_resident_id))
    )
    indexed = ports.directory.lineage_catalog_store().program_resident_observers(
        exact_resident_id,
        event_type,
    )
    dispatches: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    occurred_at = _text(completed_at, 80) or ports.state.now_iso()
    for observer_lineage_id, indexed_source_ids in sorted(indexed.items()):
        if observer_lineage_id in excluded:
            continue
        # One observer's failure is reported with the result and never stops the others.
        try:
            dispatch = _dispatch_to_observer(
                observer_lineage_id, indexed_source_ids, exact_resident_id, source_message_id,
                status, event_type, occurred_at, ports=ports,
            )
        except Exception as exc:
            failures.append(
                {
                    "lineage_id": observer_lineage_id,
                    "error_type": type(exc).__name__,
                    "error": _text(exc, 500),
                }
            )
            continue
        if dispatch is not None:
            dispatches.append(dispatch)
    return {
        "resident_id": exact_resident_id,
        "message_id": source_message_id,
        "terminal_status": status,
        "event_type": event_type,
        "indexed_lineage_count": len(indexed),
        "observer_lineage_count": len(dispatches),
        "event_source_count": sum(
            len(item["event_source_ids"]) for item in dispatches
        ),
        "dispatches": dispatches,
        "failures": failures,
    }
