"""An unchanged invalid automation configuration must not become timer work.

:design: decision_5ff0cb51ed84
"""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from management_portal.society_service import lineage_automation_identity as identity
from management_portal.society_service import lineage_program_polling as polling
from management_portal.society_service.coordinator import LineageCoordinator


@pytest.fixture
def ports(monkeypatch):
    definition = {'lineage_id': 'lineage_config', 'program': {'components': [
        {'id': 'timer', 'config': {'event_type': 'time.reached'}}]}}
    monkeypatch.setattr(polling, 'recovery_lineage_ids', lambda **_: ['lineage_config'])
    monkeypatch.setattr(polling.lineage_program_runtime, 'read_runtime', lambda _: {})
    monkeypatch.setattr(polling.lineage_program_resident_inputs, 'pending_resident_handoffs', lambda _: [])
    value = polling.ProgramPollingPorts(
        lineage_catalog_store=lambda: SimpleNamespace(get=lambda _: definition),
        lineage_runtime_actor=identity.runtime_actor, read_lineage_state=lambda _: definition,
        read_resident_activity_snapshot=lambda _: {}, ensure_manager=lambda *_: None,
        reconcile_lineage_system_events=lambda *a, **k: {'next_wake_at': '2099-01-01T00:00:00Z'},
        reconcile_lineage_runtime=lambda *a, **k: {})
    return definition, value


@pytest.mark.parametrize('leader', ['', 'sample_admin', 'resident_aaaaaaaaaaaa'])
def test_invalid_config_is_blocked_without_retry_and_graph_change_recovers(ports, leader):
    """运行身份配置无效时 Lineage 被阻断且不重试，改正配置并通知后解除阻断"""
    definition, bindings = ports
    definition['leader_resident_id'] = leader
    definition['nodes'] = []
    coordinator = LineageCoordinator(resume_residents=lambda: {}, resume_resident=lambda _: {},
        reconcile_all=lambda: polling.poll_all_system_events(ports=bindings),
        reconcile_lineage=lambda lid: polling.reconcile_system_events_as_service(lid, ports=bindings))
    coordinator._reconcile()
    assert coordinator.snapshot()['blocked_lineages']['lineage_config']
    assert coordinator.snapshot()['retry_lineage_ids'] == []
    assert coordinator.snapshot()['last_reconcile']['failed'] == 0
    coordinator._reconcile_one('lineage_config')
    assert coordinator.snapshot()['retry_lineage_ids'] == []
    definition['leader_resident_id'] = 'resident_aaaaaaaaaaaa'
    definition['nodes'] = [{'id': 'resident_aaaaaaaaaaaa'}]
    coordinator.notify_lineage('lineage_config')
    coordinator._reconcile_one('lineage_config')
    assert not coordinator.snapshot()['blocked_lineages']
    assert coordinator.snapshot()['retry_lineage_ids'] == []
    assert coordinator.snapshot()['wake_lineage_ids'] == ['lineage_config']


def test_only_explicit_configuration_failure_stops_retries():
    """只有明确的配置错误会停止重试，连接故障仍保留重试"""
    def fail(_):
        raise identity.AutomationConfigurationError('missing config')
    coordinator = LineageCoordinator(resume_residents=lambda: {}, resume_resident=lambda _: {},
        reconcile_all=lambda: {}, reconcile_lineage=fail)
    coordinator._schedule_lineage_retry('lineage_config', 5)
    coordinator._reconcile_one('lineage_config')
    assert coordinator.snapshot()['retry_lineage_ids'] == []
    def unavailable(_):
        raise ConnectionError('database unavailable')
    coordinator._reconcile_lineage = unavailable
    coordinator._reconcile_one('lineage_config')
    assert coordinator.snapshot()['retry_lineage_ids'] == ['lineage_config']


def test_recovery_only_does_not_request_new_automation_authority(ports):
    """recovery_only 轮询不要求自动运行身份，不产生失败、阻断或重试"""
    _, bindings = ports
    result = polling.poll_all_system_events(ports=bindings, recovery_only=True)
    assert result['failed'] == 0 and not result['blocked_lineages'] and not result['retry_lineage_ids']


def test_full_poll_keeps_transient_errors_retryable(ports):
    """有合法 Leader 后观测服务暂时不可用仍可重试，不是配置阻断。"""
    _, bindings = ports
    def unavailable(*args, **kwargs):
        raise ConnectionError('observation service unavailable')
    bindings = replace(bindings, reconcile_lineage_system_events=unavailable)
    ports[0]['leader_resident_id'] = 'resident_aaaaaaaaaaaa'
    ports[0]['nodes'] = [{'id': 'resident_aaaaaaaaaaaa'}]
    result = polling.poll_all_system_events(ports=bindings)
    assert result['failed'] == 1 and not result['blocked_lineages']
    assert result['retry_lineage_ids'] == ['lineage_config']
