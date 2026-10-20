"""Harness Lineage file handoff, ledger writes and maintenance safety.

:importance: P2
:design: decision_fd760e898f9a, decision_8b510cbd9c58
"""
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from scripts import harness_lineage as flow
from management_portal.execution import harness_effectiveness as effectiveness
from management_portal.execution import harness_evolution_evaluation as evaluation
from management_portal.execution import harness_evolution_offline as offline
from management_portal.execution import harness_evolution_runner as runner
from tests.cases.test_lineage_generic_primitives import primitive_store

BATCH = "script_run_" + "a" * 24


def test_switch_drafts_compile_with_real_isa_without_starting_a_proxy(primitive_store, monkeypatch):
    """Harness 开关草稿经真实 ISA 编译为手动事件接 Effect，状态脚本不调用 RunScript

    :design-point:"""
    from tests.cases.test_backup_lineage import call
    from tests.cases.test_isa_audit_g4 import make_lineage
    from tests.cases import society_service_test_support as support
    support.install_registered_lineage_members(monkeypatch)
    lid = make_lineage()["lineage_id"]
    for key in (flow.RECORDING_SWITCH, flow.TIMING_SWITCH):
        call("ApplyLineageChanges", {"lineage_id": lid, "operations": flow.switch_operations(key)})
        lineage = call("GetLineage", {"lineage_id": lid})["lineage"]
        components = lineage["program"]["components"]
        event = next(c for c in components if c.get("client_key") == key)
        effect = next(c for c in components if c.get("client_key") == key.removesuffix(":enabled") + ":status")
        assert event["config"]["event_type"] == "manual.triggered"
        assert "GetExecutionSettings" in effect["config"]["script"]
        assert "RunScript" not in effect["config"]["script"]
        assert any(w["source"] == event["id"] and w["target"] == effect["id"]
                   for w in lineage["program"]["wires"])


@pytest.fixture
def review_batch(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(evaluation, "sample_for_review", lambda **kw: seen.append(kw) or [
        {"execution_id": "one", "request": "question", "answer": "answer"}, {"execution_id": "two"}])
    paths = flow.sample_review(BATCH, {}, state_dir=tmp_path)
    assert paths["sample_count"] == 2
    assert flow.sample_review(BATCH, {}, state_dir=tmp_path) == paths
    assert len(seen) == 1
    assert seen[0]["until"] - seen[0]["since"] == 86400
    content = {"schema": flow.RESULT_SCHEMA, "batch_id": BATCH,
               "reviews": [{"execution_id": "one", "result": "pass", "reason": "满足请求", "score": 0.9}]}
    root = flow.batch_path(tmp_path, BATCH)
    flow.write_json(root / "results.json", content)
    return root, content


def test_file_handoff_records_only_reviewed_samples_and_retries_without_rewriting(review_batch, tmp_path, monkeypatch):
    """评审文件交接只记录已评审的样本，重试不重复写入，改动结果后再记录被拒绝

    :design-point:"""
    root, content = review_batch
    written = []
    monkeypatch.setattr(effectiveness, "HarnessEffectivenessStore", lambda: SimpleNamespace(
        health=lambda: {"write_failure_count": 0}, close=lambda: None))
    monkeypatch.setattr(evaluation, "record_review", lambda **kw: written.append(kw))
    first = flow.record_batch(BATCH, state_dir=tmp_path)
    assert first["recorded"] == first["skipped"] == 1
    assert written[0]["execution_id"] == "one"
    assert flow.record_batch(BATCH, state_dir=tmp_path) == first
    assert len(written) == 1
    content["reviews"][0]["result"] = "fail"
    flow.write_json(root / "results.json", content)
    with pytest.raises(ValueError, match="different results"):
        flow.record_batch(BATCH, state_dir=tmp_path)


@pytest.mark.parametrize("invalid", [
    {"execution_id": "unrelated", "result": "pass", "reason": "ok"},
    {"execution_id": "two", "result": "unknown", "reason": "ok"},
    {"execution_id": "two", "result": "pass", "reason": ""},
    {"execution_id": "two", "result": "pass", "reason": "ok", "score": 1.1},
    {"execution_id": "one", "result": "fail", "reason": "duplicate"},
])
def test_invalid_batch_is_rejected_before_any_write(review_batch, tmp_path, monkeypatch, invalid):
    """评审结果含无关、非法、空理由、越界分数或重复条目时整批拒绝且不写入任何标注

    :design-point:"""
    root, content = review_batch
    content["reviews"].append(invalid)
    flow.write_json(root / "results.json", content)
    monkeypatch.setattr(evaluation, "record_review", lambda **kw: pytest.fail("invalid batch wrote a label"))
    with pytest.raises(ValueError):
        flow.record_batch(BATCH, state_dir=tmp_path)
    assert not (root / "recorded.json").exists()


def test_database_writer_failure_keeps_batch_retryable(review_batch, tmp_path, monkeypatch):
    """ledger 写入失败时评审批次不标记为已记录，之后可以重试成功

    :design-point:"""
    root, _ = review_batch
    failed = True
    closed = []
    monkeypatch.setattr(effectiveness, "HarnessEffectivenessStore", lambda: SimpleNamespace(
        health=lambda: {"write_failure_count": int(failed)}, close=lambda: closed.append(True)))
    monkeypatch.setattr(evaluation, "record_review", lambda **kw: None)
    with pytest.raises(RuntimeError, match="ledger write failed"):
        flow.record_batch(BATCH, state_dir=tmp_path)
    assert not (root / "recorded.json").exists()
    failed = False
    assert flow.record_batch(BATCH, state_dir=tmp_path)["recorded"] == 1
    assert len(closed) == 2


def test_daily_waits_for_collect_and_keeps_release_guard(tmp_path, monkeypatch):
    """daily 维护等正在运行的 collect 完成后再执行，Release 规则不一致时不生成提议

    :importance: P1
    :design-point:
    """
    collecting, finish_collect, daily_requested = threading.Event(), threading.Event(), threading.Event()
    seen = []
    def collect(**kwargs):
        seen.append("collect")
        collecting.set()
        assert finish_collect.wait(5)
        return {"collected": 3}
    def daily(**kwargs):
        assert finish_collect.is_set()
        assert kwargs == {"no_proposals": True, "auto_promote": False,
            "report_path": str(tmp_path / "latest-report.md"), "status_path": str(tmp_path / "status.json")}
        seen.append("daily")
    def request_daily():
        daily_requested.set()
        return flow.maintain("daily", state_dir=tmp_path)
    monkeypatch.setattr(offline, "collect_turn_calls", collect)
    monkeypatch.setattr(runner, "run_daily", daily)
    monkeypatch.setattr(flow, "release_rules_current", lambda: False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(flow.maintain, "collect", state_dir=tmp_path)
        assert collecting.wait(5)
        second = pool.submit(request_daily)
        assert daily_requested.wait(5)
        assert not second.done()
        finish_collect.set()
        assert first.result()["collected"] == 3
        assert second.result()["proposals_enabled"] is False
    assert seen == ["collect", "daily"]


@pytest.mark.parametrize("returncode,expected", [(0, True), (1, False), (128, False)])
def test_release_difference_or_error_suppresses_proposals(monkeypatch, tmp_path, returncode, expected):
    """与 Release 的规则有差异或比较出错时视为规则不一致

    :importance: P1
    :design-point:
    """
    monkeypatch.setattr(flow, "read_site_config", lambda: {"deployment": {"system_workflow": {"harness_release_tree": str(tmp_path)}}})
    monkeypatch.setattr(flow.subprocess, "run", lambda *args, **kw: SimpleNamespace(returncode=returncode))
    assert flow.release_rules_current() is expected


def test_script_failure_reports_call_path_without_credentials(monkeypatch, capsys, tmp_path):
    """harness_lineage 脚本失败时输出调用路径，不泄露数据库连接凭据

    :design-point:"""
    # main() selects Release imports for its script process; keep that change
    # local to this in-process invocation instead of leaking into later tests.
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("PORTAL_DATABASE_URL", "configured")
    # The isolated database fixture already supplies a valid injected route.
    monkeypatch.setattr(flow, "read_site_config", lambda: {"deployment": {"system_workflow": {
        "harness_recording_lineage_id": "lineage_111111111111", "harness_release_tree": str(tmp_path)}}})
    monkeypatch.setattr(sys, "argv", ["harness_lineage.py", "collect"])
    monkeypatch.setitem(sys.modules, "portal_script", SimpleNamespace(
        inputs=lambda: {"lineage_id": "lineage_111111111111"}, set_output=lambda output: None))
    def fail(*args):
        raise RuntimeError("postgresql://private-secret")
    monkeypatch.setattr(flow, "maintain", fail)
    assert flow.main() == 1
    captured = capsys.readouterr()
    assert "RuntimeError" in captured.err and "in fail" in captured.err
    assert "private-secret" not in captured.err


@pytest.mark.parametrize("operation", ["collect", "daily", "sample", "record"])
def test_effect_uses_script_run_receipt_and_keeps_event_arguments(operation):
    """Harness Effect 调用 RunScript 时传入事件参数并输出 script_run_id 回执

    :design-point:"""
    from management_portal.society_service import lineage_effect_script as script
    source = ('task=$(RunScript path=/scripts/harness_lineage.py argv:=["' + operation + '"] '
              'arguments=${event.payload})\noutput script_run_id=${task.script_run_id}')
    calls = []
    def execute(request):
        calls.append(request)
        return script.CallResult(True, {"script_run_id": BATCH}, {})
    result = script.Interpreter(script.parse(source), {"event": {"payload": {"prompt": BATCH}}}, execute).run()
    assert result.status == "succeeded" and result.outputs == {"script_run_id": BATCH}
    assert len(calls) == 1 and calls[0].instruction == "RunScript"
    assert calls[0].arguments["argv"] == [operation]
    assert calls[0].arguments["arguments"] == {"prompt": BATCH}
