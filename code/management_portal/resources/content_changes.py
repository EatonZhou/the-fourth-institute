from __future__ import annotations

import copy
import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

from management_common.execution_association import current_execution_id
from management_common.portal_database import portal_database
from management_portal.execution.service import get_execution_service


_CAPTURE = ContextVar("resource_content_changes", default=None)
LOGGER = logging.getLogger(__name__)


class ContentChangeStore:
    """Immutable, execution-scoped snapshots supplied by the writing domain."""

    def __init__(self):
        self.database = portal_database(schema="resource_content_changes")
        # Terminal projections read receipts even for turns without resource
        # changes. Repeating index DDL here waits on unrelated receipt writers.
        with self.database.read() as db:
            existing = db.execute(
                "SELECT to_regclass('content_changes') AS content_table, "
                "to_regclass('content_changes_execution') AS content_index"
            ).fetchone()
        if existing["content_table"] is not None and existing["content_index"] is not None:
            return
        self.database.write(lambda db: db.execute(
            "CREATE TABLE IF NOT EXISTS content_changes ("
            "ordinal BIGSERIAL PRIMARY KEY, change_id TEXT UNIQUE NOT NULL, "
            "execution_id TEXT NOT NULL, actor TEXT NOT NULL, payload TEXT NOT NULL)"
        ))
        self.database.write(lambda db: db.execute(
            "CREATE INDEX IF NOT EXISTS content_changes_execution "
            "ON content_changes(execution_id, actor, ordinal)"
        ))

    def append(self, execution_id, actor, change):
        self.database.write(lambda db: db.execute(
            'INSERT INTO content_changes(change_id, execution_id, actor, payload) VALUES (%s,%s,%s,%s)',
            (change["change_id"], execution_id, actor, json.dumps(change, ensure_ascii=False)),
        ))

    def for_execution(self, execution_id, actor):
        with self.database.read() as db:
            rows = db.execute(
                'SELECT payload FROM content_changes WHERE execution_id=%s AND actor=%s ORDER BY ordinal',
                (execution_id, actor),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def for_changes(self, change_ids):
        """Internal evidence lookup by immutable unique IDs; does not grant receipt access."""
        with self.database.read() as db:
            rows = db.execute(
                'SELECT ordinal, change_id, execution_id, actor, payload FROM content_changes '
                'WHERE change_id = ANY(%s)', (change_ids,),
            ).fetchall()
        by_id = {row["change_id"]: {
            **{key: row[key] for key in ("ordinal", "change_id", "execution_id", "actor")},
            "change": json.loads(row["payload"]),
        } for row in rows}
        return [by_id[value] for value in change_ids if value in by_id]


@contextmanager
def capture_content_changes(*, actor, resource_id):
    execution_id = current_execution_id()
    capture = None
    if execution_id:
        snapshot = get_execution_service().status(execution_id, include_events=False, include_result=False)
        if (snapshot.get("request") or {}).get("agent_id") != actor:
            capture = {"recorded": 0, "errors": 0, "unconfirmed_reason": "execution_actor_mismatch"}
        elif snapshot.get("status") != "running":
            capture = {"recorded": 0, "errors": 0, "unconfirmed_reason": "execution_not_running"}
        else:
            capture = {"store": ContentChangeStore(), "actor": actor,
                       "execution_id": execution_id, "resource_id": resource_id,
                       "recorded": 0, "errors": 0}
    token = _CAPTURE.set(capture if capture and not capture.get("unconfirmed_reason") else None)
    try:
        yield capture
    finally:
        _CAPTURE.reset(token)


def record_text_change(address, path, before, after, *, before_version=None, after_version=None,
                       version_recovery_change_ids=None):
    """Call after a successful write, while still holding the domain mutation lock."""
    capture = _CAPTURE.get()
    if capture is None or before == after:
        return
    if str(address).partition("/")[0] != capture["resource_id"]:
        raise ValueError("Changed content must belong to the captured resource")
    def snapshot(text, version):
        return {"text": text, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "version": copy.deepcopy(version or {})}
    change = {"schema": "tfi.resource-content-change.v1",
              "change_id": uuid.uuid4().hex, "resource_id": capture["resource_id"],
              "address": str(address), "path": str(path),
              "execution_id": capture["execution_id"], "actor": capture["actor"],
              "created_at": datetime.now(timezone.utc).isoformat(),
              "before": snapshot(before, before_version), "after": snapshot(after, after_version)}
    if version_recovery_change_ids is not None:
        change["version_recovery_change_ids"] = list(version_recovery_change_ids)
    try:
        capture["store"].append(capture["execution_id"], capture["actor"], change)
        capture["recorded"] += 1
    except Exception:
        # A receipt failure must not report the already-committed write as failed.
        capture["errors"] += 1
        LOGGER.exception("Resource content committed but change receipt failed: %s", address)
