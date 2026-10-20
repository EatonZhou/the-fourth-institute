"""Retire Watchboard settings after migrating Telepus and stopping schema consumers."""
from __future__ import annotations

from management_common.format_tags import schema_matches

import json
from pathlib import Path

OLD_SCHEMA = "tfi.lineage-data.v43"
NEW_SCHEMA = "tfi.lineage-data.v44"
TELEPUS_RESIDENT = "resident_7fdebb465cdf"
VIEW_KEY = "watchboard." + TELEPUS_RESIDENT + ".view"


def migrate(connection) -> dict:
    """Caller owns the transaction and Lineage search_path; DDL rolls back with it."""
    connection.execute("LOCK TABLE lineage_meta IN ACCESS EXCLUSIVE MODE")
    version = connection.execute("SELECT value FROM lineage_meta WHERE key='schema'").fetchone()["value"]
    table = connection.execute("SELECT to_regclass('resident_watchboards') AS name").fetchone()["name"]
    if schema_matches(version, NEW_SCHEMA):
        if table is not None:
            raise ValueError("Retired resident_watchboards table still exists")
        return {"schema": NEW_SCHEMA, "already_applied": True}
    if not schema_matches(version, OLD_SCHEMA):
        raise ValueError(f"Expected {OLD_SCHEMA}, found {version}")
    if table is None:
        raise ValueError("Missing resident_watchboards before schema migration")
    connection.execute("LOCK TABLE resident_watchboards IN ACCESS EXCLUSIVE MODE")
    view = connection.execute(
        "SELECT c.node_id FROM lineage_components c "
        "JOIN lineage_nodes n ON n.node_id=c.node_id "
        "JOIN lineage_nodes r ON r.lineage_id=n.lineage_id "
        "WHERE r.node_id=%s AND r.deleted_at IS NULL AND n.deleted_at IS NULL "
        "AND n.node_type='event_source' AND c.client_key=%s",
        (TELEPUS_RESIDENT, VIEW_KEY),
    ).fetchone()
    if not view:
        raise ValueError("Migrate Telepus display into its Lineage before retiring storage")
    count = connection.execute("SELECT count(*) AS count FROM resident_watchboards").fetchone()["count"]
    connection.execute("DROP TABLE resident_watchboards")
    connection.execute("UPDATE lineage_meta SET value=%s WHERE key='schema'", (NEW_SCHEMA,))
    return {"schema": NEW_SCHEMA, "already_applied": False, "retired_records": count,
            "telepus_view_id": view["node_id"]}


def validate_display(root: Path) -> None:
    import yaml
    manifest = yaml.safe_load((root / 'agent.yaml').read_text())
    view = yaml.safe_load((root / 'watchboard.yaml').read_text())
    if not isinstance(manifest, dict) or manifest.get('commands') != {} or manifest.get('configs') != []:
        raise ValueError("Telepus display must have no script commands or bridge configuration")
    if not isinstance(view, dict) or view.get('title') != 'Telepus':
        raise ValueError("Telepus view manifest is not ready")


def main() -> None:
    import argparse
    import subprocess
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    from management_common.portal_database import portal_database_url, _database_schema
    from management_portal.society_service.state_store import LINEAGE_DATA_SCHEMA
    from scripts import portal_services

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--display-root', type=Path, required=True)
    options = parser.parse_args()
    validate_display(options.display_root)
    if not schema_matches(LINEAGE_DATA_SCHEMA, NEW_SCHEMA):
        raise RuntimeError('Run migration from the matching prepared code')
    for instance in ('dev', 'release'):
        for component in ('web', 'lineage', 'resource'):
            unit = portal_services._config(instance)['component_units'][component]
            state = subprocess.check_output(['systemctl', '--user', 'show', unit, '--property=ActiveState', '--value'], text=True).strip()
            if state not in {'inactive', 'failed'}:
                raise RuntimeError(f'Stop schema consumer before cutover: {unit} is {state}')
    with psycopg.connect(portal_database_url(), row_factory=dict_row) as connection:
        connection.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(_database_schema('lineage'))))
        result = migrate(connection)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
