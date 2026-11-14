"""Health capacity reads reuse scheduling fields, with PostgreSQL row fencing.

:module: execution.service
:aspects: functional, performance
"""
from contextlib import contextmanager

import pytest

from management_portal.execution.contracts import ExecutionRequest, SessionOptions
from management_portal.execution.scheduler import Scheduler
from management_portal.execution.scheduler_rows import compact_json
from management_common.json_columns import json_value


@pytest.fixture
def scheduler():
    return Scheduler(capacity_limits={"codex:one": 8, "codex:two": 8},
                     dispatch_interval_min_seconds=0, dispatch_interval_max_seconds=0)


def submit(scheduler, *, operation="turn", provider="codex", service_type="agent_session"):
    request = ExecutionRequest.new(
        actor="alice", prompt="p" * 262144, service_type=service_type,
        provider=provider, operation=operation,
        session=SessionOptions(mode="persistent" if operation == "release" else "stateless",
                               binding_id="release-binding" if operation == "release" else "",
                               backend_route_id="one"),
    )
    scheduler.submit(request)
    return request.execution_id


@contextmanager
def transfer_meter(scheduler):
    """Count full request values actually selected from the isolated PG server."""
    original = scheduler.changes.connect
    measured = {"rows": 0, "bytes": 0, "sql": []}

    class Connection:
        def __init__(self, raw):
            self.raw = raw

        def execute(self, sql, params=()):
            measured["sql"].append(" ".join(sql.split()))
            cursor = self.raw.execute(sql, params)
            if "SELECT status, request_json" not in sql and "request_size, request_json" not in sql:
                return cursor

            class Cursor:
                def fetchall(self):
                    rows = cursor.fetchall()
                    measured["rows"] += len(rows)
                    measured["bytes"] += sum(len(compact_json(json_value(row["request_json"])).encode()) for row in rows)
                    return rows

            return Cursor()

        def __getattr__(self, name):
            return getattr(self.raw, name)

    @contextmanager
    def connect():
        with original() as raw:
            yield Connection(raw)

    scheduler.changes.connect = connect
    try:
        yield measured
    finally:
        scheduler.changes.connect = original


def test_health_unchanged_large_requests_transfer_once(scheduler, record_property):
    ids = [submit(scheduler) for _ in range(8)]
    with scheduler.changes.connect() as connection:
        for index, execution_id in enumerate(ids):
            connection.execute("UPDATE executions SET status=%s WHERE execution_id=%s",
                               (("queued", "claimed", "running")[index % 3], execution_id))
    with transfer_meter(scheduler) as measured:
        outputs = [scheduler.health_report.health() for _ in range(3)]
    counts = [{key: (part["queued_count"], part["active_count"])
               for key, part in output["capacity_partitions"].items()} for output in outputs]
    assert counts[0] == counts[1] == counts[2]
    assert counts[0]["codex:one"] == (3, 5)
    record_property("full_request_rows", measured["rows"])
    record_property("full_request_bytes", measured["bytes"])
    record_property("unchanged_prompt_retransfer_bytes", max(0, measured["rows"] - 8) * 262144)
    assert measured["rows"] == 8, measured
    assert measured["bytes"] < 8 * 263500


def test_health_same_timestamp_same_length_route_and_status_changes(scheduler):
    execution_id = submit(scheduler)
    first = scheduler.health_report.health()
    assert first["capacity_partitions"]["codex:one"]["queued_count"] == 1
    with scheduler.changes.connect() as connection:
        row = connection.execute("SELECT request_json,updated_at FROM executions WHERE execution_id=%s", (execution_id,)).fetchone()
        request = json_value(row["request_json"])
        before = compact_json(request)
        request["session"]["backend_route_id"] = "two"
        after = compact_json(request)
        assert len(before) == len(after)
        connection.execute("UPDATE executions SET request_json=%s,status='running',updated_at=%s WHERE execution_id=%s",
                           (after, row["updated_at"], execution_id))
    current = scheduler.health_report.health()
    assert current["capacity_partitions"]["codex:one"]["queued_count"] == 0
    assert current["capacity_partitions"]["codex:two"]["active_count"] == 1
    with scheduler.changes.connect() as connection:
        connection.execute("UPDATE executions SET cancel_requested='1' WHERE execution_id=%s", (execution_id,))
    # Requested cancellation still occupies capacity until it becomes terminal.
    assert scheduler.health_report.health()["capacity_partitions"]["codex:two"]["active_count"] == 1
    with scheduler.changes.connect() as connection:
        connection.execute("UPDATE executions SET status='cancelled' WHERE execution_id=%s", (execution_id,))
    assert scheduler.health_report.health()["capacity_partitions"]["codex:two"]["active_count"] == 0
    assert scheduler.health_report.requests._scheduling_memo == {}
    with scheduler.changes.connect() as connection:
        connection.execute("DELETE FROM executions WHERE execution_id=%s", (execution_id,))
    assert scheduler.health_report.health()["active_count"] == 0


def test_health_capacity_matches_full_request_projection(scheduler):
    for operation in ("turn", "release"):
        submit(scheduler, operation=operation)
    submit(scheduler, provider="test", operation="generate", service_type="chat_completion")
    with scheduler.changes.connect() as connection:
        full = connection.execute("SELECT status,request_json FROM executions WHERE status IN ('queued','claimed','running')").fetchall()
        expected = {}
        for row in full:
            key = scheduler.capacity.capacity_key(json_value(row["request_json"]))
            expected[key] = expected.get(key, 0) + 1
    actual = scheduler.health_report.health()["capacity_partitions"]
    assert {key: part["queued_count"] for key, part in actual.items() if part["queued_count"]} == expected


@pytest.mark.parametrize("legacy_text", [False, True], ids=["jsonb", "text"])
def test_health_warm_delete_and_column_compatibility(scheduler, legacy_text):
    execution_id = submit(scheduler)
    if legacy_text:
        # Actual PG TEXT, not a dict-returning fake that hides cast/type bugs.
        with scheduler.changes.connect() as connection:
            connection.execute("ALTER TABLE executions ALTER COLUMN request_json TYPE TEXT USING request_json::text")
    with transfer_meter(scheduler) as measured:
        assert scheduler.health_report.health()["capacity_partitions"]["codex:one"]["queued_count"] == 1
        assert scheduler.health_report.health()["capacity_partitions"]["codex:one"]["queued_count"] == 1
        assert measured["rows"] == 1
    with scheduler.changes.connect() as connection:
        connection.execute("DELETE FROM executions WHERE execution_id=%s", (execution_id,))
    assert scheduler.health_report.health()["capacity_partitions"]["codex:one"]["queued_count"] == 0
    assert scheduler.health_report.requests._scheduling_memo == {}
