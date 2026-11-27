"""Timing policy is independent of Provider transports and authentication."""
from types import SimpleNamespace
import pytest
from management_portal.execution import isa, request_timing
from management_portal.execution.agent_data_output_capture import model_timing
from management_portal.execution.service_settings import ExecutionSettings
from management_portal.society_service.isa.system_workflows import update_execution_settings

START = '2026-10-15T01:00:00+00:00'
FINISH = '2026-10-15T01:00:08+00:00'


def test_switch_preserves_observed_completion_and_never_invents_dispatch():
    """关闭模型请求计时后只保留完成时间，不补造开始时间，重新开启后恢复"""
    settings = ExecutionSettings()
    value = {'started_at': START, 'finished_at': FINISH, 'content': 'never copied'}
    assert request_timing.observed_timing(value) == {'started_at': START, 'finished_at': FINISH}
    assert model_timing({}, FINISH) == {'started_at': '', 'finished_at': FINISH}
    settings.update(False)
    assert ExecutionSettings().get() == {
        'model_request_timing_enabled': False, 'harness_recording_enabled': True,
        'harness_retention_days': 30, 'permission_system_enabled': True,
    }
    assert model_timing(value, FINISH) == {'started_at': '', 'finished_at': FINISH}
    assert value['started_at'] == START
    settings.update(True)
    assert model_timing(value, FINISH) == {'started_at': START, 'finished_at': FINISH}


def test_setting_failure_drops_only_the_optional_observation(monkeypatch):
    """读取计时设置失败时只丢弃开始时间，完成时间照常记录"""
    def broken(*args, **kwargs):
        raise RuntimeError('observation unavailable')
    monkeypatch.setattr(request_timing, 'ExecutionSettings', broken)
    assert model_timing({'started_at': START}, FINISH) == {'started_at': '', 'finished_at': FINISH}


def test_settings_are_judged_once_at_the_instruction_entry(monkeypatch):
    """执行设置的读写只在指令入口按声明和方案判定一次：处理代码和 RPC 不再重检，也没有旧的 settings 方法

    :design: decision_25eb93d803a4, decision_4527448ad07f
    :design-point:
    """
    from management_portal.execution.service_rpc import RPC_HANDLERS
    monkeypatch.setattr('management_portal.permission_system.allows',
                        lambda *args, **kwargs: pytest.fail('a settings handler asked the permission system'))
    inv = SimpleNamespace(caller='anyone', arguments={'model_request_timing_enabled': False}, argument=lambda name: False)
    assert isa.get_execution_settings(inv)['settings']['model_request_timing_enabled'] is True
    assert update_execution_settings(inv)['settings']['model_request_timing_enabled'] is False
    assert isa.get_execution_settings(inv)['settings']['model_request_timing_enabled'] is False
    assert not {'settings.get', 'settings.update'} & set(RPC_HANDLERS)


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
