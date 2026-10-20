from __future__ import annotations

from management_common.format_tags import schema_matches

import json
import threading
from copy import deepcopy
from contextlib import contextmanager

from management_common.portal_database import portal_database
from management_portal.service_invocation_contract import PLAN_SCHEMA, ServiceInvocationConflict
from . import service_invocation_storage as storage


TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})

STORE_COLUMNS = {
    "service_invocations": set(storage.KEY_FIELDS) | set(storage.COLUMNS),
    "service_search_heads": {
        "actor", "surface", "search_id", "generation", "deleted",
    },
    "service_run_fences": {
        "actor", "surface", "search_id", "generation", "reason",
    },
}


class ServiceInvocationStore:
    def __init__(self, database=None):
        self.database = database if database is not None else portal_database(schema="service_invocations")
        self._initialize()

    def _initialize(self):
        with self.database.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
            connection.executescript(storage.DDL)
            connection.execute("CREATE TABLE IF NOT EXISTS service_search_heads (actor TEXT, surface TEXT, search_id TEXT, generation BIGINT, deleted BOOLEAN DEFAULT FALSE, PRIMARY KEY(actor,surface,search_id))")
            connection.execute("CREATE TABLE IF NOT EXISTS service_run_fences (actor TEXT, surface TEXT, search_id TEXT, generation BIGINT, reason TEXT, PRIMARY KEY(actor,surface,search_id,generation))")
            self._require_schema(connection)

    @contextmanager
    def transaction(self, key):
        with self.database.transaction() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || %s, 0))",
                (":" + json.dumps(key[:3], ensure_ascii=False),),
            )
            yield connection

    @staticmethod
    def _require_schema(connection):
        tables = {
            row["table_name"]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_type='BASE TABLE'"
            )
        }
        actual = {
            table: {
                row["column_name"]
                for row in connection.execute(
                    'SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s',
                    (table,),
                )
            }
            for table in STORE_COLUMNS
            if table in tables
        }
        if actual != STORE_COLUMNS:
            raise ServiceInvocationConflict(
                "service invocation store does not match its schema"
            )

    @staticmethod
    def key(job, surface):
        return (job["created_by"], surface, job["search_id"], job["run_generation"])

    @staticmethod
    def decode(row):
        return storage.decode(row)

    def _read(self, connection, key):
        record = self.decode(connection.execute('SELECT * FROM service_invocations WHERE actor=%s AND surface=%s AND search_id=%s AND generation=%s', key).fetchone())
        if record and (
            not schema_matches(record.get("schema"), "tfi.service-invocation-plan.v1")
            or not isinstance(record.get("batches"), list)
            or not record["batches"]
        ):
            raise ServiceInvocationConflict(
                "persisted service invocation does not match its contract"
            )
        return record

    @staticmethod
    def _write(connection, key, record):
        storage.write(connection, key, record)

    @staticmethod
    def _fence(connection, key, record):
        fence = connection.execute(
            'SELECT reason FROM service_run_fences WHERE actor=%s AND surface=%s AND search_id=%s AND generation=%s',
            key,
        ).fetchone()
        if fence:
            reason = str(fence["reason"] or "").strip()
            if not reason:
                raise ServiceInvocationConflict(
                    "service invocation fence requires a reason"
                )
            return reason
        return ""

    def _set_fence_reason(self, connection, key, record, reason):
        # First termination wins.
        previous = self._fence(connection, key, record or {})
        reason = previous or reason
        connection.execute('INSERT INTO service_run_fences(actor,surface,search_id,generation,reason) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING', (*key, reason))
        return reason

    def _projection_fields(self, connection, key, record):
        head = connection.execute('SELECT generation,deleted FROM service_search_heads WHERE actor=%s AND surface=%s AND search_id=%s', key[:3]).fetchone()
        fence = self._fence(connection, key, record)
        return {"is_current_generation": bool(head and not head["deleted"] and head["generation"] == key[3]),
                "fenced": bool(fence), "fence_reason": fence}

    def _project(self, connection, key, record):
        fields = self._projection_fields(connection, key, record)
        record = deepcopy(record)
        record.update(fields)
        for batch in record["batches"]:
            batch.update(fields)
        fence = fields["fence_reason"]
        if fence in {"failed", "cancelled", "deleted", "superseded"}:
            record["status"] = "cancelled" if fence == "deleted" else fence
        return record

    def _project_batch(self, connection, key, record, batch):
        return {**deepcopy(batch), **self._projection_fields(connection, key, record)}

    def get(self, job, surface):
        key = self.key(job, surface)
        with self.database.snapshot() as connection:
            record = self._read(connection, key)
            return self._project(connection, key, record) if record else None

    def read_plan(self, job, surface):
        """Read one committed plan and its fences without initializing storage."""
        key = self.key(job, surface)
        # The plan and its fence projection come from one snapshot.
        with self.database.snapshot() as connection:
            record = self._read(connection, key)
            if record is None:
                return None
            for item in (record, *record["batches"]):
                identity = tuple(item.get(field) for field in (
                    "created_by", "surface", "search_id", "run_generation",
                ))
                if identity != key or type(item.get("run_generation")) is not int:
                    raise ServiceInvocationConflict("stored plan identity does not match the requested search generation")
            return self._project(connection, key, record)

    def reserve(self, job, surface, record):
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            existing = self._read(connection, key)
            if existing:
                if existing["plan_fingerprint"] != record["plan_fingerprint"]:
                    raise ServiceInvocationConflict("search generation batch plan changed")
                return self._project(connection, key, existing)
            if self._fence(connection, key, record):
                raise ServiceInvocationConflict("search run was fenced before preparation")
            head = connection.execute('SELECT generation,deleted FROM service_search_heads WHERE actor=%s AND surface=%s AND search_id=%s', key[:3]).fetchone()
            if head and (head["deleted"] or key[3] <= head["generation"]):
                raise ServiceInvocationConflict("search generation is stale or deleted")
            previous = connection.execute('SELECT generation FROM service_invocations WHERE actor=%s AND surface=%s AND search_id=%s AND generation<%s', key).fetchall()
            for row in previous:
                previous_key = (*key[:3], row["generation"])
                old = self._read(connection, previous_key)
                if old["status"] != "completed":
                    self._set_fence_reason(connection, previous_key, old, "superseded")
            connection.execute('INSERT INTO service_search_heads(actor,surface,search_id,generation) VALUES(%s,%s,%s,%s) ON CONFLICT(actor,surface,search_id) DO UPDATE SET generation=excluded.generation', key)
            storage.write(connection, key, record, insert=True)
            return self._project(connection, key, record)

    @staticmethod
    def _batch(record, batch_id, fingerprint):
        if not record:
            raise KeyError("service invocation not found")
        if record["plan_fingerprint"] != fingerprint:
            raise ServiceInvocationConflict("search batch plan fingerprint changed")
        for batch in record["batches"]:
            if batch["batch_id"] == batch_id:
                return batch
        raise KeyError("batch is not in the frozen plan")

    @staticmethod
    def _aggregate(record):
        states = [batch["status"] for batch in record["batches"]]
        if all(state == "completed" for state in states):
            status = "completed"
        elif "failed" in states:
            status = "failed"
        elif "cancelled" in states:
            status = "cancelled"
        elif all(state == "prepared" for state in states):
            status = "prepared"
        else:
            status = "running"
        record["status"] = states[0] if len(states) == 1 else status

    def get_batch(self, job, surface, batch_id, fingerprint):
        key = self.key(job, surface)
        with self.database.snapshot() as connection:
            record = self._read(connection, key)
            batch = self._batch(record, batch_id, fingerprint)
            return self._project_batch(connection, key, record, batch)

    def claim_dispatch(self, job, surface, batch_id, fingerprint):
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            record = self._read(connection, key)
            if not record or self._fence(connection, key, record):
                raise ServiceInvocationConflict("search was fenced before dispatch")
            batch = self._batch(record, batch_id, fingerprint)
            if batch["status"] != "prepared":
                return False
            batch["status"] = "dispatching"
            self._aggregate(record)
            self._write(connection, key, record)
            return True

    def observe(self, job, surface, batch_id, fingerprint, status):
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            record = self._read(connection, key)
            if record is None:
                raise KeyError("service invocation not found")
            batch = self._batch(record, batch_id, fingerprint)
            if batch["status"] not in TERMINAL_STATUSES and batch["status"] != status:
                batch["status"] = status
                self._aggregate(record)
                self._write(connection, key, record)
            return self._project_batch(connection, key, record, batch)

    def cancel(self, job, surface, *, deleted=False):
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            record = self._read(connection, key)
            reason = self._set_fence_reason(connection, key, record, "deleted" if deleted else "cancelled")
            if deleted:
                rows = connection.execute('SELECT generation FROM service_invocations WHERE actor=%s AND surface=%s AND search_id=%s', key[:3]).fetchall()
                for row in rows:
                    target_key = (*key[:3], row["generation"])
                    target = self._read(connection, target_key)
                    self._set_fence_reason(connection, target_key, target, "deleted")
            if deleted:
                # '1' fits the BIGINT flag of older schemas and BOOLEAN alike.
                connection.execute("INSERT INTO service_search_heads(actor,surface,search_id,generation,deleted) VALUES(%s,%s,%s,%s,'1') ON CONFLICT(actor,surface,search_id) DO UPDATE SET deleted='1'", key)
            return {"receipt": self._project(connection, key, record) if record else None,
                    "fenced": True, "fence_reason": reason}

    def fail(self, job, surface, fingerprint=""):
        if not isinstance(fingerprint, str):
            raise ValueError("plan_fingerprint must be a string")
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            record = self._read(connection, key)
            if fingerprint and record is None:
                raise KeyError("service invocation not found")
            if fingerprint and record["plan_fingerprint"] != fingerprint:
                raise ServiceInvocationConflict("search batch plan fingerprint changed")
            reason = self._set_fence_reason(connection, key, record, "failed")
            return {"receipt": self._project(connection, key, record) if record else None,
                    "fenced": True, "fence_reason": reason}

    def cancellation_targets(self, job, surface):
        key = self.key(job, surface)
        with self.database.snapshot() as connection:
            rows = connection.execute('SELECT generation FROM service_invocations WHERE actor=%s AND surface=%s AND search_id=%s', key[:3]).fetchall()
            targets = []
            for row in rows:
                target_key = (*key[:3], row["generation"])
                record = self._read(connection, target_key)
                if not self._fence(connection, target_key, record):
                    continue
                for batch in record["batches"]:
                    if batch["status"] not in TERMINAL_STATUSES | {"prepared"} and not batch.get("cancel_delivered"):
                        targets.append(deepcopy(batch))
            return targets

    def mark_cancelled(self, job, surface, batch_id, fingerprint):
        key = self.key(job, surface)
        with self.transaction(key) as connection:
            record = self._read(connection, key)
            batch = self._batch(record, batch_id, fingerprint)
            batch["cancel_delivered"] = True
            self._write(connection, key, record)


_STORES = {}
_STORE_LOCK = threading.Lock()


def get_store():
    """One initialized store per database/schema in each service process."""
    database = portal_database(schema="service_invocations")
    with _STORE_LOCK:
        if database.identity not in _STORES:
            _STORES[database.identity] = ServiceInvocationStore(database)
        return _STORES[database.identity]
