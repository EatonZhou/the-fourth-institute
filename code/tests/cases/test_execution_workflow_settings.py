"""Workflow projections govern evidence without entering model transport."""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from management_portal.execution.harness_effectiveness import HarnessEffectivenessStore
from management_portal.execution.harness_evolution_offline import collect_turn_calls
from management_portal.execution.request_timing import observed_timing
from management_portal.execution.service_settings import RETENTION_DAYS, ExecutionSettings
from tests.cases.database_test_support import connect
from tests.cases.test_harness_effectiveness import _decision

TIMING = 'system:request-timing:enabled'
RECORDING = 'system:harness-recording:enabled'
DEFAULT_RETENTION = {name: default for name, (_minimum, _maximum, default) in RETENTION_DAYS.items()}
# Settings these tests leave alone keep their defaults.
UNTOUCHED = {'permission_system_enabled': True, **DEFAULT_RETENTION}


def test_reopen_settings_while_backup_holds_read_lock():
    """备份持有读锁时仍可重新加载执行设置"""
    from contextlib import contextmanager

    settings = ExecutionSettings()
    settings.project_workflow_switch(RECORDING, False)

    class BoundedDatabase:
        @contextmanager
        def transaction(self):
            with settings.database.transaction() as connection:
                connection.execute("SET LOCAL lock_timeout = '150ms'")
                yield connection

    with settings.database.transaction() as backup:
        backup.execute('LOCK TABLE service_settings IN ACCESS SHARE MODE')
        reopened = ExecutionSettings(database=BoundedDatabase())
        assert reopened.get()['harness_recording_enabled'] is False
        assert reopened.managed_workflow_keys() == {RECORDING}


def test_upgrade_legacy_settings_preserves_existing_policy():
    """升级旧执行设置时保留已有开关策略"""
    from management_common.portal_database import portal_database

    database = portal_database(schema='legacy_execution_settings')
    try:
        with database.transaction() as connection:
            connection.execute('''CREATE TABLE service_settings (
                singleton INTEGER PRIMARY KEY, provider_timing_proxy_enabled BOOLEAN NOT NULL)''')
            connection.execute('INSERT INTO service_settings VALUES (1, FALSE)')
        settings = ExecutionSettings(database=database)
        assert settings.get() == {'model_request_timing_enabled': False, 'harness_recording_enabled': True,
                                  **UNTOUCHED}
        assert settings.managed_workflow_keys() == set()
        settings.project_workflow_switch(RECORDING, False)
        assert settings.get()['harness_recording_enabled'] is False
    finally:
        database.close()


def test_install_preserves_legacy_values_and_prevents_bypassing_managed_events():
    """安装工作流保留旧设置并阻止绕过受管事件更新"""
    settings = ExecutionSettings()
    settings.update_unmanaged({'model_request_timing_enabled': False, 'harness_recording_enabled': False})
    assert not settings.managed_workflow_keys()
    assert settings.get() == {'model_request_timing_enabled': False, 'harness_recording_enabled': False,
                              **UNTOUCHED}
    settings.project_workflow_switch(RECORDING, True)
    assert settings.get() == {'model_request_timing_enabled': False, 'harness_recording_enabled': True,
                              **UNTOUCHED}
    settings.project_workflow_switch(TIMING, True)
    settings.project_workflow_switch(TIMING, False)  # deletion is a lasting closed projection
    reopened = ExecutionSettings()
    assert reopened.managed_workflow_keys() == {TIMING, RECORDING}
    with pytest.raises(ValueError, match='Lineage Event'):
        reopened.update(True)
    with pytest.raises(ValueError, match='Lineage Event'):
        reopened.update_unmanaged({'model_request_timing_enabled': True, 'harness_recording_enabled': False})
    assert reopened.get() == {'model_request_timing_enabled': False, 'harness_recording_enabled': True,
                              **UNTOUCHED}
    for key, value in [('typo', True), (TIMING, 'false')]:
        with pytest.raises(ValueError):
            reopened.project_workflow_switch(key, value)


def test_harness_retention_is_validated_and_never_claims_a_workflow_key():
    """Harness保留期独立校验并更新；不产生工作流开关或认领工作流key。"""
    settings = ExecutionSettings()
    assert settings.update_retention({'harness_retention_days': 60}) == {
        'model_request_timing_enabled': True, 'harness_recording_enabled': True, 'harness_retention_days': 60,
        'permission_system_enabled': True,
    }
    for invalid in ({'harness_retention_days': 20}, {'harness_retention_days': 366}, {'harness_retention_days': 30.5},
                    {'harness_retention_days': True}, {'harness_retention_days': 30, 'typo': 3}, {}):
        with pytest.raises(ValueError):
            settings.update_retention(invalid)
    assert ExecutionSettings().get()['harness_retention_days'] == 60
    assert settings.managed_workflow_keys() == set()


def test_unmanaged_multi_setting_update_is_atomic_if_one_field_is_owned():
    """多字段更新涉及受管字段时整体回滚"""
    settings = ExecutionSettings()
    settings.project_workflow_switch(RECORDING, True)
    with pytest.raises(ValueError, match='Lineage Event'):
        settings.update_unmanaged({'model_request_timing_enabled': False, 'harness_recording_enabled': False})
    assert settings.get()['model_request_timing_enabled'] is True
    assert settings.managed_workflow_keys() == {RECORDING}


def test_timing_projection_controls_persistence_without_changing_response():
    """计时投影控制记录持久化且保留响应内容"""
    settings = ExecutionSettings()
    response = {'started_at': '2026-10-15T00:00:00Z', 'finished_at': '2026-10-15T00:00:02Z', 'content': 'answer'}
    settings.project_workflow_switch(TIMING, False)
    assert observed_timing(response) == {}
    assert response['content'] == 'answer'
    settings.project_workflow_switch(TIMING, True)
    assert observed_timing(response) == {key: response[key] for key in ('started_at', 'finished_at')}


def _record(store, name):
    store.record_turn(decision=_decision(name), details={'status': 'completed'}, result=None)


def _ids(store):
    store.flush()
    with connect(store.database) as connection:
        return {row[0] for row in connection.execute('SELECT execution_id FROM harness_strategy_observations').fetchall()}


def test_reopen_drops_old_queued_telemetry_but_retains_history_and_reviews(monkeypatch):
    """重新开启记录丢弃旧排队遥测并保留历史与评审"""
    settings = ExecutionSettings()
    settings.project_workflow_switch(RECORDING, True)
    store = HarnessEffectivenessStore()
    entered, release = threading.Event(), threading.Event()
    original = store._write
    def blocked(kind, payload):
        if payload['execution_id'] == 'queued':
            entered.set()
            assert release.wait(5)
        original(kind, payload)
    try:
        _record(store, 'history')
        assert _ids(store) == {'history'}
        monkeypatch.setattr(store, '_write', blocked)
        _record(store, 'queued')
        assert entered.wait(5)
        settings.project_workflow_switch(RECORDING, False)
        _record(store, 'while-closed')
        settings.project_workflow_switch(RECORDING, True)
        # Repeated startup reconciliation must not discard newly admitted work.
        _record(store, 'reopened')
        settings.project_workflow_switch(RECORDING, True)
        release.set()
        assert _ids(store) == {'history', 'reopened'}
        settings.project_workflow_switch(RECORDING, False)
        _record(store, 'closed')
        store.record_quality('history', passed=True, source='review')
        assert _ids(store) == {'history', 'reopened'}
        with connect(store.database) as connection:
            assert connection.execute("SELECT quality_result FROM harness_quality_labels WHERE execution_id='history'").fetchone()[0] == 'pass'
    finally:
        release.set()
        store.close()


def test_close_waits_for_admitted_short_write_then_blocks_later_writes(monkeypatch):
    """关闭记录等待已接纳写入完成，再阻止后续写入"""
    settings = ExecutionSettings()
    settings.project_workflow_switch(RECORDING, True)
    store = HarnessEffectivenessStore()
    entered, release, requested = threading.Event(), threading.Event(), threading.Event()
    original = store._write_evidence
    def blocked(kind, payload):
        if payload['execution_id'] == 'admitted':
            entered.set()
            assert release.wait(5)
        original(kind, payload)
    monkeypatch.setattr(store, '_write_evidence', blocked)
    def close_event():
        requested.set()
        return settings.project_workflow_switch(RECORDING, False)
    try:
        _record(store, 'admitted')
        assert entered.wait(5)
        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(close_event)
            assert requested.wait(5)
            assert not closing.done()
            release.set()
            assert closing.result(5)['harness_recording_enabled'] is False
        _record(store, 'after-close')
        assert _ids(store) == {'admitted'}
    finally:
        release.set()
        store.close()


def test_disabled_collection_does_not_scan_or_copy_execution_history():
    """关闭采集时跳过执行历史的扫描与复制"""
    settings = ExecutionSettings()
    settings.project_workflow_switch(RECORDING, False)
    class UnusedStore:
        def ensure_schema(self):
            pytest.fail('disabled collection accessed evidence storage')
    assert collect_turn_calls(store=UnusedStore()) == {
        'collected': 0, 'removed': 0, 'skipped_recording_disabled': True,
    }
