""":importance: P2"""
from __future__ import annotations

import pytest

from scripts import portal_services


@pytest.fixture(autouse=True)
def paired_instance(monkeypatch, tmp_path):
    from tests.cases.test_development_runtime import write_pair
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(write_pair(tmp_path)))


def test_operation_locks_are_isolated_by_deployment(tmp_path):
    """Dev 和 Release 的操作锁在各自部署的运行目录中，互相隔离"""
    dev = portal_services._operation_lock_path('dev')
    release = portal_services._operation_lock_path('release')
    assert dev != release
    assert dev == tmp_path / 'runtime/deployments/dev/portal-services.lock'
    assert release == tmp_path / 'runtime/deployments/release/portal-services.lock'
    with portal_services.operation_lock(dev):
        with portal_services.operation_lock(release):
            pass


@pytest.mark.parametrize("deployment", ["dev", "release"])
def test_retired_source_pool_controls_refuse_without_service_actions(monkeypatch, deployment):
    """独立实例禁止旧主机站 Dev/Release 控制，服务由当前安装器管理。"""
    from scripts import tfi_development_runtime as paired
    monkeypatch.setattr(paired, 'configured_root', lambda: None)
    for operation in (lambda: portal_services.restart(deployment),
                      lambda: portal_services.request_restart(deployment)):
        with pytest.raises(RuntimeError, match="Standalone TFI"):
            operation()
    assert portal_services.status(deployment)["supported"] is False
