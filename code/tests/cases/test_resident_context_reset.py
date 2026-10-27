""":design: decision_b820faf42218"""
from __future__ import annotations
from management_portal.society_service import resident_execution_work

from contextlib import nullcontext

import pytest

from management_portal.society_service import resident_domain, resident_record_domain
from tests.cases import permission_test_support
from tests.cases.permission_test_support import Fact
from tests.cases.isa_test_support import run


def _ordinary_resident() -> dict[str, object]:
    return {
        "resident_id": "resident_0123456789ab",
        "kind": "other",
        "session_state": "ready",
    }


@pytest.fixture(autouse=True)
def no_aggregate_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resident_record_domain, "state_lock", lambda _resident_id: nullcontext())


def test_reset_context_checks_isa_permission_and_active_work_contracts(monkeypatch, subtests, isolated_portal_case):
    """清空上下文同时遵循 ISA 权限与当前任务状态

    清空上下文同时遵循 ISA 权限与当前任务状态，授权失败不提交目标状态。
    """
    with subtests.test(contract='test_reset_context_is_available_to_an_ordinary_resident_owner'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            record = _ordinary_resident()
            monkeypatch.setattr(resident_record_domain, 'find_resident', lambda _resident_id: record)
            monkeypatch.setattr(resident_execution_work, 'read', lambda _record: {'state': 'running'})
            monkeypatch.setattr(resident_execution_work, 'is_active', lambda _job: True)
            with pytest.raises(ValueError, match='当前仍有任务'):
                resident_domain.reset_resident_context('resident_0123456789ab', 'sample_admin')
    with subtests.test(contract='test_reset_context_rejects_a_caller_without_contributor'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            monkeypatch.setattr(resident_record_domain, 'find_resident', lambda _resident_id: _ordinary_resident())
            permission_test_support.enable(monkeypatch).place(Fact('resident', 'resident_0123456789ab'))
            denied = run('ClearContext', {'resident_id': 'resident_0123456789ab'}, actor='someone-else')
            assert denied.error['code'] == 'instruction.forbidden'
