"""Capacity: concurrency limits, dispatch pacing and the active work counted against them."""
from __future__ import annotations

import random
import threading
from typing import Any

from management_common.database_time import epoch_seconds, instant, iso_time

from .contracts import execution_timestamp
from .scheduler_rows import json_object


SHARED_CAPACITY_KEY = "shared-ai"
SESSION_CONTROL_CAPACITY_PREFIX = "session-control:"
SCHEDULING_MEMO_LIMIT = 4096


def _scheduling_memo_key(row: Any) -> tuple[str, str, int]:
    return (str(row["row_version"]), (iso_time(row["updated_at"]) or ""), int(row["request_size"] or 0))


def _scheduling_request(request: dict[str, Any]) -> dict[str, Any]:
    """The request fields capacity, quota and binding checks read.

    request_json also carries the whole prompt and context, often hundreds of
    KB; scheduling needs only the route and binding.
    """

    session = request.get("session") if isinstance(request.get("session"), dict) else {}
    slim: dict[str, Any] = {
        key: request[key]
        for key in ("service_type", "operation", "provider")
        if key in request
    }
    if isinstance(request.get("runtime_options"), dict):
        slim["runtime_options"] = dict(request["runtime_options"])
    slim["session"] = {
        key: session[key]
        for key in ("backend_route_id", "binding_id")
        if key in session
    }
    return slim


def capacity_settings(connection) -> dict[str, dict[str, Any]]:
    return {str(row["capacity_key"]): dict(row) for row in connection.execute(
        "SELECT * FROM capacity_settings"
    ).fetchall()}


def capacity_dispatch_state(
    connection: Any,
    capacity_key: str,
) -> dict[str, float]:
    row = connection.execute(
        '\n            SELECT last_dispatched_at, next_dispatch_at, interval_seconds\n            FROM capacity_dispatch_state\n            WHERE capacity_key = %s\n            ',
        (capacity_key,),
    ).fetchone()
    if row is None:
        return {
            "last_dispatched_at": 0.0,
            "next_dispatch_at": 0.0,
            "interval_seconds": 0.0,
        }
    return {
        "last_dispatched_at": (epoch_seconds(row["last_dispatched_at"]) or 0.0),
        "next_dispatch_at": (epoch_seconds(row["next_dispatch_at"]) or 0.0),
        "interval_seconds": float(row["interval_seconds"]),
    }


def record_dispatch_row(
    connection: Any, table: str, keys: dict[str, Any], values: dict[str, Any],
) -> None:
    """Record a dispatch for pacing or fairness, unless a concurrent claim is.

    Waiting for that claim's commit would line claims up again; either
    record serves, as pacing and fairness may be briefly exceeded.
    """
    where = " AND ".join(f"{name} = %s" for name in keys)
    key_values = tuple(keys.values())
    if connection.execute(
        f"SELECT 1 FROM {table} WHERE {where} FOR UPDATE SKIP LOCKED", key_values,
    ).fetchone() is not None:
        assignments = ", ".join(f"{name} = %s" for name in values)
        connection.execute(
            f"UPDATE {table} SET {assignments} WHERE {where}",
            (*values.values(), *key_values),
        )
    elif connection.execute(
        f"SELECT 1 FROM {table} WHERE {where}", key_values,
    ).fetchone() is None:
        columns = [*keys, *values]
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) "
            f"VALUES ({', '.join(['%s'] * len(columns))}) ON CONFLICT DO NOTHING",
            (*key_values, *values.values()),
        )


class ActiveRequests:
    """The scheduling fields of claimed and running executions, kept per row version."""

    def __init__(self) -> None:
        self._scheduling_memo_lock = threading.Lock()
        self._scheduling_memo: dict[str, tuple[tuple[str, str, int], dict[str, Any]]] = {}

    def scheduling_requests(
        self,
        connection,
        *,
        exclude: str = "",
        uncancelled: bool = False,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Scheduling fields of every claimed or running execution.

        Claims and queue positions read these on every poll.  Parsing each
        active request_json there cost about a CPU core in both ExecutionService
        and PostgreSQL even while idle.  The fields are kept per execution and
        re-read only when (row version, updated_at, request size) changes: every
        UPDATE gives the row a new xmin, even within the millisecond updated_at
        resolves, and pg_column_size reads the stored size without
        decompressing the value.
        """

        sql = (
            "SELECT execution_id, xmin::text AS row_version, updated_at, "
            "pg_column_size(request_json) AS request_size "
            "FROM executions WHERE status IN ('claimed', 'running')"
        )
        params: list[Any] = []
        if uncancelled:
            sql += " AND cancel_requested = '0'"
        if exclude:
            sql += " AND execution_id != %s"
            params.append(exclude)
        keys = {
            str(row["execution_id"]): _scheduling_memo_key(row)
            for row in connection.execute(sql, tuple(params)).fetchall()
        }
        with self._scheduling_memo_lock:
            known = {execution_id: self._scheduling_memo.get(execution_id) for execution_id in keys}
        requests = {
            execution_id: entry[1]
            for execution_id, entry in known.items()
            if entry is not None and entry[0] == keys[execution_id]
        }
        missing = [execution_id for execution_id in keys if execution_id not in requests]
        fresh: dict[str, tuple[tuple[str, int], dict[str, Any]]] = {}
        if missing:
            for row in connection.execute(
                "SELECT execution_id, xmin::text AS row_version, updated_at, "
                "pg_column_size(request_json) AS request_size, request_json "
                "FROM executions WHERE execution_id = ANY(%s)",
                (missing,),
            ).fetchall():
                execution_id = str(row["execution_id"])
                request = _scheduling_request(json_object(row["request_json"]))
                fresh[execution_id] = (_scheduling_memo_key(row), request)
                requests[execution_id] = request
        with self._scheduling_memo_lock:
            self._scheduling_memo.update(fresh)
            if not exclude and not uncancelled:
                for execution_id in [key for key in self._scheduling_memo if key not in keys]:
                    del self._scheduling_memo[execution_id]
            elif len(self._scheduling_memo) > SCHEDULING_MEMO_LIMIT:
                self._scheduling_memo.clear()
        return [(execution_id, requests[execution_id]) for execution_id in keys if execution_id in requests]


class SchedulerCapacity:
    """Concurrency limits and dispatch pacing per capacity key."""

    def __init__(
        self,
        accounts: Any,
        *,
        max_concurrency: int,
        capacity_limits: dict[str, int] | None,
        dispatch_interval_min_seconds: float,
        dispatch_interval_max_seconds: float,
    ) -> None:
        self.accounts = accounts
        self.max_concurrency = max(1, int(max_concurrency))
        self._capacity_limit_overrides = {
            str(key or "").strip(): max(1, int(limit))
            for key, limit in (capacity_limits or {}).items()
            if str(key or "").strip()
        }
        self.dispatch_interval_min_seconds = max(
            0.0,
            float(dispatch_interval_min_seconds),
        )
        self.dispatch_interval_max_seconds = max(
            self.dispatch_interval_min_seconds,
            float(dispatch_interval_max_seconds),
        )

    @property
    def default_capacity_limits(self):
        return {**{account.capacity_key: account.max_concurrency for account in self.accounts.accounts()},
                **self._capacity_limit_overrides}

    def capacity_key(self, request: dict[str, Any]) -> str:
        if request.get("service_type") == "agent_session" and request.get("operation") == "release":
            return f"{SESSION_CONTROL_CAPACITY_PREFIX}{request.get('provider', '')}"
        account = self.accounts.for_request(request)
        return account.capacity_key if account else SHARED_CAPACITY_KEY

    def capacity_limit(self, connection, capacity_key: str) -> int:
        settings = capacity_settings(connection).get(capacity_key)
        return int(settings["limit_value"]) if settings else self.default_capacity_limits.get(capacity_key, self.max_concurrency)

    def dispatch_interval_range(self, connection, capacity_key: str) -> tuple[float, float]:
        if capacity_key.startswith(SESSION_CONTROL_CAPACITY_PREFIX):
            return (0.0, 0.0)
        settings = capacity_settings(connection).get(capacity_key)
        if settings and settings["dispatch_interval_min_seconds"] is not None and settings["dispatch_interval_max_seconds"] is not None:
            return (float(settings["dispatch_interval_min_seconds"]), float(settings["dispatch_interval_max_seconds"]))
        return (self.dispatch_interval_min_seconds, self.dispatch_interval_max_seconds)

    def next_dispatch_interval_seconds(self, connection, capacity_key: str) -> float:
        lower, upper = self.dispatch_interval_range(connection, capacity_key)
        if upper <= 0:
            return 0.0
        if upper == lower:
            return lower
        return random.uniform(lower, upper)

    def reserve_capacity_dispatch(
        self,
        connection: Any,
        capacity_key: str,
        *,
        now_epoch: float,
    ) -> dict[str, float]:
        interval_seconds = self.next_dispatch_interval_seconds(connection, capacity_key)
        next_dispatch_at = now_epoch + interval_seconds
        record_dispatch_row(
            connection, "capacity_dispatch_state", {"capacity_key": capacity_key}, {
                "last_dispatched_at": instant(now_epoch),
                "next_dispatch_at": instant(next_dispatch_at),
                "interval_seconds": interval_seconds,
                "updated_at": execution_timestamp(),
            },
        )
        return {
            "last_dispatched_at": now_epoch,
            "next_dispatch_at": next_dispatch_at,
            "interval_seconds": interval_seconds,
        }
