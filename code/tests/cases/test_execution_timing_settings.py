"""Timing policy is independent of Provider transports and authentication."""
from types import SimpleNamespace
import pytest
from management_portal.execution import isa, request_timing
from management_portal.execution.agent_data_projection import _model_timing
from management_portal.execution.service import ExecutionServiceServer
from management_portal.execution.service_settings import ExecutionSettings
from management_portal.hatg.contracts import InstructionError
from management_portal.society_service.isa.system_workflows import update_execution_settings

START = '2026-10-15T01:00:00+00:00'
FINISH = '2026-10-15T01:00:08+00:00'


def test_switch_preserves_observed_completion_and_never_invents_dispatch():
    """关闭模型请求计时后只保留完成时间，不补造开始时间，重新开启后恢复"""
    module = request_timing.RequestTimingModule()
    value = {'started_at': START, 'finished_at': FINISH, 'content': 'never copied'}
    assert request_timing.observed_timing(value) == {'started_at': START, 'finished_at': FINISH}
    assert _model_timing({}, FINISH) == {'started_at': '', 'finished_at': FINISH}
    module.configure(False)
    assert ExecutionSettings().get() == {
        'model_request_timing_enabled': False, 'harness_recording_enabled': True,
        'harness_retention_days': 30, 'permission_system_enabled': True,
    }
    assert _model_timing(value, FINISH) == {'started_at': '', 'finished_at': FINISH}
    assert value['started_at'] == START
    module.configure(True)
    assert _model_timing(value, FINISH) == {'started_at': START, 'finished_at': FINISH}


def test_setting_failure_drops_only_the_optional_observation(monkeypatch):
    """读取计时设置失败时只丢弃开始时间，完成时间照常记录"""
    def broken(*args, **kwargs):
        raise RuntimeError('observation unavailable')
    monkeypatch.setattr(request_timing, 'ExecutionSettings', broken)
    assert _model_timing({'started_at': START}, FINISH) == {'started_at': '', 'finished_at': FINISH}


@pytest.mark.parametrize('admin', [False, True])
def test_settings_require_admin_at_isa_and_rpc(monkeypatch, admin):
    """读写执行设置在 ISA 和 RPC 两层都按 Get/UpdateExecutionSettings 判定

    :design: decision_25eb93d803a4, decision_425e428410e0
    :design-point:
    """
    module = request_timing.RequestTimingModule()
    server = SimpleNamespace(request_timing=module, _required=ExecutionServiceServer._required)
    server.dispatch = lambda method, params: ExecutionServiceServer.dispatch(server, method, params)
    monkeypatch.setattr(isa, '_SERVER', server)
    monkeypatch.setattr('management_portal.permission_system.allows', lambda actor, instruction, arguments=None: actor == 'admin' and admin)
    inv = SimpleNamespace(caller='admin', arguments={'model_request_timing_enabled': False}, argument=lambda name: False)
    if admin:
        assert isa.get_execution_settings(inv)['settings']['model_request_timing_enabled'] is True
        assert update_execution_settings(inv)['settings']['model_request_timing_enabled'] is False
    else:
        for handler in (isa.get_execution_settings, update_execution_settings):
            with pytest.raises(InstructionError, match='无权'):
                handler(inv)
        for method in ('settings.get', 'settings.update'):
            with pytest.raises(PermissionError, match='ExecutionSettings'):
                server.dispatch(method, {'actor': 'admin', 'model_request_timing_enabled': False})
        assert module.settings.get()['model_request_timing_enabled'] is True


@pytest.mark.parametrize('value', ['false', 0, None])
def test_settings_reject_non_boolean_values(value):
    """模型请求计时开关拒绝非布尔值，原设置不变

    :design: decision_25eb93d803a4, decision_425e428410e0
    :design-point:
    """
    settings = ExecutionSettings()
    with pytest.raises(ValueError, match='boolean'):
        settings.update(value)
    assert settings.get()['model_request_timing_enabled'] is True


def test_scheduler_restart_preserves_independently_owned_service_settings():
    """执行设置不会阻止调度器重启

    服务设置表独立于调度器校验，重启保持配置。

    :importance: P1
    :design: decision_25eb93d803a4
    :design-point:
    """
    from management_portal.execution.scheduler import Scheduler
    settings = ExecutionSettings()
    settings.update(False)
    Scheduler()
    Scheduler()
    assert ExecutionSettings().get()['model_request_timing_enabled'] is False


def test_legacy_routes_cannot_advertise_or_reenable_observation():
    """旧的计时代理路线设置固定为关闭，不能重新开启"""
    assert request_timing.LegacyRouteSettings().get() == {'provider_timing_proxy_enabled': False}
