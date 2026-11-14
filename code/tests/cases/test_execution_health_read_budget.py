"""Health reads scale in returned rows, not in database round trips per account."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from management_portal.execution.accounts import ExecutionAccount
from management_portal.execution.scheduler_capacity import SchedulerCapacity
from management_portal.execution.scheduler_health import SchedulerHealth
from management_portal.execution.scheduler_quota import SchedulerQuota


class CapacityRows:
    """This fixture isolates configuration reads; request I/O has PG tests."""
    def __init__(self, rows):
        self.rows = rows

    def health_requests(self, connection):
        return [(row["status"], row["request_json"]) for row in self.rows.active]


class HealthRows:
    def __init__(self, count):
        self.calls = []
        self.settings = {"account:0": {
            "capacity_key": "account:0", "limit_value": 17,
            "dispatch_interval_min_seconds": 3, "dispatch_interval_max_seconds": 5,
        }}
        self.metadata = {
            "quota:account:0": {"remaining_percent": 0, "observed_at": 1000, "status": "reserved"},
            "quota_threshold:account:0": {"unit": "%", "value": 7},
        }
        self.active = [{"status": "queued", "request_json": {"account": f"account:{n}"}}
                       for n in range(count)] + [
            {"status": "running", "request_json": {"account": "account:0"}},
            {"status": "queued", "request_json": {"service_type": "agent_session", "operation": "release", "provider": "codex"}},
            {"status": "queued", "request_json": {}},
        ]

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if "FROM capacity_settings" in sql:
            rows = list(self.settings.values())
        elif "FROM execution_store_meta" in sql:
            keys = params[0] if "ANY" in sql else [params[0]]
            rows = [{"key": key, "value": self.metadata[key]} for key in keys if key in self.metadata]
        elif "FROM execution_workers" in sql or "FROM capacity_dispatch_state" in sql:
            rows = []
        elif "GROUP BY status" in sql:
            rows = [{"status": "queued", "count": len(self.active) - 1}, {"status": "running", "count": 1}]
        elif "acknowledged_at IS NULL" in sql:
            rows = [{"count": 2, "oldest_updated_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}]
        elif "SELECT status, request_json" in sql:
            rows = self.active
        else:
            raise AssertionError(sql)
        return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0] if rows else None)


def health_fixture(count=20, *, enabled=True):
    rows = HealthRows(count)
    entries = tuple(ExecutionAccount(str(n), "codex", str(n), f"account:{n}",
                                     max_concurrency=n + 1, quota_policy="reserve") for n in range(count))
    accounts = SimpleNamespace(
        accounts=lambda: entries,
        for_request=lambda request: next((a for a in entries if a.capacity_key == request.get("account")), None),
    )
    capacity = SchedulerCapacity(accounts, max_concurrency=8, capacity_limits={"custom": 11},
                                 dispatch_interval_min_seconds=2, dispatch_interval_max_seconds=6)
    quota = SchedulerQuota(accounts, changes=rows, capacity=capacity,
                           active_requests=None, quota_reserve_enabled=enabled)
    maintenance = dict.fromkeys(("expired_worker_registrations", "recovered_expired_leases",
                                "rebased_dispatch_windows", "rebased_actor_dispatches"), 0)
    health = SchedulerHealth(SimpleNamespace(display_name="isolated"), changes=rows,
                             capacity=capacity, quota=quota,
                             workers=SimpleNamespace(maintenance_result=lambda: maintenance))
    health.requests = CapacityRows(rows)
    return health, rows


@pytest.mark.parametrize("count", [1, 20, 100])
@pytest.mark.parametrize("enabled", [False, True])
def test_health_batches_configuration_reads_and_preserves_quota_visibility(count, enabled):
    health, rows = health_fixture(count, enabled=enabled)
    result = health.health()
    assert sum("FROM capacity_settings" in sql for sql, _ in rows.calls) == 1
    assert sum("FROM execution_store_meta" in sql for sql, _ in rows.calls) == 1
    assert len(rows.calls) == 6
    partitions = result["capacity_partitions"]
    first = partitions["account:0"]
    assert (first["limit"], first["dispatch_interval_min_seconds"], first["dispatch_interval_max_seconds"]) == (17, 3, 5)
    assert first["close_threshold"]["value"] == 7
    assert bool(first["quota_reserve"]) == enabled
    if enabled:
        assert first["quota_reserve"]["automatic_paused"] is True
    assert (first["queued_count"], first["active_count"]) == (1, 1)
    assert partitions["custom"]["limit"] == 11
    assert partitions["session-control:codex"]["dispatch_interval_max_seconds"] == 0
    assert partitions["shared-ai"]["limit"] == 8
    assert result["unacknowledged_terminal_count"] == 2
    assert result["active_count"] == 1
    for index in range(1, count):
        assert partitions[f"account:{index}"]["limit"] == index + 1
        assert partitions[f"account:{index}"]["dispatch_interval_max_seconds"] == 6


def test_health_next_read_observes_changed_configuration():
    health, rows = health_fixture()
    previous = health.health()
    rows.settings["account:0"]["limit_value"] = 23
    rows.settings["account:0"]["dispatch_interval_max_seconds"] = 9
    rows.metadata["quota_threshold:account:0"]["value"] = 0
    current = health.health()["capacity_partitions"]["account:0"]
    assert current["limit"] == 23
    assert current["dispatch_interval_max_seconds"] == 9
    assert current["close_threshold"]["value"] == 0
    assert previous["capacity_partitions"]["account:0"]["limit"] == 17


def test_health_without_quota_accounts_does_not_read_metadata():
    health, rows = health_fixture(0)
    result = health.health()
    assert not any("FROM execution_store_meta" in sql for sql, _ in rows.calls)
    assert result["capacity_partitions"]["shared-ai"]["quota_reserve"] == {}


def test_single_partition_capacity_reads_keep_catalog_lazy():
    health, rows = health_fixture()
    health.capacity.accounts.accounts = lambda: (_ for _ in ()).throw(AssertionError("catalog read"))
    assert health.capacity.capacity_limit(rows, "account:0") == 17
    assert health.capacity.dispatch_interval_range(rows, "account:0") == (3, 5)
    before = len(rows.calls)
    assert health.capacity.dispatch_interval_range(rows, "session-control:codex") == (0, 0)
    assert len(rows.calls) == before


@pytest.mark.usefixtures("registered_execution_defaults")
def test_database_health_configuration_changes_remain_visible():
    from management_portal.execution.scheduler import Scheduler

    scheduler = Scheduler(quota_reserve_enabled=True)
    scheduler.configure_capacity("codex:zero", 13, 1, 3, actor="health-test")
    before = scheduler.health()["capacity_partitions"]["codex:zero"]
    scheduler.configure_capacity("codex:zero", 19, 2, 4, actor="health-test")
    after = scheduler.health()["capacity_partitions"]["codex:zero"]
    assert (before["limit"], before["dispatch_interval_max_seconds"]) == (13, 3)
    assert (after["limit"], after["dispatch_interval_max_seconds"]) == (19, 4)
    assert before["close_threshold"] == after["close_threshold"]
