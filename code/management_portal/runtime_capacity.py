"""Read local capacity without running work or changing scheduling policy.

Database usage counts all client backends on the shared PostgreSQL instance,
including this observation, against the slots available without reserved-slot
privileges. Disk usage measures unavailable bytes (total minus available free).
Execution counts are for the connected ExecutionService: queued includes quota,
dispatch-interval and binding waits; it is not a count of runnable requests.
"""
from __future__ import annotations
from management_common.portal_paths import path as portal_path

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any

from management_common.portal_database import portal_database
from management_portal.execution import get_execution_service


SCHEMA = "tfi.runtime-capacity.v1"
DISK_PATHS = ("/", str(portal_path("share_root")), "/data")


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("invalid capacity count")
    return value


def _database_capacity() -> dict[str, Any]:
    # Reuse the existing Web owner schema and release the database handle as
    # well as its checkout, including when acquisition or the query fails.
    with closing(portal_database(schema="web_instructions", max_size=1)) as database:
        with database.read() as connection:
            row = connection.execute("""
                SELECT COUNT(*)::bigint AS connections,
                       current_setting('max_connections')::integer AS max_connections,
                       current_setting('superuser_reserved_connections')::integer
                         + current_setting('reserved_connections')::integer
                         AS reserved_connections
                FROM pg_catalog.pg_stat_activity
                WHERE backend_type = 'client backend'
            """).fetchone()
    connections = _count(row["connections"])
    maximum = _count(row["max_connections"])
    reserved = _count(row["reserved_connections"])
    usable = maximum - reserved
    if usable <= 0:
        raise ValueError("no ordinary database connection slots")
    return {
        "status": "ok", "connections": connections,
        "max_connections": maximum, "reserved_connections": reserved,
        "usable_connections": usable,
        # Do not clamp: reserved-slot clients can take usage over 100%.
        "used_percent": round(connections * 100.0 / usable, 2),
    }


def _disk_capacity() -> dict[str, Any]:
    filesystems = []
    seen = set()
    for name in DISK_PATHS:
        path = Path(name)
        try:
            device = path.stat().st_dev
        except FileNotFoundError:
            continue
        if device in seen:
            continue
        usage = shutil.disk_usage(path)
        total, free = _count(usage.total), _count(usage.free)
        if total == 0 or free > total:
            raise ValueError("invalid filesystem capacity")
        filesystems.append({
            "key": f"device:{device}", "path": name,
            "total_bytes": total, "free_bytes": free,
            "used_percent": round((total - free) * 100.0 / total, 2),
        })
        seen.add(device)
    if not filesystems:
        raise ValueError("no monitored filesystem")
    return {"status": "ok", "filesystems": filesystems}


def _execution_capacity() -> dict[str, Any]:
    health = get_execution_service().health()
    scheduler = health["scheduler"]
    if health.get("ok") is not True or scheduler.get("ok") is not True:
        raise ValueError("execution health unavailable")
    return {
        "status": "ok",
        "queued_count": _count(scheduler["status_counts"].get("queued", 0)),
        "active_count": _count(scheduler["active_count"]),
        # A partition's default limit is not the service-wide capacity.
        "max_concurrency": _count(scheduler["total_capacity"]),
    }


def runtime_capacity() -> dict[str, Any]:
    """Collect independent observations; errors never contain source diagnostics."""
    capacity: dict[str, Any] = {
        "schema": SCHEMA,
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    for name, read in (
        ("database", _database_capacity),
        ("disks", _disk_capacity),
        ("execution_queue", _execution_capacity),
    ):
        try:
            capacity[name] = read()
        except Exception:
            capacity[name] = {"status": "unavailable", "code": f"{name}_unavailable"}
    return capacity
