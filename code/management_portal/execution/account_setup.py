"""Provider-neutral onboarding observations and real execution checks.

Runtime installation, credentials and role mappings retain their existing
owners. A check is an ordinary durable Execution, never a second model client.
"""
from __future__ import annotations

import time

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from . import account_registrations as registrations, provider_updates
from .accounts import default_execution_accounts
from .client import ExecutionClient
from .contracts import ExecutionRequest, SessionOptions
from .provider_catalog import AGENT_PROVIDERS


def account(account_id):
    row = registrations.registration(account_id)
    if not row:
        raise not_found("执行账号不存在")
    return row


def route(row):
    return row["config"]["api_id"] if row["provider"] == "provider_api" else row["route_id"]


def catalog(row):
    return default_execution_accounts().runtime_catalog(
        route(row) if row["provider"] == "provider_api" else row["account_id"], backend=row["provider"])


def signature(row):
    from management_portal.model_api_store import api_version
    return {"registration_version": row["version"],
            "api_version": api_version(row["config"]["api_id"]) if row["kind"] == "api_profile" else None}


def get_setup(inv):
    from .account_login import get_login_state
    row = account(inv.argument("account_id"))
    preparation = provider_updates.preparation_status(row["provider"])
    try:
        login = get_login_state(row)
    except InstructionError:
        login = "unavailable"
    definition = AGENT_PROVIDERS.get(row["provider"])
    result = {"account_id": row["account_id"], "provider": row["provider"], "enabled": row["enabled"],
              "runtime_ready": preparation["status"] == "ready", "preparation": preparation,
              "login_state": login, "supports_tools": bool(definition and "portal_tools" in definition.harness),
              "operations": sorted(definition.operations) if definition else [],
              "models": [], "default_model": "", "catalog_error": ""}
    try:
        info = catalog(row)
        result.update(models=info.get("models", []), default_model=info.get("default_model", ""))
    except Exception:
        result["catalog_error"] = "模型目录暂不可用，请准备环境、完成授权后重试。"
    return {"setup": result}


def prepare(inv):
    provider = inv.argument("provider")
    if provider not in registrations.PROVIDER_KINDS:
        raise invalid("未知 Provider")
    return {"preparation": provider_updates.prepare_runtime(provider)}


def _started_codex_catalog(row, *, timeout=30.0):
    """Wait for the socket after service start, without retrying model execution."""
    from management_portal.codex_app_server_client import CodexAppServerUnavailable

    deadline = time.monotonic() + timeout
    while True:
        try:
            return catalog(row)
        except CodexAppServerUnavailable:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def check(inv):
    row = account(inv.argument("account_id"))
    if not row["enabled"]:
        raise invalid("请先启用执行账号")
    if not provider_updates.runtime_available(row["provider"]):
        raise InstructionError("instruction.conflict", "请先准备运行环境", details={"reason": "account.runtime_missing"})
    if row["provider"] == "codex":
        from . import account_hosting
        # Registration may precede runtime preparation. Starting an already
        # running instance is idempotent and preserves its existing login.
        account_hosting.start_codex_instance(row["account_id"])
        # systemd Type=simple confirms the process, not its listening socket.
        # Validate only after the newly started app-server answers its catalog.
        info = _started_codex_catalog(row)
    else:
        info = None
    model = inv.argument("model") or (info if info is not None else catalog(row)).get("default_model", "")
    if not model:
        raise invalid("请选择模型")
    if row["provider"] == "provider_api":
        from .resident_configuration import validate_api_settings
        validate_api_settings(route(row), {"model": model}, {})
    else:
        AGENT_PROVIDERS[row["provider"]].validate_settings(route(row), model)
    # A scratch cwd prevents the test from encountering the operator's project
    # instructions. It is retained while the durable execution is in flight.
    cwd = provider_updates.STATE_ROOT / "account-checks" / row["account_id"]
    cwd.mkdir(parents=True, exist_ok=True)
    direct = row["provider"] == "provider_api"
    options = {"model": model, "allow_subagents": False}
    if not direct:
        definition = AGENT_PROVIDERS[row["provider"]]
        settings = definition.runtime_adapter().defaults(definition, {"model": model})
        options.update(is_owner=True, runtime_settings={**settings, "model": model, "allow_subagents": False})
    request = ExecutionRequest.new(
        actor=inv.caller, prompt="Reply exactly PORTAL_PROVIDER_OK. Do not call tools or access files or network.",
        service_type="chat_completion" if direct else "agent_session",
        provider=route(row) if direct else row["provider"], operation="generate" if direct else "turn",
        cwd=str(cwd), session=SessionOptions(backend_route_id="" if direct else route(row)),
        permission_profile="default" if direct else "owner", runtime_options=options,
        metadata={"provider_account_check": {"account_id": row["account_id"], "model": model, **signature(row)}})
    submission = ExecutionClient().submit(request)
    return {"execution_id": submission.execution_id, "status": "queued"}


def check_result(inv):
    return {"check": _check_result(inv.argument("account_id"), inv.argument("execution_id"), inv.caller)}


def _check_result(account_id, execution_id, actor):
    row = account(account_id)
    snapshot = ExecutionClient().status(execution_id, actor=actor)
    checked = (snapshot.get("request", {}).get("metadata") or {}).get("provider_account_check") or {}
    if checked.get("account_id") != row["account_id"]:
        raise invalid("该执行不属于所选账号的接入测试")
    current = all(checked.get(key) == value for key, value in signature(row).items())
    status = snapshot.get("status", "unknown")
    result = (snapshot.get("result") or {}).get("output") or {}
    text = str(result.get("message") or result.get("text") or result.get("output_text") or "")
    passed = status == "completed" and text.strip() == "PORTAL_PROVIDER_OK" and current and row["enabled"]
    queue_reason = (snapshot.get("scheduler") or {}).get("queue_reason", "") if status == "queued" else ""
    waiting = {"quota_reserve": "账号额度保护暂停了测试，等待额度恢复或选择其他账号。",
               "capacity": "账号正在处理其他任务，测试排队中。"}.get(queue_reason, "正在等待账号执行测试…")
    return {"execution_id": snapshot["execution_id"], "status": status, "passed": bool(passed),
                       "model": checked.get("model", ""), "configuration_current": current,
                       "message": "测试通过" if passed else "配置已变化，请重新测试" if not current else
                       "测试未通过，请检查账号授权、模型权限和运行环境后重试。" if status in {"completed", "failed", "cancelled"}
                       else waiting, "queue_reason": queue_reason}


def require_verified(inv):
    result = _check_result(inv.argument("account_id"), inv.argument("verification_execution_id"), inv.caller)
    if not result["passed"] or result["model"] != inv.argument("model"):
        raise invalid("请先使用当前账号和模型完成执行测试")


HANDLERS = {"GetProviderAccountSetup": get_setup, "PrepareProviderRuntime": prepare,
            "CheckProviderAccount": check, "GetProviderAccountCheck": check_result}
