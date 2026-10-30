"""Resident instructions (decision_d19b9909a674)."""
from __future__ import annotations

from typing import Any

from management_portal.hatg.contracts import InstructionError, invalid
from management_portal.hatg.catalog.resident import SETTINGS_FIELDS
from management_portal.hatg.kernel import Invocation
from management_portal.society_service.resident_context.evidence import MAX_SUMMARY_RESIDENTS, SUMMARY_FIELDS

from .common import (
    caller_account,
    lineage_branches,
    lineage_graph_edits,
    lineage_of_resident,
    lineage_resident_nodes,
    lineage_resident_workspaces,
    placement,
    protocol,
    store,
)
from .. import lineage_aggregate




def _family_to_join(lineage_id: str, inv: Invocation) -> str:
    """The Family a Resident this call creates joins, checked before anything is created (decision_44cf587c1721)."""

    from management_portal.society_service import family_familize

    requested = str(inv.argument("family_id") or "")
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=inv.caller)
    try:
        instance = family_familize.family_for_new_resident(lineage, inv.caller, requested)
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    return str((instance or {}).get("id") or "")


def _join_family(lineage_id: str, resident_id: str, instance_id: str, inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service.templates import familize

    if not instance_id or not resident_id:
        return {}
    familize.join_new_resident(lineage_id, resident_id, inv.caller, instance_id)
    return {"family_id": instance_id}


def create_resident(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    family = _family_to_join(lineage_id, inv)
    role_settings, role_provider, role_account, fallback = {}, "", "", None
    requested_role = inv.argument("backend_role") or ("default" if not inv.argument("provider") and not inv.argument("account_id") else "")
    if requested_role:
        from management_portal.execution.account_registrations import role_configuration
        configured = role_configuration(requested_role)
        role_provider = configured["backend"]
        role_account = configured["backend_route_id"]
        role_settings = configured["runtime_settings"]
        fallback = role_settings.get("backend_role_fallback")
    provider = inv.argument("provider") or role_provider or "codex"
    payload: dict[str, Any] = {
        "title": inv.argument("title"),
        "backend": provider,
        **placement(inv, lineage_id),
    }
    account = inv.argument("account_id") or (role_account if provider == role_provider else "") or caller_account(inv, provider)
    if account:
        payload["backend_route_id"] = account
    if inv.argument("settings") or role_settings:
        payload["runtime_settings"] = {**role_settings, **(inv.argument("settings") or {})}
    if inv.argument("workspace_paths"):
        payload["workspace_paths"] = inv.argument("workspace_paths")
    result = lineage_resident_nodes().create_lineage_resident(lineage_id, payload, inv.caller)
    resident = result.get("resident") or {}
    resident_id = str(resident.get("resident_id") or (result.get("node") or {}).get("id") or "")
    return {
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        **_join_family(lineage_id, resident_id, family, inv),
        **({"backend_role_fallback": fallback} if fallback else {}),
    }


def _family_role(resident_id: str) -> str:
    from management_portal.society_service import family_facade
    from management_portal.society_service import resident_record_domain

    record = resident_record_domain.find_resident(resident_id) or {}
    if family_facade.is_facade(record):
        return "facade"
    return "steward" if family_facade.is_steward(record) else ""


def _family_lineage(resident_id: str) -> dict[str, Any]:
    """The Lineage a Family role is looked up in; the caller's access is checked by the instruction itself."""

    return lineage_aggregate.read_lineage_state(lineage_of_resident(resident_id))


def _facade_router(facade_id: str) -> str:
    """A facade never runs; its settings are its Family's Router Resident's (decision_44cf587c1721)."""

    from management_portal.society_service import family_familize

    router = family_familize.router_of_facade(_family_lineage(facade_id), facade_id)
    if not router:
        raise InstructionError("instruction.conflict", "这个 Family 还没有 Router Resident")
    return router


def fork_resident(inv: Invocation) -> dict[str, Any]:
    source = inv.argument("source_resident_id")
    if _family_role(source) == "facade":
        raise InstructionError("instruction.conflict", "Family 门面不能 Fork；请 Fork 具体的成员")
    lineage_id = inv.argument("lineage_id")
    family = _family_to_join(lineage_id, inv)
    fork_mode = inv.argument("fork_mode") or "full"
    if fork_mode == "message" and not inv.argument("message"):
        raise invalid("fork_mode=message 需要 message")
    payload: dict[str, Any] = {
        "source": source,
        "fork_mode": fork_mode,
        **placement(inv, lineage_id),
    }
    for name in ("context_source_resident_id", "message"):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    if inv.argument("title"):
        payload["child_title"] = inv.argument("title")
    if inv.argument("provider"):
        payload["provider"] = inv.argument("provider")
    account = inv.argument("account_id")
    if account:
        payload["backend_route_id"] = account
    if inv.idempotency_key:
        payload["idempotency_key"] = inv.idempotency_key
    result = lineage_branches().fork_lineage_resident(lineage_id, payload, inv.caller)
    resident_id = str((result.get("created_resident") or {}).get("id") or "")
    return {
        "resident_id": resident_id,
        "lineage_id": lineage_id,
        "inheritance_edge_id": str((result.get("inheritance_edge") or {}).get("id") or ""),
        "created": bool(result.get("created", True)),
        **_join_family(lineage_id, resident_id, family, inv),
    }


def promote_seed(inv: Invocation) -> dict[str, Any]:
    seed_id = inv.argument("seed_id")
    lineage_id = inv.argument("lineage_id")
    payload = {"seed_id": seed_id}
    if inv.argument("title"):
        payload["child_title"] = inv.argument("title")
    if inv.argument("purpose"):
        payload["action_purpose"] = inv.argument("purpose")
    result = lineage_branches().promote_seed_node(lineage_id, payload, inv.caller)
    resident = result.get("resident") or {}
    resident_id = str(resident.get("id") or "")
    edge = next(
        (
            item for item in (result.get("lineage") or {}).get("edges") or []
            if item.get("type") == "inheritance" and item.get("target") == resident_id
        ),
        {},
    )
    return {
        "resident_id": resident_id,
        "inheritance_edge_id": str(edge.get("id") or ""),
        "candidate_path": str(result.get("candidate_path") or ""),
    }


def get_resident_initialization(inv: Invocation) -> dict[str, Any]:
    from ..resident_initialization import view
    return view(inv.argument('resident_id'), inv.caller)


def retry_resident_initialization(inv: Invocation) -> dict[str, Any]:
    from ..resident_initialization import retry
    result = retry(inv.argument('resident_id'), inv.caller)
    return {key: result[key] for key in ('resident_id', 'initialization_id', 'status', 'phase',
                                        'candidate_agent_id', 'candidate_binding_id')}


def get_resident(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    return {"resident": protocol().get_resident(resident_id, inv.caller)}


def search_residents(inv: Invocation) -> dict[str, Any]:
    return protocol().search_residents(
        inv.caller,
        q=str(inv.argument("q") or ""),
        resident_ids=[str(item) for item in inv.argument("resident_ids") or []],
        can_send=inv.argument("can_send") is True,
        limit=int(inv.argument("limit") or 20),
    )


def update_resident(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    lineage_id = inv.argument("lineage_id")
    avatar = inv.argument("avatar")
    if inv.argument("title") is None and avatar is None:
        raise invalid("title 与 avatar 至少提供一项")
    if inv.argument("title") is not None:
        title = str(inv.argument("title") or "").strip()
        if not title:
            raise invalid("title 不能为空")
        lineage_resident_nodes().rename_lineage_resident(
            lineage_id,
            {"resident_id": resident_id, "title": title},
            inv.caller,
        )
    saved = None
    if avatar is not None:
        # The source scope guard has already checked the Resident is in lineage_id.
        from management_portal.avatar_store import change_avatar
        from management_portal.resident_avatar import resident_avatar_store

        try:
            saved = change_avatar(resident_avatar_store(), resident_id, avatar, inv.caller)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    resident = protocol().get_resident(resident_id, inv.caller)
    return {"resident": {**resident, "avatar": saved} if saved else resident}


def delete_resident(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    from .. import resident_workspace_domain

    if resident_workspace_domain.retry_deleted_resident(resident_id, inv.caller) is not None:
        return {"resident_id": resident_id}
    lineage_id = inv.argument("lineage_id")
    result = lineage_graph_edits().delete_lineage_graph_objects(
        lineage_id,
        {"resident_ids": [resident_id]},
        inv.caller,
        # This ISA returns identity/Leader facts, not the full graph view.
        include_lineage=False,
    )
    payload = {"resident_id": resident_id}
    if result.get("leader_changed"):
        payload["new_leader_resident_id"] = str(result.get("leader_resident_id") or "")
    return payload


def _discovery():
    from management_portal.society_service.resident_context.service import resident_context

    return resident_context()


def _summary_service_error(exc: Exception) -> InstructionError:
    if isinstance(exc, PermissionError):
        return InstructionError("instruction.forbidden", str(exc) or "调用者无权读取该 Resident 的摘要")
    if isinstance(exc, KeyError):
        return InstructionError("instruction.not_found", str(exc.args[0]) if exc.args else "没有该 Resident 的摘要")
    if isinstance(exc, ValueError):
        return invalid(str(exc))
    return InstructionError("instruction.owner_unavailable", f"Resident 摘要暂时不可用：{exc}", retryable=True)


def get_resident_summaries(inv: Invocation) -> dict[str, Any]:
    """Summaries are read with the caller's own read access; nothing is widened here."""

    resident_ids = list(dict.fromkeys(
        str(value or "").strip().lower()
        for value in inv.argument("resident_ids") or []
        if str(value or "").strip()
    ))
    if not resident_ids:
        raise invalid("resident_ids 不能为空")
    if len(resident_ids) > MAX_SUMMARY_RESIDENTS:
        raise invalid(f"一次最多读取 {MAX_SUMMARY_RESIDENTS} 个 Resident 的摘要")
    fields = [str(name) for name in inv.argument("fields") or [] if str(name or "").strip()]
    unknown = sorted(set(fields) - {*SUMMARY_FIELDS, "pending_requests"})
    if unknown:
        raise invalid(f"未知的摘要字段：{'、'.join(unknown)}")
    try:
        payload = _discovery().contexts(actor=inv.caller, resident_ids=resident_ids)
    except Exception as exc:  # noqa: BLE001 - every failure maps to one instruction error
        raise _summary_service_error(exc) from exc
    rows = {
        str(item.get("resident_id") or ""): item
        for item in payload.get("residents") or []
        if isinstance(item, dict)
    }
    maintenance = inv.argument("include_maintenance") is True
    if maintenance:
        from ..resident_context import work_summary
    summaries: list[dict[str, Any]] = []
    unavailable: list[str] = []
    for resident_id in resident_ids:
        item = rows.get(resident_id)
        summary = item.get("summary") if isinstance(item, dict) and isinstance(item.get("summary"), dict) else None
        written: dict[str, Any] = {}
        if maintenance:
            try:
                written = work_summary.maintenance_view(inv.caller, resident_id)
            except (KeyError, PermissionError):
                written = {}
        if not summary and not written:
            unavailable.append(resident_id)
            continue
        # The maintenance revision and layers must describe the same snapshot;
        # the discovery read may precede a concurrent summary write.
        summary = (written.get("summary") if written else summary) or {}
        item = item or written
        row = {
            "resident_id": resident_id,
            "title": str(item.get("title") or ""),
            "lineage_id": str(item.get("lineage_id") or ""),
            "summary": {name: summary[name] for name in SUMMARY_FIELDS
                        if name in summary and (not fields or name in fields)},
            "pending_requests": list(summary.get("pending_requests") or []),
            "summary_updated_at": str(written.get("updated_at") or item.get("summary_updated_at") or ""),
            "summary_stale": bool(item.get("summary_stale")),
        }
        if fields and "pending_requests" not in fields:
            row.pop("pending_requests")
        if written:
            row.update({key: written[key] for key in ("revision", "source_versions", "missing_message_ids", "writer_id")})
            row.update({key: written[key] for key in ("layers", "maintenance", "consolidation") if key in written})
        summaries.append(row)
    return {"summaries": summaries, "unavailable_resident_ids": unavailable}


def request_resident_summary_rebuild(inv: Invocation) -> dict[str, Any]:
    resident_id = str(inv.argument("resident_id") or "").strip().lower()
    reason = str(inv.argument("reason") or "manual").strip() or "manual"
    try:
        result = _discovery().request_rebuild(actor=inv.caller, resident_id=resident_id, reason=reason)
    except Exception as exc:  # noqa: BLE001 - every failure maps to one instruction error
        raise _summary_service_error(exc) from exc
    return {"resident_id": resident_id, "status": "queued" if result.get("queued") else "not_queued"}


def get_resident_summary_queue(inv: Invocation) -> dict[str, Any]:
    """Job counts for anyone: they carry no summary or evidence."""

    del inv
    try:
        return _discovery().summary_queue()
    except Exception as exc:  # noqa: BLE001
        raise _summary_service_error(exc) from exc


def _summary_writer(inv: Invocation) -> str:
    """A summary job is owned by the configured executor of this workflow."""
    from management_portal.application_targets import summary_resident, ApplicationUnconfigured
    try:
        executor = summary_resident()
    except (KeyError, ApplicationUnconfigured) as exc:
        raise InstructionError("instruction.not_found", str(exc)) from exc
    if executor != inv.caller:
        raise InstructionError("instruction.forbidden", "Only the configured summary executor can claim and submit jobs")
    return inv.caller


def _writer_identity(resident_id: str) -> tuple[str, str]:
    """``<resident>:<provider>:<model>:<effort>`` and account of the Resident writing summaries."""

    from management_portal.society_service import resident_backend
    from management_portal.society_service import resident_runtime_domain

    record = store().resident_record(resident_id)
    settings = resident_runtime_domain.read_resident_runtime_settings(record)
    provider = resident_backend.execution_provider(record)
    try:
        account = str(resident_backend.execution_account(record).get("account_id") or "")
    except Exception:  # noqa: BLE001 - an unresolved account is recorded as unknown
        account = ""
    model = str(settings.get("model") or "")
    effort = str(settings.get("reasoning_effort") or "")
    return f"{resident_id}:{provider}:{model}:{effort}", account


def claim_resident_summary_jobs(inv: Invocation) -> dict[str, Any]:
    holder = _summary_writer(inv)
    limit = inv.argument("limit")
    try:
        return _discovery().claim_summary_jobs(holder, limit=int(limit) if limit else None)
    except Exception as exc:  # noqa: BLE001
        raise _summary_service_error(exc) from exc



def submit_resident_summary(inv: Invocation) -> dict[str, Any]:
    holder = _summary_writer(inv)
    model_version, account_id = _writer_identity(holder)
    try:
        return _discovery().submit_summary(
            holder, str(inv.argument("claim_id") or ""), inv.argument("summary"),
            model_version=model_version, account_id=account_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise _summary_service_error(exc) from exc


def release_resident_summary_job(inv: Invocation) -> dict[str, Any]:
    holder = _summary_writer(inv)
    try:
        return _discovery().release_summary_job(
            holder, str(inv.argument("claim_id") or ""), str(inv.argument("reason") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        raise _summary_service_error(exc) from exc








def _prompt_injection_view(settings: dict[str, Any]) -> dict[str, Any]:
    from management_portal import prompt_injection

    return prompt_injection.describe(settings.get("prompt_injection"), provider=str(settings.get("backend") or ""))


def get_resident_settings(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    if _family_role(resident_id) == "facade":
        resident_id = _facade_router(resident_id)
    result = protocol().get_resident_runtime_settings(resident_id, inv.caller)
    settings = result["runtime_settings"]
    # The same settings object UpdateResidentSettings accepts and returns.
    return {"settings": settings, "prompt_injection": _prompt_injection_view(settings),
            "settings_revision": result.get("settings_revision", ""),
            "application": result.get("application", {})}


def _prompt_injection_change(resident_id: str, caller: str, change: Any) -> dict[str, Any]:
    """The stored prompt injection after a change made on the given preset or the current choices."""
    from management_portal import prompt_injection

    if not isinstance(change, dict):
        raise invalid("changes.prompt_injection 必须是对象")
    unknown = sorted(set(change) - {"preset", "parts"})
    if unknown:
        raise invalid("changes.prompt_injection 不接受字段：" + "、".join(unknown), unknown_fields=unknown)
    current = protocol().get_resident_runtime_settings(resident_id, caller)["runtime_settings"]
    try:
        return prompt_injection.apply_changes(
            current.get("prompt_injection"), preset=str(change.get("preset") or ""), parts=change.get("parts"),
        )
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def update_resident_settings(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - SETTINGS_FIELDS.keys())
    if unknown:
        raise invalid("changes 不接受字段：" + "、".join(unknown), unknown_fields=unknown)
    if not changes:
        raise invalid("changes 不能为空")
    if _family_role(resident_id) == "facade":
        resident_id = _facade_router(resident_id)
    if "prompt_injection" in changes:
        changes["prompt_injection"] = _prompt_injection_change(resident_id, inv.caller, changes["prompt_injection"])
    payload = {"runtime_settings": changes}
    if inv.argument("expected_revision") is not None:
        payload["expected_revision"] = inv.argument("expected_revision")
    result = protocol().update_resident_runtime_settings(resident_id, inv.caller, payload)
    settings = result["runtime_settings"]
    next_turn = result["next_turn_fields"]
    return {"settings": settings, "next_turn_fields": list(next_turn),
            "prompt_injection": _prompt_injection_view(dict(settings)),
            "settings_revision": result.get("settings_revision", ""),
            "application": result.get("application", {})}


def _readable_settings(inv: Invocation) -> tuple[str, dict[str, Any]]:
    resident_id = inv.argument("resident_id")
    if _family_role(resident_id) == "facade":
        resident_id = _facade_router(resident_id)
    result = protocol().get_resident_runtime_settings(resident_id, inv.caller)
    settings = result["runtime_settings"]
    return resident_id, dict(settings)


def preview_prompt_injection(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import resident_execution_domain
    from management_portal.society_service import resident_record_domain

    # Reading the settings checks that the caller may see this Resident's configuration.
    resident_id, _settings = _readable_settings(inv)
    record = resident_record_domain.find_resident(resident_id)
    if not record:
        raise invalid(f"Resident 不存在：{resident_id}")
    try:
        return resident_execution_domain.preview_prompt_injection(
            record, inv.caller, str(inv.argument("turn") or "first"),
        )
    except ValueError as exc:
        raise invalid(str(exc)) from exc


def restart_session(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import resident_domain

    resident_id = inv.argument("resident_id")
    body: dict[str, Any] = {}
    if inv.argument("provider"):
        body["target_provider"] = inv.argument("provider")
    if inv.argument("account_id"):
        body["target_account"] = inv.argument("account_id")
    for name in ("operation", "context_strategy", "active_execution_policy", "model", "reasoning_effort"):
        if inv.argument(name):
            body[name] = inv.argument(name)
    if inv.argument("resume_failed_input") is not None:
        body["resume_failed_input"] = inv.argument("resume_failed_input")
    for name in ("prompt_context", "backend_options", "collaboration_mode", "service_tier", "permission_preset"):
        if inv.argument(name) is not None:
            body[name] = inv.argument(name)
    if inv.idempotency_key:
        body["idempotency_key"] = inv.idempotency_key
    # A new restart supersedes an unfinished one inside restart_resident_session
    # (decision_d19b9909a674); a conflict raised there is another caller's restart.
    result = resident_domain.restart_resident_session(resident_id, inv.caller, body)
    return {
        **result,
        "restart_id": str(result.get("restart_id") or ""),
        "status": str(result.get("status") or ""),
    }


def get_session_restart(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import resident_domain

    result = resident_domain.get_resident_session_restart(inv.argument("resident_id"), inv.caller)
    restart = result.get("restart") or {}
    return {
        **result,
        "restart_id": str(restart.get("restart_id") or ""),
        "status": str(restart.get("status") or "none"),
        "source_session": restart.get("source") or result.get("source") or {},
        "target_session": restart.get("target") or {},
        "operation_id": restart.get("operation_id") or restart.get("restart_id", ""),
        "operation": restart.get("operation", "restart"),
        "phase": restart.get("phase", ""),
        "context_result": restart.get("context_result", ""),
        "actual_binding": result.get("actual_binding") or {},
    }


def cancel_session_restart(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import resident_domain

    result = resident_domain.cancel_resident_session_restart(
        inv.argument("resident_id"), inv.caller, inv.argument("restart_id"),
    )
    return {
        **result,
        "restart_id": str(result.get("restart_id") or inv.argument("restart_id")),
        "status": str(result.get("status") or ""),
    }


def bind_workspace(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    lineage_id = lineage_of_resident(resident_id)
    payload = {"resident_id": resident_id, "path": inv.argument("path")}
    if inv.argument("purpose"):
        payload["purpose"] = inv.argument("purpose")
    result = lineage_resident_workspaces().bind_node_workspace(lineage_id, payload, inv.caller)
    roots = (result.get("workspace") or {}).get("workspaces") or []
    from pathlib import Path

    path = str(Path(inv.argument("path")).resolve())
    binding = next((root for root in roots if root.get("path") == path), None)
    if binding is None:
        raise RuntimeError("绑定完成后无法读取工作区根")
    pending = result.get("mount") or {}
    return {"binding": binding, **{key: pending[key] for key in
            ("workspace_document_pending", "operation_id") if key in pending}}


def unbind_workspace(inv: Invocation) -> dict[str, Any]:
    resident_id = inv.argument("resident_id")
    lineage_id = lineage_of_resident(resident_id)
    payload = {"resident_id": resident_id, "workspace_id": inv.argument("workspace_id")}
    result = lineage_resident_workspaces().unbind_node_workspace(lineage_id, payload, inv.caller)
    workspaces = lineage_resident_workspaces().get_node_workspaces(lineage_id, resident_id, inv.caller)
    bindings = workspaces.get("workspaces") if isinstance(workspaces, dict) else workspaces
    pending = result.get("workspace_binding") or {}
    return {"bindings": bindings or [], **{key: pending[key] for key in
            ("workspace_document_pending", "operation_id") if key in pending}}


def update_resident_work_summary(inv: Invocation) -> dict[str, Any]:
    from ..resident_context import work_summary
    return work_summary.update_resident_work_summary(
        inv.caller, str(inv.argument("resident_id")), inv.argument("expected_revision"),
        summary=inv.argument("summary"), message_ids=inv.argument("message_ids"),
        source_versions=inv.argument("source_versions"),
        consolidation=inv.argument("consolidation"),
    )


def check_resident_work_summary(inv: Invocation) -> dict[str, Any]:
    from ..resident_context import work_summary
    return work_summary.check_resident_work_summary(inv.caller, str(inv.argument("resident_id")), str(inv.argument("message_id")))


def get_family_summary_inputs(inv: Invocation) -> dict[str, Any]:
    from ..resident_context import work_summary
    return work_summary.get_family_summary_inputs(inv.caller, str(inv.argument("resident_id")),
                                                overview_only=bool(inv.argument("overview_only")))


HANDLERS = {
    "UpdateResidentWorkSummary": update_resident_work_summary,
    "CheckResidentWorkSummary": check_resident_work_summary,
    "GetFamilySummaryInputs": get_family_summary_inputs,
    "CreateResident": create_resident,
    "ForkResident": fork_resident,
    "PromoteSeed": promote_seed,
    "GetResident": get_resident,
    "GetResidentInitialization": get_resident_initialization,
    "RetryResidentInitialization": retry_resident_initialization,
    "SearchResidents": search_residents,
    "UpdateResident": update_resident,
    "DeleteResident": delete_resident,
    "GetResidentSummaries": get_resident_summaries,
    "RequestResidentSummaryRebuild": request_resident_summary_rebuild,
    "GetResidentSummaryQueue": get_resident_summary_queue,
    "ClaimResidentSummaryJobs": claim_resident_summary_jobs,
    "SubmitResidentSummary": submit_resident_summary,
    "ReleaseResidentSummaryJob": release_resident_summary_job,
    "GetResidentSettings": get_resident_settings,
    "UpdateResidentSettings": update_resident_settings,
    "PreviewPromptInjection": preview_prompt_injection,
    "RestartSession": restart_session,
    "GetSessionRestart": get_session_restart,
    "CancelSessionRestart": cancel_session_restart,
    "BindWorkspace": bind_workspace,
    "UnbindWorkspace": unbind_workspace,
}
