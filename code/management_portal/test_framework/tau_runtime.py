from __future__ import annotations

from typing import Any
import logging
import time

from management_portal.society_service import get_society_service, SocietyServiceUnavailable
from management_portal.resources.errors import ResourceServiceUnavailable
from management_portal.hatg.contracts import InstructionError
from management_portal.application_bindings import resolve_application_lineage
from management_portal.test_framework.tau import CaseNotSent
from management_portal.test_framework.tau_residents import TAU_ACTOR

LOGGER = logging.getLogger(__name__)

# TAU Campaigns work in the Lineage configured for ``tau``; a paused admission
# refuses new Campaigns before they are recorded (records.py).
APPLICATION_TARGETS = {"tau": {"target": "lineage", "admission": True}}


def ensure_module_resident(assignments, module_id: str, **options: Any) -> dict[str, Any]:
    from management_portal.resident_handoff import ensure_invocation_resident

    assignment = assignments.assignment(module_id)
    resident_id = str(assignment.get("resident_id") or "").strip()
    lineage_id = str(assignment.get("lineage_id") or "").strip()
    if bool(resident_id) != bool(lineage_id):
        raise RuntimeError("TAU 模块身份必须同时记录 Resident 与 Lineage")
    # An ordinary Portal duty selects the shared team. Modules own Resident
    # slots, never teams; an absent binding must not allocate a fallback team.
    target = resolve_application_lineage(actor=TAU_ACTOR, application="tau")
    if resident_id:
        resident = get_society_service().resident.get_resident(resident_id, TAU_ACTOR)
        if str(resident.get("lineage_id") or "") != target:
            raise RuntimeError("TAU 模块 Resident 尚未迁入 tau 职责绑定的 Lineage")
        if lineage_id != target:
            return assignments.record_identity(
                module_id, resident_id=resident_id, lineage_id=target,
                expected_slot_id=assignment["slot_id"],
            )
        return assignment

    cancelled = options.get("cancellation_requested") or (lambda: False)
    resident_scope, lineage_scope = assignments.scopes(assignment)
    while True:
        if cancelled():
            raise InterruptedError("TAU 已取消，未创建模块 Resident")
        try:
            identity = ensure_invocation_resident(
                actor=TAU_ACTOR,
                resident_scope=resident_scope,
                lineage_scope=lineage_scope,
                lineage_binding=target,
                title=f"Portal TAU · {module_id}",
                
                backend=str(options.get("provider") or "codex"),
                backend_route_id=str(options.get("backend_route_id") or ""),
                cwd=str(options.get("cwd") or ""),
                runtime_settings=options.get("runtime_options") or {},
                metadata=options.get("metadata") or {},
            )
            break
        except (SocietyServiceUnavailable, ResourceServiceUnavailable):
            pass
        except InstructionError as error:
            if error.code not in {"instruction.in_progress", "instruction.owner_unavailable"}:
                raise
        time.sleep(1.0)
    if not identity.get("resident_id") or identity.get("lineage_id") != target:
        raise RuntimeError("TAU Resident 未绑定到 tau 职责指定的 Lineage")
    return assignments.record_identity(
        module_id,
        resident_id=str(identity.get("resident_id") or ""),
        lineage_id=str(identity.get("lineage_id") or ""),
        expected_slot_id=assignment["slot_id"],
    )


def invoke_configured_resident(assignments, **options: Any) -> dict[str, Any]:
    from management_portal.resident_handoff import run_resident_turn
    from management_portal.lineage_workflows import WorkflowBindings
    from management_portal.test_framework.workflows import verification_flow

    module_id = str((options.get("metadata") or {}).get("test_module_id") or "")
    if not module_id:
        raise ValueError("TAU invocation requires test_module_id")
    cancelled = options.get("cancellation_requested") or (lambda: False)
    resident_runtime = dict(options.pop("resident_runtime", None) or {})
    # A closed module Resident that cannot restart is replaced while it is
    # prepared; its successor is then created and prepared once more.
    for attempt in range(2):
        assignment = ensure_module_resident(assignments, module_id, **options)
        identity = {
            "resident_id": str(assignment["resident_id"]),
            "lineage_id": str(assignment["lineage_id"]),
        }
        if not assignment.get("configuration_id"):
            break
        configuration = prepare_configuration(
            assignment, cancelled=cancelled, emit=lambda message: None,
            refresh_assignment=lambda: assignments.assignment(module_id),
            replace_unusable_assignment=lambda unusable: assignments.replace_unusable_resident(
                module_id,
                expected_resident_id=str(unusable.get("resident_id") or ""),
                changed_by=TAU_ACTOR,
            ),
        )
        if configuration["ready"] and configuration.get("resident_id") == identity["resident_id"]:
            break
        if attempt == 0 and configuration.get("status") == "pending_create" and not cancelled():
            continue
        raise CaseNotSent("TAU 实际绑定 Resident 的账号配置尚未就绪，未发送用例")
    if resident_runtime:
        apply_resident_runtime(identity["resident_id"], resident_runtime)
    if cancelled():
        raise InterruptedError("TAU 已取消，未发送用例")
    workflow = WorkflowBindings().install(
        actor=TAU_ACTOR,
        **identity,
        **verification_flow(),
    )
    return run_resident_turn(
        **options,
        resident_binding=identity,
        workflow_binding=workflow,
    )


def _resident_settings(resident_id: str) -> dict[str, Any]:
    from management_portal import isa_client

    try:
        result = isa_client.call(TAU_ACTOR, "GetResidentSettings", {"resident_id": resident_id}) or {}
    except InstructionError as exc:
        if exc.code == "instruction.not_found":
            raise KeyError(resident_id) from exc
        raise
    return dict(result.get("settings") or {})


def _update_resident_settings(resident_id: str, changes: dict[str, Any]) -> None:
    from management_portal import isa_client

    if changes:
        isa_client.call(TAU_ACTOR, "UpdateResidentSettings", {"resident_id": resident_id, "changes": dict(changes)})


def apply_resident_runtime(resident_id: str, runtime: dict[str, str]) -> dict[str, Any]:
    """Set the Campaign's model / effort / permission preset and verify them before sending."""
    current = _resident_settings(resident_id)
    _update_resident_settings(resident_id, {key: value for key, value in runtime.items() if current.get(key) != value})
    settings = _resident_settings(resident_id)
    mismatched = {key: settings.get(key) for key, value in runtime.items() if settings.get(key) != value}
    if mismatched:
        raise CaseNotSent(f"TAU Resident 的实际运行设置与本次 Campaign 要求不一致，未发送用例：{mismatched}")
    return settings


def _session_lacks_required_tools(resident_id: str, source: dict[str, Any]) -> bool:
    """Whether the Resident's current session never offered a required Harness tool.

    A Codex thread keeps the dynamic tools it started with, so a thread that
    started before a Harness tool existed (hatg_instruction, which TAU tasks
    require by name) never offers it.
    """
    if source.get("provider") != "codex" or not source.get("provider_session_ref"):
        return False
    from management_portal.execution.agent_data_store import get_agent_data_store
    from management_portal.execution.session_tools import session_lacks_required_tools

    binding = get_agent_data_store().provider_binding(f"resident:{resident_id}")
    return bool(
        binding.get("provider_session_ref") == source.get("provider_session_ref")
        and session_lacks_required_tools(binding)
    )


def resident_status(assignment: dict[str, Any]) -> dict[str, Any]:
    resident_id = str(assignment.get("resident_id") or "")
    desired_options = assignment.get("runtime_options") or {}
    result = {
        "module_id": assignment["module_id"],
        "resident_id": resident_id,
        "backend_route_id": assignment.get("backend_route_id") or "",
        "service_tier": desired_options.get("service_tier", ""),
        "configuration_id": assignment.get("configuration_id", ""),
    }
    if not resident_id:
        return {**result, "status": "pending_create", "ready": True}
    resident = get_society_service().resident
    try:
        options = resident.get_resident_restart_options(resident_id, TAU_ACTOR)
        runtime = _resident_settings(resident_id)
        conversation = resident.get_resident_state(resident_id, TAU_ACTOR).get("state") or {}
    except KeyError:
        return {**result, "status": "stale", "ready": False}
    source = options["source"]
    restart = options.get("restart") or {}
    pending = restart.get("status") not in {None, "", "completed", "failed", "cancelled"}
    intake_closed = str(conversation.get("intake_state") or "") == "closed"
    tools_stale = not pending and _session_lacks_required_tools(resident_id, source)
    matches = (
        source.get("provider") == "codex"
        and source.get("account_id") == result["backend_route_id"]
        and str(runtime.get("service_tier") or "") == result["service_tier"]
        and not tools_stale
    )
    return {
        **result,
        "actual_account": source.get("account_id", ""),
        "actual_service_tier": runtime.get("service_tier", ""),
        "intake_state": str(conversation.get("intake_state") or ""),
        "session_tools_stale": tools_stale,
        "restart": restart if pending or not matches else {},
        "status": "migrating" if pending else "closed" if intake_closed else "ready" if matches else "not_ready",
        "ready": bool(matches and not pending and not intake_closed),
    }


def apply_runtime_options(assignment: dict[str, Any]) -> dict[str, Any]:
    resident_id = str(assignment.get("resident_id") or "")
    if not resident_id:
        return {"status": "pending_create"}
    service_tier = (assignment.get("runtime_options") or {}).get("service_tier", "")
    _update_resident_settings(resident_id, {"service_tier": service_tier})
    runtime = _resident_settings(resident_id)
    if runtime.get("service_tier", "") != service_tier:
        raise RuntimeError("TAU Resident 的实际速度设置与目标不一致")
    return {"status": "applied", "actual_service_tier": runtime.get("service_tier", "")}


def apply_configuration(assignment: dict[str, Any]) -> dict[str, Any]:
    resident_id = str(assignment.get("resident_id") or "")
    if not resident_id:
        return resident_status(assignment)
    resident = get_society_service().resident
    options = resident.get_resident_restart_options(resident_id, TAU_ACTOR)
    source = options["source"]
    if source.get("provider") != "codex":
        raise ValueError("TAU 账号设置仅支持已有 Codex Resident")
    restart = options.get("restart") or {}
    pending = restart.get("status") not in {None, "", "completed", "failed", "cancelled"}
    if pending:
        if (restart.get("target") or {}).get("account_id") != assignment["backend_route_id"]:
            raise ValueError("TAU Resident 正在迁移到其他账号，请等待该操作完成")
        return resident_status(assignment)
    conversation = resident.get_resident_state(resident_id, TAU_ACTOR).get("state") or {}
    intake_closed = str(conversation.get("intake_state") or "") == "closed"
    _update_resident_settings(resident_id, assignment["runtime_options"])
    account_changed = source.get("account_id") != assignment["backend_route_id"]
    # A session without a required Harness tool is replaced once; the new
    # session starts with the current tools (the key names the old session).
    tools_stale = not account_changed and _session_lacks_required_tools(resident_id, source)
    if account_changed or intake_closed or tools_stale:
        idempotency_key = f"tau-config:{assignment['configuration_id']}:{resident_id}"
        if intake_closed and not account_changed:
            idempotency_key += f":closed:{source['generation']}"
        elif tools_stale:
            idempotency_key += f":tools:{source['generation']}"
        from management_portal import isa_client

        isa_client.call(TAU_ACTOR, "RestartSession", {
            "resident_id": resident_id,
            "provider": "codex",
            "account_id": assignment["backend_route_id"],
            # A native fork keeps the old thread's tools, so a session without
            # a required tool is replaced by a compact handoff.
            "context_strategy": "compact" if tools_stale else "smart",
            "active_execution_policy": "wait",
        }, key=idempotency_key, version={resident_id: source["generation"]})
    return resident_status(assignment)


def _percent(value: Any) -> str:
    return f"{value:g}%" if isinstance(value, (int, float)) else "未知"


def reserved_account_quota(route_id: str) -> dict[str, Any] | None:
    """The quota reserve of the execution account behind a route, when it has paused new work."""
    from management_portal.execution.service import ExecutionService

    try:
        accounts = ExecutionService().accounts().get("accounts", [])
    except Exception:  # noqa: BLE001 - an unreadable quota never blocks a run
        LOGGER.warning("Account quota of route %s unreadable; the run is not held for it", route_id, exc_info=True)
        return None
    for account in accounts:
        quota = account.get("quota_reserve") or {}
        if account.get("backend_route_id") == route_id and quota.get("status") == "reserved":
            return quota
    return None


def prepare_configuration(
    assignment: dict[str, Any],
    *,
    cancelled,
    emit,
    poll_interval: float = 2.0,
    refresh_assignment=None,
    replace_unusable_assignment=None,
) -> dict[str, Any]:
    from management_portal.society_service import SocietyServiceUnavailable
    from management_portal.resources.errors import ResourceServiceUnavailable

    emit(f"准备 TAU 账号配置：{assignment['module_id']} → {assignment['backend_route_id']} · {assignment['runtime_options']['service_tier'] or 'standard'}\n")
    if cancelled():
        return {"ready": False, "status": "cancelled"}
    reserved = reserved_account_quota(assignment["backend_route_id"])
    if reserved is not None:
        # The quota reserve closes intake on every Resident of the account: a
        # restart cannot reopen it and a replacement would be closed too.
        raise RuntimeError(
            f"TAU 执行账号 {assignment['backend_route_id']} 的额度已到保留线"
            f"（剩余 {_percent(reserved.get('remaining_percent'))}），新工作已暂停；"
            "未发送用例，也未替换模块 Resident。额度恢复或改用其他账号后重跑。"
        )
    last_phase = ""
    while True:
        if cancelled():
            return {"ready": False, "status": "cancelled"}
        if refresh_assignment is not None:
            assignment = refresh_assignment()
        try:
            status = resident_status(assignment)
            if status["status"] == "stale" and replace_unusable_assignment is not None:
                assignment = replace_unusable_assignment(assignment)
                emit("TAU Resident 已被删除；已保留账号配置并创建新的模块身份。\n")
                continue
            if status["status"] in {"not_ready", "closed"}:
                status = apply_configuration(assignment)
                if status["status"] == "closed" and replace_unusable_assignment is not None:
                    assignment = replace_unusable_assignment(assignment)
                    emit("TAU Resident 已关闭且无法重启；已保留账号配置并创建新的模块身份。\n")
                    continue
        except InstructionError as exc:
            # The same RestartSession still runs, or the Resident is finishing
            # another lifecycle operation (a restart reported completed while
            # its intake and next input are being resumed): wait for it.
            finishing = (exc.details or {}).get("reason") == "resident.session_restart_in_progress"
            if exc.code != "instruction.in_progress" and not finishing:
                raise
            status = {
                "ready": False,
                "status": "migrating",
                "restart": {"phase": "lifecycle_finishing" if finishing else "instruction_in_progress"},
            }
        except (SocietyServiceUnavailable, ResourceServiceUnavailable):
            if last_phase != "service_unavailable":
                emit("TAU 账号配置等待依赖服务恢复；保留当前 Resident 和迁移幂等键。\n")
                last_phase = "service_unavailable"
            time.sleep(poll_interval)
            continue
        if refresh_assignment is not None and refresh_assignment().get("configuration_id") != assignment.get("configuration_id"):
            continue
        if status["ready"]:
            break
        restart = status.get("restart") or {}
        phase = str(restart.get("phase") or status["status"])
        if phase != last_phase:
            emit(f"TAU 账号配置进度：{phase}\n")
            last_phase = phase
        if cancelled():
            return {**status, "ready": False, "status": "cancelled"}
        if status["status"] != "migrating":
            raise RuntimeError(f"TAU 账号配置未完成：{restart.get('error') or status['status']}")
        time.sleep(poll_interval)
    emit("TAU 账号配置已就绪，开始发送测试任务。\n")
    return status
