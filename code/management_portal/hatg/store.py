"""Execution records kept by each owning service.

A record exists for every executed state-changing instruction.  Records with
a Lineage target are grouped under that Lineage; the others under the caller.
Finished records remain available for at least seven days. Active recovery
dependencies are retained until acknowledged. A record keeps what recovery needs: request key, caller,
entry kind, content fingerprint, status, business object references and the
failure — never full responses, Lineage snapshots or message bodies.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Any, Mapping
from management_common.database_time import iso_time
from management_common.json_columns import json_text, json_value

from .contracts import InstructionCall, InstructionError


RETENTION_DAYS = 7
PRUNE_BATCH_SIZE = 128
RETRY_GUARDS = "instruction_retry_guards"
# An executing record can be settled only after its live execution lock has
# been released (including automatically when the owning process dies).
TABLE = "instruction_executions"
# Scalar fields kept as object references.
_REFERENCE_SCALARS = frozenset({
    "status", "state", "phase", "created", "changed", "deleted", "matched", "replayed",
    "ready", "visible", "allowed", "cleanup_pending",
    # A pending projection names what its replay must rebuild.
    "projection",
    "reason",
    # A retried upload still needs its data channel to finish writing bytes.
    "upload_url", "expires_at",
})


def create_schema(connection: Any) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            instruction_id TEXT PRIMARY KEY,
            instruction TEXT NOT NULL,
            scope_kind TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            caller TEXT NOT NULL,
            entry TEXT NOT NULL,
            request_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            references_json JSONB NOT NULL,
            error_json JSONB NOT NULL,
            recovery_key TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_request_idx "
        f"ON {TABLE}(caller, instruction, request_key) WHERE request_key <> ''"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS {TABLE}_scope_idx "
        f"ON {TABLE}(scope_kind, scope_id, updated_at)"
    )
    connection.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_expiry_idx ON {TABLE}(updated_at,instruction_id) "
                       "WHERE recovery_key='' AND status IN ('committed','failed')")
    # Only a one-way request digest survives expiry. It is a replay fence,
    # not an instruction history: no identity, arguments or result are kept.
    connection.execute(f"CREATE TABLE IF NOT EXISTS {RETRY_GUARDS} ("
                       "request_digest TEXT PRIMARY KEY, expired_at TIMESTAMPTZ NOT NULL)")


def _request_identity(caller: str, instruction: str, key: str) -> str:
    return "\x1f".join((caller, instruction, key))


def _request_digest(caller: str, instruction: str, key: str) -> str:
    return hashlib.sha256(_request_identity(caller, instruction, key).encode()).hexdigest()


def references(value: Any, depth: int = 0) -> Any:
    """Reduce a result to business object references (IDs, versions, flags)."""

    if depth > 4:
        return None
    if isinstance(value, Mapping):
        kept: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if isinstance(item, (str, int, float)) and not isinstance(item, bool):
                if (
                    name == "id"
                    or name.endswith("_id")
                    or name.endswith("_version")
                    or name in {"version", "revision", "generation", "cursor"}
                    or name in _REFERENCE_SCALARS
                ) and (not isinstance(item, str) or len(item) <= 240):
                    kept[name] = item
            elif isinstance(item, bool):
                kept[name] = item
            elif isinstance(item, list) and (name.endswith("_ids") or name == "ids"):
                kept[name] = [entry for entry in item if isinstance(entry, str)][:256]
            elif isinstance(item, Mapping):
                nested = references(item, depth + 1)
                if nested:
                    kept[name] = nested
        return kept
    return None


def fingerprint(call: InstructionCall) -> str:
    from .catalog import declaration

    parameters = declaration(call.instruction).parameter_map
    arguments = {
        name: value for name, value in call.arguments.items()
        if value is not None or name not in parameters or parameters[name].required or parameters[name].type == "any"
    }
    body = {
        "instruction": call.instruction,
        "arguments": arguments,
        "expected_version": call.expected_version,
    }
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _record(row: Any) -> dict[str, Any]:
    return {
        "instruction_id": row["instruction_id"],
        "instruction": row["instruction"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
        "caller": row["caller"],
        "entry": row["entry"],
        "status": row["status"],
        "created_at": iso_time(row["created_at"]),
        "updated_at": iso_time(row["updated_at"]),
        "references": json_value(row["references_json"]),
        "error": json_value(row["error_json"]),
    }


class ExecutionStore:
    """PostgreSQL execution records for one owning service."""

    def __init__(self, database: Any, *, create: bool = True) -> None:
        self.database = database
        self._prune_lock = threading.Lock()
        self._next_prune = 0.0
        self._interrupted_cursor = ""
        if create:
            self.database.write(create_schema)

    @contextmanager
    def active_execution(self, instruction_id: str):
        """Protect a live call/recovery across processes, without a transaction.

        Only this instruction's advisory lock spans external work. Domain rows
        and the execution receipt continue to use independent short commits.
        """
        with self.database.advisory_lock(self._activity_key(instruction_id), blocking=False) as acquired:
            yield acquired

    def _activity_key(self, instruction_id: str) -> int:
        identity = f"{self.database.schema}:hatg-active:{instruction_id}"
        return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big", signed=True)

    def _settle_interrupted(self, connection: Any, instruction_id: str, now: str) -> bool:
        if not connection.execute(
            "SELECT pg_try_advisory_xact_lock(%s)", (self._activity_key(instruction_id),),
        ).fetchone()[0]:
            return False
        interrupted = {
            "code": "instruction.failed",
            "message": "执行已中断，业务结果未获确认；请先读取目标对象并核对结果。原幂等键不会重新执行业务操作",
            "retryable": False, "commit_state": "unknown",
            "details": {"reason": "interrupted", "instruction_id": instruction_id},
        }
        return bool(connection.execute(
            f"UPDATE {TABLE} SET status='failed', updated_at=%s, error_json=%s "
            "WHERE instruction_id=%s AND status='executing'",
            (now, json_text(interrupted, ensure_ascii=False), instruction_id),
        ).rowcount)

    def settle_execution(self, instruction_id: str, *, now: str) -> bool:
        """Settle one observed receipt only when its owner is no longer active."""
        def settle(connection: Any) -> bool:
            row = connection.execute(
                f"SELECT instruction_id FROM {TABLE} WHERE instruction_id=%s "
                "AND status='executing' FOR UPDATE SKIP LOCKED", (instruction_id,),
            ).fetchone()
            return bool(row) and self._settle_interrupted(connection, instruction_id, now)

        return self.database.write(settle)

    def settle_interrupted(self, *, now: str) -> int:
        def settle(connection: Any) -> int:
            rows = connection.execute(
                f"SELECT instruction_id FROM {TABLE} WHERE status='executing' "
                "AND instruction_id > %s "
                "ORDER BY instruction_id LIMIT %s FOR UPDATE SKIP LOCKED",
                (self._interrupted_cursor, PRUNE_BATCH_SIZE),
            ).fetchall()
            self._interrupted_cursor = rows[-1][0] if len(rows) == PRUNE_BATCH_SIZE else ""
            return sum(self._settle_interrupted(connection, row[0], now) for row in rows)

        return self.database.write(settle)

    def pending_projections(self, *, after: str = "") -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                f"SELECT * FROM {TABLE} WHERE instruction_id > %s "
                "AND error_json::jsonb ->> 'code' = 'instruction.projection_pending' "
                "ORDER BY instruction_id LIMIT %s", (after, PRUNE_BATCH_SIZE),
            ).fetchall()
        return [_record(row) for row in rows]

    def begin(
        self,
        *,
        instruction_id: str,
        call: InstructionCall,
        caller: str,
        entry: str,
        scope: tuple[str, str],
        now: str,
        recovery_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        """Register an execution; return (record, replayed)."""

        request_key = call.idempotency_key
        print_ = fingerprint(call)
        # Maintenance never shares a transaction with a new request's lock.
        # Bound its frequency and batch size rather than deleting recent work
        # merely because this scope is busy.
        if time.monotonic() >= self._next_prune and self._prune_lock.acquire(blocking=False):
            try:
                self._next_prune = time.monotonic() + 60
                self.prune(now=now)
            except Exception:
                logging.getLogger(__name__).exception("Instruction recovery-window maintenance failed")
            finally:
                self._prune_lock.release()

        def reserve(connection: Any) -> tuple[dict[str, Any], bool]:
            if request_key:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || "
                    "':hatg-instruction:' || %s, 0))",
                    (_request_identity(caller, call.instruction, request_key),),
                )
                existing = connection.execute(
                    f"SELECT * FROM {TABLE} WHERE caller=%s AND instruction=%s AND request_key=%s FOR UPDATE",
                    (caller, call.instruction, request_key),
                ).fetchone()
                if existing is not None:
                    if existing["fingerprint"] != print_:
                        raise InstructionError(
                            "instruction.idempotency_conflict",
                            "同一幂等键已经用于内容不同的调用",
                            details={"instruction_id": existing["instruction_id"]},
                        )
                    if recovery_key:
                        previous = str(existing["recovery_key"] or "")
                        if previous and previous != recovery_key:
                            raise InstructionError("instruction.conflict", "同一请求仍被另一次 Effect 激活依赖")
                        if previous != recovery_key:
                            connection.execute(f"UPDATE {TABLE} SET recovery_key=%s WHERE instruction_id=%s",
                                               (recovery_key, existing["instruction_id"]))
                    if existing["status"] == "executing":
                        self._settle_interrupted(connection, existing["instruction_id"], now)
                        existing = connection.execute(
                            f"SELECT * FROM {TABLE} WHERE instruction_id=%s", (existing["instruction_id"],),
                        ).fetchone()
                if existing is not None:
                    if existing["status"] == "executing":
                        raise InstructionError(
                            "instruction.in_progress",
                            "使用同一幂等键的请求仍在执行",
                            details={"instruction_id": existing["instruction_id"]},
                            retryable=True,
                            commit_state="unknown",
                        )
                    return _record(existing), True
                if connection.execute(
                    f"SELECT 1 FROM {RETRY_GUARDS} WHERE request_digest=%s",
                    (_request_digest(caller, call.instruction, request_key),),
                ).fetchone():
                    raise InstructionError(
                        "instruction.conflict",
                        "这次请求的执行记录已超出恢复窗口；请先核对业务结果，确认后使用新的幂等键提交",
                        details={"reason": "idempotency_window_expired", "retention_days": RETENTION_DAYS},
                        commit_state="unknown",
                    )
            connection.execute(
                f"INSERT INTO {TABLE}(instruction_id, instruction, scope_kind, scope_id, caller, entry, "
                "request_key, fingerprint, status, created_at, updated_at, references_json, error_json, recovery_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'executing',%s,%s,'null','null',%s)",
                (instruction_id, call.instruction, scope[0], scope[1], caller, entry,
                 request_key, print_, now, now, recovery_key),
            )
            return {"instruction_id": instruction_id, "status": "executing"}, False

        return self.database.write(reserve)

    def prune(self, *, now: str) -> int:
        """Bounded maintenance, independent of request reservation transactions.

        Taking the same request lock as begin prevents expiry racing a retry.
        Never count a scope's entire history on the instruction hot path.
        """
        cutoff = (datetime.fromisoformat(now.replace("Z", "+00:00")) - timedelta(days=RETENTION_DAYS))
        cutoff_text = cutoff.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        eligible = ("updated_at < %s AND updated_at::timestamptz < %s::timestamptz "
                    "AND status IN ('committed','failed') AND recovery_key='' "
                    "AND coalesce(error_json::jsonb ->> 'code','') <> 'instruction.projection_pending'")

        def remove(connection: Any) -> int:
            rows = connection.execute(
                f"SELECT instruction_id,caller,instruction,request_key FROM {TABLE} WHERE {eligible} "
                "ORDER BY updated_at,instruction_id LIMIT %s", (cutoff_text, cutoff_text, PRUNE_BATCH_SIZE),
            ).fetchall()
            count = 0
            for row in rows:
                if row["request_key"] and not connection.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended(current_schema() || "
                    "':hatg-instruction:' || %s, 0))",
                    (_request_identity(row["caller"], row["instruction"], row["request_key"]),),
                ).fetchone()[0]:
                    continue
                removed = connection.execute(
                    f"DELETE FROM {TABLE} WHERE instruction_id=%s AND {eligible} RETURNING instruction_id",
                    (row["instruction_id"], cutoff_text, cutoff_text),
                ).fetchone()
                if removed is None:
                    continue
                if row["request_key"]:
                    connection.execute(f"INSERT INTO {RETRY_GUARDS}(request_digest,expired_at) VALUES (%s,%s) "
                                       "ON CONFLICT(request_digest) DO NOTHING",
                                       (_request_digest(row["caller"], row["instruction"], row["request_key"]), now))
                count += 1
            return count
        return self.database.write(remove)

    def release_recovery(self, *, caller: str, recovery_key: str, instruction_ids: list[str]) -> int:
        if not recovery_key or not instruction_ids:
            return 0
        return self.database.write(lambda connection: connection.execute(
            f"UPDATE {TABLE} SET recovery_key='' WHERE caller=%s AND recovery_key=%s AND instruction_id=ANY(%s)",
            (caller, recovery_key, instruction_ids),
        ).rowcount)

    def finish(
        self,
        instruction_id: str,
        *,
        status: str,
        now: str,
        result: Any = None,
        error: Mapping[str, Any] | None = None,
    ) -> None:
        refs = references(result)
        stored_error = None
        if error:
            stored_error = {
                key: error[key]
                for key in ("code", "message", "retryable", "commit_state")
                if key in error
            }
            stored_error["message"] = str(stored_error.get("message") or "")[:1000]
            details = error.get("details") if isinstance(error.get("details"), Mapping) else {}
            stored_error["details"] = references(details) or {}
            if isinstance(details.get("projection_failures"), list):
                stored_error["details"]["projection_failures"] = [
                    references(item) for item in details["projection_failures"]
                ]
        self.database.write(lambda connection: connection.execute(
            f"UPDATE {TABLE} SET status=%s, updated_at=%s, references_json=%s, error_json=%s "
            "WHERE instruction_id=%s",
            (
                status, now,
                json_text(refs, ensure_ascii=False, separators=(",", ":")),
                json_text(stored_error, ensure_ascii=False, separators=(",", ":")),
                instruction_id,
            ),
        ))

    def find_instruction_id(self, *, caller: str, instruction: str, request_key: str) -> str:
        """Read only the original caller/instruction/key index; never reserve or replay."""
        if not request_key:
            return ""
        with self.database.read() as connection:
            row = connection.execute(
                f"SELECT instruction_id FROM {TABLE} WHERE caller=%s AND instruction=%s AND request_key=%s",
                (caller, instruction, request_key),
            ).fetchone()
        return str(row["instruction_id"]) if row is not None else ""

    def get(self, instruction_id: str) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                f"SELECT * FROM {TABLE} WHERE instruction_id=%s", (instruction_id,),
            ).fetchone()
        return _record(row) if row is not None else None
