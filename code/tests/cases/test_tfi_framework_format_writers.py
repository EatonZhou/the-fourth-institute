"""测试框架读写均使用 TFI 标签，保留有效业务内容。

:module: operations.test-framework
:risk: no-external-side-effects
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from management_portal.test_framework import live_adapter, runner_adapters, service, tau_campaign
from management_portal.test_framework.catalog_source import read_case_ids
from management_portal.test_framework.planner import RunPlanner
from management_portal.test_framework.records import RunCoordinator, RunRecords
from management_portal.test_framework.tau_residents import TauResidentAssignments
from management_portal.test_framework.tools import sync_catalog


RUN_ID = "testrun_0123456789abcdef"
CASE_ID = "test_0123456789ab"


def test_tfi_framework_format_writers_run_index_keeps_history(tmp_path):
    """规范索引读取不回写，新写入保留原运行内容。"""
    path = tmp_path / "runs.json"
    rows = [{"run_id": RUN_ID, "status": "failed", "summary": "original failure",
             "idempotency_key": "original-key", "created_by": "fixture-user"}]
    path.write_text(json.dumps({"schema": f"tfi.test-run-index.v1", "runs": rows}))
    original = path.read_bytes()
    records = RunRecords(path)
    assert records._read_index() == rows
    assert path.read_bytes() == original
    records._write_index(rows)
    assert json.loads(path.read_bytes()) == {"schema": "tfi.test-run-index.v1", "runs": rows}


def test_tfi_framework_format_writers_deletion_receipt(tmp_path):
    """删除结果标签切换但运行 ID、数量和警告语义保持。"""
    coordinator = RunCoordinator.__new__(RunCoordinator)
    coordinator.records = SimpleNamespace(delete_many=lambda ids: [{"run_id": item} for item in ids])
    coordinator.run_root = tmp_path
    with pytest.raises(PermissionError):
        coordinator.delete_reports([RUN_ID], "")
    assert coordinator.delete_reports([RUN_ID], "fixture-user") == {
        "schema": "tfi.test-run-deletion.v1", "deleted_run_ids": [RUN_ID],
        "deleted_count": 1, "artifact_warnings": [],
    }


@pytest.mark.parametrize("method,tag", [
    ("query", "test-framework.v2"), ("query_workspace", "test-workspace-catalog.v1"),
    ("query_case_ids", "test-catalog-query.v1"),
])
def test_tfi_framework_format_writers_catalog_responses(method, tag):
    """目录查询输出 TFI 标签并保留用例身份及投影。"""
    case_data = {"id": CASE_ID, "title": "fixture", "module": "operations.test-framework"}
    case = SimpleNamespace(**case_data, public=lambda: dict(case_data), workspace_summary=lambda: {"id": CASE_ID})
    instance = service.TestFrameworkService.__new__(service.TestFrameworkService)
    instance.catalog = SimpleNamespace(query=lambda _: [case], projection=lambda: ([case], []),
                                       summary=lambda _: {"fixture": True})
    instance.registry = SimpleNamespace(public=lambda: [{"id": "pytest", "available": True}])
    instance.tau_residents = SimpleNamespace(list=lambda _: [])
    result = getattr(instance, method)({})
    assert result["schema"] == f"tfi.{tag}"
    if method == "query_case_ids":
        assert result == {"schema": f"tfi.{tag}", "case_ids": [CASE_ID], "count": 1}
    else:
        assert result["cases"] == ([case_data] if method == "query" else [{"id": CASE_ID}])
        assert result["summary"] == {"fixture": True}
        assert result["recent_runs"] == [] and result["catalog_errors"] == []


def test_tfi_framework_format_writers_plan_identity():
    """预览格式切换不参与计划 ID 摘要，也不启动运行。"""
    planner = RunPlanner(SimpleNamespace(query=lambda _: []), SimpleNamespace())
    request = {"select": {"case_ids": [CASE_ID]}, "implementation": {"fail_fast": True}}
    original = deepcopy(request)
    result = planner.preview(request)
    assert result["schema"] == "tfi.test-run-plan.v2"
    identity = {"selection": request["select"], "implementation": request["implementation"],
                "selected": [], "execution": {"mode": "batch", "groups": []}}
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    assert result["plan_id"] == f"plan-{expected}"
    assert not result["executable"] and result["campaign"] == ""
    assert request == original


def test_tfi_framework_format_writers_report_keeps_failed_and_budget():
    """报告投影保留原失败和预算，不创建 Campaign。"""
    record = {"run_id": RUN_ID, "status": "failed", "results": [{"status": "failed", "failure_class": "product"}],
              "groups": [], "plan": {"execution": {"mode": "parallel-modules", "max_parallel_modules": 3}}}
    original = deepcopy(record)
    result = tau_campaign.tau_report(record)
    assert result["schema"] == "tfi.portal-tau-report.v1"
    assert result["status"] == "failed" and result["campaign_id"] == RUN_ID
    assert result["summary"]["failed"] == 1 and result["summary"]["failure_classes"] == {"product": 1}
    assert result["execution"] == {"mode": "parallel-modules", "max_parallel_modules": 3,
                                   "account": "test-actor", "target": "DEV"}
    assert record == original


def test_tfi_framework_format_writers_assignment_preserves_existing_slots(tmp_path):
    """临时映射读写使用 TFI，现有槽位读取不回写、不换身份。"""
    path = tmp_path / "tau-residents.json"
    mapping = TauResidentAssignments(path)
    assert mapping._read() == {"schema": "tfi.portal-tau-residents.v1", "assignments": {}}
    assert not path.exists()
    existing = {"slot_id": "fixed-slot", "resident_id": "resident_0123456789ab", "actor": "test-actor"}
    path.write_text(json.dumps({"schema": f"tfi.portal-tau-residents.v1",
                                "assignments": {"operations.test-framework": existing}}))
    original = path.read_bytes()
    assert mapping.assignment("operations.test-framework") == existing
    assert path.read_bytes() == original
    for invalid in ("archive.portal-tau-residents.v1", "tfi.portal-tau-residents.v2", None):
        path.write_text(json.dumps({"schema": invalid, "assignments": {"operations.test-framework": existing}}))
        invalid_bytes = path.read_bytes()
        with pytest.raises(RuntimeError, match="结构无效"):
            mapping.assignment("operations.test-framework")
        assert path.read_bytes() == invalid_bytes
    path.write_bytes(original)
    added = mapping.assignment("operations.test-workbench")
    written = json.loads(path.read_text())
    assert written["schema"] == "tfi.portal-tau-residents.v1"
    assert written["assignments"]["operations.test-framework"] == existing
    assert written["assignments"]["operations.test-workbench"] == added
    assert added["actor"] == "test-actor" and added["resident_id"] == ""


def test_tfi_framework_format_writers_catalog_files(tmp_path, monkeypatch):
    """临时目录导出与 ID 写入切换标签，不改 ID 和定义正文。"""
    root = tmp_path / "source"
    (root / "tests/cases").mkdir(parents=True)
    ids = {"tests/cases/test_fixture.py::test_fixture": CASE_ID}
    sync_catalog.apply({"case_ids": ids, "orphan_live": []}, root)
    path = root / "tests/cases/case_ids.json"
    assert json.loads(path.read_text()) == {"schema": "tfi.test-case-ids.v1", "ids": ids}
    before = path.read_bytes()
    assert read_case_ids(root) == ids and path.read_bytes() == before
    case = {"id": CASE_ID, "title": "unchanged definition"}
    monkeypatch.setattr(sync_catalog, "CatalogRepository", lambda **_: SimpleNamespace(
        projection=lambda: ([SimpleNamespace(public=lambda: dict(case))], [])))
    export = tmp_path / "catalog.json"
    assert sync_catalog.export(export, root) == 1
    assert json.loads(export.read_text()) == {"schema": "tfi.test-catalog.v2", "cases": [case]}


def test_tfi_framework_format_writers_live_descriptor(tmp_path, monkeypatch):
    """Live 描述只换外层标签，演示摘要继续由原 Live 内容生成。"""
    live = {"mode": "sandbox", "target": "/fixture", "steps": [{"action": "inspect"}]}
    case = SimpleNamespace(id=CASE_ID, title="fixture", live=live)
    monkeypatch.setattr(live_adapter.api, "service", lambda: SimpleNamespace(catalog=SimpleNamespace(get=lambda _: case)))
    result = live_adapter.demo_descriptor(CASE_ID)
    assert result == {"schema": "tfi.portal-live-tour.v1", "case_id": CASE_ID, "title": "fixture",
                      "revision": hashlib.sha256(json.dumps(live, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24],
                      "demo": live}


@pytest.mark.parametrize("safe", [False, True])
def test_tfi_framework_format_writers_checkpoint(tmp_path, safe):
    """执行原 checkpoint 写入，保留内层原句柄与清理状态。"""
    # Only the case run's writer: executing the case would verify live DEV
    # and invoke a Resident, outside this unit contract.
    case_run = runner_adapters._TauCaseRun.__new__(runner_adapters._TauCaseRun)
    case_run.run_dir = tmp_path
    handle = {"schema": "tfi.fixture-handle.v1", "message_id": "msg_original", "idempotency_key": "original-key"}
    original = deepcopy(handle)
    case_run.save_checkpoint(handle, safe=safe)
    path = tmp_path / "tau-invocation.json"
    assert json.loads(path.read_text()) == {"schema": "tfi.tau-invocation-checkpoint.v1",
                                           "handle": handle, "safe_to_cleanup": safe}
    assert runner_adapters.TauAdapter.cleanup_ready(run_dir=tmp_path) is safe
    assert handle == original


def test_tfi_framework_format_writers_cleanup_receipt(tmp_path, monkeypatch):
    """规范 checkpoint 生成清理回执，不操作真实进程或团队。"""
    from management_portal.test_framework import tau_fixtures
    checkpoint = tmp_path / "tau-invocation.json"
    checkpoint.write_text(json.dumps({"schema": f"tfi.tau-invocation-checkpoint.v1", "safe_to_cleanup": True}))
    before = checkpoint.read_bytes()
    monkeypatch.setattr(runner_adapters, "_terminate_artifact_helpers", lambda _: [])
    monkeypatch.setattr(runner_adapters, "_artifact_helper_targets", lambda _: set())
    monkeypatch.setattr(tau_fixtures, "cleanup", lambda _: {"complete": True, "fixture_only": True})
    adapter = runner_adapters.TauAdapter.__new__(runner_adapters.TauAdapter)
    for invalid in ("archive.tau-invocation-checkpoint.v1", "tfi.tau-invocation-checkpoint.v2", None):
        checkpoint.write_text(json.dumps({"schema": invalid, "safe_to_cleanup": True}))
        invalid_bytes = checkpoint.read_bytes()
        assert not adapter.cleanup_ready(run_dir=tmp_path)
        with pytest.raises(RuntimeError, match="尚未确认终止"):
            adapter.cleanup(run_dir=tmp_path, emit=lambda _: None)
        assert checkpoint.read_bytes() == invalid_bytes
        assert not (tmp_path / "framework-helper-cleanup.json").exists()
    checkpoint.write_bytes(before)
    receipt = adapter.cleanup(run_dir=tmp_path, emit=lambda _: None)
    assert receipt["schema"] == "tfi.portal-tau-helper-cleanup.v1"
    assert receipt["complete"] and receipt["remaining_pids"] == receipt["terminated_pids"] == []
    assert json.loads((tmp_path / "framework-helper-cleanup.json").read_text()) == receipt
    assert checkpoint.read_bytes() == before


def test_tfi_framework_format_writers_javascript():
    """Node 验证观察日志与 ISA 请求标签，不启动浏览器或连接服务。"""
    source = Path(__file__).with_name("javascript") / "test_tfi_framework_format_writers.cjs"
    result = subprocess.run(["node", "--test", str(source)], capture_output=True, text=True, timeout=30)
    print(result.stdout)
    assert result.returncode == 0, result.stdout + result.stderr
