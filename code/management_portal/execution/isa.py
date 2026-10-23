"""ExecutionService instruction handlers and its kernel (decision_4527448ad07f).

ExecutionService owns model-interface registration and calls, execution
accounts, their scheduler settings and token usage.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from management_portal import model_api_store, usage_read_model
from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import InstructionKernel, Invocation, OwnerBinding
from management_portal.hatg.maintenance import recover_projections


MAX_USAGE_WINDOW_DAYS = 60
# Registry fields and their instruction names.
MODEL_API_FIELDS = {
    "base_url": "base_url",
    "default_model": "default_model",
    "secret_file": "api_key_file",
    "timeout_seconds": "timeout_s",
    "retries": "max_retries",
    "headers": "default_headers",
    "enabled": "enabled",
    "label": "label",
    "provider": "provider",
    "kind": "kind",
    "models": "models",
    "default_parameters": "default_parameters",
    "routes": "routes",
    "protocols": "protocols",
}
_SERVER: Any = None


def attach_server(server: Any) -> None:
    """The ExecutionService process hands its live scheduler to the handlers."""

    global _SERVER
    _SERVER = server


def _client():
    from .service import get_execution_service

    return get_execution_service()


# -- model interfaces --------------------------------------------------------------


def _registry():
    from management_portal.ai_provider_registry import AIProviderRegistry

    return AIProviderRegistry()


def _public_api(item: dict[str, Any], version: int) -> dict[str, Any]:
    result: dict[str, Any] = {"api_id": str(item.get("id") or "")}
    for public, stored in MODEL_API_FIELDS.items():
        if stored in item:
            result[public] = copy.deepcopy(item[stored])
    result.setdefault("enabled", True)
    result["version"] = version
    from management_portal.ai_provider_registry import HARNESS_PROTOCOLS, AIProviderRegistry
    offered = AIProviderRegistry.harness_protocols(item)
    result["harness_protocols"] = [name for name in HARNESS_PROTOCOLS if name in offered]
    routes = model_api_store.route_bindings(result["api_id"])
    if routes:
        result["routes"] = routes
    return result


def _stored_fields(values: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(values) - set(MODEL_API_FIELDS))
    if unknown:
        raise invalid("模型接口不接受字段：" + "、".join(unknown))
    stored = {MODEL_API_FIELDS[name]: copy.deepcopy(value) for name, value in values.items()}
    headers = stored.get("default_headers")
    if headers is not None and (
        not isinstance(headers, dict) or any("authorization" in str(key).lower() for key in headers)
    ):
        raise invalid("headers 只能放不含密钥的附加请求头")
    secret = stored.get("api_key_file")
    if secret is not None and not Path(str(secret)).is_absolute():
        raise invalid("secret_file 必须是绝对路径")
    if "models" in stored:
        _validate_models(stored["models"])
    if stored.get("protocols") is not None:
        _validate_protocols(stored["protocols"])
    return stored


def _validate_protocols(protocols: Any) -> None:
    from management_portal.ai_provider_registry import ANTHROPIC_AUTH, DECLARED_PROTOCOL_OPTIONS
    if not isinstance(protocols, dict) or set(protocols) - set(DECLARED_PROTOCOL_OPTIONS):
        raise invalid("protocols 只能声明 " + "、".join(DECLARED_PROTOCOL_OPTIONS))
    for name, options in protocols.items():
        if not isinstance(options, dict) or set(options) - DECLARED_PROTOCOL_OPTIONS[name]:
            raise invalid(f"protocols.{name} 只接受：" + ("、".join(sorted(DECLARED_PROTOCOL_OPTIONS[name])) or "空对象"))
    anthropic = protocols.get("anthropic_messages") or {}
    url = anthropic.get("base_url")
    if url is not None and not (isinstance(url, str) and re.fullmatch(r"https?://\S+", url.strip())):
        raise invalid("protocols.anthropic_messages.base_url 必须是 http(s) 地址")
    if anthropic.get("auth", "bearer") not in ANTHROPIC_AUTH:
        raise invalid("protocols.anthropic_messages.auth 只能是 " + "、".join(ANTHROPIC_AUTH))


def _check_api_kind(entry: dict[str, Any]) -> None:
    # Declared harness protocols describe an OpenAI-compatible endpoint.
    if entry.get("protocols") and entry.get("kind") != "openai_compatible_chat":
        raise invalid("只有 openai_compatible_chat 模型接口可以声明 protocols")


def _api_users(api_id: str) -> list[dict[str, Any]]:
    from .account_registrations import registrations
    return [row for row in registrations() if row["config"].get("api_id") == api_id]


def _required_protocol(provider: str) -> tuple[str, str]:
    from .provider_catalog import AGENT_PROVIDERS
    definition = AGENT_PROVIDERS.get(provider)
    return (definition.api_protocol, definition.label) if definition else ("", provider)


def _check_api_protocol(provider: str, api_id: str) -> None:
    """An api_profile account's Model API must speak the protocol its harness uses."""
    from management_portal.ai_provider_registry import AIProviderRegistry
    protocol, label = _required_protocol(provider)
    entry = next((item for item, _version in model_api_store.apis() if item["id"] == api_id), None)
    if protocol and entry is not None and protocol not in AIProviderRegistry.harness_protocols(entry):
        raise invalid(f"模型 API {api_id} 没有声明 {label} 需要的 {protocol} 协议")


def _check_api_users_keep_protocols(api_id: str, entry: dict[str, Any]) -> None:
    from management_portal.ai_provider_registry import AIProviderRegistry
    offered = AIProviderRegistry.harness_protocols(entry)
    stranded = [row["account_id"] for row in _api_users(api_id)
                if _required_protocol(row["provider"])[0] not in ("", *offered)]
    if stranded:
        raise InstructionError("instruction.conflict", "执行账号仍需要该模型 API 的协议：" + "、".join(stranded),
                               details={"reason": "account.api_in_use", "account_ids": stranded})


def _validate_models(models: Any) -> None:
    # Only the shape that readers index by is checked; other per-model
    # metadata (aliases, support_efforts, supported_parameters…) passes through.
    if not isinstance(models, list) or not all(
        isinstance(model, dict) and isinstance(model.get("id"), str)
        and model["id"] and model["id"] == model["id"].strip() for model in models
    ):
        raise invalid("models 必须是数组，每项是含 id 的对象；id 非空且不含首尾空白")
    ids = [model["id"] for model in models]
    if len(set(ids)) != len(ids):
        raise invalid("models 中的模型 id 不能重复")
    for model in models:
        efforts = model.get("reasoning_efforts", [])
        if not isinstance(efforts, list) or not all(isinstance(value, str) and value.strip() for value in efforts):
            raise invalid(f"模型 {model['id']} 的 reasoning_efforts 必须是字符串数组")


def list_model_apis(inv: Invocation) -> dict[str, Any]:
    return {"apis": [_public_api(item, version) for item, version in model_api_store.apis()]}


def create_model_api(inv: Invocation) -> dict[str, Any]:
    api_id = str(inv.argument("api_id") or "").strip()
    if not api_id:
        raise invalid("api_id 不能为空")
    values = {name: inv.argument(name) for name in MODEL_API_FIELDS if inv.argument(name) is not None}
    item = {"id": api_id, "kind": "openai_compatible_chat", "enabled": True, **_stored_fields(values)}
    _check_api_kind(item)
    try:
        created = model_api_store.create_api(item, inv.caller, secret=inv.argument("secret"))
    except InstructionError:
        raise
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    if not created:
        raise InstructionError("instruction.conflict", f"模型接口已经存在：{api_id}")
    return {"api": _public_api(item, 1)}


def update_model_api(inv: Invocation) -> dict[str, Any]:
    api_id = inv.argument("api_id")
    current = next((item for item, _version in model_api_store.apis() if item["id"] == api_id), None)
    if current is None:
        raise not_found(f"模型接口不存在：{api_id}")
    try:
        changes = dict(inv.argument("changes") or {})
        secret = changes.pop("secret", inv.argument("secret"))
        stored = _stored_fields(changes)
        _check_api_kind({**current, **stored})
    except InstructionError:
        raise
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    _check_api_users_keep_protocols(api_id, {**current, **stored})
    from . import codex_api_accounts
    # The edit form sends every field; only values that change reconfigure an app-server.
    changed = {name for name, value in stored.items() if current.get(name) != value}
    reconfigures = secret is not None or bool(changed & codex_api_accounts.CONFIGURED_FIELDS)
    if reconfigures:
        _refuse_busy_codex_api_accounts(api_id)
    try:
        updated = model_api_store.update_api(api_id, stored, inv.caller, secret=secret)
    except InstructionError:
        raise
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    if updated is None:
        raise not_found(f"模型接口不存在：{api_id}")
    if reconfigures:
        codex_api_accounts.refresh(codex_api_accounts.api_accounts(api_id))
    item, version = updated
    return {"api": _public_api(item, version), "version": version}


def delete_model_api(inv: Invocation) -> dict[str, Any]:
    api_id = inv.argument("api_id")
    users = [row["account_id"] for row in _api_users(api_id)]
    if users:
        raise InstructionError("instruction.conflict", "仍有执行账号使用该模型 API：" + "、".join(users),
                               details={"reason": "account.api_in_use", "account_ids": users})
    if not model_api_store.delete_api(api_id):
        raise not_found(f"模型接口不存在：{api_id}")
    return {"api_id": api_id}


_JSON_FENCE = re.compile(r"```(?:json)?[ \t]*\n?(.*?)```", re.S | re.I)
_NO_JSON = object()


def _response_validator(schema: object) -> Any:
    """A validator for the caller's JSON Schema; a malformed schema is an argument error."""

    from jsonschema import exceptions as schema_exceptions, validators

    if not isinstance(schema, dict) or not schema:
        raise invalid("response_schema 必须是非空的 JSON Schema 对象")
    validator_class = validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except schema_exceptions.SchemaError as exc:
        raise invalid(f"response_schema 不是有效的 JSON Schema：{exc.message}") from exc
    return validator_class(schema)


def _answer_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return ""


def _first_json_value(text: str) -> Any:
    """The first JSON object or array in the answer, preferring fenced code blocks."""

    decoder = json.JSONDecoder()
    for candidate in [*(match.group(1) for match in _JSON_FENCE.finditer(text)), text]:
        for index, char in enumerate(candidate):
            if char not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(candidate, index)
            except ValueError:
                continue
            return value
    return _NO_JSON


def _structured_answer(api_id: str, message: dict[str, Any], validator: Any) -> Any:
    """Parse the answer as JSON and check it against the caller's schema.

    A model can answer differently on another try, so both failures are
    retryable; a malformed schema was already rejected before the call.
    """

    from jsonschema import exceptions as schema_exceptions

    value = _first_json_value(_answer_text(message))
    if value is _NO_JSON:
        raise InstructionError("instruction.failed", f"{api_id} 的回答中没有 JSON", retryable=True)
    error = schema_exceptions.best_match(validator.iter_errors(value))
    if error is not None:
        location = "/".join(str(item) for item in error.absolute_path) or "（根）"
        raise InstructionError(
            "instruction.failed",
            f"{api_id} 的回答不符合 response_schema：{location} {error.message}",
            retryable=True,
        )
    return value


def complete_chat(inv: Invocation) -> dict[str, Any]:
    registry = _registry()
    api_id = inv.argument("api_id")
    item = registry.get(api_id)
    options = dict(inv.argument("options") or {})
    for forbidden in ("model", "messages", "stream"):
        options.pop(forbidden, None)
    messages = list(inv.argument("messages") or [])
    schema = inv.argument("response_schema")
    validator = None
    if schema is not None:
        validator = _response_validator(schema)
        messages.insert(0, {
            "role": "system",
            "content": "只输出一个 JSON 值，它必须满足下面的 JSON Schema；不要输出任何其他文字。\n"
            + json.dumps(schema, ensure_ascii=False),
        })
    payload = {
        "model": inv.argument("model") or str(item.get("default_model") or ""),
        "messages": messages,
        **options,
    }
    if not payload["model"]:
        raise invalid(f"{api_id} 没有默认模型，请提供 model")
    response = registry.request(
        api_id, "chat/completions", payload,
        timeout_seconds=float(item.get("timeout_s") or 360),
    )
    choices = response.get("choices") if isinstance(response.get("choices"), list) else []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else None
    if not isinstance(message, dict) or not str(message.get("content") or "").strip():
        raise InstructionError("instruction.failed", f"{api_id} 没有返回消息")
    result: dict[str, Any] = {"message": message, "usage": dict(response.get("usage") or {})}
    if validator is not None:
        result["structured"] = _structured_answer(api_id, message, validator)
    return result


def probe_model_api(inv: Invocation) -> dict[str, Any]:
    """Which harness protocols an endpoint serves; the arguments override a registration."""
    from management_portal.ai_provider_registry import AIProviderRegistry
    from .model_api_probe import probe
    api_id = inv.argument("api_id")
    entry: dict[str, Any] = {"id": api_id or "probe"}
    if api_id:
        entry = next((item for item, _version in model_api_store.apis() if item["id"] == api_id), None)
        if entry is None:
            raise not_found(f"模型接口不存在：{api_id}")
    draft = {name: inv.argument(name) for name in ("base_url", "default_model", "secret_file", "headers")
             if inv.argument(name) is not None}
    try:
        entry = {**entry, **_stored_fields(draft)}
    except ValueError as exc:
        raise invalid(str(exc)) from exc
    if not re.fullmatch(r"https?://\S+", str(entry.get("base_url") or "").strip()):
        raise invalid("base_url 必须是 http(s) 地址")
    if not str(entry.get("default_model") or "").strip():
        raise invalid("请提供 default_model")
    anthropic_base_url = str(inv.argument("anthropic_base_url") or "").strip()
    if anthropic_base_url and not re.fullmatch(r"https?://\S+", anthropic_base_url):
        raise invalid("anthropic_base_url 必须是 http(s) 地址")
    secret = inv.argument("secret")
    if secret is None and AIProviderRegistry.secret_path(entry):
        try:
            secret = AIProviderRegistry.read_secret(AIProviderRegistry.secret_path(entry))
        except (OSError, ValueError):
            raise invalid("无法读取接口的密钥文件") from None
    return {"model": entry["default_model"],
            "results": probe(entry, str(secret or "").strip(), anthropic_base_url=anthropic_base_url)}


# -- accounts, scheduling and usage ------------------------------------------------


def _registration_public(row, actor):
    from .account_login import get_login_state
    from .account_hosting import account_home, codex_socket_path
    from management_portal import permission_system
    try:
        login_state = get_login_state(row)
    except InstructionError as exc:
        if exc.details.get("reason") != "account.login_unavailable":
            raise
        login_state = "unavailable"
    result = {**row, "login_state": login_state}
    if (row["kind"] == "subscription_login" or row["provider"] == "codex") and permission_system.allows(actor, "UpdateProviderAccount"):
        result["home_path"] = str(account_home(row["provider"], row["account_id"]))
        if row["provider"] == "codex":
            result["socket_path"] = str(codex_socket_path(row["account_id"]))
    return result


def list_providers(inv):
    from .account_registrations import PROVIDER_KINDS, config_fields, default_route, registrations
    from .provider_catalog import AGENT_PROVIDERS
    from .provider_updates import provider_runtime_status
    runtime_status = provider_runtime_status(refresh=inv.argument("refresh") is True)
    rows = registrations()
    providers = []
    for provider, kinds in PROVIDER_KINDS.items():
        definition = AGENT_PROVIDERS.get(provider)
        own = [row for row in rows if row["provider"] == provider]
        # Providers whose registrations mark a default report the route that is used.
        marks_default = any("is_default" in config_fields(provider, kind) for kind in kinds)
        default = (default_route(own) if marks_default else "") or (definition.default_route if definition else "")
        providers.append({"provider": provider, "title": definition.label if definition else "Model API",
                          "kinds": list(kinds), "capabilities": definition.runtime_capabilities() if definition else {},
                          "runtime": runtime_status.get(provider, {"installed_version": "", "available_version": "",
                                                                   "pending_switch": False, "checked_at": "", "check_error": ""}),
                          "default_route_id": default, "account_count": len(own),
                          # The Model API protocol an api_profile account needs ("" accepts any).
                          "api_protocol": definition.api_protocol if definition else ""})
        from .provider_updates import preparation_status
        providers[-1]["preparation"] = preparation_status(provider)
    return {"providers": providers}


def _registered_account(account_id):
    from .account_registrations import registration
    row = registration(account_id)
    if row is None:
        raise not_found(f"执行账号不存在：{account_id}")
    return row


def register_provider_account(inv):
    from . import account_registrations as store
    from . import account_hosting as host
    data = {name: inv.argument(name) for name in
            ("account_id", "provider", "kind", "route_id", "config", "max_concurrency", "enabled")
            if inv.argument(name) is not None}
    store._validate(data)
    if store.registration(data["account_id"]) is not None:
        raise InstructionError("instruction.conflict", "执行账号已经登记")
    secret = inv.argument("secret")
    if secret is not None and data["kind"] != "api_profile":
        raise invalid("只有 api_profile 接收 secret")
    if data["kind"] == "api_profile":
        api_id = data["config"]["api_id"]
        if model_api_store.api_version(api_id) is None:
            raise not_found("请先登记 config.api_id 对应的模型 API")
        _check_api_protocol(data["provider"], api_id)
        if secret is not None:
            _refuse_busy_codex_api_accounts(api_id)
            model_api_store.update_api(api_id, {}, inv.caller, secret=secret)
    row = store.create_registration(data, inv.caller)
    if row is None:
        raise InstructionError("instruction.conflict", "执行账号已经登记")
    from .provider_updates import runtime_available
    if row["kind"] == "subscription_login":
        host.ensure_account_home(row["provider"], row["account_id"])
        if row["provider"] == "codex" and row["enabled"] and runtime_available("codex"):
            host.start_codex_instance(row["account_id"])
    elif row["provider"] == "codex":
        from . import codex_api_accounts
        host.ensure_account_home("codex", row["account_id"])
        if row["enabled"] and runtime_available("codex"):
            codex_api_accounts.prepare(row)
            host.start_codex_instance(row["account_id"])
    if row["kind"] == "api_profile" and secret is not None:
        from . import codex_api_accounts
        codex_api_accounts.refresh([other for other in codex_api_accounts.api_accounts(row["config"]["api_id"])
                                    if other["account_id"] != row["account_id"]])
    return {"registration": _registration_public(row, inv.caller)}


def _refuse_busy_codex_api_accounts(api_id):
    """Changing what a Codex API account authenticates with restarts its app-server.

    Bound sessions continue on the restarted app-server, as after a runtime
    update; queued or running executions would be cut off.
    """
    from .codex_api_accounts import busy_accounts
    busy = sorted(busy_accounts(api_id))
    if busy:
        raise InstructionError("instruction.conflict",
                               "使用该模型 API 的 Codex 账号正在执行任务：" + "、".join(busy) + "；请等任务结束后再保存。",
                               details={"reason": "account.running", "account_ids": busy})


def _registration_expected_version(inv, object_id, *, minimum):
    expected = inv.expected_version_for(object_id)
    if expected is not None and (type(expected) is not int or expected < minimum):
        raise invalid(f"调用信封 expected_version 必须是大于等于 {minimum} 的整数")
    return expected


def update_provider_account_registration(inv):
    from . import account_registrations as store
    from . import account_hosting as host
    account_id = inv.argument("account_id")
    previous = _registered_account(account_id)
    api_id = (inv.argument("changes") or {}).get("config", {}).get("api_id")
    changed_api = previous["kind"] == "api_profile" and bool(api_id) and api_id != previous["config"].get("api_id")
    if changed_api:
        if model_api_store.api_version(api_id) is None:
            raise not_found("请先登记 config.api_id 对应的模型 API")
        _check_api_protocol(previous["provider"], api_id)
        if previous["provider"] == "codex" and any(account_busy_counts(previous).values()):
            raise InstructionError("instruction.conflict", "账号仍有绑定会话或排队、运行中的执行",
                                   details={"reason": "account.busy", **account_busy_counts(previous)})
    row = store.update_registration(account_id, dict(inv.argument("changes") or {}),
                                    _registration_expected_version(inv, account_id, minimum=1), inv.caller)
    # Disabled accounts drain through Scheduler admission; existing turns retain
    # their Provider process until explicit deletion or service maintenance.
    if row["provider"] == "codex" and row["kind"] == "api_profile":
        from . import codex_api_accounts
        if changed_api:
            codex_api_accounts.refresh([row])
        elif not previous["enabled"] and row["enabled"]:
            from .provider_updates import runtime_available
            if runtime_available("codex"):
                codex_api_accounts.prepare(row)
                host.start_codex_instance(account_id)
    elif row["provider"] == "codex" and not previous["enabled"] and row["enabled"]:
        host.start_codex_instance(account_id)
    return {"registration": _registration_public(row, inv.caller)}


def account_busy_counts(row):
    """Count real bound Provider sessions and nonterminal scheduler work."""
    from management_common.portal_database import portal_database
    provider = row["config"].get("api_id", row["account_id"]) if row["provider"] == "provider_api" else row["provider"]
    with portal_database(schema="agent").read_current() as c:
        sessions = c.execute("""SELECT COUNT(*) FROM provider_bindings b JOIN provider_sessions s
            ON s.provider_session_ref=b.provider_session_ref WHERE s.provider=%s AND s.backend_route_id=%s""",
            (provider, row["route_id"])).fetchone()[0]
    with portal_database(schema="execution_runtime").read_current() as c:
        executions = c.execute("""SELECT COUNT(*) FROM executions WHERE status IN ('queued','claimed','running')
            AND request_json::jsonb->>'provider'=%s
            AND COALESCE(request_json::jsonb->'session'->>'backend_route_id','')=%s""",
            (provider, row["route_id"])).fetchone()[0]
    return {"session_count": sessions, "execution_count": executions}


def delete_provider_account(inv):
    from . import account_registrations as store
    from . import account_hosting as host
    from .account_login import delete_login_data
    row = _registered_account(inv.argument("account_id"))
    busy = account_busy_counts(row)
    if any(busy.values()):
        raise InstructionError("instruction.conflict", "账号仍有绑定会话或排队、运行中的执行",
                               details={"reason": "account.busy", **busy})
    store.require_no_role_references(row["account_id"])
    if row["provider"] == "codex":
        host.remove_codex_instance(row["account_id"])
    if row["provider"] == "kimi":
        from management_portal.kimi_account_runtime import remove_account
        remove_account(row)
    delete_login_data(row["account_id"])
    # A Codex API account's home holds its key in auth.json.
    if row["kind"] == "subscription_login" or row["provider"] == "codex":
        host.remove_account_home(row["provider"], row["account_id"])
    store.delete_registration(row["account_id"])
    return {"account_id": row["account_id"]}


def list_backend_roles(inv):
    from .account_registrations import roles
    return {"roles": roles()}


def update_backend_role(inv):
    from .account_registrations import update_role
    if inv.argument("verification_execution_id"):
        from .account_setup import require_verified
        require_verified(inv)
    role = inv.argument("backend_role")
    return {"role": update_role(role, inv.argument("account_id"),
                                inv.argument("model") or "", inv.argument("reasoning_effort") or "",
                                _registration_expected_version(inv, role, minimum=0), inv.caller)}


def get_execution_settings(inv: Invocation) -> dict[str, Any]:
    from management_portal import permission_system
    if not permission_system.allows(inv.caller, "GetExecutionSettings"):
        raise InstructionError("instruction.forbidden", "无权查看执行设置")
    params = {"actor": inv.caller}
    if _SERVER is not None:
        settings = _SERVER.dispatch("settings.get", params)
    else:
        settings = _client().settings(actor=inv.caller)
    return {"settings": settings}


def _accounts_snapshot(refresh: bool) -> dict[str, Any]:
    if _SERVER is not None:
        if refresh:
            _SERVER.quota_monitor.request_refresh()
        return _SERVER.quota_monitor.snapshot()
    return _client().accounts(force_refresh=refresh)


def list_provider_accounts(inv: Invocation) -> dict[str, Any]:
    snapshot = _accounts_snapshot(bool(inv.argument("refresh")))
    accounts = [item for item in snapshot.get("accounts") or [] if isinstance(item, dict)]
    from .account_registrations import registrations
    by_id = {row["account_id"]: row for row in registrations()}
    present = {item.get("account_id") or item.get("id") for item in accounts}
    accounts.extend({"account_id": key, "provider": row["provider"]} for key, row in by_id.items() if key not in present)
    for item in accounts:
        row = by_id.get(item.get("account_id") or item.get("id"))
        if row:
            public = _registration_public(row, inv.caller)
            item.update({"registration": public, "kind": row["kind"], "enabled": row["enabled"],
                         "login_state": public["login_state"], "registration_version": row["version"]})
    account_id = inv.argument("account_id")
    if account_id:
        accounts = [item for item in accounts if str(item.get("account_id") or item.get("id") or "") == account_id]
        if not accounts:
            raise not_found(f"执行账号不存在：{account_id}")
    from .accounts import default_execution_accounts

    catalog = default_execution_accounts(tolerate_unavailable=True)
    default_routes = {}
    for provider, adapter in catalog.adapters.items():
        if adapter.accounts():
            try:
                default_routes[provider] = catalog.resolve(provider).route_id
            except ValueError:
                default_routes[provider] = ""
    return {"accounts": accounts, "default_routes": default_routes}


def update_provider_account(inv: Invocation) -> dict[str, Any]:
    low = float(inv.argument("dispatch_interval_min_seconds"))
    high = float(inv.argument("dispatch_interval_max_seconds"))
    if low > high:
        raise invalid("dispatch_interval_min_seconds 不能大于 dispatch_interval_max_seconds")
    close_threshold = None
    if inv.argument("close_threshold_percent") is not None:
        close_threshold = {"value": float(inv.argument("close_threshold_percent")), "unit": "%"}
    elif inv.argument("close_threshold_amount") is not None:
        account = next(
            (item for item in _accounts_snapshot(False).get("accounts") or []
             if str(item.get("account_id") or "") == inv.argument("account_id")),
            {},
        )
        unit = str((account.get("close_threshold") or {}).get("unit") or "")
        if not unit or unit == "%":
            raise invalid("这个账号按百分比计算额度，请使用 close_threshold_percent")
        close_threshold = {"value": float(inv.argument("close_threshold_amount")), "unit": unit}
    settings = _client().configure_account_scheduler(
        inv.argument("account_id"),
        int(inv.argument("max_concurrency")),
        low,
        high,
        actor=inv.caller,
        close_threshold=close_threshold,
    )
    return {"settings": settings}


def _local_zone():
    """Usage periods follow the server's local time zone, as Home Usage does (decision_fc26f883773d)."""
    return datetime.now().astimezone().tzinfo


def _period_start(period: str, now: datetime) -> datetime:
    today = now.astimezone(_local_zone()).replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        today -= timedelta(days=today.weekday())
    elif period == "month":
        today = today.replace(day=1)
    elif period == "year":
        today = today.replace(month=1, day=1)
    return today.astimezone(timezone.utc)


def _hour(moment: datetime) -> str:
    return moment.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")


def _usage_rows(start: datetime, end: datetime, provider: str, include_accounts: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Hourly rows over [start, end) read in windows the scheduler accepts, and the source's state."""
    rows: list[dict[str, Any]] = []
    updated_at = ""
    missing: list[dict[str, str]] = []
    windows = 0
    read_windows = []
    cursor = start
    while cursor < end:
        window_end = min(end, cursor + timedelta(days=MAX_USAGE_WINDOW_DAYS))
        if _hour(window_end) <= _hour(cursor):
            break
        windows += 1
        source = _SERVER.scheduler if _SERVER is not None else _client()
        read_started_at = usage_read_model.collection_time()
        window_status = "ok"
        try:
            result = source.token_usage(
                start_hour=_hour(cursor), end_hour=_hour(window_end), provider=provider,
                include_accounts=include_accounts,
            )
        except Exception as exc:  # noqa: BLE001 - one unreadable window leaves the others usable
            window_status = "error"
            missing.append({"start": _hour(cursor), "end": _hour(window_end), "error": str(exc)[:300]})
        else:
            rows.extend(result.get("rows") or [])
            updated_at = max(updated_at, str(result.get("updated_at") or ""))
        read_windows.append({"start": _hour(cursor), "end": _hour(window_end),
                             "read_started_at": read_started_at,
                             "read_finished_at": usage_read_model.collection_time(), "status": window_status})
        cursor = window_end
    if windows and len(missing) == windows:
        raise InstructionError("instruction.owner_unavailable", "token 用量暂时无法读取", retryable=True,
                               details={"missing_windows": missing})
    return rows, {"status": "partial" if missing else "ok", "updated_at": updated_at, "missing_windows": missing, "read_windows": read_windows}


def _usage_roots(actor: str) -> dict[str, dict[str, str]]:
    # Share Home's independently authorized Resident/Lineage scope and one
    # canonical membership batch, rather than reading each Resident again.
    from management_portal.society_service.resource_client import visible_resident_usage_roots

    try:
        return visible_resident_usage_roots(actor=actor)
    except Exception as exc:
        raise InstructionError("instruction.owner_unavailable", "用量归属暂时无法读取", retryable=True) from exc


USAGE_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")


def _usage_tokens(usage: dict[str, Any], include_cached: bool) -> dict[str, int]:
    """Input includes its cached part and output its reasoning part; excluding cache removes it from input."""
    tokens = {key: int(usage.get(key) or 0) for key in USAGE_KEYS}
    if not include_cached:
        tokens["input_tokens"] = max(0, tokens["input_tokens"] - tokens["cached_input_tokens"])
        tokens["cached_input_tokens"] = 0
    from .contracts import usage_total
    tokens["total_tokens"] = usage_total(usage, include_cached_input=include_cached)
    return tokens


def _add_tokens(bucket: dict[str, int], tokens: dict[str, int]) -> None:
    for key, value in tokens.items():
        bucket[key] = bucket.get(key, 0) + value


def _series_bucket(period: str, hour: str) -> str:
    """today: hourly; week and month: daily; year: monthly; all in local time."""
    try:
        local = datetime.fromisoformat(hour.replace("Z", "+00:00")).astimezone(_local_zone())
    except ValueError:
        return hour
    if period == "today":
        return local.isoformat(timespec="seconds")
    if period == "year":
        return local.strftime("%Y-%m")
    return local.strftime("%Y-%m-%d")


def get_usage(inv: Invocation) -> dict[str, Any]:
    collection_started_at = usage_read_model.collection_time()
    now = datetime.now(timezone.utc)
    period = inv.argument("period")
    start = _period_start(period, now)
    end = now + timedelta(hours=1)
    provider = inv.argument("provider") or "*"
    account_id = inv.argument("account_id") or ""
    rows, source = _usage_rows(start, end, provider, bool(account_id))
    if account_id:
        rows = [row for row in rows if row.get("account_id") == account_id]
    # Home Usage counts cache only when "Include cache" is ticked (decision_fc26f883773d);
    # the instruction defaults the same way so both read the same total.
    include_cached = inv.argument("include_cached") is True
    group_by = inv.argument("group_by")
    visible = _usage_roots(str(getattr(inv, "caller", "") or ""))
    grouped_buckets: dict[str, dict[str, int]] = {}
    totals: dict[str, int] = {}
    series: dict[str, dict[str, int]] = {}
    groups: dict[str, dict[str, int]] = {}
    for row in rows:
        tokens = _usage_tokens(row.get("usage") or {}, include_cached)
        resident_id = str(row.get("resident_id") or "")
        root = visible.get(resident_id) or {}
        key = (root.get(f"{group_by}_id", "") if group_by in {"lineage", "project"} else resident_id) if root else ""
        _add_tokens(totals, tokens)
        _add_tokens(series.setdefault(_series_bucket(period, str(row.get("bucket_hour") or "")), {}), tokens)
        _add_tokens(groups.setdefault(key, {}), tokens)
        bucket = grouped_buckets.setdefault(_series_bucket(period, str(row.get("bucket_hour") or "")), {})
        bucket[key] = bucket.get(key, 0) + tokens["total_tokens"]
    if period == "today":
        local_start = now.astimezone(_local_zone()).replace(hour=0, minute=0, second=0, microsecond=0)
        for hour in range(24):
            grouped_buckets.setdefault((local_start + timedelta(hours=hour)).isoformat(timespec="seconds"), {})
    # Like Home Usage Attribution (decision_fc26f883773d), the ranking names only what the
    # caller may read; the rest is merged into one anonymous entry. Totals stay complete.
    anonymous: dict[str, int] = {}
    named: dict[str, dict[str, int]] = {}
    for key, tokens in groups.items():
        if key:
            named[key] = tokens
        else:
            _add_tokens(anonymous, tokens)
    ranking = [
        {"id": key, "tokens": tokens}
        for key, tokens in sorted(named.items(), key=lambda item: (-item[1].get("total_tokens", 0), item[0]))
    ]
    if anonymous:
        ranking.append({"id": "", "anonymous": True, "tokens": anonymous})
    return {"usage": {
        "period": period,
        "group_by": group_by,
        "include_cached": include_cached,
        "start": _hour(start),
        "end": _hour(end),
        "totals": totals or _usage_tokens({}, include_cached),
        "series": [{"bucket": bucket, "tokens": tokens} for bucket, tokens in sorted(series.items())],
        "ranking": ranking,
        "bucket_groups": usage_read_model.bucket_groups(grouped_buckets),
        "collection_started_at": collection_started_at,
        "collection_finished_at": usage_read_model.collection_time(),
        "collection_windows": source["read_windows"],
        "updated_at": source["updated_at"],
        "source": {"status": source["status"], "provider": provider, "account_id": account_id,
                   "missing_windows": source["missing_windows"]},
    }}




from .provider_usage_events import get_provider_usage_events
from .account_login import HANDLERS as LOGIN_HANDLERS
from .account_setup import HANDLERS as SETUP_HANDLERS


HANDLERS = {
    **LOGIN_HANDLERS,
    **SETUP_HANDLERS,
    "ListProviders": list_providers,
    "RegisterProviderAccount": register_provider_account,
    "UpdateProviderAccountRegistration": update_provider_account_registration,
    "DeleteProviderAccount": delete_provider_account,
    "ListBackendRoles": list_backend_roles,
    "UpdateBackendRole": update_backend_role,
    "GetExecutionSettings": get_execution_settings,
    "GetProviderUsageEvents": get_provider_usage_events,
    "ListModelApis": list_model_apis,
    "CreateModelApi": create_model_api,
    "UpdateModelApi": update_model_api,
    "DeleteModelApi": delete_model_api,
    "CompleteChat": complete_chat,
    "ProbeModelApi": probe_model_api,
    "ListProviderAccounts": list_provider_accounts,
    "UpdateProviderAccount": update_provider_account,
    "GetUsage": get_usage,
}


def version_of(inv: Invocation, object_id: str) -> Any:
    """A model API's registration version for the model API instructions.

    Account registrations and Backend roles compare their own versions in the
    handler; an account or role named like a model API must not be compared
    against that API's version.
    """

    if inv.declaration.target != "api_id":
        return None
    return model_api_store.api_version(object_id)


def binding() -> OwnerBinding:
    return OwnerBinding(owner="execution", handlers=HANDLERS, streams={}, version_of=version_of, recover_projections=recover_projections)


def build_kernel() -> InstructionKernel:
    from management_common.portal_database import portal_database
    from management_portal.hatg.store import ExecutionStore

    return InstructionKernel(binding(), store=ExecutionStore(portal_database(schema="execution_instructions")))
