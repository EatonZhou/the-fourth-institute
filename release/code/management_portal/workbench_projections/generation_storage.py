"""Generation task identity, progress and bounded result contracts."""
from psycopg.types.json import Jsonb
from management_common.database_time import instant, iso_time

TEXT = (
    "workbench_id", "resident_id", "lineage_id", "message_id", "status",
    "event_source_id", "effect_id", "request_prompt", "input_state", "event_id",
    "response", "trigger_path_id", "kind",
    "target_lineage_id", "plan_hash", "plan_source", "plan_author", "error",
)
TIMES = ("created_at", "input_updated_at", "applied_at")
INTEGERS = ("base_revision", "revision", "target_revision", "page_revision", "applied_revision")
BOOLEANS = ("applied",)
# receipt is the complete workflow submission/recovery handle. It is distinct
# from the Resident's save_receipt/plan_receipt and never flattened into the row.
JSON = ("save_receipt", "receipt", "plan", "apply_receipts", "plan_receipt",
        "resident_state", "input_error", "plan_history", "pending_save", "pending_apply", "pending_page")
COLUMNS = {**{key: "TEXT" for key in TEXT}, **{key: "BIGINT" for key in INTEGERS},
           **{key: "TIMESTAMPTZ" for key in TIMES},
           **{key: "BOOLEAN" for key in BOOLEANS}, **{key + "_json": "JSONB" for key in JSON}}
DDL = ("CREATE TABLE IF NOT EXISTS generations (id TEXT PRIMARY KEY, actor TEXT NOT NULL,"
       + ",".join(key + " " + kind for key, kind in COLUMNS.items()) + ");"
       "CREATE INDEX IF NOT EXISTS generations_workbench_latest ON generations(workbench_id,actor,created_at DESC,id DESC);")


def encode(identity, actor, data):
    unknown = set(data) - set(TEXT + TIMES + INTEGERS + BOOLEANS + JSON + ("id", "created_by"))
    if unknown:
        raise ValueError("Unknown generation fields: " + ", ".join(sorted(unknown)))
    if data.get("id", identity) != identity or data.get("created_by", actor) != actor:
        raise ValueError("Generation identity is immutable")
    return {"id": identity, "actor": actor,
            **{key: data.get(key) for key in TEXT + INTEGERS + BOOLEANS},
            **{key: instant(data.get(key)) for key in TIMES},
            **{key + "_json": data.get(key) for key in JSON}}


def decode(row):
    return {"id": row["id"], "created_by": row["actor"],
            **{key: row[key] for key in TEXT + INTEGERS + BOOLEANS if row[key] is not None},
            **{key: iso_time(row[key]) for key in TIMES if row[key] is not None},
            **{key: row[key + "_json"] for key in JSON if row[key + "_json"] is not None}}


def save(connection, identity, actor, data):
    row = encode(identity, actor, data)
    fields = tuple(row)
    connection.execute(
        "INSERT INTO generations (" + ",".join(fields) + ") VALUES ("
        + ",".join(["%s"] * len(fields)) + ") ON CONFLICT(id) DO UPDATE SET "
        + ",".join(key + "=excluded." + key for key in COLUMNS),
        tuple(Jsonb(value) if key.endswith("_json") and value is not None else value
              for key, value in row.items()))

