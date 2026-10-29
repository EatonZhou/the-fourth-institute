"""The scheduler health report."""
from __future__ import annotations

import time
from typing import Any

from management_common.database_time import epoch_seconds, iso_time
from management_common.json_columns import json_value

from .scheduler_capacity import SHARED_CAPACITY_KEY, SchedulerCapacity, capacity_settings
from .scheduler_changes import SchedulerChanges
from .scheduler_quota import SchedulerQuota
from .scheduler_rows import ACTIVE_STATUSES, json_object
from .scheduler_workers import WorkerRegistry


SCHEDULER_SCHEMA = "tfi.execution-scheduler.v10"


class SchedulerHealth:
    """Counts, capacity and quota state of the scheduler at a glance."""

    def __init__(self, database: Any, *, changes: SchedulerChanges, capacity: SchedulerCapacity,
                 quota: SchedulerQuota, workers: WorkerRegistry) -> None:
        self.database = database
        self.changes = changes
        self.capacity = capacity
        self.quota = quota
        self.workers = workers

    def health(self) -> dict[str, Any]:
        maintenance = self.workers.maintenance_result()
        with self.changes.connect() as connection:
            counts = {
                str(row["status"]): int(row["count"])
                for row in connection.execute(
                    """
                    SELECT status, COUNT(*) AS count
                    FROM executions
                    GROUP BY status
                    """
                ).fetchall()
            }
            unacknowledged_terminal = connection.execute(
                """
                SELECT COUNT(*) AS count, MIN(updated_at) AS oldest_updated_at
                FROM executions
                WHERE status IN ('completed', 'failed', 'cancelled')
                  AND acknowledged_at IS NULL
                """
            ).fetchone()
            workers = [
                {
                    "worker_id": str(row["worker_id"]),
                    "build_id": str(row["build_id"]),
                    "state": str(row["state"]),
                    "capabilities": json_value(row["capabilities_json"]),
                    "pid": int(row["pid"]),
                    "heartbeat_at": (epoch_seconds(row["heartbeat_at"]) or 0.0),
                }
                for row in connection.execute(
                    """
                    SELECT worker_id, build_id, state,
                           capabilities_json, pid, heartbeat_at
                    FROM execution_workers
                    ORDER BY worker_id
                    """
                ).fetchall()
            ]
            settings = capacity_settings(connection)
            capacity_counts = {
                key: {"active_count": 0, "queued_count": 0}
                for key in {
                    SHARED_CAPACITY_KEY,
                    *self.capacity.default_capacity_limits,
                    *settings,
                }
            }
            for row in connection.execute(
                """
                SELECT status, request_json
                FROM executions
                WHERE status IN ('queued', 'claimed', 'running')
                """
            ).fetchall():
                capacity_key = self.capacity.capacity_key(
                    json_object(row["request_json"])
                )
                partition = capacity_counts.setdefault(
                    capacity_key,
                    {"active_count": 0, "queued_count": 0},
                )
                if str(row["status"]) in ACTIVE_STATUSES:
                    partition["active_count"] += 1
                else:
                    partition["queued_count"] += 1
            dispatch_partitions = {
                str(row["capacity_key"]): {
                    "last_dispatched_at": (epoch_seconds(row["last_dispatched_at"]) or 0.0),
                    "next_dispatch_at": (epoch_seconds(row["next_dispatch_at"]) or 0.0),
                    "interval_seconds": float(row["interval_seconds"]),
                    "remaining_seconds": max(
                        0.0,
                        (epoch_seconds(row["next_dispatch_at"]) or 0.0) - time.time(),
                    ),
                }
                for row in connection.execute(
                    """
                    SELECT capacity_key, last_dispatched_at,
                           next_dispatch_at, interval_seconds
                    FROM capacity_dispatch_state
                    ORDER BY capacity_key
                    """
                ).fetchall()
            }
            account_quotas = {
                key: self.quota.account_quota(connection, key)
                for key in capacity_counts if key in self.quota.quota_capacity_keys
            } if self.quota.quota_reserve_enabled else {}
            thresholds = {key: account_quotas[key]["close_threshold"] if key in account_quotas else self.quota.close_threshold(connection, key)
                          for key in capacity_counts if key in self.quota.quota_capacity_keys}
            limits = {key: self.capacity.capacity_limit(connection, key) for key in capacity_counts}
            intervals = {key: self.capacity.dispatch_interval_range(connection, key) for key in capacity_counts}
        capacity_partitions = {
            key: {
                "quota_reserve": account_quotas.get(key, {}),
                "close_threshold": thresholds.get(key),
                "limit": limits[key],
                "dispatch_interval_min_seconds": (
                    intervals[key][0]
                ),
                "dispatch_interval_max_seconds": (
                    intervals[key][1]
                ),
                **values,
            }
            for key, values in sorted(capacity_counts.items())
        }
        return {
            "ok": True,
            "schema": SCHEDULER_SCHEMA,
            "database": self.database.display_name,
            "default_partition_limit": self.capacity.max_concurrency,
            "capacity_partitions": capacity_partitions,
            "dispatch_interval": {
                "min_seconds": self.capacity.dispatch_interval_min_seconds,
                "max_seconds": self.capacity.dispatch_interval_max_seconds,
                "partitions": dispatch_partitions,
            },
            "total_capacity": sum(
                partition["limit"]
                for partition in capacity_partitions.values()
            ),
            "active_count": sum(counts.get(status, 0) for status in ACTIVE_STATUSES),
            "status_counts": counts,
            "unacknowledged_terminal_count": int(
                unacknowledged_terminal["count"] if unacknowledged_terminal else 0
            ),
            "oldest_unacknowledged_terminal_at": iso_time(
                unacknowledged_terminal["oldest_updated_at"] if unacknowledged_terminal else None
            ) or "",
            "workers": workers,
            "expired_worker_registrations": maintenance[
                "expired_worker_registrations"
            ],
            "recovered_expired_leases": maintenance[
                "recovered_expired_leases"
            ],
        }
