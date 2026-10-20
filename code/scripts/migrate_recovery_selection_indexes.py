"""Install additive recovery indexes without changing records or schema contracts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql

from management_portal.society_service import (
    family_storage, graph_storage, program_work_storage, relation_work_storage,
    resident_execution_storage, resident_storage,
)

INDEXES = {
    name: definition
    for owner in (family_storage, graph_storage, program_work_storage,
                  relation_work_storage, resident_execution_storage, resident_storage)
    for name, definition in owner.RECOVERY_INDEXES.items()
}


def install(dsn: str, *, schema: str = "lineage") -> list[str]:
    # CONCURRENTLY avoids blocking live writers; no data migration is involved.
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        for name, definition in INDEXES.items():
            connection.execute(sql.SQL("CREATE INDEX CONCURRENTLY IF NOT EXISTS {} ").format(sql.Identifier(name)) + sql.SQL(definition))
    return sorted(INDEXES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        from scripts.migrate_domain_records import source_url
        names = install(source_url())
    else:
        names = sorted(INDEXES)
    print(json.dumps({"applied": args.apply, "indexes": names}))
