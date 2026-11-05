"""Society owns reporting intent; Execution receives only the materialized prompt."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import resident_record_domain as records

from .resident_reporting_policy import DEFAULT, normalize, configured, effective, prompt  # noqa: F401 - public policy surface


def _authorized(resident_id: str, actor: str, operation: str = "GetResidentSettings") -> dict:
    # Do not resolve a facade through its Router. Only this object's access is
    # relevant to its reporting configuration.
    from management_portal import permission_system
    from . import resident_access
    record = records.find_resident(resident_id)
    if not record or record.get("deleted_at"):
        raise KeyError("resident not found")
    if (not resident_access.can_manage_resident(record, actor, operation=operation)
            or not permission_system.allows(actor, operation, {"resident_id": resident_id})):
        raise PermissionError("无权访问该 Resident 的设置")
    records.require_resident_registration_ready(record)
    return record


def _runtime_target(record: dict, actor: str) -> str:
    from . import family_facade, family_familize, lineage_aggregate, resident_access
    identity = record["resident_id"]
    if not family_facade.is_facade(record):
        return identity
    lineage = lineage_aggregate.read_lineage_state(record["lineage_id"])
    router = family_familize.router_of_facade(lineage, identity)
    target = records.find_resident(router) if router else None
    from management_portal import permission_system
    if (not target or not resident_access.resident_visible_to_actor(target, actor)
            or not permission_system.allows(actor, "GetResidentSettings", {"resident_id": router})
            or not resident_access.has_resident_workspace_authority(target, actor)):
        return ""
    return router


def read(resident_id: str, actor: str) -> dict:
    from . import resident_execution_domain as execution
    with records.state_lock(resident_id):
        record = _authorized(resident_id, actor)
        target = _runtime_target(record, actor)
        result = execution.read_resident_runtime_settings(target, actor) if target else {
            "runtime_settings": {}, "application": {}, "settings_revision": "",
        }
        policy = effective(resident_id)
        selected = {key: policy[key] for key in DEFAULT}
        result["runtime_settings"] = {**result["runtime_settings"], "reporting": selected}
        result["runtime_settings_access"] = "available" if target else "unavailable"
        result["reporting_effective"] = policy
        digest = json.dumps([result["settings_revision"], selected, bool(record.get("reporting"))], sort_keys=True)
        result["settings_revision"] = hashlib.sha256(digest.encode()).hexdigest()
        result["application"] = {**result["application"],
            "schema": "tfi.resident-application.v1", "settings_revision": result["settings_revision"],
            "items": [*result["application"].get("items", []), {
                "path": "reporting", "status": "next_turn", "delivery": "unknown",
                "reason": "configured_for_future_input_without_delivery_observation",
                "source_resident_id": resident_id,
            }]}
        return result


def update(resident_id: str, actor: str, payload: dict) -> dict:
    from . import resident_catalog, resident_execution_domain as execution
    from .errors import LineageConflictError
    if not isinstance(payload, dict):
        raise ValueError("settings payload must be an object")
    if "runtime_settings" in payload and (
            set(payload) - {"runtime_settings", "expected_revision"}
            or not isinstance(payload["runtime_settings"], dict)):
        raise ValueError("runtime_settings envelope must contain an object and no extra fields")
    changes = dict(payload.get("runtime_settings", payload))
    expected = payload.get("expected_revision")
    if "expected_revision" in payload and (not isinstance(expected, str) or not expected):
        raise ValueError("expected_revision must be a nonempty settings revision")
    changes.pop("expected_revision", None)
    if not changes:
        raise ValueError("runtime_settings required")
    policy = normalize(changes.pop("reporting")) if "reporting" in changes else None
    with records.state_lock(resident_id):
        record = _authorized(resident_id, actor, "UpdateResidentSettings")
        before = read(resident_id, actor)
        if expected is not None and expected != before["settings_revision"]:
            raise LineageConflictError("Resident settings revision changed", resource="resident_runtime_settings")
        target = _runtime_target(record, actor)
        if changes and not target:
            raise PermissionError("无权修改 Router 的运行设置")
        if changes:
            from management_portal import permission_system
            if not permission_system.allows(actor, "UpdateResidentSettings", {"resident_id": target}):
                raise PermissionError("无权修改该执行 Resident 的运行设置")
        before_revision = before["settings_revision"]

    # Catalog validation may perform RPC; it precedes the short CAS commit.
    prepared = execution.prepare_resident_runtime_settings_change(target, actor, {"runtime_settings": changes}) if changes else None
    with records.state_lock(resident_id):
        record = _authorized(resident_id, actor, "UpdateResidentSettings")
        if read(resident_id, actor)["settings_revision"] != before_revision or _runtime_target(record, actor) != target:
            raise LineageConflictError("Resident settings changed during validation", resource="resident_runtime_settings")
        changed = policy is not None and (not record.get("reporting") or policy != configured(record))
        def commit():
            next_fields = []
            if changes:
                saved = prepared()
                next_fields.extend(saved.get("next_turn_fields", []))
            if changed:
                fresh = records.find_resident(resident_id)
                fresh["reporting"] = policy
                records.save_resident_record(fresh, commit_configuration=True)
                next_fields.append("reporting")
            result = read(resident_id, actor)
            result["next_turn_fields"] = next_fields
            result["active_turn_fields"] = []
            result["active_turn_settings_update"] = {"requested": False, "status": "next_turn" if next_fields else "unchanged"}
            return result
        return resident_catalog.get_catalog().state_store.transaction(commit)


def install_schema(connection: Any) -> dict:
    """Explicit additive owner migration; called by the authorized integrator."""
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':schema-init', 0))")
    relation = connection.execute(
        "SELECT relkind FROM pg_class WHERE oid=to_regclass("
        "quote_ident(current_schema()) || '.resident_execution_configuration')").fetchone()
    if not relation or relation["relkind"] not in {"r", "p"}:
        raise ValueError("Reporting upgrade requires a Resident configuration table")
    columns = {r["column_name"]:r for r in connection.execute(
        "SELECT column_name,data_type,is_nullable FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='resident_execution_configuration'").fetchall()}
    expected = {"resident_id": {"text"}, "execution_settings_json": {"text", "jsonb", "json"},
                "harness_config_json": {"text", "jsonb", "json"}}
    for name, types in expected.items():
        if name not in columns or columns[name]["data_type"] not in types:
            raise ValueError(f"Unexpected Resident configuration column: {name}")
    # Fresh owner schemas store objects as JSONB; the additive legacy upgrade
    # uses TEXT. Both are readable/writable by the same configuration owner.
    if "reporting_json" in columns and (columns["reporting_json"]["data_type"] not in {"text", "jsonb"}
                                       or columns["reporting_json"]["is_nullable"] != "YES"):
        raise ValueError("Reporting column must be nullable TEXT or JSONB")
    version = None
    if connection.execute("SELECT to_regclass(quote_ident(current_schema()) || '.lineage_meta') AS name").fetchone()["name"]:
        version = connection.execute(
            "SELECT to_jsonb(meta)->>'value' AS value FROM lineage_meta meta "
            "WHERE to_jsonb(meta)->>'key'='schema'").fetchone()
    if "reporting_json" not in columns:
        connection.execute("ALTER TABLE resident_execution_configuration ADD COLUMN reporting_json TEXT")
    return {"schema":str(version["value"] or "") if version else "", "changed":"reporting_json" not in columns,
            "default":{"initiative":"off","quality":"medium"}}
