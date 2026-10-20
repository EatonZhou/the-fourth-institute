"""Search fields owned by the search domain; results and options have bounded containers."""
from psycopg.types.json import Jsonb
from management_common.database_time import instant, epoch_seconds

TEXT = ("surface", "created_by", "status", "search_kind", "execution_contract", "query",
        "step", "error", "notes", "query_interpretation", "message_id", "project")
INTEGERS = ("run_generation", "progress", "batch_count", "completed_batch_count")
TIMES = ("created_at", "updated_at", "refreshed_at")
BOOLEANS = ("observation_pending", "deleted")
JSON = ("results", "refresh_skip_paper_ids", "input_failure")
COLUMNS = {**{key: "TEXT" for key in TEXT}, **{key: "BIGINT" for key in INTEGERS},
           **{key: "TIMESTAMPTZ" for key in TIMES},
           **{key: "BOOLEAN" for key in BOOLEANS}, **{key + "_json": "JSONB" for key in JSON}}
DDL = ("CREATE TABLE IF NOT EXISTS jobs (search_id TEXT PRIMARY KEY,"
       + ",".join(key + " " + kind for key, kind in COLUMNS.items())
       + ");CREATE INDEX IF NOT EXISTS search_surface_created ON jobs(surface,created_at,search_id);"
       "CREATE TABLE IF NOT EXISTS run_inputs(search_id TEXT PRIMARY KEY, input_json JSONB NOT NULL);")


def encode(record):
    allowed = set(TEXT + INTEGERS + TIMES + BOOLEANS + JSON + ("search_id", "result_count"))
    if set(record) - allowed:
        raise ValueError("Unknown search fields: " + ", ".join(sorted(set(record) - allowed)))
    return {"search_id": record["search_id"],
            **{key: record.get(key) for key in TEXT + INTEGERS + BOOLEANS},
            **{key: instant(record.get(key)) for key in TIMES},
            **{key + "_json": record.get(key) for key in JSON}}


def decode(row):
    if row is None:
        return None
    record = {"search_id": row["search_id"],
              **{key: row[key] for key in TEXT + INTEGERS + BOOLEANS if row[key] is not None},
              **{key: epoch_seconds(row[key]) for key in TIMES if row[key] is not None},
              **{key: row[key + "_json"] for key in JSON if row[key + "_json"] is not None}}
    if "results" in record:
        record["result_count"] = len(record["results"])
    return record


def save(connection, record):
    row = encode(record)
    cursor = connection.execute("INSERT INTO jobs (" + ",".join(row) + ") VALUES ("
                       + ",".join(["%s"] * len(row)) + ") ON CONFLICT(search_id) DO UPDATE SET "
                       + ",".join(key + "=excluded." + key for key in COLUMNS)
                       + " WHERE jobs.surface=excluded.surface",
                       tuple(Jsonb(value) if key.endswith("_json") and value is not None else value
                             for key, value in row.items()))
    if cursor.rowcount != 1:
        raise ValueError("Search ID already belongs to another surface")

