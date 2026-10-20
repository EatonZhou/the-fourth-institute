"""A durable invocation keeps its module when the executor's lock disappears."""
import threading

import pytest

from management_portal.test_framework.records import RunRecords
from management_portal.test_framework.tau_campaign import TauCampaignExecutor


def campaign(records, module):
    group = {"group_id": "module:" + module, "module_id": module}
    plan = {"selected": [], "execution": {"groups": [group]}}
    run_id = records.create(plan, "alice")["run_id"]
    records.update(run_id, {"status": "running"})
    return run_id, group


@pytest.mark.parametrize("same_module", [True, False])
def test_saved_invocation_resumes_before_fresh_campaigns(tmp_path, monkeypatch, same_module):
    """保存了调用的 TAU 运行先于新运行恢复，同模块的新运行等它结束"""
    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path / "slots"))
    records = RunRecords(tmp_path / "runs.json")
    original, group = campaign(records, "resident.chat")
    fresh, fresh_group = campaign(records, "resident.chat" if same_module else "paper")
    records.update_group(original, group["group_id"], {
        "status": "queued", "active_invocation": {"message_id": "original-message"},
    })
    executor = TauCampaignExecutor(records, tmp_path / "artifacts", object())
    entered_original, entered_fresh, release = threading.Event(), threading.Event(), threading.Event()
    errors = []

    def execute(run_id, group_spec, *_args):
        if run_id == original:
            records.update_group(original, group["group_id"], {"status": "running"})
            entered_original.set()
            assert release.wait(5)
            records.update_group(original, group["group_id"], {"active_invocation": {}})
        else:
            assert entered_original.is_set()
            entered_fresh.set()
        return "passed", ""

    monkeypatch.setattr(executor, "_execute_group_in_slot", execute)

    def run(run_id, spec):
        try:
            executor._execute_group_with_slot(run_id, spec, [], tmp_path, "alice")
        except BaseException as error:
            errors.append(error)

    fresh_thread = threading.Thread(target=run, args=(fresh, fresh_group))
    original_thread = threading.Thread(target=run, args=(original, group))
    fresh_thread.start()
    try:
        assert not entered_fresh.wait(0.1)
        original_thread.start()
        assert entered_original.wait(2)
        if same_module:
            assert not entered_fresh.wait(0.1)
        else:
            assert entered_fresh.wait(2)
    finally:
        release.set()
        original_thread.join(3)
        fresh_thread.join(3)
    assert entered_fresh.is_set()
    assert not original_thread.is_alive() and not fresh_thread.is_alive()
    assert errors == []


def test_cancelled_saved_invocation_still_reaches_the_withdrawal_path(tmp_path, monkeypatch):
    """已取消但保存了调用的 TAU 运行仍进入撤回调用的流程"""
    monkeypatch.setenv("PORTAL_TEST_SLOT_ROOT", str(tmp_path / "slots"))
    records = RunRecords(tmp_path / "runs.json")
    run_id, group = campaign(records, "resident.chat")
    records.update_group(run_id, group["group_id"], {
        "status": "queued", "active_invocation": {"message_id": "original-message"},
    })
    executor = TauCampaignExecutor(records, tmp_path / "artifacts", object())
    monkeypatch.setattr(executor, "_is_cancelled", lambda _run_id: True)
    calls = []
    monkeypatch.setattr(executor, "_execute_group_in_slot", lambda *args: calls.append(args[0]) or ("cancelled", ""))
    assert executor._execute_group_with_slot(run_id, group, [], tmp_path, "alice") == ("cancelled", "")
    assert calls == [run_id]


def test_terminal_runs_do_not_reserve_modules(tmp_path):
    """已结束的 TAU 运行不再占用模块"""
    records = RunRecords(tmp_path / "runs.json")
    run_id, group = campaign(records, "resident.chat")
    records.update_group(run_id, group["group_id"], {"active_invocation": {"message_id": "finished"}})
    records.update(run_id, {"status": "failed"})
    assert records.invocation_holders() == {}
