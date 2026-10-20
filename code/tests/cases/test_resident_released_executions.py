"""Released Resident executions cannot keep their old Provider execution alive."""
from unittest.mock import Mock

import pytest

from management_portal.society_service import resident_released_executions as cleanup
from management_portal.society_service.domain_ports import execution_port
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_cross_owner_storage_integration import ACTOR, owners  # noqa: F401
from tests.cases.test_wall04_residual_20260919 import team  # noqa: F401


def _receipt(team, *, execution_id='execution-old', acknowledged=False):
    store = get_lineage_data_store()
    resident_id = team.target['resident_id']
    if not acknowledged:
        store.database.write(lambda c: c.execute(
            "INSERT INTO resident_execution_release_work VALUES (%s,%s,%s)",
            (execution_id, resident_id, ACTOR),
        ))
    return store, resident_id


def _pending(store):
    with store.database.read() as c:
        return c.execute("SELECT count(*) FROM resident_execution_release_work").fetchone()[0]


def test_released_execution_is_cancelled_then_acknowledged_after_worker_finishes(team, monkeypatch):
    """Resident 释放的旧执行仍在运行时只取消一次，进入终态后才确认并清除记录"""
    store, resident_id = _receipt(team)
    service = Mock()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    service.status.return_value = {'status': 'running', 'scheduler': {'agent_id': resident_id}}
    assert cleanup.reconcile() == 0
    service.cancel.assert_called_once_with('execution-old', actor=ACTOR,
                                          reason='Resident released this Execution')
    service.acknowledge.assert_not_called()
    service.status.return_value['scheduler']['cancel_requested'] = True
    assert cleanup.reconcile() == 0
    assert service.cancel.call_count == 1
    service.status.return_value = {'status': 'cancelled'}
    assert cleanup.reconcile() == 1
    service.acknowledge.assert_called_once_with('execution-old', actor=ACTOR)
    assert cleanup.reconcile() == 0
    assert _pending(store) == 0


def test_current_execution_and_acknowledged_receipt_are_excluded(team, monkeypatch):
    """清理已释放执行时跳过 Resident 当前的执行和已确认的回执"""
    store, resident_id = _receipt(team)
    store.put_resident_execution_record(resident_id, {
        'user_message_id': 'input-old',
        'execution_id': 'execution-old', 'actor': ACTOR,
    })
    _receipt(team, execution_id='execution-acked', acknowledged=True)
    monkeypatch.setattr(execution_port, 'service', lambda: pytest.fail('current turn must remain untouched'))
    assert cleanup.reconcile() == 0


def test_transport_failure_retains_cleanup_intent_for_next_scan(team, monkeypatch):
    """查询执行状态失败时保留清理记录，下一轮扫描再处理"""
    store, resident_id = _receipt(team)
    service = Mock()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    service.status.side_effect = ConnectionError('replacing control service')
    assert cleanup.reconcile() == 0
    assert _pending(store) == 1
    service.status.side_effect = None
    service.status.return_value = {'status': 'failed'}
    assert cleanup.reconcile() == 1


def test_mismatched_resident_is_never_cancelled(team, monkeypatch):
    """执行实际归属另一个 Resident 时不取消也不确认，清理记录保留"""
    store, resident_id = _receipt(team)
    service = Mock()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    service.status.return_value = {'status': 'running', 'scheduler': {'agent_id': 'resident_other'}}
    assert cleanup.reconcile() == 0
    service.cancel.assert_not_called()
    service.acknowledge.assert_not_called()
    assert _pending(store) == 1


def test_pruned_execution_settles_without_cancelling_any_provider(team, monkeypatch):
    """执行记录已被清理时清理任务直接结束，不取消也不确认"""
    _receipt(team)
    service = Mock()
    monkeypatch.setattr(execution_port, 'service', lambda: service)
    service.status.side_effect = KeyError('execution-old')
    assert cleanup.reconcile() == 1
    service.cancel.assert_not_called()
    service.acknowledge.assert_not_called()
    assert cleanup.reconcile() == 0
