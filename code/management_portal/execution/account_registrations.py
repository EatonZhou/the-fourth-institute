"""Versioned execution identities and backend roles, owned by ExecutionService.

Scheduler settings and login observations have their own owners. Registration
contains only identity, initial capacity and non-secret Provider configuration.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

from psycopg.types.json import Jsonb

from management_common.domain_database import DomainDatabase
from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.model_api_store import now

PROVIDER_KINDS = {
    "codex": ("subscription_login", "api_profile"), "claude": ("subscription_login", "api_profile"),
    "kimi": ("service",), "dsh": ("api_profile",),
    "opencode": ("service", "api_profile"), "provider_api": ("api_profile",),
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
DDL = """
CREATE TABLE IF NOT EXISTS registrations (
    account_id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL,
    route_id TEXT NOT NULL DEFAULT '', config JSONB NOT NULL DEFAULT '{}',
    max_concurrency INTEGER NOT NULL CHECK(max_concurrency > 0),
    enabled BOOLEAN NOT NULL DEFAULT TRUE, version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL, created_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backend_roles (
    role TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES registrations(account_id),
    model TEXT NOT NULL DEFAULT '', reasoning_effort TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL, created_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("execution_accounts", "registrations", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def _decode(row):
    if row is None:
        return None
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in dict(row).items()}


def registrations(provider=None):
    with database().read() as c:
        rows = c.execute("SELECT * FROM registrations WHERE (%s::text IS NULL OR provider=%s) ORDER BY created_at,account_id",
                         (provider, provider))
        return [_decode(row) for row in rows]


def registration(account_id):
    with database().read() as c:
        return _decode(c.execute("SELECT * FROM registrations WHERE account_id=%s", (account_id,)).fetchone())


def _validate(data):
    if not IDENTIFIER.fullmatch(str(data.get("account_id") or "")):
        raise invalid("account_id 只允许小写字母、数字、下划线和短横线")
    provider, kind = data.get("provider"), data.get("kind")
    if provider not in PROVIDER_KINDS or kind not in PROVIDER_KINDS[provider]:
        raise invalid("Provider 不支持这个账号种类")
    route = data.get("route_id", "")
    if not isinstance(route, str) or (route and not IDENTIFIER.fullmatch(route)):
        raise invalid("route_id 格式无效")
    config = data.get("config", {})
    # A Codex API account can be the default route; it never falls back to a subscription.
    allowed = ({"api_id", "is_default"} if provider == "codex" else {"api_id"}) if kind == "api_profile" else \
        {"fallback_account_ids", "is_default"} if provider == "codex" else {"models", "default_model"} if provider == "claude" else set()
    if not isinstance(config, dict) or set(config) - allowed:
        raise invalid("config 含不支持的 Provider 字段")
    if kind == "api_profile" and not IDENTIFIER.fullmatch(str(config.get("api_id") or "")):
        raise invalid("api_profile 必须指定 config.api_id")
    fallbacks = config.get("fallback_account_ids", [])
    if not isinstance(fallbacks, list) or any(not isinstance(v, str) or not IDENTIFIER.fullmatch(v) for v in fallbacks):
        raise invalid("fallback_account_ids 必须是账号 ID 数组")
    if len(fallbacks) != len(set(fallbacks)) or data["account_id"] in fallbacks:
        raise invalid("备用账号不能重复或指向自身")
    if "is_default" in config and type(config["is_default"]) is not bool:
        raise invalid("is_default 必须是 boolean")
    if "models" in config and (not isinstance(config["models"], list) or any(not isinstance(v, (str, dict)) for v in config["models"])):
        raise invalid("models 必须是模型数组")
    limit = data.get("max_concurrency", 8)
    if type(limit) is not int or not 1 <= limit <= 256:
        raise invalid("max_concurrency 必须在 1–256 之间")
    if type(data.get("enabled", True)) is not bool:
        raise invalid("enabled 必须是 boolean")


def create_registration(data, actor):
    data = dict(data)
    if not data.get("route_id") and data.get("provider") not in {"provider_api", "kimi"}:
        data["route_id"] = str(data.get("account_id") or "").removeprefix("codex_") if data.get("provider") == "codex" else data.get("account_id", "")
    _validate(data)
    with database().transaction("registrations") as c:
        if c.execute("SELECT 1 FROM registrations WHERE account_id=%s", (data["account_id"],)).fetchone():
            return None
        _check_relationships(c, data)
        return _decode(c.execute("""INSERT INTO registrations
            (account_id,provider,kind,route_id,config,max_concurrency,enabled,version,created_at,created_by,updated_at,updated_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
            ON CONFLICT(account_id) DO NOTHING RETURNING *""",
            (data["account_id"], data["provider"], data["kind"], data.get("route_id", ""),
             Jsonb(data.get("config", {})), data.get("max_concurrency", 8), data.get("enabled", True),
             now(), actor, now(), actor)).fetchone())


def update_registration(account_id, changes, expected_version, actor):
    if set(changes) - {"config", "route_id", "enabled", "max_concurrency"}:
        raise invalid("账号身份与 Provider 不能修改")
    with database().transaction("registrations") as c:
        row = _decode(c.execute("SELECT * FROM registrations WHERE account_id=%s FOR UPDATE", (account_id,)).fetchone())
        if row is None:
            raise not_found("执行账号不存在")
        _check_version(account_id, row["version"], expected_version, "账号登记版本已改变")
        row.update(changes)
        _validate(row)
        _check_relationships(c, row)
        return _decode(c.execute("""UPDATE registrations SET config=%s,route_id=%s,enabled=%s,
            max_concurrency=%s,version=version+1,updated_at=%s,updated_by=%s WHERE account_id=%s RETURNING *""",
            (Jsonb(row["config"]), row["route_id"], row["enabled"], row["max_concurrency"], now(), actor, account_id)).fetchone())


def _check_relationships(c, row):
    if row["provider"] != "provider_api" and c.execute("SELECT 1 FROM registrations WHERE provider=%s AND route_id=%s AND account_id<>%s",
                                                       (row["provider"], row.get("route_id", ""), row["account_id"])).fetchone():
        raise InstructionError("instruction.conflict", "Provider 路线已被其他账号登记")
    fallbacks = row.get("config", {}).get("fallback_account_ids", [])
    if fallbacks and c.execute("SELECT 1 FROM registrations WHERE provider=%s AND kind='api_profile' AND account_id = ANY(%s)",
                               (row["provider"], fallbacks)).fetchone():
        # Falling back from a subscription to an API key would spend its balance unasked.
        raise invalid("备用账号不能是 API 账号")
    if row["provider"] == "codex" and row.get("config", {}).get("is_default") and c.execute(
        "SELECT 1 FROM registrations WHERE provider='codex' AND config->>'is_default'='true' AND account_id<>%s",
        (row["account_id"],)).fetchone():
        raise InstructionError("instruction.conflict", "Codex 已有默认账号，请先取消原默认标记")


def delete_registration(account_id):
    with database().transaction("registrations") as c:
        if c.execute("SELECT 1 FROM backend_roles WHERE account_id=%s", (account_id,)).fetchone():
            raise InstructionError("instruction.conflict", "请先修改引用该账号的 Backend 角色", details={"reason": "account.role_in_use"})
        return bool(c.execute("DELETE FROM registrations WHERE account_id=%s", (account_id,)).rowcount)


def roles():
    with database().read() as c:
        return [_decode(row) for row in c.execute("SELECT * FROM backend_roles ORDER BY role")]


def _check_version(object_id, current, expected, message):
    """Compare the caller's envelope version when given; a missing one updates the current state."""
    if expected is not None and current != expected:
        raise InstructionError("instruction.version_conflict", message, details={
            "object_id": object_id, "expected_version": expected, "current_version": current})


def update_role(role, account_id, model, reasoning_effort, expected_version, actor):
    if not IDENTIFIER.fullmatch(str(role or "")):
        raise invalid("角色名格式无效")
    with database().transaction("registrations") as c:
        account = c.execute("SELECT * FROM registrations WHERE account_id=%s", (account_id,)).fetchone()
        if account is None or not account["enabled"]:
            raise invalid("角色必须指向已启用的执行账号")
        row = c.execute("SELECT version FROM backend_roles WHERE role=%s FOR UPDATE", (role,)).fetchone()
        _check_version(role, row["version"] if row else 0, expected_version, "Backend 角色版本已改变")
        return _decode(c.execute("""INSERT INTO backend_roles
            (role,account_id,model,reasoning_effort,version,created_at,created_by,updated_at,updated_by)
            VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s) ON CONFLICT(role) DO UPDATE SET
            account_id=EXCLUDED.account_id,model=EXCLUDED.model,reasoning_effort=EXCLUDED.reasoning_effort,
            version=backend_roles.version+1,updated_at=EXCLUDED.updated_at,updated_by=EXCLUDED.updated_by RETURNING *""",
            (role, account_id, model, reasoning_effort, now(), actor, now(), actor)).fetchone())


def resolve_role(requested):
    by_role = {row["role"]: row for row in roles()}
    if requested in by_role and by_role[requested].get("account_id"):
        return by_role[requested], None
    if not by_role.get("default", {}).get("account_id"):
        raise InstructionError("instruction.conflict", "站点尚未设置 default Backend 角色",
                               details={"reason": "backend_role.default_missing"})
    return by_role["default"], {"requested": requested, "used": "default"}


def role_configuration(requested="default"):
    """Resolve a site's role once into the existing Resident configuration."""
    role, fallback = resolve_role(requested)
    row = registration(role["account_id"])
    if not row or not row["enabled"]:
        raise invalid("Backend 角色引用的账号已停用或不存在")
    settings = {"backend_role": requested}
    if role["model"]:
        settings["model"] = role["model"]
    if role["reasoning_effort"]:
        settings["reasoning_effort"] = role["reasoning_effort"]
    if fallback:
        settings["backend_role_fallback"] = fallback
    return {"backend": row["provider"],
            "backend_route_id": row["config"]["api_id"] if row["provider"] == "provider_api" else row["route_id"],
            "runtime_settings": settings}
