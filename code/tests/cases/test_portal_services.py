""":importance: P2"""
from __future__ import annotations

import subprocess
import sys

import pytest

from scripts import portal_services
from tests.cases.portal_restart_order_support import RestartOrderTrace


def test_resource_health_client_works_without_server_database_dependencies():
    """ResourceService 健康检查客户端不加载数据库驱动和服务端模块"""
    probe = subprocess.run(
        [sys.executable, "-S", "-B", "-c", """
import sys
from scripts.portal_services import _resource_service_health
from management_portal.resources.transport import ResourceServiceClient
ResourceServiceClient.status = lambda self: {'schema': 'resource-health'}
assert _resource_service_health('dev')['ok']
assert 'psycopg' not in sys.modules
assert 'management_portal.resources.service' not in sys.modules
assert 'management_portal.resources.runtime' not in sys.modules
"""],
        cwd=portal_services.ROOT, text=True, capture_output=True, timeout=10,
    )
    assert probe.returncode == 0, probe.stderr




def _health(*, active: int = 0, queued: int = 0) -> dict[str, object]:
    return {
        "ok": True,
        "scheduler": {
            "active_count": active,
            "capacity_partitions": {
                "codex:zero": {"queued_count": queued},
            },
        },
    }


def test_queue_counts_use_scheduler_partitions_contracts(subtests, isolated_portal_case):
    """队列计数取调度分区的活动和排队数，重启通知时长固定为 5 秒"""
    with subtests.test(contract='test_queue_counts_use_scheduler_partitions'), isolated_portal_case() as case_path:
        assert portal_services._queue_counts(_health(active=2, queued=3)) == {'active': 2, 'queued': 3}
    with subtests.test(contract='test_restart_command_defaults_to_five_second_notice'), isolated_portal_case() as case_path:
        args = portal_services._parser().parse_args(['restart', 'dev'])
        assert args.notice == 5
    with subtests.test(contract='test_restart_command_rejects_other_notice_durations'), isolated_portal_case() as case_path:
        with pytest.raises(SystemExit):
            portal_services._parser().parse_args(['restart', 'dev', '--notice', '10'])
    with subtests.test(contract='test_restart_api_rejects_other_notice_durations'), isolated_portal_case() as case_path:
        with pytest.raises(RuntimeError, match='Standalone TFI'):
            portal_services.restart('dev', notice_seconds=10)












def test_database_schema_status_checks_database_integrity_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """数据库 schema 状态检查 PostgreSQL 完整性，Dev 和 Release 的操作锁互相隔离"""
    with subtests.test(contract='test_database_schema_status_checks_database_integrity'), isolated_portal_case() as case_path:
        del case_path
        result = portal_services._database_schema_status('release')
        assert result['ok'] is True
        assert result['backend'] == 'postgresql'
        assert result['database']
        assert result['server_version'] > 0
        assert isinstance(result['schemas'], list)
    with subtests.test(contract='test_operation_locks_are_isolated_by_portal_instance'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            tmp_path = case_path
            monkeypatch.setattr(portal_services, 'INSTANCE_ROOT', tmp_path)
            dev = portal_services._operation_lock_path('dev')
            release = portal_services._operation_lock_path('release')
            assert dev != release
            assert dev.parent.name == 'dev'
            assert release.parent.name == 'release'
            with portal_services.operation_lock(dev):
                with portal_services.operation_lock(release):
                    pass








@pytest.fixture(autouse=True)
def isolated_peer_pool(monkeypatch, tmp_path):
    from scripts import portal_dev_source_reloader as reloader
    monkeypatch.setattr(portal_services, 'INSTANCE_ROOT', tmp_path)
    monkeypatch.setattr(reloader, 'DEPLOYED_SOURCE_STATE', tmp_path / 'deployed.json')
    monkeypatch.setattr(reloader, 'component_source_states', lambda source: {name: () for name in reloader.ALL_COMPONENTS})
    monkeypatch.setattr(reloader, 'source_state', lambda: ())
    monkeypatch.setattr(portal_services.portal_release, "RELEASE_ROOT", tmp_path / "not-deployed")


def test_request_path_units_run_ahead_of_agent_commands_and_tests():
    """处理请求的服务单元 CPUWeight 为 1000，优先于 agent 命令和测试"""
    # Agent shells keep the default CPU weight and test runs a lower one (management_portal.test_framework.executor).
    from scripts import portal_service_units
    from scripts.portal_source_layout import INSTANCE_LAYOUT

    units = ["portal-user-router.service", "portal-postgresql.service", INSTANCE_LAYOUT["release"]["web"]]
    units += [unit for layout in INSTANCE_LAYOUT.values() for unit in layout["services"] if "execution-worker" not in unit]
    sources = [portal_service_units.DEV_WEB_UNIT_SOURCE, *(portal_service_units.DEV_ROOT / "systemd" / unit for unit in units)]
    for source in sources:
        assert "\nCPUWeight=1000\n" in source.read_text(encoding="utf-8"), source.name




@pytest.mark.parametrize("instance", ["dev", "release"])
def test_retired_source_pool_controls_refuse_without_service_actions(monkeypatch, instance):
    """独立实例禁止旧主机站 Dev/Release 控制，服务由当前安装器管理。"""
    monkeypatch.setattr(portal_services, "_controller", lambda: pytest.fail("retired control reached system services"))
    for operation in (lambda: portal_services.restart(instance, notice_seconds=5),
                      lambda: portal_services.request_restart(instance)):
        with pytest.raises(RuntimeError, match="Standalone TFI"):
            operation()
