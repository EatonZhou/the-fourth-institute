"""TAU reports use; missing observations never become a product verdict."""
import json

from management_portal.test_framework.tau import parse_result
from management_portal.test_framework.records import TERMINAL, aggregate_status, failure_class


def test_report_can_finish_with_blocked_process_and_no_timing_or_screenshot():
    report = {
        "summary": "保存后不能继续编辑",
        "process": {"status": "blocked", "steps": ["创建", "保存"],
                    "stopped_at": "编辑", "reason": "编辑器未恢复"},
        "issues": [{"category": "product", "severity": "blocking", "step": "编辑",
                    "symptom": "刷新后仍不能编辑", "evidence": []}],
        "performance": [], "evidence": [], "evidence_gaps": ["截图未记录", "耗时未记录"],
    }
    result = parse_result("PORTAL_TAU_RESULT: " + json.dumps(report))
    assert result["status"] == "reported"
    assert result["process"] == report["process"]
    assert result["issues"] == report["issues"]
    assert result["evidence_gaps"] == report["evidence_gaps"]


def test_report_keeps_case_tool_and_execution_notes_separate_from_product():
    issues = [{"category": category, "severity": "minor", "step": "准备",
               "symptom": category, "evidence": []}
              for category in ("case", "tool", "execution")]
    result = parse_result("PORTAL_TAU_RESULT: " + json.dumps({
        "process": {"status": "completed"}, "issues": issues,
        "performance": [{"step": "打开", "duration_ms": None, "observation": "未记录"}],
    }))
    assert result["status"] == "reported"
    assert result["issues"] == issues
    assert result["performance"][0]["duration_ms"] is None


def test_report_is_terminal_without_changing_pytest_verdicts():
    assert "reported" in TERMINAL
    assert aggregate_status(["reported", "reported"]) == "reported"
    assert failure_class("reported", None, {}) == ""
    assert aggregate_status(["passed", "failed"]) == "failed"
    assert aggregate_status(["passed"]) == "passed"


def test_missing_material_is_recorded_without_changing_process_or_issues(tmp_path):
    from management_portal.test_framework.runner_adapters import _TauCaseRun

    execution = object.__new__(_TauCaseRun)
    execution.run_dir = tmp_path
    (tmp_path / "notes.json").write_text("{}")
    report = {"status": "reported", "process": {"status": "completed"},
              "issues": [], "evidence": ["missing.png", "notes.json", "../outside"]}
    execution._record_material_gaps(report)
    assert report["status"] == "reported"
    assert report["issues"] == []
    assert report["evidence"] == ["notes.json"]
    assert len(report["evidence_gaps"]) == 2


def test_uncited_material_remains_available_for_usage_report(tmp_path):
    from management_portal.test_framework.runner_adapters import TauAdapter

    adapter = object.__new__(TauAdapter)
    (tmp_path / "observation.json").write_text("{}")
    adapter.retain_observations(run_dir=tmp_path, details={"evidence": []})
    assert (tmp_path / "observation.json").exists()


def test_interrupted_run_gets_three_part_report_without_product_claim():
    from management_portal.test_framework.tau_usage_report import interrupted_report, report_counts

    details = interrupted_report("error", {"summary": "执行器断开", "issues": ["没有收到结构化报告"]})
    assert details["process"]["status"] == "blocked"
    assert details["issues"][0]["category"] == "tool"
    assert details["performance"] == []
    assert details["evidence_gaps"]
    waiting = interrupted_report("unsupported", {"summary": "浏览器不可用"})
    assert waiting["process"]["status"] == "not_started"
    counts = report_counts([{"status": "error", "details": details}, {"status": "unsupported", "details": waiting}])
    assert counts["reported"] == 2
    assert counts["issues"]["product"] == 0


def test_single_observations_are_normalized_to_lists():
    from management_portal.test_framework.tau_usage_report import usage_report

    report = usage_report({"process": {"status": "completed", "steps": "打开"},
                          "performance": {"step": "打开", "duration_ms": 120},
                          "evidence": "screen.png", "evidence_gaps": "截图时间未记录",
                          "issues": {"category": "case", "severity": "minor", "symptom": "步骤已修正", "evidence": "notes.txt"}})
    assert report["process"]["steps"] == ["打开"]
    assert report["performance"] == [{"step": "打开", "duration_ms": 120}]
    assert report["evidence"] == ["screen.png"]
    assert report["issues"][0]["step"] == "未记录"
    assert report["issues"][0]["evidence"] == ["notes.txt"]
