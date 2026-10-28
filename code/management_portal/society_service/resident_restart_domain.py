"""Resident domain responsibilities: restart."""
from __future__ import annotations

from .resident_restart_handoff import can_preserve as can_preserve_restart_input

from . import (
    resident_backend,
    resident_catalog,
    resident_conversation_projection_domain,
    resident_conversation_stream,
    resident_execution_domain,
    resident_execution_provider,
    resident_execution_work,
    resident_input_queue_domain,
    resident_record_domain,
    resident_runtime_domain,
    resident_session_backend_domain,
    resident_state,
)
from .domain_ports import execution_port
from .resident_access import manage_refusal
from .resident_lifecycle import LifecycleStepPending
from management_common.actor_identity import canonical_actor_id
from management_portal.execution.accounts import (
    resolve_account_configuration,
    runtime_configuration_catalog,
)
from management_portal.execution.contracts import ExecutionRequest
from management_portal.execution.provider_catalog import AGENT_PROVIDERS
from dataclasses import dataclass, replace
from typing import Any, Mapping
import copy
import hashlib
import json
import math
import secrets
import time

from . import resident_domain_common, resident_session_preservation as preservation

SESSION_RESTART_STRATEGIES = frozenset(
    {"preserve", "smart", "native", "compact", "visible", "fresh"}
)
SESSION_RESTART_ACTIVE_EXECUTION_POLICIES = frozenset({"wait", "cancel"})
# Runtime settings a restart request may choose for the target session.
SESSION_RESTART_SETTING_FIELDS = (
    "model", "reasoning_effort", "collaboration_mode", "service_tier", "permission_preset",
    "prompt_context", "backend_options",
)


@dataclass(frozen=True)
class _RestartTarget:
    """The Provider and account route a restart moves the Resident's session to, from its current ones."""

    source_provider: str
    source_route: str
    provider: str
    route: str
    definition: Any
    account: dict[str, Any]

    @property
    def same_route(self) -> bool:
        return self.source_provider == self.provider and self.source_route == self.route


@dataclass(frozen=True)
class _ActiveWork:
    """What a restart does with the Resident's active work before it replaces the session."""

    record: dict[str, Any]
    reassigned_execution_id: str = ""
    preserved_execution_id: str = ""
    preserved_message_id: str = ""
    # The restart waits for the running turn to end (resident_deferred_restart).
    scheduled: dict[str, Any] | None = None


@dataclass(frozen=True)
class _SessionRestart:
    """One admitted RestartSession: what it replaces, where it goes and how context moves there."""

    resident_id: str
    target_resident_id: str
    actor_id: str
    body: dict[str, Any]
    portal_reason: str
    intake_transition_id: str
    request_id: str
    public_request_id: str
    restart_id: str
    deferred: dict[str, Any]
    operation: str
    requested_strategy: str
    strategy: str
    intent: dict[str, Any]
    source: dict[str, Any]
    target: _RestartTarget
    target_runtime_settings: dict[str, Any]
    source_binding: dict[str, Any]
    source_configuration: Any
    failed_input: dict[str, Any]
    resume_message_id: str
    work: _ActiveWork


def _session_restart_provider(value: object) -> str:
    key = str(value or "").strip().lower()
    if key == "provider_api":
        return key
    provider = AGENT_PROVIDERS.get(key)
    if provider is None:
        raise ValueError("Unknown target Agent provider")
    return provider.provider


def _session_restart_source(record: dict[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    has_session = bool(resident_backend.session_id(binding.get("backend_session_id")))
    provider = str(binding.get("provider") or "") if has_session else resident_backend.execution_backend(record)
    account = str(binding.get("backend_route_id") or "") if has_session else str(resident_backend.execution_account(record, binding).get("backend_route_id") or "")
    return {
        "provider": provider, "account_id": account,
        "has_session": has_session,
        "binding_status": "current" if has_session else "unbound",
        "backend_session_id": resident_backend.session_id(binding.get("backend_session_id")) if has_session else "",
        "provider_session_ref": str(binding.get("provider_session_ref") or "") if has_session else "",
        "generation": hashlib.sha256(json.dumps([
            provider, account, binding.get("provider_session_ref"), binding.get("backend_session_id"),
        ], ensure_ascii=False).encode()).hexdigest(),
    }


# How long a new RestartSession waits for the unfinished restart it replaces to be cancelled.
SESSION_RESTART_SUPERSEDE_SECONDS = 180.0


class _SessionRestartSuperseded(Exception):
    """An unfinished restart with other parameters is replaced by the new one."""

    def __init__(self, previous: Mapping[str, Any]) -> None:
        super().__init__("session restart superseded")
        self.previous = dict(previous)


def _pending_session_restart(record: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any] | None:
    if not resident_record_domain.lifecycle_operations().pending(resident_domain_common.record_resident_id(record)):
        return None
    previous = record.get("session_restart") or {}
    if previous.get("status") == "accepted" and previous.get("intent") == intent:
        return copy.deepcopy(previous)
    if previous.get("status") in {"accepted", "cancelling"} and previous.get("restart_id"):
        # decision_d19b9909a674: a new restart replaces an unfinished one.
        raise _SessionRestartSuperseded(previous)
    source = previous.get("source") or {}
    target = previous.get("target") or {}
    route = f"{source.get('account_id') or source.get('provider') or '当前 Session'} → {target.get('account_id') or target.get('provider') or '新 Session'}"
    raise resident_domain_common.ResidentSessionConflictError(
        f"Resident 生命周期操作正在完成：{route}。本次新迁移未执行，请查看当前进度。",
        code="resident.session_restart_in_progress", restart=previous,
    )


def _admitted_session_restart(record: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any] | None:
    """Re-check under the Resident lock: a restart admitted meanwhile by someone else is a conflict here."""
    try:
        return _pending_session_restart(record, intent)
    except _SessionRestartSuperseded as superseded:
        raise resident_domain_common.ResidentSessionConflictError(
            "另一次 Session 重启刚刚开始，本次重启未执行，请查看当前进度后重试。",
            code="resident.session_restart_in_progress", restart=superseded.previous,
        ) from None


def _record_session_restart_phase(resident_id: str, restart_id: str, phase: str) -> None:
    with resident_record_domain.state_lock(resident_id):
        current = resident_record_domain.find_resident(resident_id)
        state = (current or {}).get("session_restart") or {}
        if state.get("restart_id") != restart_id or state.get("status") != "accepted" or state.get("phase") == phase:
            return
        now = resident_domain_common.now_iso()
        current["session_restart"] = {**state, "phase": phase, "phase_started_at": now}
        current["updated_at"] = now
        resident_record_domain.save_resident_record(current)


def _apply_resident_session_target(
    record: dict[str, Any],
    provider: str,
    route: str,
    *,
    runtime_settings: Mapping[str, Any] | None = None,
) -> None:
    from .resident_settings_contract import stage_runtime_settings
    definition = AGENT_PROVIDERS.get(provider)
    record["backend"] = provider
    record["backend_route_id"] = route
    settings = dict(runtime_settings or (definition.runtime_adapter().defaults(definition, {}) if definition else {}))
    settings["backend"] = provider
    # The provider and its configuration change in the same Resident commit.
    # A prepared candidate must not mutate the still-active provider settings.
    stage_runtime_settings(record, settings)


def _session_restart_messages(detail: Mapping[str, Any]) -> list[dict[str, str]]:
    """The user and agent text of a conversation snapshot, for the handoff prompt."""

    roles = {"user": "user", "agent": "assistant"}
    items: list[dict[str, str]] = []
    for item in detail.get("messages") or []:
        role = roles.get(str(item.get("role") or ""))
        content = "\n".join(
            str(part.get("text") or "")
            for part in item.get("content_parts") or []
            if isinstance(part, Mapping) and part.get("type") == "text"
        ).strip()
        if role and content:
            items.append({"role": role, "content": content})
    return items


_COMPACTION_SUMMARY_LIMIT = 40_000


def _previous_compaction_summary(resident_id: str, *, since: str = "") -> str:
    """Text of the compaction that heads the previous session's context.

    A Provider compaction summarises the whole session, including work that
    never became a visible message.  A restart hands it over together with
    the recent visible conversation.  A session from before the Resident's
    context was cleared (``since``) has nothing to hand over.
    """

    try:
        segment = execution_port.latest_context_compaction(resident_id, since=since)
    except Exception:  # noqa: BLE001 - the handoff still carries the visible conversation
        return ""
    content = (segment or {}).get("content")
    history = content.get("replacement_history") if isinstance(content, Mapping) else None
    texts: list[str] = []
    for item in history if isinstance(history, list) else []:
        if not isinstance(item, Mapping) or item.get("type") != "message":
            continue
        parts = item.get("content")
        for part in parts if isinstance(parts, list) else []:
            text = str(part.get("text") or "").strip() if isinstance(part, Mapping) else ""
            if text:
                texts.append(text)
    summary = "\n\n".join(texts)
    # Codex keeps recent user messages ahead of its summary; the summary is last.
    return summary[-_COMPACTION_SUMMARY_LIMIT:]


def _source_session_context(record: dict[str, Any], limit: int = 18) -> str:
    session_id = resident_backend.record_session_id(record)
    if not session_id:
        return "(empty)"
    messages = resident_conversation_projection_domain.resident_conversation_messages(record, limit=limit)
    agent_label = resident_backend.backend_label(record)
    lines = []
    for item in messages[-limit:]:
        role = str(item.get("role") or "")
        author = (
            str(item.get("author_id") or "User")
            if role == "user"
            else agent_label
            if role == "agent"
            else "Portal"
        )
        content = resident_domain_common.protocol_message_text(item)
        if content:
            lines.append(f"{author}: {content}")
    return "\n\n".join(lines) or "(empty)"


def _session_restart_handoff_prompt(
    record: Mapping[str, Any],
    detail: Mapping[str, Any],
    *,
    strategy: str,
) -> str:
    messages = _session_restart_messages(detail)
    message_limit = 36 if strategy == "visible" else 12
    char_limit = 60_000 if strategy == "visible" else 24_000
    selected: list[str] = []
    used = 0
    for item in reversed(messages[-message_limit:]):
        text = str(item.get("content") or "").strip()
        remaining = char_limit - used
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = text[-remaining:]
        selected.append(f"{item.get('role')}: {text}")
        used += len(text)
    selected.reverse()
    native_goal = record.get("codex_goal")
    goal = dict(native_goal) if isinstance(native_goal, Mapping) else {}
    if not goal:
        try:
            goal = resident_record_domain.resident_goal_payload(dict(record))
        except (OSError, ValueError):
            goal = {}
    objective = str(
        goal.get("objective")
        or ""
    ).strip()
    transcript = "\n\n".join(selected)
    if not transcript:
        try:
            transcript = _source_session_context(
                dict(record),
                limit=36 if strategy == "visible" else 12,
            )
        except (OSError, ValueError):
            transcript = ""
    transcript = transcript or "（没有可迁移的可见对话）"
    summary = _previous_compaction_summary(
        str(record.get("resident_id") or ""), since=str(record.get("context_reset_at") or ""),
    )
    summary_section = (
        ["--- 上一 Session 的压缩摘要（Provider 生成，涵盖整个 Session） ---", summary]
        if summary
        else []
    )
    return "\n\n".join(
        [
            "[Portal Session Initialization Context]",
            "当前任务是为同一 Resident 初始化 Provider session 的可见上下文。"
            "以下历史仅作为参考资料。",
            f"稳定 Resident ID：{record.get('resident_id') or ''}",
            f"当前任务：{objective or '未设置'}",
            f"迁移策略：{strategy}",
            "请整理与当前职责有关的事实、进度、决定、未决问题和约束，并确认初始化完成。"
            "本次初始化不派发业务任务，也不启动后台目标。",
            *summary_section,
            "--- 可移植对话记录 ---",
            transcript,
        ]
    )


def _session_restart_runtime_options(
    record: dict[str, Any],
    provider: str,
    actor: str,
    *,
    runtime_settings: Mapping[str, Any] | None = None,
    target_route: str = "",
) -> dict[str, Any]:
    settings = copy.deepcopy(dict(runtime_settings if runtime_settings is not None
                                  else resident_runtime_domain.read_resident_runtime_settings(record)))
    return {
        "runtime_settings": settings,
        "resident_assembly": {"settings_revision": resident_runtime_domain.resident_settings_revision(
            {**record, "backend": provider, "backend_route_id": target_route}, settings,
        )},
        "provider_profile": target_route,
        # The new session is created where the Resident's turns look for it.
        # Without this a Provider that keeps sessions in a per-binding
        # directory (Claude Code on an API route) writes it under the
        # restart's temporary binding, and the next turn cannot resume it.
        "runtime_root": str(resident_record_domain.sessions_path(record) / "backend"),
        "title": str(record.get("title") or "Portal Resident"),
        "is_owner": True,
        "resident_environment": resident_runtime_domain.resident_cli_environment(record) if record.get("lineage_id") else {},
        "ephemeral": False,
        "exclude_turns": True,
        "thread_source": "user",
    }


def _account_session_open_quota(account: Mapping[str, Any]) -> dict[str, Any]:
    try:
        health = execution_port.health()
    except Exception as error:
        raise ValueError("无法读取目标 Backend 账号额度，请刷新后重试") from error
    quota = (
        ((health.get("scheduler") or {}).get("capacity_partitions") or {})
        .get(str(account["capacity_key"]), {})
        .get("quota_reserve")
        or {}
    )
    if quota.get("used_up") is True:
        raise ValueError("目标 Backend 账号额度已用完，不能创建新 Session")
    if quota.get("threshold_enabled") is False:
        return copy.deepcopy(quota)
    remaining = quota.get("remaining")
    if (
        quota.get("status") not in {"available", "reserved"}
        or quota.get("fresh") is False
        or isinstance(remaining, bool)
        or not isinstance(remaining, (int, float))
        or not math.isfinite(float(remaining))
        or float(remaining) <= 0
    ):
        raise ValueError("目标 Backend 账号没有可用额度，不能创建新 Session")
    return copy.deepcopy(quota)


def _release_session_restart_quota_override(step: Mapping[str, Any]) -> None:
    policy = step.get("quota_override")
    if not isinstance(policy, dict) or not policy.get("binding_id"):
        return
    # A restart's binding is used once: its override goes when the restart ends.
    execution_port.forget_binding_quota_overrides([str(policy["binding_id"])])


def get_resident_runtime_route(resident_id: str, actor: str = "") -> dict[str, Any]:

    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor, require_visible=True)
    account = resident_backend.execution_account(target.record)
    return {
        "backend": resident_backend.execution_backend(target.record),
        "account_id": str(account.get("account_id") or ""),
    }


def get_resident_restart_options(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    from . import resident_access
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    actor_id = canonical_actor_id(actor)
    record = target.record
    if not resident_access.can_manage(record, actor_id):
        raise manage_refusal(record)
    source = resident_backend.session_record(record)
    source_snapshot = _session_restart_source(record, source)
    has_context = source_snapshot["has_session"] or _unbound_previous_context(record, source_snapshot)
    catalog = runtime_configuration_catalog()["backends"]
    return {
        "resident_id": target.resident_id,
        "source": source_snapshot,
        "restart": copy.deepcopy(record.get("session_restart") or {}),
        "runtime_settings": resident_runtime_domain.read_resident_runtime_settings(record),
        "choose_runtime_settings": True,
        "preservation_capabilities": preservation.preservation_capabilities(),
        "providers": [{"id": row["provider"], "label": row.get("label", row["provider"]),
                       "operations": row.get("operations", [])} for row in catalog],
        "accounts": {row["provider"]: [{"id": route["backend_route_id"], "account_id": route.get("account_id", ""), "label": route["label"],
                                      "context_options": preservation.context_options(source_snapshot, row["provider"], route["backend_route_id"], has_context=has_context)} for route in row["routes"]]
                     for row in catalog},
        "strategies": [
            {"id": "preserve", "label": "完整保留上下文", "recommended": True},
            {"id": "compact", "label": "精简交接（模型操作）"},
            {"id": "visible", "label": "可见对话交接（模型操作）"},
            {"id": "native", "label": "Provider 原生 Fork"},
            {"id": "fresh", "label": "全新上下文"},
        ],
        "active_job_policies": ["wait", "cancel"],
    }


def get_resident_session_restart(
    resident_id: str,
    actor: str = "",
) -> dict[str, Any]:
    """Return the narrow authoritative migration snapshot for HATG readers."""

    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor,
        require_visible=True,
    )
    return {
        "resident_id": target.resident_id,
        "source": _session_restart_source(
            target.record,
            resident_backend.session_record(target.record),
        ),
        "restart": copy.deepcopy(target.record.get("session_restart") or {}),
        "actual_binding": resident_backend.session_record(target.record),
    }


def restart_resident_session(
    resident_id: str,
    actor: str,
    payload: object,
    *,
    portal_reason: str = "",
    intake_transition_id: str = "",
) -> dict[str, Any]:
    """Select an unbound Provider/account or admit a durable session replacement.

    ``portal_reason`` preserves the origin when a previously admitted restart
    resumes. Ordinary inputs do not trigger session replacement for tool metadata.
    """
    from . import resident_access

    body = payload if isinstance(payload, dict) else {}
    operation, requested_strategy = _requested_restart(body)
    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后重启 session")
    target = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor_id,
        require_visible=True,
    )
    record = target.record
    if not portal_reason and not resident_access.can_manage(record, actor_id):
        raise manage_refusal(record)
    if resident_backend.execution_backend(record) != "provider_api" and not resident_execution_provider.provider_for(
        record
    ).capabilities.supports_session_restart:
        raise ValueError("该 Resident ExecutionProvider 没有可重启 session")
    request_id = str(body.get("idempotency_key") or "")
    with resident_record_domain.state_lock(resident_id):
        record = copy.deepcopy(resident_record_domain.find_resident(resident_id) or record)
        if not portal_reason and not resident_access.can_manage(record, actor_id):
            raise manage_refusal(record)
        previous_restart = record.get("session_restart") or {}
        from .resident_deferred_restart import pending as deferred_restart
        deferred = deferred_restart(record)
        replayed = _replayed_restart(previous_restart, deferred, request_id, body)
        if replayed is not None:
            return replayed
        source_binding = resident_backend.session_record(record)
        source_configuration = preservation.configuration_version(record)
    source = _session_restart_source(record, source_binding)
    source_provider, target_provider = _restart_providers(body, source)
    if requested_strategy not in SESSION_RESTART_STRATEGIES:
        raise ValueError("未知的 session 重启上下文策略")
    active_execution_policy = str(body.get("active_execution_policy") or "wait").strip().lower()
    if active_execution_policy not in SESSION_RESTART_ACTIVE_EXECUTION_POLICIES:
        raise ValueError("active_execution_policy 必须是 wait 或 cancel")
    restart_target = _restart_target(body, source, source_provider, target_provider)
    if operation == "switch_account" and restart_target.same_route:
        return _unchanged_restart(resident_id, operation, source_binding)
    target_runtime_settings = _target_runtime_settings(record, body, restart_target, actor_id)
    strategy, unbound_previous_session = _restart_strategy(record, source, restart_target, requested_strategy)
    intent = _restart_intent(body, operation, source, restart_target, strategy, active_execution_policy)
    failed_input = _failed_input_to_resume(record, body, intent, restart_target)
    pending = _pending_restart(resident_id, actor_id, record, intent)
    if pending:
        return pending
    work = _settle_active_work(
        record, target.resident_id, actor_id, body, source, restart_target, intent,
        active_execution_policy=active_execution_policy, portal_reason=portal_reason,
    )
    if work.scheduled is not None:
        return work.scheduled
    restart = _SessionRestart(
        resident_id=resident_id,
        target_resident_id=target.resident_id,
        actor_id=actor_id,
        body=body,
        portal_reason=portal_reason,
        intake_transition_id=intake_transition_id,
        request_id=request_id,
        # A deferred request advances the same operation, including after a lost
        # handoff response. Its public identity must not become a second restart.
        public_request_id=str(deferred['request_id']) if deferred else request_id,
        restart_id=str(previous_restart['restart_id']) if deferred else f"restart_{secrets.token_hex(12)}",
        deferred=deferred,
        operation=operation,
        requested_strategy=requested_strategy,
        strategy=strategy,
        intent=intent,
        source=source,
        target=restart_target,
        target_runtime_settings=target_runtime_settings,
        source_binding=source_binding,
        source_configuration=source_configuration,
        failed_input=failed_input,
        resume_message_id=str(failed_input.get("message_id") or "") if intent["resume_failed_input"] else "",
        work=work,
    )
    if (restart_target.provider == "provider_api" or strategy == "fresh"
            or (not source["has_session"] and not unbound_previous_session)):
        return _configure_session_target(restart)
    return _admit_session_replacement(restart)


def _requested_restart(body: Mapping[str, Any]) -> tuple[str, str]:
    """The requested operation and context strategy, checked against each other."""

    operation = str(body.get("operation") or "restart")
    if operation not in {"switch_account", "restart"}:
        raise ValueError("operation 必须是 switch_account 或 restart")
    requested_strategy = str(body.get("context_strategy") or "preserve").strip().lower()
    if operation == "switch_account" and requested_strategy != "preserve":
        raise ValueError("switch_account requires context_strategy=preserve")
    if operation == "switch_account" and body.get("resume_failed_input"):
        raise ValueError("账号切换不重派失败输入；请对具体输入单独重试")
    return operation, requested_strategy


def _replayed_restart(
    previous_restart: Mapping[str, Any], deferred: Mapping[str, Any], request_id: str, body: Mapping[str, Any],
) -> dict[str, Any] | None:
    """The restart a repeated request already started; None for a new request."""

    if request_id and deferred.get("request_id") == request_id:
        preservation.check_replay(previous_restart, body)
        return copy.deepcopy(previous_restart)
    if deferred and request_id != (deferred.get("payload") or {}).get("idempotency_key"):
        raise ValueError("已有等待本轮结束的迁移，请先取消原迁移")
    if request_id and previous_restart.get("request_id") == request_id:
        preservation.check_replay(previous_restart, body)
        return copy.deepcopy(previous_restart)
    return None


def _restart_providers(body: Mapping[str, Any], source: Mapping[str, Any]) -> tuple[str, str]:
    source_provider = _session_restart_provider(source["provider"])
    target_provider = _session_restart_provider(
        body.get("target_provider") or source_provider
    )
    if source_provider != target_provider and (
        not isinstance(body.get("model"), str) or not body["model"].strip()
    ):
        raise ValueError("跨 Provider 切换必须明确指定目标 model")
    return source_provider, target_provider


def _restart_target(
    body: Mapping[str, Any], source: Mapping[str, Any], source_provider: str, target_provider: str,
) -> _RestartTarget:
    source_route = source["account_id"]
    requested_route = str(body.get("target_account") or (source_route if source_provider == target_provider else ""))
    if target_provider == "provider_api" and not requested_route:
        raise ValueError("请选择 Model API 账号")
    selected_account = resolve_account_configuration(requested_route if target_provider == "provider_api" else target_provider, requested_route)
    return _RestartTarget(
        source_provider=source_provider,
        source_route=source_route,
        provider=target_provider,
        route=requested_route if target_provider == "provider_api" else str(selected_account.get("backend_route_id") or ""),
        definition=AGENT_PROVIDERS.get(target_provider),
        account=selected_account,
    )


def _unchanged_restart(resident_id: str, operation: str, source_binding: Mapping[str, Any]) -> dict[str, Any]:
    return {"status": "completed", "phase": "unchanged", "no_op": True,
            "operation": operation, "operation_id": "", "restart_id": "", "resident_id": resident_id,
            "context_strategy": "preserve", "context_result": "unchanged",
            "actual_binding": copy.deepcopy(source_binding), "model_operation": False}


def _target_runtime_settings(
    record: dict[str, Any], body: Mapping[str, Any], target: _RestartTarget, actor_id: str,
) -> dict[str, Any]:
    """The runtime settings the Resident has on the target: its current ones reconciled with the request."""

    definition, target_provider, target_route = target.definition, target.provider, target.route
    current_settings = resident_runtime_domain.read_resident_runtime_settings(record)
    if target.source_provider != target_provider:
        # Portal intent survives Provider changes. Provider model/transport
        # parameters are reconciled independently against the destination.
        current_settings = {key: copy.deepcopy(current_settings[key]) for key in (
            "capability_config", "prompt_injection", "agent_harness", "prompt_context", "allow_subagents",
        ) if key in current_settings}
    native_fields = ("collaboration_mode", "service_tier", "permission_preset")
    native_updates = {key: body[key] for key in native_fields if key in body}
    if target_provider == "provider_api":
        from management_portal.execution.resident_configuration import API_RUNTIME_SETTINGS
        if native_updates:
            raise ValueError("Model API 不支持原生会话的 Mode、Speed 或 Permissions")
        if body.get("reasoning_effort"):
            raise ValueError("Model API 推理参数应放在 backend_options.parameters 中")
        settings = {key: value for key, value in current_settings.items()
                    if key in {*API_RUNTIME_SETTINGS, "capability_config", "allow_subagents"}}
        settings.update({key: body[key] for key in ("model", "prompt_context", "backend_options") if key in body})
        target_runtime_settings = resident_runtime_domain.resident_runtime_settings_from_register_payload(
            {**record, "backend": target_provider, "backend_route_id": target_route},
            {"runtime_settings": settings}, actor_id, resident_domain_common.now_iso(),
        )
    else:
        if "prompt_context" in body or "backend_options" in body:
            raise ValueError("迁移中的 prompt_context/backend_options 仅适用于 Model API")
        target_runtime_settings = definition.runtime_adapter().reconcile(definition, target_route, current_settings)
        from management_portal.execution.resident_configuration import normalize_configuration_settings
        target_runtime_settings.update(normalize_configuration_settings(target_runtime_settings, target_provider))
    if definition and (body.get("model") or body.get("reasoning_effort")):
        model, effort = definition.validate_settings(target_route,
            str(body.get("model") or target_runtime_settings.get("model") or ""),
            str(body.get("reasoning_effort") or ""))
        requested_settings = {**target_runtime_settings, "model": model}
        if "reasoning_effort" in body:
            requested_settings["reasoning_effort"] = effort
        target_runtime_settings = definition.runtime_adapter().reconcile(definition, target_route, requested_settings)
    if definition and native_updates:
        checked = definition.runtime_adapter().validate_updates(definition, target_route, target_runtime_settings, native_updates)
        target_runtime_settings = definition.runtime_adapter().reconcile(definition, target_route, {**target_runtime_settings, **checked})
    return target_runtime_settings


def _unbound_previous_context(record: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    # A failed input can leave history before the Provider session was bound.
    # Cleared context never makes an old session eligible for handoff again.
    return source["provider"] == "provider_api" or (not source["has_session"] and bool(
        execution_port.latest_provider_session_ref(
            record["resident_id"], since=str(record.get("context_reset_at") or ""),
        )
    ))


def _restart_strategy(
    record: dict[str, Any],
    source: Mapping[str, Any],
    target: _RestartTarget,
    requested_strategy: str,
) -> tuple[str, bool]:
    """How context moves to the new session, and whether an unbound previous session holds work."""

    target_provider = target.provider
    same_native_route = (
        target.source_provider == target_provider
        and target.definition is not None and "fork" in target.definition.operations
        and target.source_route == target.route
    )
    # A turn that failed before its session was bound still left a Provider
    # session with the Resident's work in it.  Restarting such a Resident hands
    # that work to a new session instead of only changing its configuration.
    # The sessions released when the context was cleared hold no such work.
    unbound_previous_session = _unbound_previous_context(record, source)
    strategy = requested_strategy
    if target_provider == "provider_api" and strategy == "native":
        raise ValueError("Model API 不支持 Provider 原生 Fork")
    if strategy == "smart":
        strategy = "preserve"  # Compatibility never implies a lossy handoff.
    if strategy == "preserve" and (source["has_session"] or unbound_previous_session):
        error = preservation.preserve_route_error(source, target_provider, target.route, has_context=True)
        if error:
            raise ValueError(error)
        if same_native_route and source["has_session"]:
            strategy = "native"
    if target_provider == "provider_api" and strategy in {"compact", "visible"}:
        raise ValueError("Model API does not support an explicit session handoff operation")
    if source["has_session"] and strategy == "native" and not same_native_route:
        raise ValueError(
            "Provider 原生 Fork 只适用于同一 provider/account；请使用智能选择或精简交接"
        )
    if unbound_previous_session and strategy == "native":
        raise ValueError(
            "上一个 Provider session 尚未绑定，不能原生 Fork；请使用智能选择或精简交接"
        )
    return strategy, unbound_previous_session


def _restart_intent(
    body: Mapping[str, Any],
    operation: str,
    source: Mapping[str, Any],
    target: _RestartTarget,
    strategy: str,
    active_execution_policy: str,
) -> dict[str, Any]:
    """What the restart asks for; an unfinished restart with the same intent is the same restart."""

    intent = {
        "operation": operation,
        "source_generation": source["generation"], "target_provider": target.provider,
        "target_account": target.route, "context_strategy": strategy, "active_execution_policy": active_execution_policy,
        "resume_failed_input": body.get("resume_failed_input", False),
        **({"runtime_settings": {key: body[key] for key in SESSION_RESTART_SETTING_FIELDS if key in body}}
           if any(key in body for key in SESSION_RESTART_SETTING_FIELDS) else {}),
    }
    if not isinstance(intent["resume_failed_input"], bool):
        raise ValueError("resume_failed_input 必须是 boolean")
    return intent


def _failed_input_to_resume(
    record: dict[str, Any], body: Mapping[str, Any], intent: Mapping[str, Any], target: _RestartTarget,
) -> dict[str, Any]:
    from .resident_work_recovery import latest_failed_input
    # An explicit opt-out must also suppress an older automatic recovery choice.
    # An omitted choice keeps the default non-retry path free of unrelated inputs.
    if body.get("resume_failed_input") is False or intent["resume_failed_input"] and not target.same_route:
        return latest_failed_input(record)
    return {}


def _pending_restart(
    resident_id: str, actor_id: str, record: dict[str, Any], intent: dict[str, Any],
) -> dict[str, Any] | None:
    """The unfinished restart with this intent, after cancelling one with another intent."""

    try:
        with resident_record_domain.state_lock(resident_id):
            return _pending_session_restart(resident_record_domain.find_resident(resident_id) or record, intent)
    except _SessionRestartSuperseded as superseded:
        _supersede_session_restart(resident_id, actor_id, superseded.previous)
        with resident_record_domain.state_lock(resident_id):
            return _admitted_session_restart(resident_record_domain.find_resident(resident_id) or record, intent)


def _settle_active_work(
    record: dict[str, Any],
    resident_id: str,
    actor_id: str,
    body: dict[str, Any],
    source: dict[str, Any],
    target: _RestartTarget,
    intent: dict[str, Any],
    *,
    active_execution_policy: str,
    portal_reason: str,
) -> _ActiveWork:
    """Keep, move, wait for or cancel the active work, as the restart's policy and the work allow."""

    current_job = resident_execution_work.read(record)
    if not current_job or not resident_execution_work.is_active(current_job):
        return _ActiveWork(record)
    if active_execution_policy == "wait" and can_preserve_restart_input(record, current_job):
        # Intake may already be closed while durable inputs wait for a new
        # session, or admission may be waiting for a safe account. Such
        # inputs have not entered Execution, so session migration
        # can preserve their message/execution identities.
        return _ActiveWork(
            record,
            preserved_execution_id=str(current_job.get("execution_id") or ""),
            preserved_message_id=str(current_job.get("user_message_id") or ""),
        )
    if active_execution_policy == "wait":
        candidate_execution_id = resident_execution_work.submitted_execution_id(current_job)
        can_preserve_queued_execution = (
            not source["has_session"]
            and target.source_provider == target.provider
            and bool(target.route)
            and bool(candidate_execution_id)
            and str(current_job.get("status") or "") != "running"
        )
        if not can_preserve_queued_execution:
            from .resident_deferred_restart import schedule
            return _ActiveWork(record, scheduled=schedule(record, actor_id, body, source, intent, portal_reason=portal_reason))
        execution_port.service().reassign_queued_route(
            candidate_execution_id,
            actor=resident_execution_work.request_actor(current_job),
            backend_route_id=target.route,
            performed_by=actor_id,
        )
        return _ActiveWork(record, reassigned_execution_id=candidate_execution_id)
    resident_execution_domain.request_resident_execution_cancellation(
        resident_id,
        actor_id,
        str(current_job.get("user_message_id") or ""),
    )
    refreshed = resident_record_domain.resolve_resident_runtime_target(
        resident_id,
        actor_id,
        require_visible=True,
    ).record
    current_job = resident_execution_work.read(refreshed)
    if current_job and resident_execution_work.is_active(current_job):
        raise RuntimeError("当前工作正在停止，请稍后重试 session 重启")
    return _ActiveWork(refreshed)


def _is_preserved_held_job(record: dict[str, Any], job: dict[str, Any], execution_id: str, message_id: str) -> bool:
    """Whether ``job`` is still the held input a restart keeps across the session switch."""

    return bool(
        execution_id
        and resident_execution_work.is_active(job)
        and can_preserve_restart_input(record, job)
        and str(job.get("execution_id") or "") == execution_id
        and str(job.get("user_message_id") or "") == message_id
    )


def _check_unchanged_since_admission(
    current: dict[str, Any], restart: _SessionRestart, *, reassigned_execution_id: str = "",
) -> None:
    """Under the Resident lock: the configuration, session and work the restart was admitted for still stand."""

    current_binding = resident_backend.session_record(current, expected_binding=restart.source_binding)
    if preservation.configuration_version(current) != restart.source_configuration:
        raise ValueError("Resident configuration changed before session preparation")
    if bool(current_binding) != bool(restart.source_binding):
        raise ValueError("当前 Session 已变化，请刷新后重试")
    active_now = resident_execution_work.read(current)
    same_held_job = _is_preserved_held_job(
        current, active_now, restart.work.preserved_execution_id, restart.work.preserved_message_id,
    )
    if resident_execution_work.is_active(active_now) and (
        not same_held_job
        and (
            not reassigned_execution_id
            or resident_execution_work.submitted_execution_id(active_now) != reassigned_execution_id
        )
    ):
        raise ValueError("Resident 已开始新的工作，请稍后重试")


def _configure_session_target(restart: _SessionRestart) -> dict[str, Any]:
    """A restart that needs no prepared session only changes the Resident's Provider configuration."""

    from . import resident_access

    resident_id, actor_id, target = restart.resident_id, restart.actor_id, restart.target
    with resident_record_domain.state_lock(resident_id):
        current = copy.deepcopy(resident_record_domain.find_resident(resident_id) or {})
        if not current:
            raise KeyError("resident not found")
        if not restart.portal_reason and not resident_access.can_manage(current, actor_id):
            raise manage_refusal(current)
        previous = current.get("session_restart") or {}
        if restart.request_id and previous.get("request_id") == restart.request_id:
            preservation.check_replay(previous, restart.body)
            return copy.deepcopy(previous)
        pending = _admitted_session_restart(current, restart.intent)
        if pending:
            return pending
        _check_unchanged_since_admission(current, restart, reassigned_execution_id=restart.work.reassigned_execution_id)
        _apply_resident_session_target(
            current,
            target.provider,
            target.route,
            runtime_settings=restart.target_runtime_settings,
        )
        if restart.strategy == "fresh":
            current["session_state"] = "pending_create"
        state = _configured_restart_state(restart)
        current["session_restart"] = state
        current["updated_at"] = state["configured_at"]
        replacement = None
        try:
            if target.provider == "provider_api" or restart.strategy == "fresh":
                replacement = _detach_previous_session(current, restart.source_binding)
            resident_catalog.get_catalog().state_store.transaction(
                lambda: _commit_session_configuration(current, state, restart)
            )
        except Exception:
            if replacement is not None:
                resident_backend.restore_replaced_session(restart.work.record, replacement)
            raise
    if restart.intake_transition_id:
        resident_execution_domain.resume_resident_execution(resident_id)
    return copy.deepcopy(state)


def _configured_restart_state(restart: _SessionRestart) -> dict[str, Any]:
    target, strategy = restart.target, restart.strategy
    reassigned_execution_id = restart.work.reassigned_execution_id
    state = {
        "operation": restart.operation, "operation_id": restart.restart_id,
        "request_signature": preservation.request_signature(restart.body),
        "context_result": "fresh" if strategy == "fresh" else "preserved",
        "model_operation": False,
        "status": "completed", "phase": "completed", "configuration_only": True,
        "restart_id": restart.restart_id, "request_id": restart.public_request_id,
        "resident_id": restart.target_resident_id,
        "source": {"provider": target.source_provider, "account_id": target.source_route},
        "target": {"provider": target.provider, "account_id": target.route},
        "provider": target.provider, "account_id": target.route,
        "previous_session_retained": restart.source["has_session"],
        "context_strategy": strategy,
        "active_execution_reassigned": bool(reassigned_execution_id),
        "execution_id": reassigned_execution_id,
        "configured_by": restart.actor_id, "configured_at": resident_domain_common.now_iso(),
        "resume_message_id": restart.resume_message_id,
        "resume_failed_input": restart.intent["resume_failed_input"],
        "failed_message_id": str(restart.failed_input.get("message_id") or ""),
    }
    if restart.portal_reason:
        state["reason"] = restart.portal_reason
    if restart.deferred:
        state["deferred"] = copy.deepcopy(restart.deferred)
        state["execution_request_id"] = restart.request_id
    return state


def _detach_previous_session(current: dict[str, Any], source_binding: dict[str, Any]) -> dict[str, Any] | None:
    """Release the session a configuration-only restart leaves behind; its paused goal stays paused.

    Returns the replacement to restore when the configuration commit fails.
    """

    replacement = None
    if source_binding:
        resident_backend.delete_session_identity(current, expected_provider_session_ref=str(source_binding.get("provider_session_ref") or ""))
        replacement = {"previous_binding": source_binding, "current_binding": {
            "binding_id": resident_backend.execution_binding_id(current), "provider_session_ref": "", "detached": True}}
    native_goal = current.get("codex_goal")
    if isinstance(native_goal, dict) and native_goal.get("status") == "active":
        current["codex_goal"] = {**native_goal, "status": "paused", "paused_reason": "session_restart"}
    return replacement


def _commit_session_configuration(current: dict[str, Any], state: dict[str, Any], restart: _SessionRestart) -> None:
    resident_id = restart.resident_id
    resident_record_domain.save_resident_record(current, commit_configuration=True)
    if restart.strategy == "fresh":
        execution_port.reset_provider_context(resident_id, actor_ref=restart.actor_id, reset_at=state["configured_at"])
    refreshed = current
    if restart.failed_input:
        from .resident_work_recovery import resume_after_restart, set_recovery_entry
        if not restart.intent["resume_failed_input"]:
            set_recovery_entry(current, str(restart.failed_input["message_id"]), disabled=True)
            resident_record_domain.save_resident_record(current)
        else:
            resume_after_restart(current, state)
            refreshed = resident_record_domain.find_resident(resident_id)
            refreshed["session_restart"] = state
            resident_record_domain.save_resident_record(refreshed)
    if restart.intake_transition_id and refreshed.get("intake_transition_id") == restart.intake_transition_id:
        resident_input_queue_domain.commit_resident_intake_state(refreshed, resident_state.INTAKE_OPEN)


def _admit_session_replacement(restart: _SessionRestart) -> dict[str, Any]:
    """Prepare a new session as a lifecycle operation; the Resident switches to it when it is ready."""

    resident_id, record = restart.resident_id, restart.work.record
    prepared_binding_id = f"session-restart:{restart.restart_id}"
    quota_override: dict[str, Any] = {}
    quota_admission: dict[str, Any] = {}
    if restart.target.account.get("quota_protection_enabled") is True:
        quota_admission = _account_session_open_quota(restart.target.account)
        quota_override = {
            "binding_id": prepared_binding_id,
            "revision": 1,
            "enabled": True,
        }
    prompt = ""
    if restart.strategy in {"compact", "visible"}:
        prompt = _session_restart_handoff_prompt(
            record,
            resident_conversation_stream.get_repository().snapshot(restart.target_resident_id, history_limit=36),
            strategy=restart.strategy,
        )
    request = _session_operation_request(restart, prompt, prepared_binding_id)
    state = _accepted_restart_state(restart, request, quota_admission)
    with resident_record_domain.state_lock(resident_id):
        current = copy.deepcopy(resident_record_domain.find_resident(resident_id) or {})
        if not current:
            raise KeyError("resident not found")
        pending = _admitted_session_restart(current, restart.intent)
        if pending:
            return pending
        _check_unchanged_since_admission(current, restart)
        if restart.strategy == "preserve":
            request = replace(request, metadata={**request.metadata,
                "fork_context_input": preservation.canonical_seed(current, restart.source_binding)})

        def commit():
            transition_id = restart.intake_transition_id
            if current.get("intake_open") is not False:
                resident_input_queue_domain.commit_resident_intake_state(current, resident_state.INTAKE_CLOSED)
                current.update(resident_record_domain.find_resident(resident_id))
                transition_id = str(current.get("intake_transition_id") or "")
            current["session_restart"] = state
            resident_record_domain.save_resident_record(current)
            resident_record_domain.lifecycle_operations().enqueue(resident_id, [{
                "kind": "session_restart", "resident_id": resident_id,
                "request": request.to_payload(), "state": state,
                "record": copy.deepcopy(current), "source_binding": restart.source_binding,
                "prepared_binding_id": prepared_binding_id,
                "intake_transition_id": transition_id,
                "quota_override": quota_override,
            }, {"kind": "execution_ack", "execution_id": request.execution_id, "actor": restart.actor_id}])
        resident_catalog.get_catalog().state_store.transaction(commit)
    return copy.deepcopy(state)


def _session_operation_request(restart: _SessionRestart, prompt: str, prepared_binding_id: str) -> ExecutionRequest:
    """The Execution that prepares the new session: a native Fork, a handoff turn or a plain creation."""

    record, strategy = restart.work.record, restart.strategy
    target_provider, target_route = restart.target.provider, restart.target.route
    injection_record = {**record, "backend": target_provider, "backend_route_id": target_route}
    return resident_session_backend_domain.build_backend_session_operation_request(
        backend=target_provider, operation=("fork" if strategy == "native" else
                                           "turn" if strategy in {"compact", "visible"} else "create"),
        actor=restart.actor_id, cwd=resident_runtime_domain.resident_working_directory(record), prompt=prompt,
        binding_id=prepared_binding_id, correlation_id=restart.restart_id,
        source_binding_id=resident_backend.execution_binding_id(record) if strategy == "native" else "",
        backend_route_id=target_route,
        runtime_options=_session_restart_runtime_options(
            record,
            target_provider,
            restart.actor_id,
            runtime_settings=restart.target_runtime_settings,
            target_route=target_route,
        ),
        # The prepared binding does not name the Resident, and the new thread
        # keeps the tools it starts with: without this it never has hatg_instruction.
        metadata={"resident_id": restart.target_resident_id, "preserve_session_context": strategy in {"native", "preserve"},
                  "expected_source_binding": copy.deepcopy(restart.source_binding)},
        system_instructions=(
            resident_execution_domain.resident_system_instructions(injection_record, {}, settings=restart.target_runtime_settings)
            if strategy != "native" else None
        ),
    )


def _accepted_restart_state(
    restart: _SessionRestart, request: ExecutionRequest, quota_admission: dict[str, Any],
) -> dict[str, Any]:
    target, strategy = restart.target, restart.strategy
    state = {
        "operation": restart.operation, "operation_id": restart.restart_id,
        "request_signature": preservation.request_signature(restart.body),
        "source_configuration": restart.source_configuration,
        "model_operation": strategy in {"compact", "visible"},
        "context_result": "pending",
        "status": "accepted", "phase": "accepted", "restart_id": restart.restart_id, "request_id": restart.public_request_id,
        "intent": restart.intent, "execution_id": request.execution_id, "submitted_by": restart.actor_id,
        "resident_id": restart.target_resident_id, "requested_strategy": restart.requested_strategy,
        "context_strategy": strategy, "submitted_at": resident_domain_common.now_iso(),
        "source": {"provider": target.source_provider, "account_id": target.source_route},
        "target": {"provider": target.provider, "account_id": target.route},
        "target_runtime_settings": copy.deepcopy(restart.target_runtime_settings),
        "quota_admission": copy.deepcopy(quota_admission),
        "previous_session_retained": True,
        "resume_message_id": restart.resume_message_id,
        "resume_failed_input": restart.intent["resume_failed_input"],
        "failed_message_id": str(restart.failed_input.get("message_id") or ""),
    }
    if restart.portal_reason:
        state["reason"] = restart.portal_reason
    if restart.deferred:
        state["deferred"] = copy.deepcopy(restart.deferred)
        state["execution_request_id"] = restart.request_id
    if restart.work.preserved_execution_id:
        state["preserved_held_input"] = {
            "execution_id": restart.work.preserved_execution_id,
            "message_id": restart.work.preserved_message_id,
        }
    return state


def resume_resident_session_restart(step: dict[str, Any]) -> None:
    resident_id, state = step["resident_id"], step["state"]
    saved = (resident_record_domain.find_resident(resident_id) or {}).get("session_restart") or {}
    if saved.get("restart_id") != state["restart_id"]:
        raise ValueError("Resident session restart identity changed")
    if saved.get("status") in {"completed", "failed", "cancelled"}:
        _release_session_restart_quota_override(step)
        resident_backend.discard_prepared_session(step["prepared_binding_id"])
        return
    request = ExecutionRequest.from_payload(step["request"])
    service = execution_port.service()
    cancel_requested = saved.get("status") == "cancelling"
    if cancel_requested:
        try:
            snapshot = service.resume(
                request.execution_id, actor=request.actor,
                correlation_id=request.correlation_id, service_type="agent_session", provider=request.provider,
            )
        except KeyError:
            snapshot = {"status": "cancelled", "result": {"status": "cancelled"}}
        if snapshot.get("status") not in {"completed", "failed", "cancelled"}:
            snapshot = service.cancel(
                request.execution_id,
                actor=request.actor,
                reason="resident_session_restart_cancelled",
                performed_by=str(saved.get("cancelled_by") or request.actor),
            )
    else:
        quota_override = step.get("quota_override")
        if isinstance(quota_override, dict) and quota_override:
            execution_port.configure_binding_quota_overrides([quota_override])
        try:
            snapshot = service.resume(
                request.execution_id, actor=request.actor,
                correlation_id=request.correlation_id, service_type="agent_session", provider=request.provider,
            )
        except KeyError:
            try:
                snapshot = service.submit(request)
            except (ValueError, PermissionError) as error:
                snapshot = {"status": "failed", "result": {"status": "failed", "error": str(error)}}
    if snapshot.get("status") not in {"completed", "failed", "cancelled"}:
        phase = "cancelling" if cancel_requested else "queued"
        if snapshot.get("status") in {"claimed", "running"}:
            prepared = resident_backend.session_record_for_binding(step["prepared_binding_id"])
            phase = ("initializing" if state.get("model_operation") else "importing_context") if prepared.get("backend_session_id") else "creating_session"
        else:
            reason = (snapshot.get("scheduler") or {}).get("queue_reason")
            if reason in {"quota_reserve", "capacity", "dispatch_interval", "session_binding"}:
                phase = f"waiting_{reason}"
        _record_session_restart_phase(resident_id, state["restart_id"], phase)
        raise LifecycleStepPending()
    result = snapshot.get("result") or {}
    failure = ""
    if not cancel_requested and (
        snapshot.get("status") != "completed"
        or result.get("status", "completed") != "completed"
    ):
        failure = str(resident_domain_common.ResidentExecutionFailed(snapshot, "Session initialization failed"))
    prepared_id = step["prepared_binding_id"]
    _record_session_restart_phase(resident_id, state["restart_id"], "switching" if not failure else "failed")
    prepared = resident_backend.session_record_for_binding(prepared_id)
    replacement = None
    with resident_record_domain.state_lock(resident_id):
        current = copy.deepcopy(resident_record_domain.find_resident(resident_id) or {})
        if (current.get("session_restart") or {}).get("restart_id") != state["restart_id"]:
            raise ValueError("Resident session restart identity changed")
        current_restart = current.get("session_restart") or {}
        cancel_requested = cancel_requested or current_restart.get("status") == "cancelling"
        target_provider, target_route = state["target"]["provider"], state["target"]["account_id"]
        same_held_job = False
        if not failure and not cancel_requested:
            try:
                if (state.get("source_configuration") and
                        preservation.configuration_version(current) != state["source_configuration"]):
                    raise ValueError("Resident configuration changed while preparing candidate session")
                active_now = resident_execution_work.read(current)
                preserved_held_input = state.get("preserved_held_input") or {}
                same_held_job = _is_preserved_held_job(
                    current, active_now,
                    str(preserved_held_input.get("execution_id") or ""),
                    str(preserved_held_input.get("message_id") or ""),
                )
                if resident_execution_work.is_active(active_now) and not same_held_job:
                    raise ValueError("Resident 已开始新的工作，不能替换其 Session")
                if prepared.get("backend_session_id"):
                    replacement = resident_backend.replace_with_prepared_session(current, prepared_id, expected_binding=step["source_binding"])
                else:
                    binding = resident_backend.session_record(current)
                    if (
                        not result.get("backend_session_id")
                        or binding.get("backend_session_id") != result["backend_session_id"]
                        or binding.get("provider") != target_provider
                        or str(binding.get("backend_route_id") or "") != target_route
                    ):
                        raise RuntimeError("新 Provider session 缺少可提升的持久 binding")
                    replacement = {"previous_binding": step["source_binding"], "current_binding": binding}
            except (ValueError, RuntimeError, KeyError) as error:
                if execution_port.is_unavailable(error):
                    raise
                failure = str(error)
        if not failure and not cancel_requested:
            _apply_resident_session_target(
                current,
                target_provider,
                target_route,
                runtime_settings=(
                    state.get("target_runtime_settings")
                    if isinstance(state.get("target_runtime_settings"), dict)
                    else None
                ),
            )
            native_goal = current.get("codex_goal")
            if state["context_strategy"] == "fresh":
                execution_port.reset_provider_context(resident_id, actor_ref=request.actor, reset_at=state["submitted_at"])
                current.pop("context_compaction_policy", None)
                current.pop("context_compaction_state", None)
            if isinstance(native_goal, dict) and native_goal.get("status") == "active":
                current["codex_goal"] = {**native_goal, "status": "paused", "paused_reason": "session_restart"}
        next_state = {
            **state,
            "status": "cancelled" if cancel_requested else "failed" if failure else "completed",
            "phase": "cancelled" if cancel_requested else "failed" if failure else "completed",
            "provider": target_provider, "account_id": target_route,
            "restarted_by": request.actor, "restarted_at": resident_domain_common.now_iso(),
            "context_result": ("unchanged" if failure or cancel_requested else
                               "fresh" if state["context_strategy"] == "fresh" else
                               "summarized" if state.get("model_operation") else "preserved"),
        }
        if cancel_requested:
            next_state["cancelled_by"] = str(current_restart.get("cancelled_by") or request.actor)
            next_state["cancelled_at"] = str(current_restart.get("cancelled_at") or resident_domain_common.now_iso())
            if current_restart.get("cancelled_reason"):
                next_state["cancelled_reason"] = str(current_restart["cancelled_reason"])
        if failure:
            next_state["error"] = failure
        def commit():
            current["session_restart"] = next_state
            current["updated_at"] = resident_domain_common.now_iso()
            resident_record_domain.save_resident_record(current, commit_configuration=not failure and not cancel_requested)
            if step["intake_transition_id"] and current.get("intake_transition_id") == step["intake_transition_id"]:
                resident_input_queue_domain.commit_resident_intake_state(current, resident_state.INTAKE_OPEN)
            if not failure and not cancel_requested:
                from .resident_work_recovery import resume_after_restart, set_recovery_entry
                if state.get("failed_message_id") and state.get("resume_failed_input") is False:
                    set_recovery_entry(current, state["failed_message_id"], disabled=True)
                    resident_record_domain.save_resident_record(current)
                elif state.get("resume_failed_input"):
                    resume_after_restart(current, next_state)
                    refreshed = resident_record_domain.find_resident(resident_id)
                    refreshed["session_restart"] = next_state
                    resident_record_domain.save_resident_record(refreshed)
        try:
            resident_catalog.get_catalog().state_store.transaction(commit)
        except Exception:
            if replacement is not None:
                resident_backend.restore_replaced_session(step["record"], replacement)
            raise
    resident_backend.discard_prepared_session(prepared_id)
    _release_session_restart_quota_override(step)


def _supersede_session_restart(resident_id: str, actor_id: str, previous: Mapping[str, Any]) -> None:
    """Cancel the unfinished restart a new RestartSession replaces, then wait until its operation has ended.

    The Lineage coordinator finishes the cancelled operation (it cancels the restart's execution, keeps the
    source session and reopens intake); only then can the new restart be admitted.
    """

    _cancel_session_restart(resident_id, actor_id, str(previous.get("restart_id") or ""), reason="superseded")
    deadline = time.monotonic() + SESSION_RESTART_SUPERSEDE_SECONDS
    while resident_record_domain.lifecycle_operations().pending(resident_id):
        if time.monotonic() >= deadline:
            raise resident_domain_common.ResidentSessionConflictError(
                "上一次 Session 重启仍在取消，本次重启未执行，请稍后重试。",
                code="resident.session_restart_in_progress", restart=dict(previous),
            )
        time.sleep(0.25)


def cancel_resident_session_restart(
    resident_id: str,
    actor: str,
    restart_id: str,
) -> dict[str, Any]:
    """Cancel one accepted Session migration without consulting quota."""
    from . import resident_access

    actor_id = canonical_actor_id(actor)
    if not actor_id:
        raise ValueError("需要登录后取消 Session 迁移")
    target = resident_record_domain.resolve_resident_runtime_target(resident_id, actor_id, require_visible=True)
    if not resident_access.can_manage(target.record, actor_id):
        raise manage_refusal(target.record)
    return _cancel_session_restart(target.resident_id, actor_id, restart_id)


def _cancel_session_restart(
    resident_id: str,
    actor_id: str,
    restart_id: str,
    *,
    reason: str = "",
) -> dict[str, Any]:
    from . import resident_access

    expected_restart_id = str(restart_id or "").strip()
    if not expected_restart_id:
        raise ValueError("restart_id required")
    with resident_record_domain.state_lock(resident_id):
        current = copy.deepcopy(resident_record_domain.find_resident(resident_id) or {})
        if not current:
            raise KeyError("resident not found")
        if not resident_access.can_manage(current, actor_id):
            raise manage_refusal(current)
        state = copy.deepcopy(current.get("session_restart") or {})
        if state.get("restart_id") != expected_restart_id:
            raise resident_domain_common.ResidentSessionConflictError(
                "Session 迁移记录已变化，请刷新后重试。",
                code="resident.session_restart_changed",
                restart=state,
            )
        from .resident_deferred_restart import cancel as cancel_deferred
        cancelled = cancel_deferred(current, expected_restart_id)
        if cancelled is not None:
            return cancelled
        if state.get("status") == "cancelled":
            return state
        if state.get("status") not in {"accepted", "cancelling"}:
            raise ValueError("当前没有可取消的 Session 迁移")
        if state.get("status") == "accepted":
            now = resident_domain_common.now_iso()
            state = {
                **state,
                "status": "cancelling",
                "phase": "cancelling",
                "cancelled_by": actor_id,
                "cancelled_at": now,
                **({"cancelled_reason": reason} if reason else {}),
            }
            current["session_restart"] = state
            current["updated_at"] = now
            resident_record_domain.save_resident_record(current)
    execution_id = str(state.get("execution_id") or "")
    if execution_id:
        try:
            execution_port.service().cancel(
                execution_id,
                actor=str(state.get("submitted_by") or actor_id),
                reason="resident_session_restart_cancelled",
                performed_by=actor_id,
            )
        except KeyError:
            pass
    return copy.deepcopy(state)
