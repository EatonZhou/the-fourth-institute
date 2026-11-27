"""Freeze effective pre-upgrade quota choices into each live Resident. Preview by default."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.json_columns import json_value
from management_common.portal_database import portal_database
from management_portal.execution.account_registrations import roles
from management_portal.society_service.family_storage import resident_roles
from management_portal.society_service.lineage_data_columns import json_column


def old_switch(settings, *, facade, family_default, role_modes):
    if facade:
        return False
    explicit = settings.get("auto_switch_on_quota")
    if type(explicit) is bool:
        return explicit
    if family_default is not None:
        return family_default
    if "account_selection" in settings:
        return settings["account_selection"] == "auto"
    role = settings.get("backend_role")
    return bool(role and role_modes.get(role, role_modes.get("default")) == "auto")


def migrate(apply=False):
    modes = {row["role"]: row["account_selection"] for row in roles()}
    counts = Counter()
    database = portal_database(schema="lineage")
    with database.transaction() as connection:
        connection.execute("SET LOCAL lock_timeout='5s'")
        rows = connection.execute("""
            SELECT r.resident_id, c.execution_settings_json,
                   EXISTS(SELECT 1 FROM facade_members fm WHERE fm.facade_id=r.resident_id) AS facade
            FROM residents r JOIN resident_execution_configuration c USING(resident_id)
            WHERE r.deleted_at IS NULL ORDER BY r.resident_id
        """ + (" FOR UPDATE OF r, c" if apply else "")).fetchall()
        families = resident_roles(connection, [row["resident_id"] for row in rows])
        defaults = {row["family_id"]: row["auto_migrate"] for row in connection.execute(
            "SELECT family_id, auto_migrate FROM families WHERE deleted_at IS NULL")}
        for row in rows:
            rid = row["resident_id"]
            settings = json_value(row["execution_settings_json"]) or {}
            if "emergency_switch_on_quota" in settings or "emergency_switch_order" in settings:
                counts["already_configured"] += 1
                continue
            family = families.get(rid, {})
            family_default = defaults.get(family.get("family_id")) if family.get("role") in {"router", "member", "steward"} else None
            enabled = old_switch(settings, facade=row["facade"], family_default=family_default, role_modes=modes)
            counts["same_type_on" if enabled else "same_type_off"] += 1
            if apply:
                settings.pop("quota_switch_strategy", None)
                settings.update(auto_switch_on_quota=enabled, emergency_switch_on_quota=False, emergency_switch_order=[])
                connection.execute("UPDATE resident_execution_configuration SET execution_settings_json=%s WHERE resident_id=%s",
                                   (json_column(settings), rid))
    return dict(counts, total=len(rows), committed=apply)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(migrate(parser.parse_args().apply)))
