"""New TAU acceptance is frozen independently of accepted Campaign execution.

:design: decision_c953eab654cd
"""
from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

import pytest

from management_common.operation_errors import OperationError
from management_portal.test_framework.records import RunCoordinator, RunRecords, now_iso
from management_portal.test_framework.runner_adapters import AdapterResult
from management_portal.test_framework.tau_campaign import TauCampaignExecutor
from management_portal.test_framework.tau_limits import tau_max_parallel_modules


class StubAdmissionPaused(OperationError):
    code = "instruction.conflict"


@pytest.fixture
def admission(monkeypatch):
    """Model the storage boundary; persistence and SQL locking have API tests."""
    class Gate:
        paused = False
        held = False
        calls = 0

        @contextmanager
        def scope(self, application):
            assert application == "tau"
            self.calls += 1
            if self.paused:
                raise StubAdmissionPaused("TAU admission is paused")
            self.held = True
            try:
                yield
            finally:
                self.held = False

    gate = Gate()
    module = ModuleType("management_portal.society_service.application_target_configuration")
    module.admission_scope = gate.scope
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return gate


def tau_plan():
    source = "tests/cases/test_tau_admission.py"
    return {
        "executable": True,
        "campaign": "tau",
        "selected": [{
            "case_id": "test_000000000008", "case_title": "resume accepted TAU",
            "module": "operations.test-workbench",
            "implementation": {"id": "impl-admission", "runner": "tau", "source": source},
            "runner": {"id": "tau"},
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }],
        "execution": {"mode": "parallel-modules", "groups": [{
            "group_id": "module:operations.test-workbench",
            "module_id": "operations.test-workbench", "runner": "tau",
            "item_indexes": [0], "continue_on_failure": True,
        }]},
    }


@pytest.mark.parametrize("selection", ["tau", "mixed", "campaign"])
def test_paused_tau_creates_nothing_and_resume_accepts_under_lock(tmp_path, monkeypatch, admission, selection):
    """TAU 准入暂停时不创建任何运行记录，恢复后在锁内创建、锁外启动

    :design-point:"""
    records = RunRecords(tmp_path / "runs.json")
    created_under_lock = []
    launched = []
    create = records.create

    def checked_create(plan, actor):
        assert admission.held
        record = create(plan, actor)
        created_under_lock.append(record["run_id"])
        return record

    def launch(run_id):
        assert not admission.held
        assert records.get(run_id)["status"] == "queued"
        launched.append(run_id)

    monkeypatch.setattr(records, "create", checked_create)
    coordinator = RunCoordinator(records, tmp_path / "artifacts", launch=launch)
    plan = tau_plan()
    if selection == "mixed":
        plan["selected"].insert(0, {"runner": {"id": "pytest"}})
    elif selection == "campaign":
        plan["selected"][0]["runner"]["id"] = "pytest"
    admission.paused = True
    with pytest.raises(StubAdmissionPaused) as failure:
        coordinator.start(plan, "alice")
    assert failure.value.code == "instruction.conflict"
    assert not records.path.exists()
    assert records.run_ids() == set()
    assert created_under_lock == launched == []

    admission.paused = False
    accepted = coordinator.start(plan, "alice")
    assert records.run_ids() == {accepted["run_id"]}
    assert created_under_lock == launched == [accepted["run_id"]]


def test_non_tau_acceptance_does_not_consult_freeze(tmp_path, admission):
    """受理非 TAU 运行时不检查 TAU 准入冻结

    :design-point:"""
    admission.paused = True
    launched = []
    coordinator = RunCoordinator(RunRecords(tmp_path / "runs.json"), tmp_path / "artifacts", launch=launched.append)
    record = coordinator.start({"executable": True, "selected": [{"runner": {"id": "pytest"}}]}, "alice")
    assert launched == [record["run_id"]]
    assert admission.calls == 0


def test_frozen_admission_does_not_cancel_or_duplicate_recovered_campaign(tmp_path, admission):
    """TAU 准入冻结时，重启恢复的运行继续原调用，不取消也不重复

    :design-point:"""
    records = RunRecords(tmp_path / "runs.json")
    accepted = RunCoordinator(records, tmp_path / "artifacts", launch=lambda _: None).start(tau_plan(), "alice")
    run_id = accepted["run_id"]
    invocation = {
        "case_id": "test_000000000008", "resident_id": "resident-stable",
        "lineage_id": "lineage-stable", "message_id": "message-existing",
    }
    records.update(run_id, {
        "status": "running", "started_at": now_iso(),
        "executor": {**accepted["executor"], "pid": 999_999_999},
    })
    records.update_group(run_id, "module:operations.test-workbench", {
        "status": "running", "current_case_id": "test_000000000008",
        "active_invocation": invocation, "case_started_at_epoch": 1000.0,
    })
    admission.paused = True
    launched = []
    restarted = RunCoordinator(RunRecords(records.path), tmp_path / "artifacts", launch=launched.append)
    assert restarted.recover() == [run_id]
    assert restarted.recover() == []
    assert launched == [run_id]

    observed = []

    class ResumingAdapter:
        def execute(self, **kwargs):
            assert not kwargs["cancelled"]()
            observed.append(kwargs["planned"]["_resume_invocation"])
            return AdapterResult(status="passed")

    executor = TauCampaignExecutor(records, tmp_path / "artifacts", ResumingAdapter())
    assert executor.run(run_id) == "passed", records.get(run_id)["error"]
    assert executor.run(run_id) == ""
    assert observed == [invocation]
    assert admission.calls == 1
    assert records.run_ids() == {run_id}
    assert records.get(run_id)["groups"][0]["active_invocation"] == {}
    with pytest.raises(StubAdmissionPaused):
        restarted.start(tau_plan(), "alice")
    assert records.run_ids() == {run_id}


@pytest.mark.parametrize("override,expected", [
    (None, 8), ("invalid", 8), ("0", 1), ("-2", 1), ("33", 32), ("3", 3), ("16", 16),
])
def test_default_eight_preserves_environment_override_and_bounds(monkeypatch, override, expected):
    """TAU 并行模块数默认为 8，环境变量覆盖值限制在 1 到 32 之间

    :design-point:"""
    if override is None:
        monkeypatch.delenv("PORTAL_TEST_TAU_MAX_PARALLEL_MODULES", raising=False)
    else:
        monkeypatch.setenv("PORTAL_TEST_TAU_MAX_PARALLEL_MODULES", override)
    assert tau_max_parallel_modules() == expected
