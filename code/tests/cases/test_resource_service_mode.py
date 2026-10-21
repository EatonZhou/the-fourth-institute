"""Only the instance's resource service keeps a resource index.

Processes that do not configure a ResourceService mode are clients of their
instance's service; a process that runs it locally must name the index schema,
so no process silently builds a second index of its own.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from management_portal.resources import runtime
from management_portal.resources.config import resource_database_schema, resource_service_mode
from management_portal.resources.transport import ResourceServiceClient

ROOT = Path(__file__).resolve().parents[2]


def test_unconfigured_process_is_a_client_of_its_instance_service(monkeypatch, tmp_path):
    """未配置模式的进程是 ResourceService 客户端

    未设置 PORTAL_RESOURCE_SERVICE_MODE 的进程通过实例的 resource-service.sock 访问 ResourceService，不在本进程建立资源索引。
    """
    monkeypatch.delenv("PORTAL_RESOURCE_SERVICE_MODE", raising=False)
    monkeypatch.setenv("PORTAL_RESOURCE_SERVICE_SOCKET", str(tmp_path / "resource-service.sock"))
    monkeypatch.setattr(runtime, "_SERVICE", None)
    assert resource_service_mode() == "client"
    service = runtime.get_resource_service()
    assert isinstance(service, ResourceServiceClient)
    assert service.socket_path == tmp_path / "resource-service.sock"


def test_local_index_schema_must_be_named_outside_test_namespaces(monkeypatch):
    """本地资源索引必须指定 schema

    在本进程运行 ResourceService 时必须设置 PORTAL_RESOURCE_DATABASE_SCHEMA；只有隔离的测试命名空间可回退到 resources。
    """
    monkeypatch.delenv("PORTAL_RESOURCE_DATABASE_SCHEMA", raising=False)
    monkeypatch.delenv("PORTAL_DATABASE_NAMESPACE", raising=False)
    with pytest.raises(RuntimeError, match="PORTAL_RESOURCE_DATABASE_SCHEMA"):
        resource_database_schema()
    monkeypatch.setenv("PORTAL_DATABASE_NAMESPACE", "pytest_probe")
    assert resource_database_schema() == "resources"
    monkeypatch.setenv("PORTAL_RESOURCE_DATABASE_SCHEMA", "resource_dev")
    assert resource_database_schema() == "resource_dev"


def test_every_unit_running_resource_service_locally_names_its_index():
    """只有资源服务单元在本地运行索引

    systemd 单元中只有 Dev 与 Release 的 resource service 以 local 模式运行，并各自指定 resource_dev、resource_release 索引 schema。
    """
    local_units = []
    for unit in sorted((ROOT / "systemd").glob("*.service")):
        text = unit.read_text(encoding="utf-8")
        mode = re.search(r"^Environment=PORTAL_RESOURCE_SERVICE_MODE=(\S+)$", text, re.M)
        if mode and mode.group(1) in {"local", "server"}:
            local_units.append(unit.name)
            schema = re.search(r"^Environment=PORTAL_RESOURCE_DATABASE_SCHEMA=(resource_\w+)$", text, re.M)
            assert schema, f"{unit.name} runs ResourceService locally without naming its index schema"
    assert local_units == ["portal-dev-resource-service.service", "portal-release-resource-service.service"]
