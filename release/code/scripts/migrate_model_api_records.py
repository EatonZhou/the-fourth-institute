"""One-time Model API column conversion and explicit route selection.

Run in the caller's transaction with writers quiesced and a verified backup.
The former effective choice is materialized exactly once; runtime selection
thereafter uses the persisted relationship.
"""
from psycopg import sql
from psycopg.rows import dict_row

from management_common.database_time import instant
from management_portal import model_api_store as store


def migrate(connection, *, schema="model_apis"):
    with connection.cursor(row_factory=dict_row) as c:
        c.execute(sql.SQL("SET LOCAL search_path TO {},public").format(sql.Identifier(schema)))
        c.execute("LOCK TABLE apis,relay_resources IN ACCESS EXCLUSIVE MODE")
        old = c.execute("SELECT * FROM apis ORDER BY position,api_id").fetchall()
        entries = [{"id": row["api_id"], **row["entry_json"]} for row in old]
        prepared = [store._values(entry) for entry in entries]
        bindings = []
        for (backend, route_id), provider in store.SERVICE_ROUTES.items():
            entry = next((entry for entry in entries if entry.get("enabled", True)
                          and entry.get("provider") == provider
                          and entry.get("kind") == "openai_compatible_chat"), None)
            if entry:
                bindings.append((backend, route_id, entry["id"]))
        c.execute("ALTER TABLE apis RENAME TO apis_record_cutover")
        c.execute("""ALTER TABLE relay_resources ALTER COLUMN updated_at TYPE TIMESTAMPTZ
                     USING updated_at::timestamptz""")
        c.execute(store.DDL)
        for row, values in zip(old, prepared):
            record = {"api_id": row["api_id"], "position": row["position"], **values,
                      "version": row["version"], "updated_at": instant(row["updated_at"]),
                      "updated_by": row["updated_by"]}
            c.execute(sql.SQL("INSERT INTO apis ({}) VALUES ({})").format(
                sql.SQL(",").join(map(sql.Identifier, record)),
                sql.SQL(",").join(sql.Placeholder() for _ in record)), tuple(record.values()))
        for binding in bindings:
            c.execute("INSERT INTO routes(backend,route_id,api_id) VALUES (%s,%s,%s)", binding)
        assert c.execute("SELECT count(*) AS n FROM apis").fetchone()["n"] == len(old)
        c.execute("DROP TABLE apis_record_cutover")
        return {"apis": len(old), "routes": bindings}
