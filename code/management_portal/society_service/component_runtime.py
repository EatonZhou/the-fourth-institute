"""Run-time control of Event Sources, Effects and loops.

Pausing and resuming change only runtime state: the Lineage definition and
its version stay as they are.  A paused Event Source produces no Event; on
resume a timed source continues from its next planned time without replaying
the paused window, and an observing source re-establishes its baseline.
"""
from __future__ import annotations

from management_common.operation_errors import OperationError
from management_common.clock import utc_now_iso
from management_portal.execution.service_settings import ExecutionSettings
from management_portal.hatg.contracts import InstructionError

from datetime import datetime, timezone
from typing import Any

from . import lineage_loop_runtime, lineage_program, lineage_program_runtime, lineage_recurring_schedule
from . import system_workflow_switches
from .lineage_program_queries import _compiled_program
from .lineage_program_runtime_projection import event_source_runtime_view
from . import lineage_aggregate

OBSERVATION_BASELINE_FIELDS = (
    "file_locator",
    "file_signature",
    "file_generation",
    "state_locator",
    "state_signature",
    "state_value",
    "state_generation",
    "state_epoch",
    "completion_locator",
    "completion_message_id",
    "completion_at",
    "completion_generation",
)
RECURRING_PLAN_FIELDS = (
    "next_scheduled_at",
    "next_due_at",
    "next_missed_count",
    "pending_scheduled_at",
    "pending_due_at",
    "pending_next_scheduled_at",
    "pending_next_due_at",
    "pending_observed_at",
    "pending_missed_count",
    "pending_generation",
)


class ComponentRuntimeError(OperationError, LookupError):
    http_status = 404
    commit_state = "not_committed"
    code = "instruction.not_found"


def _plan(lineage_id: str, actor: str) -> dict[str, Any]:
    return _compiled_program(lineage_aggregate.read_lineage(lineage_id, actor=actor))


def _event_sources(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(source_id): source
        for source_id, source in (plan.get("event_sources") or {}).items()
        if isinstance(source, dict) and source.get("kind") == "event_source"
    }


def _kind(plan: dict[str, Any], component_id: str) -> str:
    if component_id in _event_sources(plan):
        return "event_source"
    if component_id in (plan.get("loops") or {}):
        return "loop"
    if component_id in (plan.get("effects") or {}):
        return "effect"
    raise ComponentRuntimeError(f"组件不存在，或不是事件源、Effect 或循环组件：{component_id}")


def _event_source_projection(source_id: str, source: dict[str, Any], runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "component_id": source_id,
        "kind": "event_source",
        "title": str(source.get("title") or ""),
        "event_type": str(source.get("event_type") or ""),
        **runtime,
    }


def views(lineage_id: str, actor: str, *, component_id: str = "") -> list[dict[str, Any]]:
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    plan = _compiled_program(lineage)
    if component_id:
        _kind(plan, component_id)
    result: list[dict[str, Any]] = []
    sources = {source_id: source for source_id, source in sorted(_event_sources(plan).items())
               if not component_id or source_id == component_id}
    if sources:
        runtimes = event_source_runtime_view(lineage, sources)
        result.extend(_event_source_projection(source_id, source, runtimes[source_id])
                      for source_id, source in sources.items())
    for loop_id, loop in sorted((plan.get("loops") or {}).items()):
        if not component_id or loop_id == component_id:
            result.append({"title": str(loop.get("title") or ""), **lineage_loop_runtime.view(lineage_id, loop_id, loop)})
    for effect_id, effect in sorted((plan.get("effects") or {}).items()):
        if not component_id or effect_id == component_id:
            state = lineage_program_runtime.read_operator_state(lineage_id, effect_id)
            paused = bool(state.get("paused"))
            result.append({"component_id": effect_id, "kind": "effect", "title": str(effect.get("title") or ""),
                           "paused": paused, "state": "paused" if paused else "active",
                           "paused_at": str(state.get("paused_at") or "")})
    return result


def _reached(value: object) -> bool:
    moment = lineage_recurring_schedule.parse_utc(value)
    return moment is not None and moment <= datetime.now(timezone.utc)


def _resume_event_source(source: dict[str, Any], config: dict[str, Any]) -> None:
    source.pop("paused", None)
    source.pop("paused_at", None)
    for field in OBSERVATION_BASELINE_FIELDS:
        source.pop(field, None)
    source.pop("subjects", None)
    if str(config.get("event_type") or "") != "time.reached":
        watched = lineage_program.event_source_subject_ids(config)
        if len(watched) > 1:
            # Each watched Resident starts again from its current state.
            source["subjects"] = {subject: {"rebaseline": True} for subject in watched}
        else:
            source["rebaseline"] = True
        return
    mode = str(config.get("schedule_mode") or "at")
    consumed = list(source.get("consumed_cursors") or [])
    if mode == "recurring":
        # The next occurrence is planned again from now; the paused window is not replayed.
        for field in RECURRING_PLAN_FIELDS:
            source.pop(field, None)
    elif mode == "countdown":
        due_at = str(source.get("timer_due_at") or "")
        if due_at and _reached(due_at):
            consumed.append(f"timer:{int(source.get('timer_generation') or 0)}:{due_at}")
    else:
        scheduled_at = str(config.get("scheduled_at") or "")
        if scheduled_at and _reached(scheduled_at):
            consumed.append(f"time:{scheduled_at}")
    source["consumed_cursors"] = consumed[-lineage_program_runtime.MAX_EVENT_CURSORS:]


def update_pause_checkpoint(state: dict[str, Any], kind: str, paused: bool,
                            config: dict[str, Any] | None = None) -> None:
    """Native checkpoint transition, shared with offline target installation.

    This pure mutation neither acquires authority nor persists/distributes work.
    Callers own their storage transaction and admission boundary.
    """
    if kind not in {"event_source", "effect"} or type(paused) is not bool:
        raise ValueError("Unsupported component pause transition")
    if paused:
        if not state.get("paused"):
            state.update(paused=True, paused_at=utc_now_iso())
    elif kind == "effect":
        state.pop("paused", None)
        state.pop("paused_at", None)
    elif state.get("paused"):
        _resume_event_source(state, config or {})


def set_paused(lineage_id: str, component_id: str, paused: bool, actor: str) -> dict[str, Any]:
    with lineage_aggregate.execution_authority(lineage_id):
        result = _set_paused(lineage_id, component_id, paused, actor)
        # Runtime has committed and its state lock is released. Projection
        # completes before the public command returns, under the same authority.
        system_workflow_switches.project_after_commit(lineage_id)
        return result


def update_settings(changes: dict[str, bool], actor: str) -> dict[str, bool]:
    """Route installed settings through the same public component switch owner."""
    bindings = system_workflow_switches.workflow_switches()
    requested = {key: (lineage_id, field) for key, (lineage_id, field) in bindings.items()
                 if field in changes}
    with lineage_aggregate.execution_authorities({owner for owner, _field in requested.values()}):
        settings = ExecutionSettings()
        managed = settings.managed_workflow_keys()
        installed: list[tuple[str, str, bool]] = []
        unmanaged: dict[str, bool] = {}
        # Fresh installations may have no workflow yet. Once a key was managed,
        # removing its deployment binding must not bypass its Event authority.
        for key, (_option, field) in system_workflow_switches.WORKFLOW_FIELDS.items():
            if field not in changes or key in bindings:
                continue
            if key in managed:
                raise InstructionError("instruction.conflict", "系统开关部署绑定缺失", details={"client_key": key})
            unmanaged[field] = changes[field]
        # Validate every requested binding before changing either setting.
        for key, (lineage_id, field) in requested.items():
            matches = system_workflow_switches.bound_components(system_workflow_switches.current_definition(lineage_id), key)
            if len(matches) == 1 and matches[0].get("kind") == "event_source":
                lineage_aggregate.read_lineage(lineage_id, actor=actor)
                installed.append((lineage_id, str(matches[0]["id"]), changes[field]))
            elif matches or key in managed:
                raise InstructionError(
                    "instruction.conflict", "系统开关 Event 已删除或绑定无效，请先恢复 Lineage 中的开关节点",
                    details={"lineage_id": lineage_id, "client_key": key,
                             "binding_state": "invalid" if matches else "missing"},
                )
            else:
                unmanaged[field] = changes[field]
        if unmanaged:
            settings.update_unmanaged(unmanaged)
        for lineage_id, component_id, enabled in installed:
            set_paused(lineage_id, component_id, not enabled, actor)
        return settings.get()


def _set_paused(lineage_id: str, component_id: str, paused: bool, actor: str) -> dict[str, Any]:
    plan = _plan(lineage_id, actor)
    kind = _kind(plan, component_id)
    if kind == "loop":
        lineage_loop_runtime.set_paused(lineage_id, component_id, paused)
        return views(lineage_id, actor, component_id=component_id)[0]
    if kind == "effect":
        with lineage_program_runtime.operator_state_transaction(lineage_id, component_id) as state:
            update_pause_checkpoint(state, kind, paused)
        return views(lineage_id, actor, component_id=component_id)[0]
    config = _event_sources(plan)[component_id]
    with lineage_program_runtime._runtime_transaction(lineage_id) as runtime:
        source = runtime["event_sources"].setdefault(component_id, {"consumed_cursors": []})
        update_pause_checkpoint(source, kind, paused, config)
    return views(lineage_id, actor, component_id=component_id)[0]


def reset(lineage_id: str, component_id: str, actor: str) -> dict[str, Any]:
    plan = _plan(lineage_id, actor)
    if _kind(plan, component_id) == "effect":
        raise ValueError("Effect 没有可重置的运行计数；打开或关闭请使用 PauseComponent")
    if _kind(plan, component_id) == "loop":
        lineage_loop_runtime.reset(lineage_id, component_id)
        return views(lineage_id, actor, component_id=component_id)[0]
    lineage_program_runtime.reset_event_source_fire_budget(lineage_id, component_id)
    return views(lineage_id, actor, component_id=component_id)[0]
