"""Codex's runtime settings: its model catalog, configuration, CLI overrides and native slash commands.

CodexOptions and CodexAccounts use it to resolve a Resident's settings for
the Codex app-server and to answer /status, /goal, /plan, /model, /fast,
/permissions and /compact in a Codex session.
"""
from __future__ import annotations

import os
import time
from copy import deepcopy
from typing import Any
from .provider_catalog import NATIVE_COMMANDS
from .resident_runtime_settings import PERMISSION_PRESETS, _apply_permission_preset, _clean_effort, _clean_model, _clean_service_tier, _permission_preset, _permission_preset_id, mark_portal_manual_authority, merge_settings, normalize_settings, with_permission_preset
from .slash_commands import _result, _split_command, _token_usage_content, parse_goal_command


BUILTIN_PORTAL_DEFAULT_MODEL = "gpt-6.1-sol"


BUILTIN_PORTAL_DEFAULT_REASONING_EFFORT = "high"


PORTAL_DEFAULT_MODEL = (
    os.getenv("PORTAL_CODEX_DEFAULT_MODEL", BUILTIN_PORTAL_DEFAULT_MODEL).strip()
    or BUILTIN_PORTAL_DEFAULT_MODEL
)


PORTAL_DEFAULT_REASONING_EFFORT = (
    os.getenv(
        "PORTAL_CODEX_DEFAULT_REASONING_EFFORT",
        BUILTIN_PORTAL_DEFAULT_REASONING_EFFORT,
    ).strip().lower()
    or BUILTIN_PORTAL_DEFAULT_REASONING_EFFORT
)


def codex_runtime_catalog(*, account_id: str = "") -> dict[str, Any]:
    from management_portal.execution import get_execution_service

    catalog = deepcopy(get_execution_service().runtime_catalog(account_id))
    if not catalog.get("models"):
        raise ValueError("当前账号的 Provider 目录没有可用模型")
    return catalog


def resolve_codex_runtime_catalog(provider_catalog: dict[str, Any]) -> dict[str, Any]:
    """Resolve the public catalog at the Codex account adapter boundary."""
    catalog = deepcopy(provider_catalog)
    defaults = _resolve_codex_runtime_configuration(
        effective_codex_runtime_settings({}),
        catalog,
    )
    # Empty effort in a Portal form means the same resolved default as execution,
    # not the Provider's model/list recommendation. Resolve against the raw
    # catalog so models which cannot use the Portal default keep their fallback.
    effective_default_efforts = {
        model: _resolve_codex_runtime_configuration(
            {"model": model}, catalog,
        )["reasoning_effort"]
        for model in catalog.get("models", [])
    }
    catalog.update({
        "defaults_source": (
            "portal"
            if defaults["model"] == PORTAL_DEFAULT_MODEL
            and defaults["reasoning_effort"]
            == PORTAL_DEFAULT_REASONING_EFFORT
            else "provider_account"
        ),
        "default_model": defaults["model"],
        "default_reasoning_effort": defaults["reasoning_effort"],
        "default_efforts_by_model": effective_default_efforts,
    })
    return catalog


CODEX_PROVIDER_CONFIGURATION_FIELDS = (
    "model",
    "reasoning_effort",
    "service_tier",
)


def _catalog_service_tier_ids(catalog: dict[str, Any], model: str) -> list[str]:
    tiers = (catalog.get("service_tiers_by_model") or {}).get(model, [])
    result: list[str] = []
    for item in tiers if isinstance(tiers, list) else []:
        value = item.get("id") if isinstance(item, dict) else item
        clean = _clean_service_tier(value)
        if clean and clean not in result:
            result.append(clean)
    return result


def _resolve_codex_runtime_configuration(
    settings: object,
    catalog: dict[str, Any],
    *,
    explicit_fields: object = (),
) -> dict[str, Any]:
    """Resolve one account/model/effort/speed tuple against one Provider catalog."""

    requested = normalize_settings(settings)
    resolved = effective_codex_runtime_settings(requested)
    explicit = {
        str(field)
        for field in (explicit_fields or ())
        if str(field) in CODEX_PROVIDER_CONFIGURATION_FIELDS and requested.get(str(field))
    }
    models = [
        str(model).strip()
        for model in catalog.get("models", [])
        if str(model).strip()
    ]
    if not models:
        raise ValueError("当前账号的 Provider 目录没有可用模型")

    requested_model = str(resolved.get("model") or "").strip()
    if requested_model not in models:
        if "model" in explicit:
            raise ValueError(
                f"当前账号的 Provider 目录未列出 {requested_model}"
            )
        provider_default = str(catalog.get("default_model") or "").strip()
        requested_model = provider_default if provider_default in models else models[0]
    resolved["model"] = requested_model

    efforts_by_model = catalog.get("efforts_by_model") or {}
    efforts = [
        str(effort).strip().lower()
        for effort in efforts_by_model.get(requested_model, [])
        if str(effort).strip()
    ]
    requested_effort = str(
        resolved.get("reasoning_effort") or ""
    ).strip().lower()
    if efforts and requested_effort not in efforts:
        if "reasoning_effort" in explicit:
            raise ValueError(
                f"{requested_model} 不支持 reasoning effort "
                f"{requested_effort}；可选：{' / '.join(efforts)}"
            )
        model_default = str(
            (catalog.get("default_efforts_by_model") or {}).get(
                requested_model
            )
            or (
                catalog.get("default_reasoning_effort")
                if requested_model == str(catalog.get("default_model") or "")
                else ""
            )
            or ""
        ).strip().lower()
        requested_effort = (
            model_default if model_default in efforts else efforts[0]
        )
    elif not efforts:
        if "reasoning_effort" in explicit and requested_effort:
            raise ValueError(
                f"{requested_model} 未声明可用 reasoning effort"
            )
        requested_effort = ""
    resolved["reasoning_effort"] = requested_effort

    requested_tier = _clean_service_tier(resolved.get("service_tier"))
    tier_ids = _catalog_service_tier_ids(catalog, requested_model)
    if requested_tier and requested_tier not in tier_ids:
        if "service_tier" in explicit:
            available = " / ".join(tier_ids) or "1x"
            raise ValueError(
                f"{requested_model} 不支持 speed {requested_tier}；可选：{available}"
            )
        requested_tier = ""
    resolved["service_tier"] = requested_tier
    return resolved


def resolve_codex_runtime_configuration(
    settings: object,
    *,
    account_id: str,
    explicit_fields: object = (),
) -> dict[str, Any]:
    return _resolve_codex_runtime_configuration(
        settings,
        codex_runtime_catalog(account_id=account_id),
        explicit_fields=explicit_fields,
    )


def validate_codex_runtime_updates(
    current: object, updates: dict[str, Any], *, account_id: str = "",
) -> dict[str, Any]:
    checked = dict(updates)
    candidate = merge_settings(current, checked)
    if not set(checked).intersection(CODEX_PROVIDER_CONFIGURATION_FIELDS):
        # Harness and local controls are independent of the selected model.
        # Keep existing settings until model validation or actual execution.
        return checked
    before = effective_codex_runtime_settings(current)
    resolved = resolve_codex_runtime_configuration(
        candidate,
        account_id=account_id,
        explicit_fields=set(checked) & set(CODEX_PROVIDER_CONFIGURATION_FIELDS),
    )
    reconciled = any(
        resolved[field] != before[field]
        for field in CODEX_PROVIDER_CONFIGURATION_FIELDS
    )
    for field in CODEX_PROVIDER_CONFIGURATION_FIELDS:
        if field in checked or reconciled:
            checked[field] = resolved[field]
    return checked


def codex_cli_overrides(settings: object) -> dict[str, str]:
    data = effective_codex_runtime_settings(settings)
    return {
        "model": str(data.get("model") or ""),
        "reasoning_effort": str(data.get("reasoning_effort") or ""),
        "service_tier": str(data.get("service_tier") or ""),
        "collaboration_mode": str(data.get("collaboration_mode") or "default"),
        "permissions": str(data.get("permissions") or ""),
        "approvals_reviewer": str(data.get("approvals_reviewer") or ""),
        "approval_policy": str(data.get("approval_policy") or ""),
        "sandbox": str(data.get("sandbox") or ""),
        "permission_preset": str(data.get("permission_preset") or ""),
    }


def effective_codex_runtime_settings(settings: object) -> dict[str, Any]:
    data = normalize_settings(settings)
    if not data.get("model"):
        data["model"] = _clean_model(PORTAL_DEFAULT_MODEL)
    if not data.get("reasoning_effort"):
        data["reasoning_effort"] = _clean_effort(PORTAL_DEFAULT_REASONING_EFFORT)
    return with_permission_preset(data)


def _codex_model_command(
    args: list[str],
    current: dict[str, Any],
    session_id: str,
    cwd: str,
    actor: str,
    can_update: bool,
    scope_label: str,
    updated_at: str,
    account_id: str,
) -> dict[str, Any]:
    if not args:
        display_current = effective_codex_runtime_settings(current)
        catalog = codex_runtime_catalog(account_id=account_id)
        return _result(
            current,
            "选择该 Codex session 后续 turn 使用的模型和 reasoning effort。",
            title="Codex model",
            command_payload={
                "type": "model_picker",
                "current": display_current,
                "models": catalog["models"],
                "efforts": catalog["efforts_by_model"].get(display_current["model"], []),
                "efforts_by_model": catalog["efforts_by_model"],
                "default_efforts_by_model": catalog["default_efforts_by_model"],
            },
        )
    if not can_update:
        return _result(current, "当前用户不能修改这个会话的 Codex runtime 设置。", title="Permission denied")
    model = _clean_model(args[0])
    if not model or _is_service_tier_token(model):
        return _result(current, "模型名无效。用法：`/model gpt-5.6-terra high`", title="Invalid model")
    updates: dict[str, Any] = {"model": model}
    if len(args) > 1:
        updates["reasoning_effort"] = str(args[1]).strip().lower()
    try:
        updates = validate_codex_runtime_updates(
            current,
            updates,
            account_id=account_id,
        )
    except ValueError as exc:
        return _result(
            current,
            str(exc),
            title="Invalid reasoning effort",
        )
    next_settings = mark_portal_manual_authority(
        merge_settings(current, updates, actor=actor, updated_at=updated_at),
        actor=actor,
        updated_at=updated_at,
    )
    return _result(
        next_settings,
        _applied_content(next_settings, scope_label, "Codex model 设置已更新，后续 turn 会带给 Codex。"),
        title="Codex model updated",
        updates=next_settings,
    )


def _codex_fast_command(
    current: dict[str, Any],
    session_id: str,
    cwd: str,
    actor: str,
    can_update: bool,
    scope_label: str,
    updated_at: str,
) -> dict[str, Any]:
    if not can_update:
        return _result(current, "当前用户不能修改这个会话的 Codex runtime 设置。", title="Permission denied")
    current_tier = str(current.get("service_tier") or "").strip()
    enabling = not _is_fast_tier(current_tier)
    target_tier = "priority" if enabling else ""
    next_settings = merge_settings(
        current,
        {"service_tier": target_tier},
        actor=actor,
        updated_at=updated_at,
    )
    next_settings = mark_portal_manual_authority(next_settings, actor=actor, updated_at=updated_at)
    headline = "Codex Fast mode 已开启。" if enabling else "Codex Fast mode 已关闭。"
    return _result(
        next_settings,
        headline + " 设置已写入当前 Session；有运行中的 Codex turn 时，会原生更新其后续尚未开始的步骤，下一条 turn 也会使用新速度。",
        title="Codex fast mode",
        updates=next_settings,
        command_payload={
            "type": "codex_runtime_control",
            "command": "fast",
            "service_tier": target_tier,
            "session_id": session_id,
        },
    )


def _codex_permissions_command(
    args: list[str],
    current: dict[str, Any],
    session_id: str,
    cwd: str,
    actor: str,
    can_manage: bool,
    scope_label: str,
    updated_at: str,
) -> dict[str, Any]:
    if not args:
        current_preset = _permission_preset_id(current)
        return _result(
            current,
            "选择该 Codex session 使用的原生权限模式。",
            title="Codex permissions",
            command_payload={
                "type": "permissions_picker",
                "current": {**current, "permission_preset": current_preset},
                "presets": [dict(item) for item in PERMISSION_PRESETS],
            },
        )
    if not can_manage:
        return _result(current, "当前用户不能修改这个会话的 Codex permissions。", title="Permission denied")
    preset = _permission_preset(args[0] if args else "")
    if not preset:
        return _result(current, "权限模式无效。可选：`ask-for-permissions`、`auto-review`、`full-access`。", title="Invalid permissions")
    next_settings = merge_settings(
        current,
        _apply_permission_preset({}, preset["id"]),
        actor=actor,
        updated_at=updated_at,
    )
    next_settings = mark_portal_manual_authority(next_settings, actor=actor, updated_at=updated_at)
    return _result(
        next_settings,
        _applied_content(next_settings, scope_label, f"Codex permissions 已切换为 {preset['label']}，后续 turn 会带给 Codex。"),
        title="Codex permissions updated",
        updates=next_settings,
    )




def _codex_status_content(
    settings: dict[str, Any],
    scope_label: str,
    token_usage: object,
) -> str:
    effective = effective_codex_runtime_settings(settings)
    lines = [
        f"当前会话：`{scope_label}`",
        "",
        # The Provider first, as on the other Providers' /status.
        "- Provider: `Codex`",
        f"- Model: `{effective.get('model') or PORTAL_DEFAULT_MODEL}`",
        f"- Reasoning effort: `{effective.get('reasoning_effort') or PORTAL_DEFAULT_REASONING_EFFORT}`",
        f"- Fast mode: `{_service_tier_label(effective.get('service_tier') or '')}`",
        f"- Permissions: `{_permission_label(effective)}`",
    ]
    token_text = _token_usage_content(token_usage)
    if token_text:
        lines.extend(["", token_text])
    return "\n".join(lines)


def _clean_goal_text(value: object, limit: int = 1200) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "\n\n[truncated]"


def _codex_goal_content(goal: object, scope_label: str) -> str:
    data = goal if isinstance(goal, dict) else {}
    native_objective = _clean_goal_text(data.get("objective"))
    native_status = str(data.get("status") or "").strip()
    if native_objective or native_status:
        lines = [
            f"当前会话：`{scope_label}`",
            "",
            f"- Status: `{native_status or '-'}`",
            f"- Tokens used: `{max(0, int(data.get('tokensUsed') or 0))}`",
            f"- Time used: `{max(0, int(data.get('timeUsedSeconds') or 0))}s`",
        ]
        token_budget = data.get("tokenBudget")
        if token_budget is not None:
            lines.append(f"- Token budget: `{max(0, int(token_budget or 0))}`")
        if native_objective:
            lines.extend(["", "**Objective**", "", native_objective])
        return "\n".join(lines)
    initial_goal = _clean_goal_text(data.get("initial_goal"))
    current_goal = _clean_goal_text(data.get("current_goal") or data.get("current_job"))
    last_user_goal = _clean_goal_text(data.get("last_user_message"), limit=800)
    title = str(data.get("title") or data.get("resident_id") or data.get("workspace_id") or "").strip()
    state = str(data.get("state") or data.get("session_state") or "").strip()
    lines = [
        f"当前会话：`{scope_label}`",
    ]
    if title:
        lines.append(f"- Target: `{title}`")
    if state:
        lines.append(f"- State: `{state}`")
    sections: list[tuple[str, str]] = []
    if initial_goal:
        sections.append(("Initial goal", initial_goal))
    if current_goal:
        sections.append(("Current job", current_goal))
    if last_user_goal and last_user_goal not in {initial_goal, current_goal}:
        sections.append(("Latest user message", last_user_goal))
    if not sections:
        lines.extend(["", "当前没有可显示的 goal 记录。"])
        return "\n".join(lines)
    for heading, body in sections:
        lines.extend(["", f"**{heading}**", "", body])
    return "\n".join(lines)


def _service_tier_signature(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in {"fast", "priority", "2x"}:
        return "fast"
    if text in {"", "default", "standard", "1x"}:
        return "standard"
    return text


def handle_codex_native_command(
    content: str,
    settings: object,
    *,
    session_id: str = "",
    account_id: str = "",
    cwd: str = "",
    actor: str = "",
    can_update_runtime: bool = True,
    can_manage_permissions: bool = False,
    scope_label: str = "session",
    updated_at: str = "",
    token_usage: object = None,
    goal: object = None,
) -> dict[str, Any]:
    text = str(content or "").strip()
    if not text.startswith("/"):
        return {"handled": False}
    command, args = _split_command(text)
    if command not in NATIVE_COMMANDS:
        return {"handled": False}
    current = normalize_settings(settings)
    session_id = _clean_session_id(session_id)
    cwd = str(cwd or "").strip()
    updated_at = updated_at or _now_utc()

    if command == "status":
        return _result(
            current,
            _codex_status_content(current, scope_label, token_usage),
            title="Codex status",
        )

    if command == "goal":
        goal_command = parse_goal_command(text)
        action = str(goal_command.get("action") or "get")
        if action != "get":
            if not can_update_runtime:
                return _result(
                    current,
                    "当前用户不能修改这个会话的 goal。",
                    title="Permission denied",
                )
            if action != "set" and not session_id:
                return _result(
                    current,
                    "当前还没有 Codex session，无法控制 goal。",
                    title="Codex goal unavailable",
                )
            return {
                "handled": False,
                "native_goal": True,
                "goal_action": goal_command,
            }
        return _result(
            current,
            _codex_goal_content(goal, scope_label),
            title="Codex goal",
        )

    if command == "plan":
        if not can_update_runtime:
            return _result(
                current,
                "当前用户不能修改这个会话的 Plan mode。",
                title="Permission denied",
            )
        explicit = str(args[0] if args else "").strip().casefold()
        if explicit in {"on", "plan", "enable", "enabled"}:
            target = "plan"
        elif explicit in {"off", "default", "disable", "disabled"}:
            target = "default"
        elif explicit:
            return _result(
                current,
                "用法：`/plan` 切换模式，或使用 `/plan on`、`/plan off`。",
                title="Invalid Plan mode",
            )
        else:
            target = (
                "default"
                if current.get("collaboration_mode") == "plan"
                else "plan"
            )
        next_settings = mark_portal_manual_authority(
            merge_settings(
                current,
                {"collaboration_mode": target},
                actor=actor,
                updated_at=updated_at,
            ),
            actor=actor,
            updated_at=updated_at,
        )
        return _result(
            next_settings,
            (
                f"Codex Plan mode 已{'开启' if target == 'plan' else '关闭'}；"
                "从下一条消息开始生效。"
            ),
            title="Codex Plan mode updated",
            updates=next_settings,
            command_payload={
                "type": "codex_native_command",
                "command": "plan",
                "mode": target,
                "session_id": session_id,
            },
        )

    if command == "model":
        return _codex_model_command(args, current, session_id, cwd, actor, can_update_runtime, scope_label, updated_at, account_id)

    if command == "fast":
        return _codex_fast_command(current, session_id, cwd, actor, can_update_runtime, scope_label, updated_at)

    if command == "permissions":
        return _codex_permissions_command(
            args,
            current,
            session_id,
            cwd,
            actor,
            can_manage_permissions,
            scope_label,
            updated_at,
        )

    if command == "compact":
        return _result(
            current,
            f"Codex native compact should be handled by the session backend for `{scope_label}`.",
            title="Codex compact",
            command_payload={"type": "codex_native_command", "command": "compact", "args": args, "session_id": session_id},
        ) | {"native_compact": True}

    return {"handled": False}


def _applied_content(settings: dict[str, Any], scope_label: str, headline: str) -> str:
    effective = effective_codex_runtime_settings(settings)
    return (
        f"{headline}\n\n"
        f"- Session: `{scope_label}`\n"
        f"- Backend: `{effective.get('backend') or 'codex'}`\n"
        f"- Model: `{effective.get('model') or PORTAL_DEFAULT_MODEL}`\n"
        f"- Reasoning effort: `{effective.get('reasoning_effort') or PORTAL_DEFAULT_REASONING_EFFORT}`\n"
        f"- Fast mode: `{_service_tier_label(effective.get('service_tier') or '')}`\n"
        f"- Permissions: `{_permission_label(effective)}`"
    )


def _clean_session_id(value: object) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 160:
        return ""
    return text if all(ch.isalnum() or ch in "._:-" for ch in text) else ""


def _is_fast_tier(value: object) -> bool:
    return str(value or "").strip().lower() == "priority"


def _is_service_tier_token(value: object) -> bool:
    return str(value or "").strip().lower() in {"fast", "priority", "standard", "default", "1x", "2x"}


def _service_tier_label(value: object) -> str:
    tier = str(value or "").strip()
    if not tier:
        return "1x (default)"
    if _service_tier_signature(tier) == "standard":
        return "1x"
    if _is_fast_tier(tier):
        return f"Fast ({tier})"
    return tier


def _permission_label(settings: dict[str, Any]) -> str:
    preset = _permission_preset(_permission_preset_id(settings))
    if preset:
        return preset["label"]
    profile = str(settings.get("permissions") or "").strip()
    if profile == ":read-only":
        return "Read only"
    if profile:
        return f"Custom ({profile})"
    return "Full Access (default)"


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
