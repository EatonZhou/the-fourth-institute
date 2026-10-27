""":importance: P2"""
from __future__ import annotations

import pytest

from scripts import portal_services


@pytest.fixture(autouse=True)
def isolated_instance_root(monkeypatch, tmp_path):
    monkeypatch.setattr(portal_services, 'INSTANCE_ROOT', tmp_path)


def test_restart_notice_is_fixed_at_five_seconds_contracts(monkeypatch, subtests, isolated_portal_case):
    """重启通知时长固定为 5 秒，其他时长在到达运行时之前被拒绝"""
    with subtests.test(contract='test_restart_command_defaults_to_five_second_notice'), isolated_portal_case() as case_path:
        args = portal_services._parser().parse_args(['restart', 'dev'])
        assert args.notice == 5
    with subtests.test(contract='test_restart_command_rejects_other_notice_durations'), isolated_portal_case() as case_path:
        with pytest.raises(SystemExit):
            portal_services._parser().parse_args(['restart', 'dev', '--notice', '10'])
    with subtests.test(contract='test_restart_api_rejects_other_notice_durations'), isolated_portal_case() as case_path:
        from scripts import tfi_development_runtime as paired
        monkeypatch.setattr(paired, 'configured_root', lambda: pytest.fail('invalid request reached runtime discovery'))
        with pytest.raises(ValueError, match='fixed at 5 seconds'):
            portal_services.restart('dev', notice_seconds=10)


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
    for operation in (lambda: portal_services.restart(instance, notice_seconds=5),
                      lambda: portal_services.request_restart(instance)):
        with pytest.raises(RuntimeError, match="Standalone TFI"):
            operation()
    assert portal_services.status(instance)["supported"] is False
