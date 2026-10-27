""":importance: P2"""
from __future__ import annotations

import pytest

from scripts import portal_services


@pytest.fixture(autouse=True)
def isolated_instance_root(monkeypatch, tmp_path):
    monkeypatch.setattr(portal_services, 'INSTANCE_ROOT', tmp_path)


def test_operation_locks_are_isolated_by_portal_instance(tmp_path):
    """Dev 和 Release 的操作锁互相隔离"""
    dev = portal_services._operation_lock_path('dev')
    release = portal_services._operation_lock_path('release')
    assert dev != release
    assert dev.parent.name == 'dev'
    assert release.parent.name == 'release'
    with portal_services.operation_lock(dev):
        with portal_services.operation_lock(release):
            pass


@pytest.mark.parametrize("instance", ["dev", "release"])
def test_retired_source_pool_controls_refuse_without_service_actions(monkeypatch, instance):
    """独立实例禁止旧主机站 Dev/Release 控制，服务由当前安装器管理。"""
    from scripts import tfi_development_runtime as paired
    monkeypatch.setattr(paired, 'configured_root', lambda: None)
    for operation in (lambda: portal_services.restart(instance),
                      lambda: portal_services.request_restart(instance)):
        with pytest.raises(RuntimeError, match="Standalone TFI"):
            operation()
    assert portal_services.status(instance)["supported"] is False
