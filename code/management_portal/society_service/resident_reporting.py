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


def _policy_projection(record: dict) -> dict:
    from .resident_reporting_policy import policy_for_record

    policy = policy_for_record(record)
    selected = {key: policy[key] for key in DEFAULT}
    digest = json.dumps([record["resident_id"], selected, policy["configured"]], sort_keys=True)
    return {
        "reporting": selected,
        "reporting_effective": policy,
        "reporting_revision": hashlib.sha256(digest.encode()).hexdigest(),
    }


def _application(resident_id: str, application: dict) -> dict:
    return {**application, "schema": "tfi.resident-application.v1",
            "items": [*application.get("items", []), {
                "path": "reporting", "status": "next_turn", "delivery": "unknown",
                "reason": "configured_for_future_input_without_delivery_observation",
                "source_resident_id": resident_id,
            }]}


def read(resident_id: str, actor: str) -> dict:
    from management_portal import permission_system
    from . import family_facade, resident_access, resident_catalog, resident_execution_domain as execution

    with records.state_lock(resident_id):
        record = _authorized(resident_id, actor)
        reporting = _policy_projection(record)
        target_id = execution.settings_router_id(record) if family_facade.is_facade(record) else resident_id

    result = {"runtime_settings": {}, "application": {}, "settings_revision": "",
              "runtime_settings_access": "unavailable", "runtime_settings_resident_id": ""}
    if target_id:
        with records.state_lock(target_id):
            target = records.find_resident(target_id)
            if (target and not target.get("deleted_at")
                    and resident_access.resident_visible_to_actor(target, actor)
                    and permission_system.allows(actor, "GetResidentSettings", {"resident_id": target_id})
                    and resident_access.has_resident_workspace_authority(target, actor)
                    and resident_catalog.registration_ready(target)):
                result = execution.read_resident_runtime_settings(target_id, actor)
                result["runtime_settings_resident_id"] = target_id
                result["runtime_settings_access"] = "available"
    result.update(reporting, runtime_settings_revision=result["settings_revision"])
    result["application"] = _application(resident_id, result["application"])
    return result


def update(resident_id: str, actor: str, reporting: dict, *, expected_revision: str | None = None) -> dict:
    from . import changes, resident_catalog
    from .errors import LineageConflictError

    policy = normalize(reporting)
    if expected_revision is not None and (not isinstance(expected_revision, str) or not expected_revision):
        raise ValueError("expected_revision must be a nonempty reporting revision")
    with records.state_lock(resident_id):
        record = _authorized(resident_id, actor, "UpdateResidentReporting")
        result = _policy_projection(record)
        if expected_revision is not None and expected_revision != result["reporting_revision"]:
            raise LineageConflictError("Resident reporting revision changed", resource="resident_reporting")
        changed = not result["reporting_effective"]["configured"] or policy != result["reporting"]
        if changed:
            resident_catalog.get_catalog().state_store.write_resident_reporting(resident_id, policy)
            changes.resident_changed(resident_id, scope="runtime")
            result = _policy_projection({**record, "reporting": policy})
        return {**result, "resident_id": resident_id, "next_turn_fields": ["reporting"] if changed else [],
                "application": _application(resident_id, {"reporting_revision": result["reporting_revision"]})}


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
