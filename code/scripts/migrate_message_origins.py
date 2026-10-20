"""One-time, pinned message-origin metadata migration; never a runtime reader.

Retired same-version schema names come from an explicit private plan. Message
payloads, sender identities, ordering, receipts and business text are untouched.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from management_common.principal import message_attribution


def checked_sources(sources):
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Explicit source schemas are required")
    for old, new in sources.items():
        if (not isinstance(old, str) or new not in {"tfi.message-origin.v1", "tfi.message-origin.v2"}
                or old.startswith("tfi.") or not old.endswith(new.removeprefix("tfi"))):
            raise ValueError("Only explicit same-version message-origin schema changes are allowed")
    return sources


def rewrite_message(raw, path, sources):
    """Change just the schema token at a known message path, preserving bytes."""
    checked_sources(sources)
    value = json.loads(raw)
    message = value
    for key in path:
        message = message.get(key, {}) if isinstance(message, dict) else {}
    origin = message.get("origin") if isinstance(message, dict) else None
    if origin is None:
        return raw
    if not isinstance(origin, dict):
        raise ValueError("Message origin must be an object")
    old = origin.get("schema")
    if old in {"tfi.message-origin.v1", "tfi.message-origin.v2"}:
        message_attribution(message)
        return raw
    if old not in sources:
        raise ValueError("Message origin schema is not in the explicit plan")
    expected = copy.deepcopy(value)
    migrated = expected
    for key in path:
        migrated = migrated[key]
    migrated["origin"]["schema"] = sources[old]
    message_attribution(migrated)  # Identity/author checks stay strict.
    pattern = re.compile(r'("schema"\s*:\s*)' + re.escape(json.dumps(old)))
    candidates = []
    for match in pattern.finditer(raw):
        after = raw[:match.start()] + match[1] + json.dumps(sources[old]) + raw[match.end():]
        if json.loads(after) == expected:
            candidates.append(after)
    if len(candidates) != 1:
        raise ValueError("Cannot uniquely locate the message origin schema token")
    return candidates[0]


def migrate(connection, *, agent_schema, lineage_schema, sources, backup_path,
            apply=False, expected_plan=None):
    """Validate all selected rows, then CAS them in one transaction with backup.

    The caller supplies a connection bound to the reviewed database. The plan
    fingerprint covers the binding, keys and exact before/after bytes. Concurrent
    metadata changes cause a complete rollback; new canonical writes are ignored.
    """
    checked_sources(sources)
    targets = [
        (agent_schema, "segments", ("segment_id",), "metadata_json", ("message",), None),
        (agent_schema, "agent_operations", ("operation_id",), "data_json", (), "message_artifact"),
        (agent_schema, "agent_operations", ("operation_id",), "data_json", ("metadata", "message"), "notice"),
        (lineage_schema, "resident_mailbox", ("resident_id", "message_id"), "message_json", (), None),
    ]
    with connection.transaction():
        with connection.cursor(row_factory=dict_row) as c:
            binding = c.execute("SELECT current_database() AS database,current_user AS db_user").fetchone()
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                      (agent_schema + ":message-origin-migration",))
            c.execute("CREATE TEMP TABLE origin_migration_stage (target integer,keys text[],before_value text,after_value text,revision text) ON COMMIT DROP")
            fingerprint = hashlib.sha256(json.dumps([binding, agent_schema, lineage_schema, sources], sort_keys=True).encode())
            counts = []
            for index, (namespace, table, keys, column, path, kind) in enumerate(targets):
                query = sql.SQL("SELECT ARRAY[{keys}] AS keys,{column}::text AS raw,xmin::text AS revision FROM {table} WHERE {column}::jsonb #>> %s = ANY(%s)").format(
                    keys=sql.SQL(",").join(map(sql.Identifier, keys)),
                    column=sql.Identifier(column), table=sql.Identifier(namespace, table))
                params = [list(path + ("origin", "schema")), list(sources)]
                if kind:
                    query += sql.SQL(" AND kind=%s")
                    params.append(kind)
                query += sql.SQL(" ORDER BY ") + sql.SQL(",").join(map(sql.Identifier, keys))
                count = 0
                with connection.cursor(name="origin_source_" + str(index), row_factory=dict_row) as rows:
                    rows.execute(query, params)
                    while batch := rows.fetchmany(1000):
                        changes = []
                        for row in batch:
                            after = rewrite_message(row["raw"], path, sources)
                            if after == row["raw"]:
                                raise ValueError("Selected origin unexpectedly unchanged")
                            fingerprint.update(json.dumps([index, row["keys"], row["raw"], after], ensure_ascii=False).encode())
                            changes.append((index, row["keys"], row["raw"], after, row["revision"]))
                        with c.copy("COPY origin_migration_stage FROM STDIN") as writer:
                            for change in changes:
                                writer.write_row(change)
                        count += len(changes)
                counts.append(count)
            plan = fingerprint.hexdigest()
            if expected_plan is not None and plan != expected_plan:
                raise ValueError("Message origin migration plan changed")
            report = {"binding": binding, "plan_sha256": plan, "selected": sum(counts),
                      "counts": counts, "updated": 0, "status": "validated"}
            if not apply:
                return report
            backup_path = Path(backup_path)
            # Exclusive private backup exists and is durable before any UPDATE.
            descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as backup:
                backup.write(json.dumps({"plan": report, "targets": targets}) + "\n")
                with connection.cursor(name="origin_backup", row_factory=dict_row) as rows:
                    rows.execute("SELECT target,keys,before_value,revision FROM origin_migration_stage ORDER BY target,keys")
                    for row in rows:
                        backup.write(json.dumps(row, ensure_ascii=False) + "\n")
                backup.flush()
                os.fsync(backup.fileno())
            if os.name == "posix":
                directory = os.open(backup_path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            for index, (namespace, table, keys, column, _, _) in enumerate(targets):
                key_match = sql.SQL(" AND ").join(
                    sql.SQL("live.{} = staged.keys[{}]").format(sql.Identifier(key), sql.Literal(i + 1))
                    for i, key in enumerate(keys))
                updated = c.execute(sql.SQL("UPDATE {table} live SET {column}=staged.after_value FROM origin_migration_stage staged WHERE staged.target=%s AND {keys} AND live.{column}::text=staged.before_value AND live.xmin::text=staged.revision").format(
                    table=sql.Identifier(namespace, table), column=sql.Identifier(column), keys=key_match), (index,)).rowcount
                if updated != counts[index]:
                    raise ValueError("Concurrent message metadata change; migration rolled back")
                different = c.execute(sql.SQL("SELECT count(*) AS n FROM origin_migration_stage staged LEFT JOIN {table} live ON {keys} WHERE staged.target=%s AND live.{column}::text IS DISTINCT FROM staged.after_value").format(
                    table=sql.Identifier(namespace, table), column=sql.Identifier(column), keys=key_match), (index,)).fetchone()["n"]
                if different:
                    raise ValueError("Message origin readback mismatch; migration rolled back")
                report["updated"] += updated
            report.update(status="migrated", backup=str(backup_path))
            return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan")
    args = parser.parse_args()
    if args.apply and not args.expected_plan:
        parser.error("--apply requires the dry-run --expected-plan")
    plan = json.loads(args.plan.read_text())
    with psycopg.connect(os.environ["PORTAL_DATABASE_URL"],
                         options="-c lock_timeout=5000 -c statement_timeout=120000") as connection:
        if connection.info.dbname != plan["database"] or connection.info.user != plan["db_user"]:
            raise ValueError("Database binding differs from the reviewed plan")
        result = migrate(connection, agent_schema=plan["agent_schema"], lineage_schema=plan["lineage_schema"],
                         sources=plan["sources"], backup_path=args.backup,
                         apply=args.apply, expected_plan=args.expected_plan)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
