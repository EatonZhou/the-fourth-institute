"""Explicit additive Resident reporting upgrade; preview rolls back by default.

Select the instance with PORTAL_INSTANCE_ROOT. Run --apply before saving reporting
settings on a database without the column; reading uses off/medium defaults.
Only the configuration table's required fields and types are prerequisites;
descriptive metadata may be missing or carry a different version. A valid existing
nullable TEXT or owner-created JSONB column causes no ALTER. Existing policy values, configurations and
metadata are never rewritten; use UpdateResidentSettings to configure policies.
The transaction retains the schema-init mutex and a five-second lock timeout.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from management_common.portal_database import portal_database
from management_portal.society_service.resident_reporting import install_schema


class PreviewRollback(Exception):
    pass


def migrate(database, *, apply=False):
    result = {}
    try:
        with database.transaction() as connection:
            connection.execute("SET LOCAL lock_timeout='5s'")
            result = install_schema(connection)
            if not apply:
                raise PreviewRollback()
    except PreviewRollback:
        pass
    return {**result, "committed":apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(migrate(portal_database(schema="lineage"), apply=args.apply)))


if __name__ == "__main__":
    main()
