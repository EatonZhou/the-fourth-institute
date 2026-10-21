"""Capacity observations and the real read-only Web ISA authorization route.

:importance: P2
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from management_portal import runtime_capacity as capacity, web_isa
from management_portal.hatg import kernel as kernel_module, routing
from management_portal.hatg.catalog import instruction_catalog
from management_portal.hatg.contracts import InstructionContext
from management_portal.hatg.kernel import InstructionKernel


PRIVATE_ERROR = "postgresql://private:secret@host/db?token=do-not-return"
ACTOR = "resident_e0076dfaca67"


@pytest.fixture
def sources(monkeypatch):
    state = SimpleNamespace(
        events=[], queries=[], disk_reads=[],
        row={"connections": 76, "max_connections": 100, "reserved_connections": 5},
        database_error=None, devices={"/": 11, "/share": 11, "/data": 22},
        disks={11: (1000, 200), 22: (2000, 1400)},
        health={"ok": True, "scheduler": {
            "ok": True, "status_counts": {"queued": 7, "claimed": 1, "running": 2, "completed": 80},
            "active_count": 3, "total_capacity": 12, "default_partition_limit": 4,
            "database": PRIVATE_ERROR,
            "capacity_partitions": {"quota_paused": {"queued_count": 7, "limit": 4}},
        }},
    )

    class Database:
        @contextmanager
        def read(self):
            state.events.append("read_enter")
            try:
                if state.database_error == "acquire":
                    raise OSError(PRIVATE_ERROR)
                yield self
            finally:
                state.events.append("read_exit")

        def execute(self, query):
            state.queries.append(query)
            if state.database_error == "query":
                raise OSError(PRIVATE_ERROR)
            return SimpleNamespace(fetchone=lambda: state.row)

        def close(self):
            state.events.append("close")

    def database(**kwargs):
        state.events.append("open")
        return Database()

    def stat(path, **kwargs):
        name = str(path)
        if name not in state.devices:
            raise FileNotFoundError(name)
        device = state.devices[name]
        if isinstance(device, Exception):
            raise device
        return SimpleNamespace(st_dev=device)

    def disk_usage(path):
        name = str(path)
        state.disk_reads.append(name)
        total, free = state.disks[state.devices[name]]
        return SimpleNamespace(total=total, free=free, used=total-free)

    monkeypatch.setattr(capacity, "portal_database", database)
    # disk_usage receives the Path object, which must retain its path spelling.
    class DiskPath:
        def __init__(self, name):
            self.name = name

        def __str__(self):
            return self.name

        def stat(self):
            return stat(self.name)

    monkeypatch.setattr(capacity, "Path", DiskPath)
    # Match the fake filesystem, independently of the invoking site's paths.
    monkeypatch.setattr(capacity, "DISK_PATHS", ("/", "/share", "/data"))
    monkeypatch.setattr(capacity.shutil, "disk_usage", disk_usage)
    monkeypatch.setattr(capacity, "get_execution_service", lambda: SimpleNamespace(health=lambda: state.health))
    return state


def test_complete_snapshot_ordinary_database_slots_disks_and_queue_semantics(sources):
    """运行容量快照给出数据库连接占用、按设备去重的磁盘和执行队列，不泄露内部错误"""
    before = datetime.now(timezone.utc)
    result = capacity.runtime_capacity()
    assert result["schema"] == "tfi.runtime-capacity.v1"
    assert before <= datetime.fromisoformat(result["observed_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc)
    assert result["database"] == {
        "status": "ok", "connections": 76, "max_connections": 100,
        "reserved_connections": 5, "usable_connections": 95, "used_percent": 80.0,
    }
    assert result["disks"] == {"status": "ok", "filesystems": [
        {"key": "device:11", "path": "/", "total_bytes": 1000, "free_bytes": 200, "used_percent": 80.0},
        {"key": "device:22", "path": "/data", "total_bytes": 2000, "free_bytes": 1400, "used_percent": 30.0},
    ]}
    assert sources.disk_reads == ["/", "/data"]
    # All seven requests are quota-paused: report them, without claiming they
    # are eligible. Completed work is not queue occupancy; capacity is 12, not 4.
    assert result["execution_queue"] == {
        "status": "ok", "queued_count": 7, "active_count": 3, "max_concurrency": 12,
    }
    assert PRIVATE_ERROR not in json.dumps(result)
    assert sources.events == ["open", "read_enter", "read_exit", "close"]
    assert len(sources.queries) == 1
    query = sources.queries[0]
    assert "backend_type = 'client backend'" in query
    assert "current_setting('superuser_reserved_connections')" in query
    assert "current_setting('reserved_connections')" in query
    assert "datname =" not in query  # Shared instance, not only the Portal DB.


@pytest.mark.parametrize("source", ["database", "disks", "execution_queue"])
def test_source_failure_is_independent_and_redacted(sources, source):
    """数据库、磁盘或执行队列任一来源失败只标记该项不可用，并隐去错误细节"""
    if source == "database":
        sources.database_error = "query"
    elif source == "disks":
        sources.devices["/share"] = PermissionError(PRIVATE_ERROR)
    else:
        sources.health = {"ok": False, "scheduler": {}, "error": PRIVATE_ERROR}
    result = capacity.runtime_capacity()
    assert result[source] == {"status": "unavailable", "code": f"{source}_unavailable"}
    for other in {"database", "disks", "execution_queue"} - {source}:
        assert result[other]["status"] == "ok"
    assert PRIVATE_ERROR not in json.dumps(result)


@pytest.mark.parametrize("failure", ["acquire", "query", "invalid_row"])
def test_database_handle_and_checkout_released_on_failure(sources, failure):
    """数据库连接获取、查询失败或返回值无效时，容量读取仍释放并关闭连接

    :importance: P1"""
    if failure == "invalid_row":
        sources.row["reserved_connections"] = 100
    else:
        sources.database_error = failure
    assert capacity.runtime_capacity()["database"]["status"] == "unavailable"
    assert sources.events == ["open", "read_enter", "read_exit", "close"]


def test_missing_optional_mount_and_reserved_slot_overflow(sources):
    """可选挂载点缺失时跳过该磁盘，连接数超过可用名额时占用率可超过 100%"""
    del sources.devices["/data"]
    sources.row["connections"] = 98
    result = capacity.runtime_capacity()
    assert result["database"]["used_percent"] == 103.16
    assert len(result["disks"]["filesystems"]) == 1
    assert sources.disk_reads == ["/"]


def test_real_postgresql_snapshot_closes_its_handle(monkeypatch):
    """在真实 PostgreSQL 上读取连接容量后关闭数据库句柄"""
    # conftest selects a separate disposable PostgreSQL server. This verifies
    # the actual pg_stat_activity / PG16 settings query, never the Portal DB.
    from management_common.portal_database import portal_database

    handles = []

    def open_database(**kwargs):
        database = portal_database(**kwargs)
        handles.append(database)
        return database

    monkeypatch.setattr(capacity, "portal_database", open_database)
    result = capacity._database_capacity()
    assert result["status"] == "ok" and result["connections"] >= 1
    assert result["usable_connections"] == result["max_connections"] - result["reserved_connections"] > 0
    assert result["used_percent"] == round(result["connections"] * 100.0 / result["usable_connections"], 2)
    assert len(handles) == 1 and handles[0]._pool is None


@pytest.mark.parametrize("source,field,value", [
    ("database", "connections", -1),
    ("execution_queue", "active_count", None),
    ("execution_queue", "total_capacity", True),
    ("execution_queue", "ok", False),
])
def test_invalid_source_values_do_not_become_healthy_zero(sources, source, field, value):
    """来源返回负数、空值或错误类型时标记为不可用，不当作健康的 0"""
    target = sources.row if source == "database" else sources.health["scheduler"]
    target[field] = value
    result = capacity.runtime_capacity()
    assert result[source]["status"] == "unavailable"


def test_empty_queue_zero_capacity_and_failed_health_transport(sources, monkeypatch):
    """空队列和零容量照常报告，执行服务连接失败时只有队列不可用"""
    sources.health["scheduler"].update(status_counts={}, active_count=0, total_capacity=0)
    assert capacity.runtime_capacity()["execution_queue"] == {
        "status": "ok", "queued_count": 0, "active_count": 0, "max_concurrency": 0,
    }

    def fail():
        raise OSError(PRIVATE_ERROR)

    monkeypatch.setattr(capacity, "get_execution_service", fail)
    result = capacity.runtime_capacity()
    assert result["execution_queue"] == {"status": "unavailable", "code": "execution_queue_unavailable"}
    assert result["database"]["status"] == result["disks"]["status"] == "ok"


@pytest.mark.parametrize("allowed", [True, False])
def test_real_isa_routes_to_web_and_checks_resident_authorization(sources, monkeypatch, allowed):
    """GetRuntimeCapacity 路由到 Web 并先由权限系统检查调用者，拒绝时不采集数据"""
    from management_portal import permission_system
    from management_portal.hatg.contracts import InstructionError

    authorization_calls = []

    def authorize(declaration, arguments, context, *, instruction_id=""):
        authorization_calls.append((declaration.name, dict(arguments), context))
        if not allowed:
            raise InstructionError("instruction.forbidden", "denied")

    monkeypatch.setattr(permission_system, "authorize_instruction", authorize)
    kernel = InstructionKernel(web_isa.binding(), store=None)
    monkeypatch.setattr(kernel_module, "_KERNELS", {"web": kernel})
    declaration = instruction_catalog()["GetRuntimeCapacity"]
    assert (declaration.owner, declaration.mutates, declaration.parameters) == ("web", False, ())
    result = routing.execute({"instruction": "GetRuntimeCapacity", "arguments": {}},
                             InstructionContext(caller=ACTOR, entry="harness"))
    assert len(authorization_calls) == 1
    name, arguments, context = authorization_calls[0]
    assert (name, arguments, context.caller, context.entry) == ("GetRuntimeCapacity", {}, ACTOR, "harness")
    if allowed:
        assert result.status == "committed", result.error
        assert result.result["capacity"]["database"]["connections"] == 76
    else:
        assert result.status == "failed"
        assert result.error["code"] == "instruction.forbidden"
        assert not sources.events and not sources.disk_reads


def test_isa_rejects_unrecognized_arguments_without_collecting(sources):
    """GetRuntimeCapacity 收到未知参数时直接拒绝，不采集数据"""
    kernel = InstructionKernel(web_isa.binding(), store=None)
    from management_portal.hatg.contracts import InstructionCall

    result = kernel.execute(InstructionCall.from_payload({
        "instruction": "GetRuntimeCapacity", "arguments": {"refresh": True},
    }), InstructionContext(caller=ACTOR, entry="harness"))
    assert result.status == "failed"
    assert result.error["code"] == "instruction.invalid_arguments"
    assert sources.events == []
