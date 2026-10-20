"""Typed invocation identity and bounded batch execution data."""
from copy import deepcopy
from psycopg.types.json import Jsonb
from management_portal.service_invocation_contract import PLAN_SCHEMA

KEY_FIELDS = ("actor", "surface", "search_id", "generation")
TEXT = ("resident_id", "lineage_id", "message_id", "status", "plan_fingerprint",
        "binding_fingerprint", "input_fingerprint", "agent_id", "execution_id")
JSON = ("binding", "authorization", "route_constraint", "result_ref")
PROJECTIONS = {"schema", "batch_count", "fenced", "fence_reason", "is_current_generation"}
COLUMNS = {**{key: "TEXT" for key in TEXT},
           "cancel_requested": "BOOLEAN",
           **{key + "_json": "JSONB" for key in JSON}, "batches_json": "JSONB NOT NULL"}
SHARED = ("created_by", "surface", "search_id", "run_generation", "resident_id", "lineage_id",
          "plan_fingerprint", "binding_fingerprint", "binding", "authorization")
BATCH_FIELDS = {"batch_id", "input_fingerprint", "message_id", "status", "cancel_delivered",
                "agent_id", "execution_id", "result_ref", "route_constraint", "cancel_requested"}
DDL = ("CREATE TABLE IF NOT EXISTS service_invocations (actor TEXT NOT NULL,surface TEXT NOT NULL,"
       "search_id TEXT NOT NULL,generation BIGINT NOT NULL,"
       + ",".join(key + " " + kind for key, kind in COLUMNS.items())
       + ",PRIMARY KEY(actor,surface,search_id,generation),UNIQUE(message_id));"
       "CREATE INDEX IF NOT EXISTS service_invocation_reverse ON service_invocations(actor,resident_id,lineage_id);")


def encode(record):
    allowed = set(TEXT) | set(JSON) | {"created_by", "surface", "search_id", "run_generation", "batches", "cancel_requested"} | PROJECTIONS
    if set(record) - allowed:
        raise ValueError("Unknown service invocation fields: " + ", ".join(sorted(set(record) - allowed)))
    batches = []
    for original in record["batches"]:
        batch = deepcopy(original)
        for key in SHARED:
            if key in batch and batch[key] != record.get(key):
                raise ValueError("Invocation batch identity differs from its parent: " + key)
            batch.pop(key, None)
        for key in PROJECTIONS:
            batch.pop(key, None)
        if set(batch) - BATCH_FIELDS:
            raise ValueError("Unknown invocation batch fields: " + ", ".join(sorted(set(batch) - BATCH_FIELDS)))
        batches.append(batch)
    if not batches:
        raise ValueError("Invocation requires at least one batch")
    return {"actor": record["created_by"], "surface": record["surface"],
            "search_id": record["search_id"], "generation": record["run_generation"],
            **{key: record.get(key) for key in TEXT},
            "cancel_requested": record.get("cancel_requested"),
            **{key + "_json": record.get(key) for key in JSON}, "batches_json": batches}


def decode(row):
    if row is None:
        return None
    record = {"schema": PLAN_SCHEMA, "created_by": row["actor"], "surface": row["surface"],
              "search_id": row["search_id"], "run_generation": row["generation"],
              **{key: row[key] for key in TEXT if row[key] is not None},
              **{key: row[key + "_json"] for key in JSON if row[key + "_json"] is not None},
              **({"cancel_requested": row["cancel_requested"]} if row["cancel_requested"] is not None else {}), "fenced": False,
              "batch_count": len(row["batches_json"])}
    inherited = {key: record[key] for key in SHARED if key in record}
    inherited.update(schema=PLAN_SCHEMA, batch_count=record["batch_count"], fenced=False)
    record["batches"] = [{**deepcopy(inherited), **batch} for batch in row["batches_json"]]
    return record


def write(connection, key, record, *, insert=False):
    row = encode(record)
    if tuple(row[field] for field in KEY_FIELDS) != tuple(key):
        raise ValueError("Invocation identity is immutable")
    fields = (*KEY_FIELDS, *COLUMNS) if insert else tuple(COLUMNS)
    values = tuple(Jsonb(row[field]) if field.endswith("_json") and row[field] is not None
                   else row[field] for field in fields)
    if insert:
        connection.execute("INSERT INTO service_invocations (" + ",".join(fields)
                           + ") VALUES (" + ",".join(["%s"] * len(fields)) + ")", values)
    else:
        connection.execute("UPDATE service_invocations SET "
                           + ",".join(field + "=%s" for field in fields)
                           + " WHERE actor=%s AND surface=%s AND search_id=%s AND generation=%s",
                           (*values, *key))

