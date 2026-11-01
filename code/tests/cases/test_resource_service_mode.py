"""Only the instance's resource service keeps a resource index.

Processes that do not configure a ResourceService mode are clients of their
instance's service; a process that runs it locally must name the index schema,
so no process silently builds a second index of its own.
"""
from __future__ import annotations

import pytest

from management_portal.resources import runtime
from management_portal.resources.config import resource_database_schema, resource_service_mode
from management_portal.resources.transport import ResourceServiceClient


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


def test_only_the_installed_resource_component_runs_the_index_locally(monkeypatch, tmp_path):
    """只有资源服务组件在本地运行索引

    实例服务的启动命令中，只有 resource 组件以 local 模式运行 ResourceService，其余进程都是它的客户端。
    """
    from management_portal import service_runtime as instance_runtime
    from management_common.instance_manifest import load
    from tests.cases.instance_test_support import write_instance

    instance = load(write_instance(tmp_path / "instance"))
    monkeypatch.setattr(instance_runtime.code_environment, "runtime_python", lambda code: "python")
    monkeypatch.setattr(instance_runtime.code_environment, "native_binary", lambda code, name: tmp_path / name)
    local = [component for component in instance.document["services"]["components"]
             if component not in {"postgres", "nats"}
             and instance_runtime.component_command(instance, component)[1]["PORTAL_RESOURCE_SERVICE_MODE"] == "local"]
    assert local == ["resource"]
