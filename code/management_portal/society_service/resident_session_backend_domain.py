"""Resident domain responsibilities: session backend."""
from __future__ import annotations

from . import resident_backend, resident_execution_work
from .domain_ports import execution_port
from .resident_lifecycle import LifecycleAttemptFailed
from dataclasses import replace
from management_portal.execution.provider_catalog import AGENT_PROVIDERS, NATIVE_COMMANDS
from management_portal.execution.contracts import ExecutionRequest, SystemInstructions
from management_portal.execution.requests import build_agent_session_request
from pathlib import Path
from typing import Any, Mapping
import hashlib
from management_portal.execution import resident_runtime_settings

from .resident_domain_common import CODEX_COMPACT_TIMEOUT_SECONDS, ResidentExecutionFailed


def _build_backend_session_operation_request(
    *, backend: str, operation: str, actor: str, cwd: Path,
    binding_id: str, correlation_id: str, prompt: str = "",
    source_binding_id: str = "", backend_route_id: str = "",
    runtime_options: dict[str, Any] | None = None,
    agent_id: str = "",
    metadata: Mapping[str, object] | None = None,
    system_instructions: SystemInstructions | None = None,
) -> ExecutionRequest:
    operation_runtime_options = dict(runtime_options or {})
    if backend_route_id:
        operation_runtime_options["allow_backend_route_fallback"] = False
    target_binding_id = str(binding_id or "").strip()
    if not target_binding_id:
        raise ValueError("Backend session operation requires a binding id")
    # A session operation that starts a Resident's thread (restart, account
    # switch, create) must name the Resident: the HATG ISA Harness tool is
    # attached only to Resident executions, and a Codex thread keeps the tools
    # it started with, so a thread started without it never had hatg_instruction.
    binding_resident = target_binding_id.removeprefix("resident:") if target_binding_id.startswith("resident:resident_") else ""
    resident_metadata = {"resident_id": binding_resident} if binding_resident else {}
    # A turn the operation runs (a restart's initialization or handoff turn) is
    # the Resident's own work: it runs as the Resident and its Segments go to
    # the Resident's trace, not to one named after the binding.  Operations
    # without a model turn (release, native fork) keep the binding's name, so
    # they never count as the Resident's latest session.
    named_resident = (
        binding_resident or str(dict(metadata or {}).get("resident_id") or "").strip()
    ) if operation == "turn" else ""
    return build_agent_session_request(
        actor=actor,
        prompt=(
            str(prompt or "").strip()
            or f"Run backend session operation: {operation}"
        ),
        provider=backend,
        operation=operation,
        correlation_id=correlation_id,
        cwd=str(cwd),
        permission_profile="owner",
        runtime_options=operation_runtime_options,
        binding_id=target_binding_id,
        context_source_binding_id=(
            str(source_binding_id or "").strip()
            if operation == "fork"
            else ""
        ),
        backend_route_id=backend_route_id,
        system_instructions=system_instructions,
        metadata={
            **resident_metadata,
            **dict(metadata or {}),
            "business_type": "resident_session_operation",
            "operation": operation,
        },
        agent_id=str(agent_id or "").strip() or named_resident,
        priority=100 if operation == "release" else 0,
    )


def _run_backend_session_operation(
    *,
    backend: str,
    operation: str,
    actor: str,
    cwd: Path,
    prompt: str = "",
    source_binding_id: str = "",
    backend_route_id: str = "",
    binding_id: str = "",
    runtime_options: dict[str, Any] | None = None,
    resident_record: dict[str, Any] | None = None,
    execution_work: dict[str, Any] | None = None,
    wait_for_completion: bool = True,
    operation_id: str = "",
    preparation_id: str = "",
    agent_id: str = "",
    metadata: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    from .resident_execution_domain import _bind_or_resume_resident_execution, _durable_resident_execution_request, _wait_for_resident_execution
    from .resident_lifecycle_domain import _lifecycle_operations
    from .resident_record_domain import _resident_id
    managed_execution_work = bool(resident_record and execution_work)
    if preparation_id:
        if operation != "create" or managed_execution_work or operation_id:
            raise ValueError("Registration preparation must own standalone session creation")
        operation_id = f"{preparation_id}:session-create"
    correlation_id = (
        str((execution_work or {}).get("execution_id") or "")
        if managed_execution_work
        else f"backend-session:{operation}"
    )
    if not correlation_id:
        raise ValueError("Managed Resident session operation requires an Execution ID")
    # Managed preparation already reserved this Execution for the Resident.
    # Its request must keep that Agent identity, including create/fork operations
    # without a model turn. Standalone session operations have their own owner.
    request_agent_id = _resident_id(resident_record or {}) if managed_execution_work else agent_id
    request = _build_backend_session_operation_request(
        backend=backend, operation=operation, actor=actor, cwd=cwd,
        binding_id=binding_id, correlation_id=correlation_id, prompt=prompt,
        source_binding_id=source_binding_id, backend_route_id=backend_route_id,
        runtime_options=runtime_options, agent_id=request_agent_id, metadata=metadata,
    )
    service = execution_port.service()
    if operation_id:
        request = replace(
            request,
            execution_id="execution-" + hashlib.sha256(operation_id.encode()).hexdigest()[:24],
        )
    if preparation_id:
        _lifecycle_operations().stage_preparation_execution(preparation_id, request.to_payload())
    if managed_execution_work:
        if not wait_for_completion:
            raise ValueError(
                "Managed Resident executions require a completed backend operation"
            )
        exact_record = resident_record or {}
        exact_work = execution_work or {}
        request = _durable_resident_execution_request(
            exact_record,
            exact_work,
            lambda: request,
        )
        execution_id = _bind_or_resume_resident_execution(
            service,
            request,
            exact_record,
            exact_work,
        )
        snapshot = _wait_for_resident_execution(
            service,
            execution_id=execution_id,
            actor=actor,
            resident_id=_resident_id(exact_record),

            persist_messages=False,
            timeout_seconds=CODEX_COMPACT_TIMEOUT_SECONDS + 90,
        )
    else:
        execution_id = request.execution_id
        if operation_id:
            # The durable operation owns this identity. Recover an accepted
            # request instead of rebuilding it with a different timestamp.
            try:
                submitted = service.resume(
                    execution_id, actor=actor,
                    correlation_id=correlation_id,
                    service_type="agent_session", provider=backend,
                )
            except KeyError:
                submitted = service.submit(request)
        else:
            submitted = service.submit(request)
        if not wait_for_completion:
            return {
                **(submitted if isinstance(submitted, dict) else {}),
                "execution_id": execution_id,
            }
        snapshot = service.wait(
            execution_id,
            actor=actor,
            timeout_seconds=CODEX_COMPACT_TIMEOUT_SECONDS + 90,
        )
    result = (
        snapshot.get("result")
        if isinstance(snapshot.get("result"), dict)
        else {}
    )
    try:
        if str(result.get("status") or snapshot.get("status") or "") != "completed":
            error = ResidentExecutionFailed(snapshot, f"Backend {operation} failed")
            if (
                operation_id and operation == "release"
                and snapshot.get("status") in {"failed", "cancelled"}
                and result.get("status") == snapshot.get("status")
            ):
                # Release is replay-safe and this attempt has a known outcome.
                # Its durable owner allocates the next attempt; a transport
                # failure must keep the current identity instead.
                raise LifecycleAttemptFailed(str(error)) from error
            raise error
        return result
    finally:
        if not managed_execution_work and not preparation_id:
            service.acknowledge(
                execution_id,
                actor=actor,
            )


def _stage_agent_data_fork(
    resident: dict[str, Any], source_agent_id: str, *, segment_ids: list[str],
) -> None:
    # An ephemeral creation command. After commit the child's trace owns context.
    resident["_pending_agent_data_fork"] = {
        "source_agent_id": source_agent_id,
        "target_agent_id": str(resident["resident_id"]),
        "segment_ids": list(segment_ids),
    }


def _materialize_agent_data_fork(
    target_resident_id: str,
    spec: Mapping[str, Any],
    actor: str,
) -> None:
    source_agent_id = str(spec.get("source_agent_id") or "").strip()
    target_agent_id = str(spec.get("target_agent_id") or "").strip()
    if not source_agent_id or target_agent_id != target_resident_id:
        raise RuntimeError("Resident creation has invalid AgentData fork metadata")
    execution_port.fork_agent_data(
        source_agent_id,
        target_agent_id,
        actor=actor,
        segment_ids=list(spec["segment_ids"]),
    )
    from management_portal.society_service.changes import resident_changed

    resident_changed(target_agent_id, scope="conversation")


def _materialize_queued_agent_data_fork(
    record: Mapping[str, Any],
    job: Mapping[str, Any],
) -> None:
    from .resident_record_domain import _resident_id
    spec = job.get("agent_data_fork")
    if not isinstance(spec, Mapping):
        return
    _materialize_agent_data_fork(
        _resident_id(record),
        spec,
        resident_execution_work.request_actor(job),
    )


def _materialize_pending_agent_data_fork(
    resident: dict[str, Any],
    actor: str,
) -> None:
    from .resident_record_domain import _resident_id
    spec = resident.pop("_pending_agent_data_fork", None)
    if isinstance(spec, Mapping):
        _materialize_agent_data_fork(_resident_id(resident), spec, actor)


def _create_exact_resident_session(
    record: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    """Materialize one Resident's own Provider session from its creation job."""
    from .resident_directory_domain import _has_resident_workspace_authority
    from .resident_execution_domain import _resident_execution_runtime_options
    from .resident_runtime_domain import _resident_working_directory
    creation_kind = str(job.get("operation") or "")
    if creation_kind != "context_fork_create":
        raise ValueError(f"unsupported Resident creation job: {creation_kind}")
    actor = resident_execution_work.request_actor(job)
    _materialize_queued_agent_data_fork(record, job)
    backend = resident_backend.execution_backend(record)
    target_binding_id = resident_backend.execution_binding_id(record)
    cwd = _resident_working_directory(record)
    is_owner = _has_resident_workspace_authority(record, actor)

    definition = AGENT_PROVIDERS[backend]
    if "create" not in definition.operations:
        raise ValueError(f"{definition.label} does not support session creation")
    account = resident_backend.execution_account(record)
    route = str(account.get("backend_route_id") or "")
    options = _resident_execution_runtime_options(record, job, is_owner=is_owner)
    options.update(ephemeral=False, exclude_turns=True, thread_source="user")
    result = _run_backend_session_operation(
        backend=definition.provider, operation="create", actor=actor, cwd=cwd,
        binding_id=target_binding_id,
        backend_route_id=route, runtime_options=options, resident_record=record, execution_work=job,
    )

    created_session_id = resident_backend.session_id(
        result.get("backend_session_id")
    )
    return {
        "id": created_session_id,
        "sessionId": created_session_id,
        "backend_route_id": str(result.get("backend_route_id") or ""),
    }


def _compact_portal_result(
    status: str,
    scope_label: str,
    error: str = "",
    backend_label: str = "Codex",
    detail: str = "",
) -> dict[str, Any]:
    clean_status = str(status or "").strip() or "running"
    if clean_status == "completed":
        title = f"{backend_label} compact completed"
        content = f"{backend_label} compact 已完成。" + (f"{detail}" if detail else "")
    elif clean_status == "queued":
        title = f"{backend_label} compact queued"
        content = f"{backend_label} compact 已加入队列。"
    elif clean_status == "failed":
        title = f"{backend_label} compact failed"
        content = f"{backend_label} compact 失败：`{str(error or '')[:300]}`"
    else:
        title = f"{backend_label} compact"
        content = f"{backend_label} 正在 compact `{scope_label}`。"
    return {
        "handled": True,
        "title": title,
        "content": content,
        "settings": {},
        "updates": {},
        "command_payload": {
            "type": "backend_native_command",
            "command": "compact",
            "status": clean_status,
        },
    }


def _backend_command_result(
    settings: dict[str, Any],
    content: str,
    *,
    title: str,
    updates: dict[str, Any] | None = None,
    command_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "handled": True,
        "title": title,
        "content": content,
        "settings": dict(settings),
        "updates": dict(updates) if isinstance(updates, dict) else {},
        "command_payload": dict(command_payload) if isinstance(command_payload, dict) else {},
    }


def _handle_resident_native_command(
    record: dict[str, Any],
    content: str,
    settings: dict[str, Any],
    *,
    session_id: str,
    cwd: str,
    actor: str,
    can_update_runtime: bool,
    can_manage_permissions: bool,
    scope_label: str,
    updated_at: str,
    token_usage: object,
    goal: object,
) -> dict[str, Any]:
    from management_portal.execution.slash_commands import _token_usage_content
    from .resident_runtime_domain import _resident_runtime_defaults
    backend = resident_backend.execution_backend(record)
    definition = AGENT_PROVIDERS[backend]
    native_result = definition.runtime_adapter().handle_native_command(
            content,
            settings,
            session_id=session_id,
            account_route=lambda: str(resident_backend.execution_account(record).get("backend_route_id") or ""),
            cwd=cwd,
            actor=actor,
            can_update_runtime=can_update_runtime,
            can_manage_permissions=can_manage_permissions,
            scope_label=scope_label,
            updated_at=updated_at,
            token_usage=token_usage,
            goal=goal,
        )
    if native_result is not None:
        return native_result
    text = str(content or "").strip()
    if not text.startswith("/"):
        return {"handled": False}
    parts = text[1:].split()
    command = (parts[0].lower() if parts else "")
    args = parts[1:]
    if command not in NATIVE_COMMANDS:
        return {"handled": False}
    current = dict(settings)
    backend_label = resident_backend.backend_label(record)
    # A command the Provider does not declare is answered here, never sent to the model.
    if command not in definition.native_commands():
        return _backend_command_result(
            current, f"{backend_label} 没有 `/{command}` 命令。", title=f"/{command} unavailable",
        )
    defaults = _resident_runtime_defaults(record)
    default_model = defaults.get("model", "")
    options_adapter = definition.runtime_adapter()
    permission_labels = options_adapter.permission_labels
    permission_preset = str(current.get("permission_preset") or options_adapter.default_permission_preset)
    provider_profile = str(record.get("backend_route_id") or record.get("provider_profile") or definition.default_route)
    current_model = str(current.get("model") or default_model)
    if command == "compact":
        if not session_id:
            return _backend_command_result(
                current, f"{backend_label} 还没有会话，没有可压缩的上下文。", title=f"{backend_label} compact",
            )
        return _backend_command_result(
            current,
            f"{backend_label} compact will run through the Resident queue for `{scope_label}`.",
            title=f"{backend_label} compact",
            command_payload={
                "type": "backend_native_command",
                "backend": backend,
                "command": "compact",
                "session_id": session_id,
            },
        ) | {"native_compact": True}
    if command == "status":
        lines = [f"当前会话：`{scope_label}`", "", f"- Provider: `{definition.label}`"]
        if definition.accepts_routes:
            lines.append(f"- Route: `{provider_profile}`")
        lines.append(f"- Model: `{current_model}`")
        if "reasoning_effort" in definition.runtime_settings:
            effort = str(current.get("reasoning_effort") or defaults.get("reasoning_effort") or "")
            lines.append(f"- Reasoning effort: `{effort or 'default'}`")
        if "permissions" in definition.runtime_settings:
            lines.append(f"- Permissions: `{permission_labels.get(permission_preset, permission_preset)}`")
        # The same token statistics as Codex's /status and /portal-tokens.
        token_text = _token_usage_content(token_usage)
        if token_text:
            lines.extend(["", token_text])
        return _backend_command_result(current, "\n".join(lines), title=f"{backend_label} status")
    if command == "model":
        runtime_catalog = definition.catalog(provider_profile)
        available_models = runtime_catalog["models"]
        if not args:
            return _backend_command_result(
                current,
                f"选择该 {backend_label} session 后续 turn 使用的模型。",
                title=f"{backend_label} model",
                command_payload={
                    "type": "model_picker",
                    "backend": backend,
                    "current": {"model": current_model},
                    "models": sorted(available_models),
                    "efforts": runtime_catalog.get("efforts", []),
                    "efforts_by_model": runtime_catalog.get("efforts_by_model", {}),
                    "default_efforts_by_model": {},
                },
            )
        if not can_update_runtime:
            return _backend_command_result(current, "当前用户不能修改这个会话的模型。", title="Permission denied")
        model = str(args[0] or "").strip()
        if model not in available_models:
            return _backend_command_result(
                current,
                f"模型无效。可选：`{'`、`'.join(sorted(available_models))}`。",
                title="Invalid model",
            )
        model_updates = {"model": model}
        if "reasoning_effort" in definition.runtime_settings:
            efforts = runtime_catalog.get("efforts_by_model", {}).get(model, [])
            effort = args[1] if len(args) > 1 else str(current.get("reasoning_effort") or "")
            if len(args) > 2 or (len(args) > 1 and effort not in efforts):
                return _backend_command_result(current, "该模型不支持指定的 reasoning effort。", title="Invalid effort")
            model_updates["reasoning_effort"] = effort if effort in efforts else ""
        next_settings = resident_runtime_settings.mark_portal_manual_authority(
            resident_runtime_settings.merge_settings(current, model_updates, actor=actor, updated_at=updated_at),
            actor=actor,
            updated_at=updated_at,
        )
        return _backend_command_result(
            next_settings,
            f"{backend_label} model 已更新为 `{model}`，后续 turn 生效。",
            title=f"{backend_label} model updated",
            updates=next_settings,
        )
    if command == "permissions":
        # The Provider's own permission modes, as in the Resident's settings and Codex's /permissions.
        if not args:
            choices = "、".join(f"`{preset}`（{label}）" for preset, label in permission_labels.items())
            return _backend_command_result(
                current,
                f"{backend_label} permissions 当前为 `{permission_labels.get(permission_preset, permission_preset)}`。"
                f"可选：{choices}；用 `/permissions <模式>` 切换，后续 turn 生效。",
                title=f"{backend_label} permissions",
            )
        if not can_manage_permissions:
            return _backend_command_result(
                current, f"当前用户不能修改这个会话的 {backend_label} permissions。", title="Permission denied",
            )
        preset = str(args[0]).strip()
        if len(args) > 1 or preset not in permission_labels:
            return _backend_command_result(
                current, f"权限模式无效。可选：`{'`、`'.join(permission_labels)}`。", title="Invalid permissions",
            )
        next_settings = resident_runtime_settings.mark_portal_manual_authority(
            resident_runtime_settings.merge_settings(current, {"permission_preset": preset}, actor=actor, updated_at=updated_at),
            actor=actor,
            updated_at=updated_at,
        )
        return _backend_command_result(
            next_settings,
            f"{backend_label} permissions 已切换为 `{permission_labels[preset]}`，后续 turn 生效。",
            title=f"{backend_label} permissions updated",
            updates=next_settings,
        )
    # The adapter's ``slash_commands`` cover every command its Provider declares.
    return _backend_command_result(
        current, f"{backend_label} 没有 `/{command}` 命令。", title=f"/{command} unavailable",
    )
