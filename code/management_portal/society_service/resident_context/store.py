"""PostgreSQL store of Resident discovery context (schema ``resident_context``).

A summary job moves ``queued``/``retry`` → ``preparing`` (the worker reads the
Resident's conversation) → ``ready`` (evidence stored, waiting for the
``resident-summary`` duty holder) → ``claimed`` (held by that Resident under a
lease) → deleted when its summary is submitted.  Jobs that fail
``max_attempts`` times park as ``failed`` until their inputs change.  An
expired lease returns the job to the step before it, so a stopped worker or
an abandoned claim never strands a job.
"""
from __future__ import annotations

import re
import secrets
import threading
from array import array
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from management_common.json_columns import json_text, json_value
from management_common.portal_database import portal_database
from management_common.database_time import instant, iso_time, decode_times

from .evidence import check_budget, fingerprint, project_summary_metadata, summary_text, utc_now

SCHEMA = "resident_context"
RESIDENT_ID = re.compile(r"resident_[0-9a-f]{12}")
DOCUMENT_COLUMNS = """
    r.resident_id, r.lineage_id, r.lineage_title, r.project_id, r.title,
    r.lineage_description, r.status, r.metadata_fingerprint,
    r.active, r.discovery_visible, r.global_summary_managed, r.discovered_at, r.updated_at, r.last_checked_at,
    w.summary_json, w.revision AS summary_revision, w.writer_id, s.source_cursor, s.source_fingerprint,
    s.message_fingerprint, s.message_ids_json, s.generation, s.incremental_generations,
    s.model_version, s.account_id, s.prompt_version,
    CASE WHEN r.global_summary_managed THEN COALESCE(s.stale, TRUE)
         ELSE COALESCE(w.revision, 0)=0 OR EXISTS (
             SELECT 1 FROM work_summary_rounds c WHERE c.resident_id=r.resident_id AND NOT c.covered)
    END AS stale,
    s.last_error, s.rebuilt_at, w.updated_at AS summary_updated_at
"""
DOCUMENT_FROM = """residents r LEFT JOIN snapshots s USING(resident_id)
    LEFT JOIN work_summaries w USING(resident_id)"""
SCHEMA_SQL = (
    """CREATE TABLE IF NOT EXISTS work_summary_layers (
        resident_id TEXT PRIMARY KEY, layers JSONB NOT NULL, maintenance JSONB NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS work_summaries (
        resident_id TEXT PRIMARY KEY, summary_json JSONB NOT NULL DEFAULT '{}',
        revision BIGINT NOT NULL DEFAULT 0, source_versions JSONB NOT NULL DEFAULT '{}',
        updated_at TIMESTAMPTZ, writer_id TEXT NOT NULL DEFAULT '')""",
    """CREATE TABLE IF NOT EXISTS work_summary_rounds (
        resident_id TEXT NOT NULL, message_id TEXT NOT NULL, covered BOOLEAN NOT NULL DEFAULT FALSE,
        PRIMARY KEY(resident_id,message_id))""",
    """CREATE TABLE IF NOT EXISTS residents (
        resident_id TEXT PRIMARY KEY,
        lineage_id TEXT NOT NULL DEFAULT '',
        lineage_title TEXT NOT NULL DEFAULT '',
        project_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        lineage_description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT '',
        metadata_fingerprint TEXT NOT NULL DEFAULT '',
        active BOOLEAN NOT NULL DEFAULT TRUE,
        discovery_visible BOOLEAN NOT NULL DEFAULT TRUE,
        discovered_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        last_checked_at TIMESTAMPTZ,
        global_summary_managed BOOLEAN NOT NULL DEFAULT TRUE
    )""",
    "CREATE INDEX IF NOT EXISTS residents_checked ON residents(active, last_checked_at)",
    """CREATE TABLE IF NOT EXISTS snapshots (
        resident_id TEXT PRIMARY KEY REFERENCES residents(resident_id) ON DELETE CASCADE,
        source_cursor BIGINT NOT NULL DEFAULT 0,
        source_fingerprint TEXT NOT NULL DEFAULT '',
        message_fingerprint TEXT NOT NULL DEFAULT '',
        message_ids_json JSONB NOT NULL DEFAULT '[]',
        generation BIGINT NOT NULL DEFAULT 0,
        incremental_generations BIGINT NOT NULL DEFAULT 0,
        model_version TEXT NOT NULL DEFAULT '',
        account_id TEXT NOT NULL DEFAULT '',
        prompt_version TEXT NOT NULL DEFAULT '',
        stale BOOLEAN NOT NULL DEFAULT TRUE,
        last_error TEXT NOT NULL DEFAULT '',
        rebuilt_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ
    )""",
    """CREATE TABLE IF NOT EXISTS summary_jobs (
        resident_id TEXT PRIMARY KEY REFERENCES residents(resident_id) ON DELETE CASCADE,
        mode TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        attempts BIGINT NOT NULL DEFAULT 0,
        available_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        last_error TEXT NOT NULL DEFAULT '',
        failed_source TEXT NOT NULL DEFAULT '',
        evidence_json JSONB NOT NULL DEFAULT '{}',
        claim_id TEXT NOT NULL DEFAULT '',
        claimed_by TEXT NOT NULL DEFAULT '',
        lease_until TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS summary_jobs_due ON summary_jobs(status, available_at, updated_at)",
    "CREATE INDEX IF NOT EXISTS summary_jobs_claim ON summary_jobs(claim_id)",
    """CREATE TABLE IF NOT EXISTS resident_embeddings (
        resident_id TEXT PRIMARY KEY REFERENCES residents(resident_id) ON DELETE CASCADE,
        model_version TEXT NOT NULL,
        source_fingerprint TEXT NOT NULL,
        dimensions BIGINT NOT NULL,
        vector BYTEA NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS resident_embeddings_model ON resident_embeddings(model_version)",
    """CREATE TABLE IF NOT EXISTS embedding_jobs (
        resident_id TEXT PRIMARY KEY REFERENCES residents(resident_id) ON DELETE CASCADE,
        reason TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        attempts BIGINT NOT NULL DEFAULT 0,
        available_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        last_error TEXT NOT NULL DEFAULT ''
    )""",
    "CREATE INDEX IF NOT EXISTS embedding_jobs_due ON embedding_jobs(status, available_at, updated_at)",
    """CREATE TABLE IF NOT EXISTS routing_eval_cases (
        case_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        ground_truth_json JSONB NOT NULL,
        tags_json JSONB NOT NULL DEFAULT '[]',
        rationale TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS routing_eval_submissions (
        submission_id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        predicted_json JSONB NOT NULL DEFAULT '[]',
        ground_truth_resident_id TEXT NOT NULL,
        ground_truth_action TEXT NOT NULL,
        rationale TEXT NOT NULL DEFAULT '',
        submitted_by TEXT NOT NULL,
        dataset_case_id TEXT NOT NULL DEFAULT '',
        dataset_added BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS service_state (
        key TEXT PRIMARY KEY,
        value_json JSONB NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )""",
)
SCHEMA_RELATIONS = frozenset(re.findall(r"CREATE (?:TABLE|INDEX) IF NOT EXISTS (\w+)", "\n".join(SCHEMA_SQL)))


def after(seconds: float) -> str:
    value = datetime.now(timezone.utc) + timedelta(seconds=max(0.0, float(seconds)))
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _document(value: Any) -> str:
    """JSON text for a JSON column, keys sorted so equal documents compare equal."""
    return json_text(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(row: Any) -> dict[str, Any]:
    item = decode_times(row, ('created_at', 'updated_at', 'discovered_at', 'last_checked_at', 'rebuilt_at', 'available_at', 'lease_until', 'summary_updated_at'))
    for key, default in (("summary_json", {}), ("message_ids_json", [])):
        if key in item:
            raw = item.pop(key)
            try:
                item[key.removesuffix("_json")] = json_value(raw) if raw else default
            except (TypeError, ValueError):
                item[key.removesuffix("_json")] = default
    for key in ("active", "stale"):
        if key in item and item[key] is not None:
            item[key] = bool(item[key])
    if "summary_revision" in item:
        item["summary_text"] = summary_text(item["summary"])
    return item


class ResidentContextStore:
    def __init__(self, *, database: Any = None) -> None:
        self._database = database
        self._ready = False
        self._lock = threading.Lock()

    @property
    def database(self) -> Any:
        if self._database is None:
            self._database = portal_database(schema=SCHEMA, max_size=4)
        return self._database

    def ensure_schema(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            with self.database.transaction() as connection:
                # Even when nothing is missing, CREATE INDEX IF NOT EXISTS
                # locks its table against writers until the transaction ends.
                present = {row["relname"] for row in connection.execute(
                    "SELECT relname FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relname=ANY(%s)",
                    (sorted(SCHEMA_RELATIONS),),
                )}
                if present != SCHEMA_RELATIONS:
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_schema(), 0))")
                    for statement in SCHEMA_SQL:
                        connection.execute(statement)
            self._ready = True

    def transaction(self):
        self.ensure_schema()
        return self.database.transaction()

    def read(self):
        self.ensure_schema()
        return self.database.read_current()

    # -- catalog --------------------------------------------------------------

    def upsert_catalog(self, records: list[dict[str, Any]]) -> dict[str, int]:
        now = utc_now()
        seen: set[str] = set()
        created = changed = 0
        with self.transaction() as connection:
            previous = {
                row["resident_id"]: row["metadata_fingerprint"]
                for row in connection.execute("SELECT resident_id, metadata_fingerprint FROM residents")
            }
            for record in records:
                resident_id = str(record.get("resident_id") or "").strip().lower()
                if not RESIDENT_ID.fullmatch(resident_id) or resident_id in seen:
                    continue
                seen.add(resident_id)
                digest = fingerprint(project_summary_metadata(record))
                global_managed = record.get("global_summary_managed", record.get("discovery_visible", True))
                if not global_managed:
                    digest = fingerprint({"metadata": digest, "family_managed": True})
                known = previous.get(resident_id)
                connection.execute("""
                    INSERT INTO residents(
                        resident_id, lineage_id, lineage_title, project_id, title,
                        lineage_description, status,
                        metadata_fingerprint, active, discovered_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                    ON CONFLICT(resident_id) DO UPDATE SET
                        lineage_id=excluded.lineage_id, lineage_title=excluded.lineage_title,
                        project_id=excluded.project_id, title=excluded.title,
                        lineage_description=excluded.lineage_description, status=excluded.status,
                        metadata_fingerprint=excluded.metadata_fingerprint, active=TRUE,
                        updated_at=CASE
                            WHEN residents.metadata_fingerprint=excluded.metadata_fingerprint AND residents.active
                            THEN residents.updated_at ELSE excluded.updated_at END
                """, (
                    resident_id,
                    str(record.get("lineage_id") or ""),
                    str(record.get("lineage_title") or ""),
                    str(record.get("project_id") or ""),
                    str(record.get("title") or resident_id),
                    str(record.get("lineage_description") or ""),
                    str(record.get("status") or ""),
                    digest,
                    now,
                    now,
                ))
                connection.execute("UPDATE residents SET discovery_visible=%s,global_summary_managed=%s WHERE resident_id=%s",
                                   (record.get("discovery_visible", True), global_managed, resident_id))
                if not global_managed:
                    connection.execute("DELETE FROM summary_jobs WHERE resident_id=%s", (resident_id,))
                if known is not None and known == digest:
                    continue
                if known is None:
                    created += 1
                else:
                    changed += 1
                reason = "catalog_created" if known is None else "metadata_changed"
                if global_managed:
                    self._enqueue(
                        connection, resident_id, mode="full" if known is None else "incremental",
                        reason=reason, available_at=now, revive_failed=True,
                    )
                self._enqueue_embedding(connection, resident_id, reason=reason, available_at=now)
            connection.execute(
                "UPDATE residents SET active=FALSE, updated_at=%s WHERE active AND NOT (resident_id = ANY(%s))",
                (now, sorted(seen)),
            )
            self._discard_inactive_jobs(connection)
        return {"seen": len(seen), "created": created, "changed": changed}

    @staticmethod
    def _discard_inactive_jobs(connection: Any) -> dict[str, int]:
        summary = connection.execute(
            "DELETE FROM summary_jobs WHERE resident_id IN (SELECT resident_id FROM residents WHERE NOT active)"
        ).rowcount
        embeddings = connection.execute(
            "DELETE FROM embedding_jobs WHERE resident_id IN (SELECT resident_id FROM residents WHERE NOT active)"
        ).rowcount
        return {"summary_jobs": int(summary or 0), "embedding_jobs": int(embeddings or 0)}

    def discard_inactive_jobs(self) -> dict[str, int]:
        with self.transaction() as connection:
            return self._discard_inactive_jobs(connection)

    def inactive_resident_ids(self) -> list[str]:
        with self.read() as connection:
            return [str(row["resident_id"]) for row in connection.execute(
                "SELECT resident_id FROM residents WHERE NOT active ORDER BY resident_id"
            )]

    def purge(self, resident_ids: list[str]) -> int:
        """Delete inactive Residents with their summaries and vectors."""

        if not resident_ids:
            return 0
        with self.transaction() as connection:
            gone = [str(row["resident_id"]) for row in connection.execute(
                "SELECT resident_id FROM residents WHERE NOT active AND resident_id = ANY(%s)",
                (sorted(resident_ids),),
            )]
            if gone:
                connection.execute("DELETE FROM work_summary_rounds WHERE resident_id = ANY(%s)", (gone,))
                connection.execute("DELETE FROM work_summaries WHERE resident_id = ANY(%s)", (gone,))
                # Snapshots, jobs and vectors follow by ON DELETE CASCADE.
                connection.execute("DELETE FROM residents WHERE resident_id = ANY(%s)", (gone,))
        return len(gone)

    def residents_due_for_check(self, limit: int) -> list[str]:
        with self.read() as connection:
            rows = connection.execute("""
                SELECT resident_id FROM residents WHERE active AND discovery_visible
                ORDER BY last_checked_at NULLS FIRST, resident_id
                LIMIT %s
            """, (max(1, int(limit)),)).fetchall()
        return [str(row["resident_id"]) for row in rows]

    def mark_checked(self, resident_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE residents SET last_checked_at=%s WHERE resident_id=%s", (utc_now(), resident_id),
            )

    # -- documents --------------------------------------------------------------

    def resident(self, resident_id: str) -> dict[str, Any] | None:
        with self.read() as connection:
            row = connection.execute(
                f"SELECT {DOCUMENT_COLUMNS} FROM {DOCUMENT_FROM} "
                "WHERE r.resident_id=%s",
                (resident_id,),
            ).fetchone()
        return _decode(row) if row else None

    def documents(self, resident_ids: Iterable[str] | None = None) -> list[dict[str, Any]]:
        """Every active Resident with its summary; optionally only the given ones."""

        query = (
            f"SELECT {DOCUMENT_COLUMNS} FROM {DOCUMENT_FROM} WHERE r.active"
        )
        parameters: tuple[Any, ...] = ()
        if resident_ids is not None:
            query += " AND r.resident_id = ANY(%s)"
            parameters = (sorted({str(value) for value in resident_ids if str(value)}),)
        else:
            query += " AND r.discovery_visible"
        with self.read() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_decode(row) for row in rows]

    def work_summary(self, resident_id: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT w.*,l.layers,l.maintenance FROM work_summaries w LEFT JOIN work_summary_layers l USING(resident_id) WHERE resident_id=%s", (resident_id,)).fetchone()
            missing = connection.execute("SELECT message_id FROM work_summary_rounds WHERE resident_id=%s AND NOT covered ORDER BY message_id",
                                         (resident_id,)).fetchall()
        item = _decode(row) if row else {"resident_id": resident_id, "revision": 0, "updated_at": "", "writer_id": ""}
        item.setdefault("summary", {})
        item["source_versions"] = json_value(item.get("source_versions", "{}"))
        item["missing_message_ids"] = [r["message_id"] for r in missing]
        from . import summary_layers
        layers, state = summary_layers.initial(item["summary"], item.get("updated_at"))
        if row and row["layers"] is not None:
            layers, state = json_value(row["layers"]), json_value(row["maintenance"])
        item.update(layers=layers, maintenance=state, consolidation=summary_layers.due(layers, state))
        return item

    def write_work_summary(self, resident_id: str, writer: str, summary: dict | None,
                           message_ids: list[str], expected_revision: int, source_versions: dict,
                           *, temporal: bool = False, consolidation: dict | None = None,
                           counted_message_ids: list[str] | None = None) -> dict:
        with self.transaction() as connection:
            if temporal:
                from . import summary_layers
                connection.execute("INSERT INTO work_summaries(resident_id) VALUES (%s) ON CONFLICT DO NOTHING", (resident_id,))
                row = connection.execute("SELECT * FROM work_summaries WHERE resident_id=%s FOR UPDATE", (resident_id,)).fetchone()
                if row["revision"] != expected_revision:
                    raise ValueError("工作摘要版本已变化，请重新读取后提交")
                saved = connection.execute("SELECT * FROM work_summary_layers WHERE resident_id=%s", (resident_id,)).fetchone()
                layers, state = summary_layers.initial(json_value(row["summary_json"]), iso_time(row["updated_at"]) if row["updated_at"] else "")
                if saved:
                    layers, state = json_value(saved["layers"]), json_value(saved["maintenance"])
                previous_layers = _document(layers)
                if consolidation is not None:
                    layers, state = summary_layers.consolidate(layers, state, consolidation)
                elif summary is not None:
                    layers["recent"] = summary
                    check_budget(layers)
                # No-content coverage remains legal for legacy oversized
                # summaries, so it cannot prevent the subsequent condensation.
                new_count = 0
                for mid in message_ids if counted_message_ids is None else counted_message_ids:
                    covered = connection.execute("SELECT covered FROM work_summary_rounds WHERE resident_id=%s AND message_id=%s", (resident_id, mid)).fetchone()
                    new_count += int(not covered or not covered["covered"])
                state["phase_rounds"] += new_count
                state["long_term_rounds"] += new_count
                content = summary_layers.project(layers) if summary is not None or consolidation is not None else None
                self._write_summary(connection, resident_id, writer, content, expected_revision, source_versions,
                                    preserve_layers=True, force_revision=previous_layers != _document(layers))
                connection.execute("INSERT INTO work_summary_layers VALUES (%s,%s,%s) ON CONFLICT(resident_id) DO UPDATE SET layers=EXCLUDED.layers,maintenance=EXCLUDED.maintenance",
                                   (resident_id, _document(layers), _document(state)))
            else:
                self._write_summary(connection, resident_id, writer, summary, expected_revision, source_versions)
            for mid in message_ids:
                connection.execute("INSERT INTO work_summary_rounds VALUES (%s,%s,TRUE) ON CONFLICT(resident_id,message_id) DO UPDATE SET covered=TRUE", (resident_id,mid))
        return self.work_summary(resident_id)

    @staticmethod
    def _write_summary(connection: Any, resident_id: str, writer: str | None, summary: dict | None,
                       expected_revision: int | None, source_versions: dict, *, preserve_layers: bool = False,
                       force_revision: bool = False) -> int:
        """The only content writer, shared by members, stewards and global claims."""
        connection.execute("INSERT INTO work_summaries(resident_id) VALUES (%s) ON CONFLICT DO NOTHING", (resident_id,))
        row = connection.execute("SELECT * FROM work_summaries WHERE resident_id=%s FOR UPDATE", (resident_id,)).fetchone()
        if expected_revision is not None and int(row["revision"]) != expected_revision:
            raise ValueError("工作摘要版本已变化，请重新读取后提交")
        if summary is None and not row["revision"]:
            raise ValueError("首次提交必须提供摘要，不能声明无新增")
        stored = _document(json_value(row["summary_json"]))
        content = _document(summary) if summary is not None else stored
        revision = int(row["revision"]) + int(content != stored or not row["revision"] or force_revision)
        changed = revision != int(row["revision"])
        changed_at = utc_now() if changed else row["updated_at"]
        author = writer if writer is not None and summary is not None else row["writer_id"]
        connection.execute("UPDATE work_summaries SET summary_json=%s,revision=%s,source_versions=%s,updated_at=%s,writer_id=%s WHERE resident_id=%s",
                           (content, revision, _document(source_versions), changed_at, author, resident_id))
        if summary is not None and not preserve_layers:
            connection.execute("DELETE FROM work_summary_layers WHERE resident_id=%s", (resident_id,))
        if changed and connection.execute("SELECT 1 FROM residents WHERE resident_id=%s", (resident_id,)).fetchone():
            # Lexical search reads the new text immediately; don't rank with the old vector meanwhile.
            connection.execute("DELETE FROM resident_embeddings WHERE resident_id=%s", (resident_id,))
            ResidentContextStore._enqueue_embedding(connection, resident_id, reason="summary_updated", available_at=utc_now())
        return revision

    def missing_work_round(self, resident_id: str, message_id: str) -> bool:
        with self.transaction() as connection:
            connection.execute("INSERT INTO work_summary_rounds VALUES (%s,%s,FALSE) ON CONFLICT DO NOTHING", (resident_id,message_id))
            row = connection.execute("SELECT covered FROM work_summary_rounds WHERE resident_id=%s AND message_id=%s", (resident_id,message_id)).fetchone()
        return not row["covered"]

    def discard_summary_job(self, resident_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM summary_jobs WHERE resident_id=%s", (resident_id,))

    def vectors(self, model_version: str, resident_ids: Iterable[str] | None = None) -> dict[str, array]:
        query = (
            "SELECT e.resident_id, e.vector FROM resident_embeddings e JOIN residents r USING(resident_id) "
            "WHERE r.active AND e.model_version=%s"
        )
        parameters: tuple[Any, ...] = (model_version,)
        if resident_ids is not None:
            query += " AND e.resident_id = ANY(%s)"
            parameters += (sorted({str(value) for value in resident_ids if str(value)}),)
        output: dict[str, array] = {}
        with self.read() as connection:
            for row in connection.execute(query, parameters):
                values = array("f")
                values.frombytes(bytes(row["vector"]))
                output[str(row["resident_id"])] = values
        return output

    def set_message_fingerprint_baseline(self, resident_id: str, value: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE snapshots SET message_fingerprint=%s WHERE resident_id=%s AND generation>0",
                (str(value or ""), resident_id),
            )

    # -- summary jobs -----------------------------------------------------------

    def enqueue(
        self, resident_id: str, *, mode: str, reason: str,
        available_at: str | None = None, revive_failed: bool = False,
    ) -> None:
        with self.transaction() as connection:
            self._enqueue(
                connection, resident_id, mode=mode, reason=reason,
                available_at=available_at or utc_now(), revive_failed=revive_failed,
            )

    @staticmethod
    def _enqueue(
        connection: Any, resident_id: str, *, mode: str, reason: str,
        available_at: str, revive_failed: bool = False,
    ) -> None:
        """Queue summary work without letting repeated triggers bypass failure policy.

        A ``failed`` job stays parked unless its inputs changed
        (``revive_failed``) or someone asked for it (``manual:`` reason);
        reviving starts a fresh attempt budget.  A ``retry`` job keeps its
        backoff deadline.  Prepared evidence that is not yet claimed is
        discarded, so the writer never receives a superseded conversation; a
        job already being prepared or written finishes, and reconcile notices
        what changed meanwhile.
        """

        now = utc_now()
        manual = reason.startswith("manual:")
        inserted = connection.execute("""
            INSERT INTO summary_jobs(resident_id, mode, reason, status, attempts, available_at, created_at, updated_at)
            VALUES (%s, %s, %s, 'queued', 0, %s, %s, %s) ON CONFLICT(resident_id) DO NOTHING
            RETURNING resident_id
        """, (resident_id, mode, reason[:300], available_at, now, now)).fetchone()
        if inserted is not None:
            return
        existing = connection.execute(
            "SELECT mode, status, available_at FROM summary_jobs WHERE resident_id=%s FOR UPDATE",
            (resident_id,),
        ).fetchone()
        if existing is None:
            return
        status = str(existing["status"] or "")
        if status == "failed" and not (revive_failed or manual):
            return
        next_mode = "full" if mode == "full" or existing["mode"] == "full" else "incremental"
        if status in {"preparing", "claimed"}:
            next_status, next_available = status, iso_time(existing["available_at"])
        elif status == "retry" and not manual:
            next_status, next_available = "retry", iso_time(max(existing["available_at"], instant(available_at)))
        else:
            next_status, next_available = "queued", available_at
        fresh_budget = status == "failed" or (manual and status not in {"preparing", "claimed"})
        connection.execute("""
            UPDATE summary_jobs
            SET mode=%s, reason=%s, status=%s, available_at=%s, updated_at=%s,
                evidence_json=CASE WHEN %s='queued' THEN '{}' ELSE evidence_json END,
                attempts=CASE WHEN %s THEN 0 ELSE attempts END,
                failed_source=CASE WHEN %s THEN '' ELSE failed_source END
            WHERE resident_id=%s
        """, (
            next_mode, reason[:300], next_status, next_available, now,
            next_status, fresh_budget, fresh_budget, resident_id,
        ))

    def enqueue_summary_backfill(self, prompt_version: str) -> int:
        """Rebuild summaries written for another evidence contract, once."""

        now = utc_now()
        queued = 0
        with self.transaction() as connection:
            rows = connection.execute("""
                SELECT r.resident_id, j.mode FROM residents r
                JOIN snapshots s USING(resident_id)
                LEFT JOIN summary_jobs j USING(resident_id)
                WHERE r.active AND r.global_summary_managed AND s.generation>0 AND s.prompt_version<>%s
            """, (prompt_version,)).fetchall()
            for row in rows:
                connection.execute("UPDATE snapshots SET stale=TRUE WHERE resident_id=%s", (row["resident_id"],))
                if row["mode"] == "full":
                    continue
                self._enqueue(
                    connection, row["resident_id"], mode="full",
                    reason="summary_projection_changed", available_at=now,
                )
                queued += 1
        return queued

    def summary_job(self, resident_id: str) -> dict[str, Any] | None:
        with self.read() as connection:
            row = connection.execute("SELECT * FROM summary_jobs WHERE resident_id=%s", (resident_id,)).fetchone()
        return dict(zip(row.keys(), row)) if row else None

    def set_failed_source(self, resident_id: str, failed_source: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE summary_jobs SET failed_source=%s WHERE resident_id=%s AND status='failed'",
                (failed_source, resident_id),
            )

    def take_jobs_to_prepare(self, limit: int, *, lease_seconds: float) -> list[dict[str, Any]]:
        """Due jobs whose evidence the worker reads next; each counts one attempt."""

        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute("""
                SELECT j.* FROM summary_jobs j JOIN residents r USING(resident_id)
                WHERE r.active AND r.global_summary_managed AND j.status IN ('queued', 'retry') AND j.available_at <= %s
                ORDER BY CASE WHEN j.reason LIKE 'manual:%%' THEN 0 ELSE 1 END,
                         CASE j.mode WHEN 'full' THEN 0 ELSE 1 END, j.updated_at, j.resident_id
                LIMIT %s FOR UPDATE OF j SKIP LOCKED
            """, (now, max(1, int(limit)))).fetchall()
            lease = after(lease_seconds)
            for row in rows:
                connection.execute(
                    "UPDATE summary_jobs SET status='preparing', attempts=attempts+1, lease_until=%s, updated_at=%s "
                    "WHERE resident_id=%s",
                    (lease, now, row["resident_id"]),
                )
        return [dict(zip(row.keys(), row)) for row in rows]

    def mark_ready(self, resident_id: str, evidence: dict[str, Any]) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute("""
                UPDATE summary_jobs SET status='ready', evidence_json=%s, lease_until=NULL, updated_at=%s
                WHERE resident_id=%s AND status='preparing'
            """, (_document(evidence), utc_now(), resident_id))
        return bool(cursor.rowcount)

    def claim_ready_jobs(
        self, holder: str, *, limit: int | None, lease_seconds: float, evidence_budget: int,
    ) -> list[dict[str, Any]]:
        """Hand prepared jobs to the summary writer, bounded by evidence size and an optional count."""

        now = utc_now()
        claimed: list[dict[str, Any]] = []
        with self.transaction() as connection:
            rows = connection.execute("""
                SELECT j.resident_id, j.mode, j.reason, j.attempts, j.evidence_json,
                       length(j.evidence_json::text) AS evidence_size, r.title, r.lineage_id
                FROM summary_jobs j JOIN residents r USING(resident_id)
                WHERE r.active AND r.global_summary_managed AND j.status='ready'
                ORDER BY CASE WHEN j.reason LIKE 'manual:%%' THEN 0 ELSE 1 END,
                         CASE j.mode WHEN 'full' THEN 0 ELSE 1 END, j.updated_at, j.resident_id
                LIMIT %s FOR UPDATE OF j SKIP LOCKED
            """, (None if limit is None else max(1, int(limit)),)).fetchall()
            lease = after(lease_seconds)
            used = 0
            for row in rows:
                size = int(row["evidence_size"] or 0)
                if claimed and used + size > evidence_budget:
                    break
                used += size
                claim_id = f"summary_claim_{secrets.token_hex(12)}"
                connection.execute("""
                    UPDATE summary_jobs SET status='claimed', claim_id=%s, claimed_by=%s, lease_until=%s, updated_at=%s
                    WHERE resident_id=%s
                """, (claim_id, holder, lease, now, row["resident_id"]))
                claimed.append({
                    "claim_id": claim_id,
                    "resident_id": str(row["resident_id"]),
                    "title": str(row["title"] or ""),
                    "lineage_id": str(row["lineage_id"] or ""),
                    "mode": str(row["mode"] or ""),
                    "reason": str(row["reason"] or ""),
                    "attempt": int(row["attempts"] or 0),
                    "evidence_chars": size,
                    "lease_until": lease,
                    "input": json_value(row["evidence_json"] or "{}").get("input") or {},
                })
        return claimed

    def claimed_job(self, claim_id: str, holder: str) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM summary_jobs WHERE claim_id=%s AND status='claimed'", (claim_id,),
            ).fetchone()
        if row is None:
            raise KeyError("没有这个摘要认领，或认领已过期被收回")
        if row["claimed_by"] != holder:
            raise PermissionError("这个摘要认领属于另一个 Resident")
        job = dict(zip(row.keys(), row))
        job["evidence"] = json_value(job.pop("evidence_json") or "{}")
        return job

    def complete_claimed(
        self, claim_id: str, holder: str, *, summary: dict[str, Any], summary_text: str,
        model_version: str, account_id: str, prompt_version: str,
    ) -> dict[str, Any]:
        """Record a written summary for a claim still held by ``holder``."""

        now = utc_now()
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT * FROM summary_jobs WHERE claim_id=%s AND status='claimed' FOR UPDATE", (claim_id,),
            ).fetchone()
            if job is None:
                raise KeyError("没有这个摘要认领，或认领已过期被收回")
            if job["claimed_by"] != holder:
                raise PermissionError("这个摘要认领属于另一个 Resident")
            evidence = json_value(job["evidence_json"] or "{}")
            source = evidence.get("source") or {}
            result = self._write_snapshot(
                connection, str(job["resident_id"]), mode=str(job["mode"]), summary=summary,
                summary_text=summary_text, source=source, model_version=model_version,
                account_id=account_id, prompt_version=prompt_version, now=now, writer=holder,
            )
            connection.execute("DELETE FROM summary_jobs WHERE resident_id=%s", (job["resident_id"],))
            self._enqueue_embedding(connection, str(job["resident_id"]), reason="summary_updated", available_at=now)
        return {"resident_id": str(job["resident_id"]), "mode": str(job["mode"]), **result}

    def release_claim(
        self, claim_id: str, holder: str, error: str, *, retry_at: str, max_attempts: int, failed_source: str,
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT * FROM summary_jobs WHERE claim_id=%s AND status='claimed' FOR UPDATE", (claim_id,),
            ).fetchone()
            if job is None:
                raise KeyError("没有这个摘要认领，或认领已过期被收回")
            if job["claimed_by"] != holder:
                raise PermissionError("这个摘要认领属于另一个 Resident")
            terminal = int(job["attempts"] or 0) >= max_attempts
            self._fail(connection, str(job["resident_id"]), error, retry_at=retry_at,
                       terminal=terminal, failed_source=failed_source)
        return {"resident_id": str(job["resident_id"]), "parked": terminal, "attempts": int(job["attempts"] or 0)}

    def complete_pending(
        self, resident_id: str, *, summary: dict[str, Any], summary_text: str,
        source: dict[str, Any], prompt_version: str,
    ) -> dict[str, Any]:
        """Advance the observed conversation without changing stable discovery text."""

        now = utc_now()
        with self.transaction() as connection:
            result = self._write_snapshot(
                connection, resident_id, mode="pending", summary=summary, summary_text=summary_text,
                source=source, model_version="", account_id=None,
                prompt_version=prompt_version, now=now,
            )
            connection.execute("DELETE FROM summary_jobs WHERE resident_id=%s", (resident_id,))
        return result

    @staticmethod
    def _write_snapshot(
        connection: Any, resident_id: str, *, mode: str, summary: dict[str, Any], summary_text: str,
        source: dict[str, Any], model_version: str, account_id: str | None, prompt_version: str, now: str,
        writer: str | None = None,
    ) -> dict[str, Any]:
        previous = connection.execute(
            "SELECT generation, incremental_generations, account_id, rebuilt_at, model_version "
            "FROM snapshots WHERE resident_id=%s FOR UPDATE",
            (resident_id,),
        ).fetchone()
        generation = int(previous["generation"] or 0) + 1 if previous else 1
        prior_incrementals = int(previous["incremental_generations"] or 0) if previous else 0
        if mode == "pending":
            incremental_generations = prior_incrementals
            account = str(previous["account_id"] or "") if previous else ""
            rebuilt_at = iso_time(previous["rebuilt_at"]) if previous else ""
            # A pending record keeps the writer of the stable text it carries.
            model_version = str(previous["model_version"] or "") if previous else ""
        else:
            incremental_generations = 0 if mode == "full" else prior_incrementals + 1
            account = str(account_id or "")
            rebuilt_at = now if mode == "full" else (iso_time(previous["rebuilt_at"]) if previous else "")
        message_ids = [str(value) for value in source.get("message_ids") or []][-1000:]
        source_cursor = int(source.get("cursor") or 0)
        revision = ResidentContextStore._write_summary(
            connection, resident_id, writer, summary, source.get("summary_revision"), {},
        )
        connection.execute("""
            INSERT INTO snapshots(
                resident_id, source_cursor, source_fingerprint,
                message_fingerprint, message_ids_json, generation, incremental_generations,
                model_version, account_id, prompt_version, stale, last_error, rebuilt_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, '', %s, %s)
            ON CONFLICT(resident_id) DO UPDATE SET
                source_cursor=excluded.source_cursor, source_fingerprint=excluded.source_fingerprint,
                message_fingerprint=excluded.message_fingerprint, message_ids_json=excluded.message_ids_json,
                generation=excluded.generation, incremental_generations=excluded.incremental_generations,
                model_version=excluded.model_version, account_id=excluded.account_id,
                prompt_version=excluded.prompt_version, stale=FALSE, last_error='',
                rebuilt_at=excluded.rebuilt_at, updated_at=excluded.updated_at
        """, (
            resident_id, source_cursor,
            str(source.get("fingerprint") or ""), str(source.get("message_fingerprint") or ""),
            _document(message_ids), generation, incremental_generations,
            model_version, account, prompt_version, instant(rebuilt_at), now,
        ))
        return {"generation": generation, "incremental_generations": incremental_generations, "revision": revision}

    def fail_job(
        self, resident_id: str, error: str, *, retry_at: str, terminal: bool = False, failed_source: str = "",
    ) -> None:
        with self.transaction() as connection:
            self._fail(connection, resident_id, error, retry_at=retry_at, terminal=terminal,
                       failed_source=failed_source)

    @staticmethod
    def _fail(
        connection: Any, resident_id: str, error: str, *, retry_at: str, terminal: bool, failed_source: str,
    ) -> None:
        """Back off for another attempt, or park as ``failed`` until ``failed_source`` changes."""

        now = utc_now()
        connection.execute("""
            UPDATE summary_jobs
            SET status=%s, available_at=%s, updated_at=%s, last_error=%s, failed_source=%s,
                evidence_json='{}', claim_id='', claimed_by='', lease_until=NULL
            WHERE resident_id=%s
        """, (
            "failed" if terminal else "retry", retry_at, now, error[:1000],
            failed_source if terminal else "", resident_id,
        ))
        connection.execute("""
            INSERT INTO snapshots(resident_id, stale, last_error, updated_at) VALUES (%s, TRUE, %s, %s)
            ON CONFLICT(resident_id) DO UPDATE SET stale=TRUE, last_error=excluded.last_error,
                updated_at=excluded.updated_at
        """, (resident_id, error[:1000], now))

    def recover_expired_leases(self) -> dict[str, int]:
        """Return abandoned work to the step before it."""

        now = utc_now()
        with self.transaction() as connection:
            claimed = connection.execute("""
                UPDATE summary_jobs SET status='ready', claim_id='', claimed_by='', lease_until=NULL, updated_at=%s
                WHERE status='claimed' AND lease_until IS NOT NULL AND lease_until<%s
            """, (now, now)).rowcount
            preparing = connection.execute("""
                UPDATE summary_jobs SET status='queued', lease_until=NULL, updated_at=%s,
                    last_error=CASE WHEN last_error='' THEN 'recovered after worker interruption' ELSE last_error END
                WHERE status='preparing' AND (lease_until IS NULL OR lease_until<%s)
            """, (now, now)).rowcount
        return {"claimed": int(claimed or 0), "preparing": int(preparing or 0)}

    # -- embeddings -------------------------------------------------------------

    @staticmethod
    def _enqueue_embedding(connection: Any, resident_id: str, *, reason: str, available_at: str) -> None:
        now = utc_now()
        connection.execute("""
            INSERT INTO embedding_jobs(resident_id, reason, status, attempts, available_at, created_at, updated_at)
            VALUES (%s, %s, 'queued', 0, %s, %s, %s)
            ON CONFLICT(resident_id) DO UPDATE SET
                reason=excluded.reason,
                attempts=CASE WHEN embedding_jobs.status='failed' THEN 0 ELSE embedding_jobs.attempts END,
                status=CASE WHEN embedding_jobs.status='running' THEN 'running' ELSE 'queued' END,
                available_at=CASE WHEN embedding_jobs.status='running' THEN embedding_jobs.available_at
                                  ELSE excluded.available_at END,
                updated_at=excluded.updated_at
        """, (resident_id, reason[:300], available_at, now, now))

    def enqueue_embedding_backfill(self, model_version: str) -> int:
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute("""
                SELECT r.resident_id FROM residents r LEFT JOIN resident_embeddings e USING(resident_id)
                WHERE r.active AND (e.resident_id IS NULL OR e.model_version<>%s)
                ORDER BY r.resident_id
            """, (model_version,)).fetchall()
            for row in rows:
                self._enqueue_embedding(
                    connection, str(row["resident_id"]), reason="missing_or_model_changed", available_at=now,
                )
        return len(rows)

    def recover_running_embeddings(self) -> int:
        with self.transaction() as connection:
            return int(connection.execute(
                "UPDATE embedding_jobs SET status='queued', updated_at=%s WHERE status='running'", (utc_now(),),
            ).rowcount or 0)

    def claim_embedding_job(self) -> dict[str, Any] | None:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("""
                SELECT j.* FROM embedding_jobs j JOIN residents r USING(resident_id)
                WHERE r.active AND j.status IN ('queued', 'retry') AND j.available_at<=%s
                ORDER BY j.updated_at, j.resident_id LIMIT 1 FOR UPDATE OF j SKIP LOCKED
            """, (now,)).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE embedding_jobs SET status='running', attempts=attempts+1, updated_at=%s WHERE resident_id=%s",
                (now, row["resident_id"]),
            )
        return dict(zip(row.keys(), row))

    def complete_embedding(
        self, resident_id: str, *, vector: list[float], model_version: str, source_fingerprint: str,
        summary_revision: int,
    ) -> bool:
        if not vector:
            raise ValueError("embedding vector is empty")
        values = array("f", (float(value) for value in vector))
        now = utc_now()
        with self.transaction() as connection:
            connection.execute("INSERT INTO work_summaries(resident_id) VALUES (%s) ON CONFLICT DO NOTHING", (resident_id,))
            row = connection.execute("SELECT revision FROM work_summaries WHERE resident_id=%s FOR UPDATE", (resident_id,)).fetchone()
            if row["revision"] != summary_revision:
                connection.execute("UPDATE embedding_jobs SET status='queued',available_at=%s WHERE resident_id=%s", (now, resident_id))
                return False
            connection.execute("""
                INSERT INTO resident_embeddings(resident_id, model_version, source_fingerprint, dimensions, vector, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT(resident_id) DO UPDATE SET
                    model_version=excluded.model_version, source_fingerprint=excluded.source_fingerprint,
                    dimensions=excluded.dimensions, vector=excluded.vector, updated_at=excluded.updated_at
            """, (resident_id, model_version, source_fingerprint, len(values), values.tobytes(), now))
            connection.execute("DELETE FROM embedding_jobs WHERE resident_id=%s", (resident_id,))
        return True

    def discard_embedding_job(self, resident_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM embedding_jobs WHERE resident_id=%s", (resident_id,))

    def fail_embedding(self, resident_id: str, error: str, *, retry_at: str, terminal: bool = False) -> None:
        with self.transaction() as connection:
            connection.execute("""
                UPDATE embedding_jobs SET status=%s, available_at=%s, updated_at=%s, last_error=%s
                WHERE resident_id=%s
            """, ("failed" if terminal else "retry", retry_at, utc_now(), error[:1000], resident_id))

    # -- routing evaluation -----------------------------------------------------

    def add_routing_eval_case(self, case: dict[str, Any]) -> dict[str, Any]:
        """Add a ground-truth case unless the same query already has this ground truth."""

        ground_truth = _document(case["ground_truth"])
        with self.transaction() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('routing_eval_cases', 0))")
            existing = connection.execute(
                "SELECT case_id FROM routing_eval_cases WHERE query=%s AND ground_truth_json=%s",
                (case["query"], ground_truth),
            ).fetchone()
            if existing is not None:
                return {"case_id": str(existing["case_id"]), "added": False}
            connection.execute("""
                INSERT INTO routing_eval_cases(case_id, query, ground_truth_json, tags_json, rationale, created_at)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT(case_id) DO NOTHING
            """, (
                case["case_id"], case["query"], ground_truth, _document(list(case.get("tags") or [])),
                str(case.get("rationale") or ""), str(case.get("created_at") or utc_now()),
            ))
        return {"case_id": case["case_id"], "added": True}

    def record_routing_eval_submission(self, submission: dict[str, Any]) -> dict[str, Any]:
        row = {
            "submission_id": submission["submission_id"],
            "query": submission["query"],
            "predicted_json": _document(submission.get("predicted") or []),
            "ground_truth_resident_id": submission["ground_truth_resident_id"],
            "ground_truth_action": submission["ground_truth_action"],
            "rationale": str(submission.get("rationale") or ""),
            "submitted_by": submission["submitted_by"],
            "dataset_case_id": str(submission.get("dataset_case_id") or ""),
            "dataset_added": bool(submission.get("dataset_added")),
            "created_at": str(submission.get("created_at") or utc_now()),
        }
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO routing_eval_submissions({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})",
                tuple(row.values()),
            )
        output = {key: value for key, value in row.items() if key != "predicted_json"}
        output["predicted"] = list(submission.get("predicted") or [])
        return output

    # -- worker lease and status ------------------------------------------------

    def hold_worker_lease(self, holder: str, *, ttl_seconds: float) -> bool:
        """One worker at a time across the Portal instances sharing this data."""

        now = utc_now()
        with self.transaction() as connection:
            connection.execute("""
                INSERT INTO service_state(key, value_json, updated_at) VALUES ('worker_lease', '{}', %s)
                ON CONFLICT(key) DO NOTHING
            """, (now,))
            row = connection.execute(
                "SELECT value_json FROM service_state WHERE key='worker_lease' FOR UPDATE",
            ).fetchone()
            current = json_value(row["value_json"] or "{}") if row else {}
            if current.get("holder") not in {None, "", holder} and str(current.get("lease_until") or "") > now:
                return False
            connection.execute(
                "UPDATE service_state SET value_json=%s, updated_at=%s WHERE key='worker_lease'",
                (_document({"holder": holder, "lease_until": after(ttl_seconds)}), now),
            )
        return True

    def release_worker_lease(self, holder: str) -> None:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT value_json FROM service_state WHERE key='worker_lease' FOR UPDATE",
            ).fetchone()
            if row and json_value(row["value_json"] or "{}").get("holder") == holder:
                connection.execute(
                    "UPDATE service_state SET value_json='{}', updated_at=%s WHERE key='worker_lease'", (utc_now(),),
                )

    def worker_lease(self) -> dict[str, Any]:
        with self.read() as connection:
            row = connection.execute("SELECT value_json FROM service_state WHERE key='worker_lease'").fetchone()
        return json_value(row["value_json"] or "{}") if row else {}

    def summary_queue(self) -> dict[str, int]:
        """Summary jobs of active Residents by step: ready to claim, still being read, claimed, parked."""

        with self.read() as connection:
            rows = connection.execute("""
                SELECT j.status, COUNT(*) AS n FROM summary_jobs j JOIN residents r USING(resident_id)
                WHERE r.active GROUP BY j.status
            """).fetchall()
        by_status = {str(row["status"]): int(row["n"] or 0) for row in rows}
        return {
            "ready": by_status.get("ready", 0),
            "waiting": sum(by_status.get(status, 0) for status in ("queued", "retry", "preparing")),
            "claimed": by_status.get("claimed", 0),
            "parked": by_status.get("failed", 0),
        }

    def counts(self) -> dict[str, int]:
        with self.read() as connection:
            row = connection.execute("""
                SELECT
                    (SELECT COUNT(*) FROM residents WHERE active) AS residents,
                    (SELECT COUNT(*) FROM snapshots WHERE generation>0) AS snapshots,
                    (SELECT COUNT(*) FROM snapshots s JOIN residents r USING(resident_id)
                     WHERE r.active AND s.stale) AS stale_snapshots,
                    (SELECT COUNT(*) FROM summary_jobs WHERE status IN ('queued','retry')) AS queued_jobs,
                    (SELECT COUNT(*) FROM summary_jobs WHERE status='preparing') AS preparing_jobs,
                    (SELECT COUNT(*) FROM summary_jobs WHERE status='ready') AS ready_jobs,
                    (SELECT COUNT(*) FROM summary_jobs WHERE status='claimed') AS claimed_jobs,
                    (SELECT COUNT(*) FROM summary_jobs j JOIN residents r USING(resident_id)
                     WHERE r.active AND j.status='failed') AS failed_jobs,
                    (SELECT COUNT(*) FROM resident_embeddings) AS embeddings,
                    (SELECT COUNT(*) FROM embedding_jobs WHERE status IN ('queued','retry')) AS queued_embedding_jobs,
                    (SELECT COUNT(*) FROM embedding_jobs WHERE status='failed') AS failed_embedding_jobs
            """).fetchone()
        return {key: int(value or 0) for key, value in zip(row.keys(), row)}
