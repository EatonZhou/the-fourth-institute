"""ExecutionService answers every wire method its clients send, through one table.

The business facade, the Worker's SchedulerClient, the WorkerManager and the
transport's health probe name their wire methods as string literals; the
server looks each one up in ``RPC_HANDLERS``.  A method missing from the table
fails only when a client sends it, with "unsupported ExecutionService method".

:module: execution.service
:importance: P2
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from management_portal.execution import service
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.service_rpc import RPC_HANDLERS

CLIENT_MODULES = ("service.py", "scheduler.py", "transport.py", "worker_manager.py")
WIRE_METHOD = re.compile(
    r"[\"']((?:execution|scheduler|accounts|runtime|settings)\.[a-z_.]+|health)[\"']"
)


def _client_methods() -> set[str]:
    root = Path(service.__file__).parent
    return {
        method
        for name in CLIENT_MODULES
        for method in WIRE_METHOD.findall((root / name).read_text(encoding="utf-8"))
    }


def test_every_client_wire_method_has_exactly_one_handler():
    """客户端发出的每个 ExecutionService 方法都有处理函数，表中也没有无人调用的方法"""
    assert _client_methods() == set(RPC_HANDLERS)


def test_an_unknown_method_is_refused_before_any_handler_runs():
    """未登记的方法名直接被拒绝，不触达调度器"""
    server = object.__new__(ExecutionServiceServer)
    with pytest.raises(ValueError, match="unsupported ExecutionService method: execution.unknown"):
        server.dispatch("execution.unknown", {})
