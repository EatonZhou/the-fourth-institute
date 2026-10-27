"""Provider-neutral onboarding observations and real execution checks.

Runtime installation, credentials and role mappings retain their existing
owners. A check is an ordinary durable Execution, never a second model client.
"""
from __future__ import annotations

import time
import logging
import subprocess
import json
import sys

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from . import account_registrations as registrations, provider_updates
from .accounts import default_execution_accounts
from .client import ExecutionClient
from .contracts import ExecutionRequest, SessionOptions
from .provider_catalog import AGENT_PROVIDERS
from .activity_redaction import redact_sensitive_mapping
from .provider_failures import failure_code, is_egress_block_failure, is_network_failure, is_usage_limit_failure
from management_portal.provider_transport_diagnostics import sanitize_transport_observation

logger = logging.getLogger(__name__)


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
        result["catalog_error"] = "模型列表尚未加载；完成授权后，点击“测试并完成”启动后台并验证连接。"
    return {"setup": result}


def prepare(inv):
    provider = inv.argument("provider")
    if provider not in registrations.PROVIDER_KINDS:
        raise invalid("未知 Provider")
    return {"preparation": provider_updates.prepare_runtime(provider)}


def _started_codex_catalog(row, *, timeout=30.0):
    """Wait for the socket after service start, without retrying model execution."""
    from management_portal.codex_app_server_client import CodexAppServerUnavailable, client_for_socket
    from .account_hosting import codex_socket_path

    deadline = time.monotonic() + timeout
    while True:
        try:
            if row.get("kind") == "api_profile":
                # Its catalog is local data; ask the app-server so the turn finds it listening.
                client_for_socket(codex_socket_path(row["account_id"])).request("account/read", {}, timeout=10.0)
            return catalog(row)
        except CodexAppServerUnavailable:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(0.1, remaining))


def _started_claude_catalog(row, *, timeout=30.0):
    from management_common.portal_paths import path
    from management_portal.claude_code_client import ClaudeCodeClient, ClaudeCodeUnavailable

    # A reusable SDK says nothing about the new instance's listening daemon.
    # Do not inherit a shell's CLAUDE_CODE_SOCKET from another installation.
    client = ClaudeCodeClient(path("staging_root") / "portal_locks/claude-code-daemon.sock")
    deadline = time.monotonic() + timeout
    while True:
        try:
            health = client.health()
            if health.get("backend") != "claude" or health.get("protocol_version") != 1:
                raise ClaudeCodeUnavailable("Unexpected Claude daemon protocol")
            # health's default profile may not be registered on a fresh site.
            # Validate this account's catalog; the durable check tests its login.
            return catalog(row)
        except ClaudeCodeUnavailable:
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
        if row["kind"] == "api_profile":
            # The home may predate the runtime or the Model API's current key;
            # an app-server whose configuration changes is restarted.
            from .codex_api_accounts import refresh
            refresh([row])
        # Registration may precede runtime preparation. Starting an already
        # running instance is idempotent and preserves its existing login.
        from management_portal.codex_app_server_client import CodexAppServerUnavailable
        try:
            account_hosting.start_codex_instance(row["account_id"])
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("Codex service start failed for %s (%s)", row["account_id"], type(exc).__name__)
            raise InstructionError("instruction.owner_unavailable",
                "Codex 本地后台启动失败。登录授权仍然保留；请重试“测试并完成”，若仍失败请检查该账号的后台服务日志。",
                details={"reason": "account.service_start_failed"}, retryable=True) from None
        try:
            # Process startup does not prove that the listening socket is ready.
            info = _started_codex_catalog(row)
        except CodexAppServerUnavailable:
            raise InstructionError("instruction.owner_unavailable",
                "Codex 本地后台未能在规定时间内就绪。登录授权仍然保留；请重试“测试并完成”，若仍失败请检查该账号的后台服务日志。",
                details={"reason": "account.service_not_ready"}, retryable=True) from None
    elif row["provider"] == "claude" and (sys.platform.startswith("linux") or sys.platform in {"win32", "darwin"}):
        from scripts.portal_service_units import start_claude_service
        from management_portal.claude_code_client import ClaudeCodeUnavailable
        try:
            start_claude_service()
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("Claude service start failed for %s (%s)", row["account_id"], type(exc).__name__)
            raise InstructionError("instruction.owner_unavailable",
                "Claude 本地后台启动失败。登录授权仍然保留；请重试“测试并完成”，若仍失败请检查本站后台服务日志。",
                details={"reason": "account.service_start_failed"}, retryable=True) from None
        try:
            info = _started_claude_catalog(row)
        except ClaudeCodeUnavailable:
            raise InstructionError("instruction.owner_unavailable",
                "Claude 本地后台未能在规定时间内就绪。登录授权仍然保留；请重试“测试并完成”，若仍失败请检查本站后台服务日志。",
                details={"reason": "account.service_not_ready"}, retryable=True) from None
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
    if direct:
        from management_portal.ai_provider_registry import shared_ai_provider_registry
        if shared_ai_provider_registry.get(route(row))["kind"] == "typesafe_system_one":
            options["parameters"] = {"questions": {"greeting": {"type": "noul", "instructions": "Does the message request a greeting?"}}}
    if not direct:
        definition = AGENT_PROVIDERS[row["provider"]]
        settings = definition.runtime_adapter().defaults(definition, {"model": model})
        options.update(is_owner=True, runtime_settings={**settings, "model": model, "allow_subagents": False})
    request = ExecutionRequest.new(
        actor=inv.caller, prompt="Reply with a short greeting. Do not call tools or access files or network.",
        service_type="chat_completion" if direct else "agent_session",
        provider=route(row) if direct else row["provider"], operation="generate" if direct else "turn",
        cwd=str(cwd), session=SessionOptions(backend_route_id="" if direct else route(row)),
        permission_profile="default" if direct else "owner", runtime_options=options,
        metadata={"provider_account_check": {"account_id": row["account_id"], "model": model, **signature(row)}})
    submission = ExecutionClient().submit(request)
    return {"execution_id": submission.execution_id, "status": "queued"}


def check_result(inv):
    return {"check": _check_result(inv.argument("account_id"), inv.argument("execution_id"), inv.caller)}


_CHECK_FAILURES = {
    "authentication": ("测试未通过：服务商拒绝了身份认证。", "请编辑已登记的模型 API，核对 API Key 与 Base URL 是否属于同一服务商，再重新测试；无需重新注册。"),
    "login": ("测试未通过：服务商拒绝了账号授权。", "请重新登录该订阅账号，再测试连接。"),
    "access_denied": ("测试未通过：服务商拒绝访问。", "请在服务商控制台检查模型权限和访问限制；此结果不能单独判定 API Key 无效。"),
    "not_found": ("测试未通过：服务商未找到请求的接口或模型。", "请核对 Base URL、模型名称和所选 Agent 运行器需要的协议；Base URL 不要包含 /chat/completions。"),
    "model": ("测试未通过：服务商无法提供所选模型。", "请核对模型名称，并在服务商控制台确认该账号具有模型权限。"),
    "quota": ("测试未通过：服务商报告账号额度不足。", "请在服务商控制台查看余额或套餐额度，恢复额度后重新测试。"),
    "rate_limit": ("测试未通过：服务商限制了此次请求。", "请稍后重试；若持续出现，请在服务商控制台查看请求频率限制和账号额度。"),
    "provider_unavailable": ("测试未通过：服务商或网关暂时不可用。", "请稍后重试；若持续出现，请联系接口服务商并提供下方 HTTP 状态码。"),
    "request_rejected": ("测试未通过：服务商不接受本次请求。", "请核对模型名称和接口协议是否匹配所选 Agent 运行器；不要仅因此更换 API Key。"),
    "timeout": ("测试未通过：等待服务商响应超时。", "请检查运行 Portal 的电脑能否连接服务商、网络代理是否正常，再重试。"),
    "tls": ("测试未通过：无法建立安全连接。", "请检查运行 Portal 的电脑的系统时间、证书与代理配置，不要关闭证书校验。"),
    "network": ("测试未通过：Portal 无法连接服务商。", "请检查运行 Portal 的电脑的网络、Base URL 和代理配置，再重试。"),
    "invalid_response": ("测试未通过：接口返回了无法解析的响应。", "请确认 Base URL 指向模型 API，接口协议与所选运行器匹配，而非网页或登录页。"),
    "empty_reply": ("接口调用已完成，但没有返回可用的文本回复。", "请确认接口协议和模型名称正确，模型支持文本回复后重试。"),
    "cancelled": ("本次接入测试已取消。", "登记仍然保留。请点击“测试并完成”重新测试。"),
    "runtime": ("测试未通过：Agent 运行器未能恢复执行。", "请重试；若仍失败，请提供下方测试编号以检查运行器后台日志。"),
    "unknown": ("测试失败，现有诊断尚不能确定原因。", "请复制下方测试编号和失败类别交给管理员排查；无需重新注册或再次发送 API Key。"),
}


def _check_failure(snapshot, row):
    """Project existing execution evidence, never expose provider bodies or credentials."""
    result = snapshot.get("result") or {}
    transport = sanitize_transport_observation((result.get("error_details") or {}).get("provider_transport")) or {}
    http_status = (transport.get("response") or {}).get("http_status")
    kind = transport.get("error_kind")
    code = failure_code(result)
    status = snapshot.get("status")
    if status == "cancelled" or result.get("status") == "cancelled":
        reason = "cancelled"
    elif status == "completed" and not (result.get("error") or result.get("error_code")) and result.get("status") != "failed":
        reason = "empty_reply"
    elif is_usage_limit_failure(result):
        reason = "quota"
    elif http_status == 401 or code in {"invalid_api_key", "authentication_error", "unauthorized"}:
        reason = "login" if row["kind"] == "subscription_login" else "authentication"
    elif http_status == 403 or is_egress_block_failure(result):
        reason = "access_denied"
    elif code == "model_not_found":
        reason = "model"
    elif http_status == 404:
        reason = "not_found"
    elif http_status == 429:
        reason = "rate_limit"
    elif http_status and http_status >= 500:
        reason = "provider_unavailable"
    elif http_status and 400 <= http_status < 500:
        reason = "request_rejected"
    elif kind in {"timeout", "read_timeout"}:
        reason = "timeout"
    elif kind == "tls_error":
        reason = "tls"
    elif kind in {"connection_error", "url_error"} or is_network_failure(result):
        reason = "network"
    elif kind == "invalid_json":
        reason = "invalid_response"
    elif code in {"provider_recovery_exhausted", "provider_runtime_missing",
                  "provider_runtime_launch_failed", "provider_runtime_startup_failed", "provider_runtime_startup_timeout"}:
        reason = "runtime"
    else:
        reason = "unknown"
    message, next_step = _CHECK_FAILURES[reason]
    return {"message": message, "next_step": next_step,
            "diagnostic": {"reason": reason, "http_status": http_status}}


def _check_log(snapshot, row, response):
    """Bounded diagnostic log from this authorized check, not arbitrary host logs."""
    result = snapshot.get("result") or {}
    scheduler = snapshot.get("scheduler") or {}
    report = redact_sensitive_mapping({
        "execution_id": snapshot["execution_id"], "account_id": row["account_id"],
        "runner": row["provider"], "model": response["model"], "status": snapshot.get("status"),
        "submitted_at": scheduler.get("created_at") or None,
        "message": response["message"], "next_step": response["next_step"],
        "diagnostic": response["diagnostic"],
        "error_recorded": bool(result.get("error") or result.get("error_code")),
    })
    # Provider errors can embed arbitrary response bodies or unlabelled credentials.
    # Include only the classified failure and allowlisted transport observations.
    report["transport"] = sanitize_transport_observation((result.get("error_details") or {}).get("provider_transport"))
    code = failure_code(result)
    if code in {"provider_runtime_missing", "provider_runtime_launch_failed",
                "provider_runtime_startup_failed", "provider_runtime_startup_timeout"}:
        report["runtime_failure"] = code
    return json.dumps(report, ensure_ascii=False, indent=2)


def _check_result(account_id, execution_id, actor):
    row = account(account_id)
    snapshot = ExecutionClient().status(execution_id, actor=actor)
    checked = (snapshot.get("request", {}).get("metadata") or {}).get("provider_account_check") or {}
    if checked.get("account_id") != row["account_id"]:
        raise invalid("该执行不属于所选账号的接入测试")
    current = all(checked.get(key) == value for key, value in signature(row).items())
    status = snapshot.get("status", "unknown")
    result = snapshot.get("result") or {}
    output = result.get("output") or {}
    # Connectivity does not depend on exact wording, language or punctuation.
    # Do not coerce an error object or other non-text output into a reply.
    has_reply = isinstance(output, dict) and any(
        isinstance(output.get(key), str) and output[key].strip()
        for key in ("message", "text", "output_text"))
    failed = bool(result.get("error") or result.get("error_code")) or result.get("status") in {"failed", "cancelled"}
    passed = status == "completed" and has_reply and not failed and current and row["enabled"]
    queue_reason = (snapshot.get("scheduler") or {}).get("queue_reason", "") if status == "queued" else ""
    waiting = {"quota_reserve": "账号额度保护暂停了测试，等待额度恢复或选择其他账号。",
               "capacity": "账号正在处理其他任务，测试排队中。"}.get(queue_reason, "正在等待账号执行测试…")
    response = {"execution_id": snapshot["execution_id"], "status": status, "passed": bool(passed),
                "model": checked.get("model", ""), "configuration_current": current,
                "message": "测试通过" if passed else "配置已变化，请重新测试" if not current else waiting,
                "queue_reason": queue_reason, "next_step": "", "diagnostic": None, "log": ""}
    if not row["enabled"]:
        response["message"] = "账号已停用，请启用后重新测试。"
    elif current and not passed and status in {"completed", "failed", "cancelled"}:
        response.update(_check_failure(snapshot, row))
    if not passed and status in {"completed", "failed", "cancelled"}:
        response["log"] = _check_log(snapshot, row, response)
    return response


def require_verified(inv):
    result = _check_result(inv.argument("account_id"), inv.argument("verification_execution_id"), inv.caller)
    if not result["passed"] or result["model"] != inv.argument("model"):
        raise invalid("请先使用当前账号和模型完成执行测试")


HANDLERS = {"GetProviderAccountSetup": get_setup, "PrepareProviderRuntime": prepare,
            "CheckProviderAccount": check, "GetProviderAccountCheck": check_result}
