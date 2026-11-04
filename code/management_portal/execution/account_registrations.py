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
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")

PROVIDER_KINDS = {
    "antigravity": ("subscription_login",),
    "codex": ("subscription_login", "api_profile"), "claude": ("subscription_login", "api_profile"),
    "kimi": ("subscription_login", "service", "api_profile"), "dsh": ("api_profile",),
    "opencode": ("service", "api_profile"), "provider_api": ("api_profile",),
}
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{0,127}$")
DDL = """
CREATE TABLE IF NOT EXISTS registrations (
    account_id TEXT PRIMARY KEY, provider TEXT NOT NULL, kind TEXT NOT NULL,
    route_id TEXT NOT NULL DEFAULT '', config JSONB NOT NULL DEFAULT '{}',
    directory_origin TEXT GENERATED ALWAYS AS
        (CASE WHEN config ? 'account_dir' THEN 'adopted'::text ELSE NULL::text END) STORED,
    directory_owner TEXT GENERATED ALWAYS AS
        (CASE WHEN config ? 'account_dir' THEN 'host'::text ELSE NULL::text END) STORED,
    max_concurrency INTEGER NOT NULL CHECK(max_concurrency > 0),
    enabled BOOLEAN NOT NULL DEFAULT TRUE, version INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL, created_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backend_roles (
    role TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES registrations(account_id),
    account_selection TEXT NOT NULL DEFAULT 'auto' CHECK(account_selection IN ('auto','fixed')),
    model TEXT NOT NULL DEFAULT '', reasoning_effort TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL, created_by TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL, updated_by TEXT NOT NULL
);
ALTER TABLE backend_roles ADD COLUMN IF NOT EXISTS account_selection TEXT NOT NULL DEFAULT 'auto' CHECK(account_selection IN ('auto','fixed'));
ALTER TABLE backend_roles ADD COLUMN IF NOT EXISTS backend_priority JSONB NOT NULL DEFAULT '[]';
UPDATE registrations SET config=config - 'is_default' - 'fallback_account_ids' WHERE config ?| ARRAY['is_default','fallback_account_ids'];
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


def config_fields(provider, kind):
    """The config keys a registration of this Provider and kind accepts."""
    if kind == "api_profile":
        return {"api_id"}
    fields = {"models", "default_model"} if provider == "claude" else set()
    return fields | ({"account_dir"} if kind == "subscription_login" else set())


def default_route(rows):
    """A sole route is a catalog convenience, never an account-selection policy."""
    enabled = [row for row in rows if row["enabled"]]
    return enabled[0]["route_id"] if len(enabled) == 1 else ""


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
    allowed = config_fields(provider, kind)
    if not isinstance(config, dict) or set(config) - allowed:
        raise invalid("config 含不支持的 Provider 字段")
    if kind == "api_profile" and not IDENTIFIER.fullmatch(str(config.get("api_id") or "")):
        raise invalid("api_profile 必须指定 config.api_id")
    if kind == "subscription_login" and "account_dir" not in config:
        raise invalid("订阅账号必须指定 config.account_dir")
    if "account_dir" in config:
        from management_common.provider_account_paths import canonical_directory
        try:
            canonical_directory(config["account_dir"])
        except ValueError as exc:
            raise invalid(str(exc)) from None
    if "models" in config and (not isinstance(config["models"], list) or any(not isinstance(v, (str, dict)) for v in config["models"])):
        raise invalid("models 必须是模型数组")
    limit = data.get("max_concurrency", 8)
    if type(limit) is not int or not 1 <= limit <= 256:
        raise invalid("max_concurrency 必须在 1–256 之间")
    if type(data.get("enabled", True)) is not bool:
        raise invalid("enabled 必须是 boolean")


def create_registration(data, actor):
    data = dict(data)
    if not data.get("route_id") and data.get("provider") != "provider_api" and not (
            data.get("provider") == "kimi" and data.get("kind") == "service"):
        data["route_id"] = str(data.get("account_id") or "").removeprefix("codex_") if data.get("provider") == "codex" else data.get("account_id", "")
    _validate(data)
    if data["provider"] == "antigravity":
        from management_portal.antigravity_runtime import platform_key
        try:
            platform_key()
        except RuntimeError:
            raise invalid("当前平台暂不支持 Antigravity 订阅账号，未创建账号登记") from None
    created = None
    try:
        with database().transaction("registrations") as c:
            if c.execute("SELECT 1 FROM registrations WHERE account_id=%s", (data["account_id"],)).fetchone():
                return None
            _check_relationships(c, data)
            from management_common.provider_account_paths import prepare_registration_directory
            try:
                created = prepare_registration_directory(data)
            except (OSError, ValueError) as exc:
                raise invalid(str(exc)) from None
            _check_relationships(c, data)
            return _decode(c.execute("""INSERT INTO registrations
                (account_id,provider,kind,route_id,config,max_concurrency,enabled,version,created_at,created_by,updated_at,updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
                ON CONFLICT(account_id) DO NOTHING RETURNING *""",
                (data["account_id"], data["provider"], data["kind"], data.get("route_id", ""),
                 Jsonb(data.get("config", {})), data.get("max_concurrency", 8), data.get("enabled", True),
                 now(), actor, now(), actor)).fetchone())
    except BaseException:
        if created is not None:
            try:
                created.rmdir()  # Only our still-empty directory; never Provider files.
            except OSError:
                pass
        raise


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
        if row["kind"] == "subscription_login":
            if not row["config"].get("account_dir"):
                raise invalid("订阅登记必须保留 config.account_dir")
            from management_common.provider_account_paths import registered_home, prepare_registration_directory
            if not registered_home(row).is_dir():
                raise invalid("账号目录不存在")
            try:
                prepare_registration_directory(row)
            except (OSError, ValueError) as exc:
                raise invalid(str(exc)) from None
        _check_relationships(c, row)
        return _decode(c.execute("""UPDATE registrations SET config=%s,route_id=%s,enabled=%s,
            max_concurrency=%s,version=version+1,updated_at=%s,updated_by=%s WHERE account_id=%s RETURNING *""",
            (Jsonb(row["config"]), row["route_id"], row["enabled"], row["max_concurrency"], now(), actor, account_id)).fetchone())


def _check_relationships(c, row):
    directory = row.get("config", {}).get("account_dir")
    if directory and c.execute("SELECT 1 FROM registrations WHERE config->>'account_dir'=%s AND account_id<>%s",
                               (directory, row["account_id"])).fetchone():
        raise InstructionError("instruction.conflict", "账号目录已在本实例登记")
    if row["provider"] != "provider_api" and c.execute("SELECT 1 FROM registrations WHERE provider=%s AND route_id=%s AND account_id<>%s",
                                                       (row["provider"], row.get("route_id", ""), row["account_id"])).fetchone():
        raise InstructionError("instruction.conflict", "Provider 路线已被其他账号登记")


def require_no_role_references(account_id, connection=None):
    if connection is None:
        with database().read() as c:
            return require_no_role_references(account_id, c)
    names = [row["role"] for row in connection.execute(
        "SELECT role FROM backend_roles WHERE account_id=%s ORDER BY role", (account_id,))]
    if names:
        raise InstructionError("instruction.conflict", "请先修改引用该账号的 Backend 角色",
                               details={"reason": "account.role_in_use", "roles": names})


def delete_registration(account_id):
    with database().transaction("registrations") as c:
        require_no_role_references(account_id, c)
        return bool(c.execute("DELETE FROM registrations WHERE account_id=%s", (account_id,)).rowcount)


def roles():
    with database().read() as c:
        return [_decode(row) for row in c.execute("SELECT * FROM backend_roles ORDER BY role")]


def _check_version(object_id, current, expected, message):
    """Compare the caller's envelope version when given; a missing one updates the current state."""
    if expected is not None and current != expected:
        raise InstructionError("instruction.version_conflict", message, details={
            "object_id": object_id, "expected_version": expected, "current_version": current})


def update_role(role, account_id, model, reasoning_effort, expected_version, actor, account_selection="auto", backend_priority=None):
    from .account_selection import mode
    account_selection = mode(account_selection)
    if backend_priority is not None:
        from .account_migration_types import validate_priority
        backend_priority = validate_priority(backend_priority)
    if not IDENTIFIER.fullmatch(str(role or "")):
        raise invalid("角色名格式无效")
    with database().transaction("registrations") as c:
        account = c.execute("SELECT * FROM registrations WHERE account_id=%s", (account_id,)).fetchone()
        if account is None or not account["enabled"]:
            raise invalid("角色必须指向已启用的执行账号")
        row = c.execute("SELECT version,backend_priority FROM backend_roles WHERE role=%s FOR UPDATE", (role,)).fetchone()
        _check_version(role, row["version"] if row else 0, expected_version, "Backend 角色版本已改变")
        priority = backend_priority if backend_priority is not None else (row["backend_priority"] if row else [])
        return _decode(c.execute("""INSERT INTO backend_roles
            (role,account_id,model,reasoning_effort,account_selection,backend_priority,version,created_at,created_by,updated_at,updated_by)
            VALUES (%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s) ON CONFLICT(role) DO UPDATE SET
            account_id=EXCLUDED.account_id,model=EXCLUDED.model,reasoning_effort=EXCLUDED.reasoning_effort,account_selection=EXCLUDED.account_selection,backend_priority=EXCLUDED.backend_priority,
            version=backend_roles.version+1,updated_at=EXCLUDED.updated_at,updated_by=EXCLUDED.updated_by RETURNING *""",
            (role, account_id, model, reasoning_effort, account_selection, Jsonb(priority), now(), actor, now(), actor)).fetchone())


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
    settings = {"backend_role": requested, "account_selection": role["account_selection"]}
    if role["model"]:
        settings["model"] = role["model"]
    if role["reasoning_effort"]:
        settings["reasoning_effort"] = role["reasoning_effort"]
    if fallback:
        settings["backend_role_fallback"] = fallback
    return {"backend": row["provider"],
            "backend_route_id": row["config"]["api_id"] if row["provider"] == "provider_api" else row["route_id"],
            "runtime_settings": settings}
