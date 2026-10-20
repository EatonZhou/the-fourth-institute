"""One-time conversion of Paper dictionaries to domain-owned columns.

Run within a transaction after a verified backup and quiescing Paper writers.
The archived source keeps retired provider-launch metadata; no runtime code
continues reading those former execution mechanisms.
"""
from psycopg import sql
from psycopg.rows import dict_row

from management_common.format_tags import schema_matches
from management_portal import paper_state_store as store

RETIRED_EXECUTION_FIELDS = frozenset({
    "execution_id", "execution_namespace", "provider_id", "provider_index",
    "runner", "runner_unit", "command",
})


def migrate(connection, *, schema="paper", lineage_schema="lineage"):
    with connection.cursor(row_factory=dict_row) as c:
        c.execute("SET LOCAL lock_timeout='10s'")
        c.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(schema)))
        for kind in store.KINDS:
            c.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.Identifier(kind)))
        sources = {}
        for kind in store.KINDS:
            rows = c.execute(sql.SQL("SELECT record FROM {} ORDER BY position").format(sql.Identifier(kind))).fetchall()
            sources[kind] = [row["record"] for row in rows]
        paths = {(row["lineage_id"], row["occurrence_id"]): row["trigger_path_id"]
                 for row in c.execute(sql.SQL(
                     "SELECT lineage_id,occurrence_id,trigger_path_id FROM {}.trigger_paths"
                 ).format(sql.Identifier(lineage_schema)))}

        def receipt(value):
            if isinstance(value, list):
                return [receipt(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: receipt(item) for key, item in value.items()}
            if schema_matches(result.get("schema"), "tfi.paper-resident-input.v1"):
                result.pop("invocation_id", None)
                result["trigger_path_id"] = paths.get((result.get("lineage_id"), result.get("event_id")), "")
            return result

        # A saved review result is authoritative in reviews. Recover a result
        # missing there from the task before replacing its duplicate with an ID.
        reviews = {row["review_id"]: row for row in sources["reviews"]}
        for job in sources["review_jobs"]:
            result = job.pop("result", None)
            if result:
                result_id = result.get("review_id")
                if not result_id or (job.get("review_id") and job["review_id"] != result_id):
                    raise ValueError("Paper task result does not identify its review")
                job["review_id"] = result_id
                reviews.setdefault(result_id, result)
        sources["reviews"] = list(reviews.values())
        prepared = {}
        retired = {}
        for kind, records in sources.items():
            prepared[kind] = []
            for record in records:
                record = receipt(record)
                for key in RETIRED_EXECUTION_FIELDS:
                    if key in record:
                        retired[key] = retired.get(key, 0) + 1
                        record.pop(key)
                if kind == "deep_read_jobs" and "resident_invocation" in record:
                    if "resident_handoff" in record:
                        raise ValueError("Ambiguous Paper Resident receipt")
                    record["resident_handoff"] = record.pop("resident_invocation")
                prepared[kind].append(store.encode(kind, record))
        for kind in store.KINDS:
            c.execute(sql.SQL("ALTER TABLE {} RENAME TO {}").format(
                sql.Identifier(kind), sql.Identifier(kind + "_record_cutover")))
        c.execute(store.DDL)
        for kind, records in prepared.items():
            for record in records:
                c.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(kind), sql.SQL(",").join(map(sql.Identifier, record)),
                    sql.SQL(",").join(sql.Placeholder() for _ in record),
                ), tuple(record.values()))
            actual = c.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(kind))).fetchone()["n"]
            if actual != len(records):
                raise ValueError("Paper record count changed during migration")
            c.execute(sql.SQL("DROP TABLE {}").format(sql.Identifier(kind + "_record_cutover")))
        return {"records": {kind: len(rows) for kind, rows in prepared.items()},
                "retired_execution_fields": retired}
