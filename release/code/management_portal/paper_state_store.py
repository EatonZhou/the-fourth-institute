"""Paper content and tasks with explicit fields and no count-based retention.

Chronology uses created_at with a stable ID tie-breaker. JSON is reserved for
named generated results, usage, process ownership and input receipts.
"""
from __future__ import annotations
import os
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from psycopg.types.json import Jsonb
from management_common.database_time import epoch_seconds, instant
from management_common.domain_database import DomainDatabase

SCHEMA = "paper"
OWNER = "state"
CONTENT = {"paper_id": "TEXT", "created_by": "TEXT", "created_at": "TIMESTAMPTZ"}
TASK = {
    "status": "TEXT", "step": "TEXT", "created_by": "TEXT",
    "created_at": "TIMESTAMPTZ", "updated_at": "TIMESTAMPTZ", "error": "TEXT",
}
PAPER_TASK = {**TASK, "paper_id": "TEXT", "paper_title": "TEXT"}
ANALYSIS_TASK = {**PAPER_TASK, "progress": "INTEGER", "usage": "JSONB"}
KINDS = {
    "review_jobs": ("job_id", {
        **PAPER_TASK, "review_id": "TEXT", "title": "TEXT", "process_owner": "JSONB",
    }),
    "reviews": ("review_id", {
        **CONTENT, "title": "TEXT", "status": "TEXT", "source": "TEXT",
        "summary": "TEXT", "overall_recommendation": "TEXT", "confidence": "TEXT",
        "author_action_memo": "TEXT", "raw_response": "TEXT", "error": "TEXT",
        "reviewers": "JSONB", "library_check": "JSONB", "usage": "JSONB",
    }),
    "notes": ("note_id", {**CONTENT, "body": "TEXT"}),
    "deep_read_jobs": ("job_id", {
        **ANALYSIS_TASK, "analysis_path": "TEXT", "resident_id": "TEXT",
        "resident_input_message_id": "TEXT", "resident_reply_message_ids": "TEXT[]",
        "resident_usage_message_id": "TEXT", "resident_handoff": "JSONB", "lineage_id": "TEXT",
    }),
    "exp_jobs": ("job_id", {**ANALYSIS_TASK, "exp_path": "TEXT"}),
    "collector_jobs": ("job_id", {
        **TASK, "direction": "TEXT", "collector_contract": "TEXT",
        "requested_message_id": "TEXT", "process_owner": "JSONB",
        "max_papers": "INTEGER", "progress": "INTEGER", "registered_count": "INTEGER",
        "existing_paper_ids": "TEXT[]", "deleted": "BOOLEAN",
        "summary": "TEXT", "result_path": "TEXT", "stdout_path": "TEXT", "stderr_path": "TEXT",
        "results": "JSONB", "invocation": "JSONB", "usage": "JSONB",
    }),
}


def _record_table(kind):
    identity, fields = KINDS[kind]
    columns = ",".join(f"{key} {sql_type}" for key, sql_type in fields.items())
    ddl = f"CREATE TABLE IF NOT EXISTS {kind} ({identity} TEXT PRIMARY KEY,{columns});\n"
    ddl += f"CREATE INDEX IF NOT EXISTS {kind}_created ON {kind}(created_at,{identity});\n"
    if "paper_id" in fields:
        ddl += f"CREATE INDEX IF NOT EXISTS {kind}_paper_created ON {kind}(paper_id,created_at,{identity});\n"
    return ddl


DDL = "".join(_record_table(kind) for kind in KINDS) + """
CREATE TABLE IF NOT EXISTS curated_venues (
    dblp_conf_key TEXT PRIMARY KEY,
    position BIGSERIAL NOT NULL,
    venue JSONB NOT NULL CHECK (venue->>'dblp_conf_key' = dblp_conf_key)
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase(SCHEMA, OWNER, DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


def _kind(kind):
    if kind not in KINDS:
        raise ValueError(f"unknown Paper record kind: {kind}")
    return KINDS[kind]


def lock_key(kind):
    _kind(kind)
    return "records:" + kind


@contextmanager
def locked(*kinds):
    for kind in kinds:
        _kind(kind)
    with ExitStack() as stack:
        connection = None
        for kind in KINDS:
            if kind in kinds:
                connection = stack.enter_context(database().transaction(lock_key(kind)))
        if connection is None:
            connection = stack.enter_context(database().transaction())
        yield connection


def decode(kind, row):
    """The Paper wire contract uses epoch seconds; SQL uses TIMESTAMPTZ."""
    identity, fields = _kind(kind)
    record = {key: epoch_seconds(value) if fields.get(key) == "TIMESTAMPTZ" else value
              for key, value in dict(row).items() if value is not None and (key in fields or key == identity)}
    if kind == "review_jobs" and record.get("review_id"):
        record["result"] = get("reviews", record.get("review_id")) or {}
    return record


def encode(kind, record):
    identity, fields = _kind(kind)
    unknown = set(record) - {identity, *fields}
    if unknown:
        raise ValueError(f"Unknown Paper {kind} fields: {sorted(unknown)}")
    if not str(record.get(identity) or "").strip():
        raise ValueError("Paper record ID required")
    return {key: Jsonb(value) if fields.get(key) == "JSONB" else
            instant(value) if fields.get(key) == "TIMESTAMPTZ" else value
            for key, value in record.items()}


def records(kind, *, paper_id=None):
    identity, fields = _kind(kind)
    if paper_id is not None and "paper_id" not in fields:
        raise ValueError(f"Paper record kind has no paper: {kind}")
    where = " WHERE paper_id=%s" if paper_id is not None else ""
    with database().read() as connection:
        rows = connection.execute(
            f"SELECT * FROM {kind}{where} ORDER BY created_at NULLS FIRST,{identity}",
            (str(paper_id),) if paper_id is not None else (),
        ).fetchall()
        return [decode(kind, row) for row in rows]


def get(kind, record_id):
    identity, _fields = _kind(kind)
    if not record_id:
        return None
    with database().read() as connection:
        row = connection.execute(f"SELECT * FROM {kind} WHERE {identity}=%s", (str(record_id),)).fetchone()
        return decode(kind, row) if row else None


def append(kind, record):
    values = encode(kind, record)
    with database().transaction(lock_key(kind)) as connection:
        row = connection.execute(
            f"INSERT INTO {kind} ({','.join(values)}) VALUES ({','.join(['%s'] * len(values))}) RETURNING *",
            tuple(values.values()),
        ).fetchone()
        return decode(kind, row)


def merge(kind, record_id, patch):
    identity, _fields = _kind(kind)
    values = encode(kind, {**patch, identity: record_id})
    changes = [key for key in values if key != identity]
    update = ",".join(f"{key}=excluded.{key}" for key in changes) or f"{identity}=excluded.{identity}"
    with database().transaction(lock_key(kind)) as connection:
        row = connection.execute(
            f"INSERT INTO {kind} ({','.join(values)}) VALUES ({','.join(['%s'] * len(values))}) "
            f"ON CONFLICT ({identity}) DO UPDATE SET {update} RETURNING *", tuple(values.values()),
        ).fetchone()
        return decode(kind, row)


def delete(kind, record_id):
    identity, _fields = _kind(kind)
    with database().transaction(lock_key(kind)) as connection:
        row = connection.execute(f"DELETE FROM {kind} WHERE {identity}=%s RETURNING *", (str(record_id),)).fetchone()
        return decode(kind, row) if row else None


def delete_paper(kind, paper_id):
    if "paper_id" not in _kind(kind)[1]:
        raise ValueError(f"Paper record kind has no paper: {kind}")
    with database().transaction(lock_key(kind)) as connection:
        return connection.execute(f"DELETE FROM {kind} WHERE paper_id=%s", (str(paper_id),)).rowcount


def curated_venues():
    with database().read() as connection:
        return [row[0] for row in connection.execute("SELECT venue FROM curated_venues ORDER BY position")]
