"""
:module: operations.test-workbench
:importance: P1
:aspects: functional, architecture-conformance
:design: decision_c953eab654cd
"""
from __future__ import annotations

import copy
import hashlib
import json
import pytest

from management_portal.test_framework.records import RunRecords
from management_portal.test_framework.runner_adapters import AdapterResult
from management_portal.test_framework.tau_campaign import TauCampaignExecutor
from management_portal.test_framework.tau_cleanup import TauTeamCleanup, team_marker


RUN = "testrun_0123456789abcdef"
MODULE = "operations.test-workbench"


def graph(name, **changes):
    return {"lineage_id": name, "title": team_marker(RUN, MODULE) + " 临时团队",
            "created_by": "test-actor",
            "revision": 3, "nodes": [{"id": "resident-test", "status": "idle"}], **changes}


def test_team_cleanup_preserves_unrelated_active_and_persistent_teams(tmp_path):
    """分页清理只作用于本 Run/模块；异主、运行中及长期模块团队保留并报告。

    分页清理只作用于本 Run/模块；异主、运行中及长期模块团队保留并报告。

    :aspects: functional
    :risk: no-external-side-effects
    """
    graphs = {name: graph(name) for name in ["ok", "foreign", "active", "protected", "other", "unknown", "renamed"]}
    graphs["foreign"]["created_by"] = "alice"
    graphs["active"]["nodes"][0]["status"] = "running"
    graphs["unknown"]["nodes"][0].pop("status")
    graphs["protected"]["nodes"][0]["id"] = "resident-module"
    graphs["other"]["title"] = team_marker(RUN, "resident.conversation")
    rows = [{"resource_id": name, "title": g["title"]} for name, g in graphs.items()]
    graphs["renamed"]["title"] = "已改为长期用途"
    calls = []

    def query(**kw):
        # Deliberately split pages even though the consumer allows larger pages.
        return {"resources": rows[kw["offset"]:kw["offset"] + 2], "total": len(rows)}

    def instruction(name, arguments, *, key="", version=None):
        lid = arguments["lineage_id"]
        if name == "GetLineage":
            return {"lineage": copy.deepcopy(graphs[lid])}
        calls.append(lid)
        del graphs[lid]
        return {"lineage_id": lid, "deleted_resident_ids": ["resident-test"]}

    result = TauTeamCleanup(query=query, instruction=instruction).run(
        run_id=RUN, module_id=MODULE, run_dir=tmp_path,
        protected_lineages=set(), protected_residents={"resident-module"}, emit=lambda _: None,
    )
    assert calls == ["ok"]
    assert result["status"] == "error"
    assert len(result["errors"]) == 5
    assert "other" not in result["teams"]
    assert result["teams"]["ok"]["status"] == "deleted"


def test_team_cleanup_recovers_exact_request_and_verifies_deletion(tmp_path, subtests):
    """丢失回执时恢复相同删除请求；成功回执不能替代实际不存在核验。

    丢失回执时恢复相同删除请求；成功回执不能替代实际不存在核验。

    :aspects: functional
    :risk: no-external-side-effects
    """
    for mode in ("before_commit", "after_commit", "false_success"):
        with subtests.test(mode=mode):
            root = tmp_path / mode
            graphs = {"team": graph("team")}
            calls = []
            def query(**_kw):
                # Simulate a stale search index after deletion.
                return {"resources": [{"resource_id": "team", "title": graph("team")["title"]}], "total": 1}
            def instruction(name, arguments, *, key="", version=None):
                lid = arguments["lineage_id"]
                if name == "GetLineage":
                    return {"lineage": copy.deepcopy(graphs[lid])}
                calls.append({"arguments": dict(arguments), "key": key})
                if len(calls) == 1:
                    if mode == "after_commit":
                        del graphs[lid]
                    if mode == "false_success":
                        return {"accepted": True}
                    raise ConnectionError("lost response")
                del graphs[lid]
                return {"deleted": True}
            def run():
                return TauTeamCleanup(query=query, instruction=instruction).run(
                    run_id=RUN, module_id=MODULE, run_dir=root,
                    protected_lineages=set(), protected_residents=set(), emit=lambda _: None,
                )
            first = run()
            assert first["status"] == "error"
            if "team" in graphs:
                graphs["team"]["revision"] = 4
            second = run()
            assert second["status"] == "complete"
            assert len(calls) == (1 if mode == "after_commit" else 2)
            assert all(request == calls[0] for request in calls)
            assert calls[0]["key"] == f"tau-cleanup:{RUN}:{MODULE}:team"
            assert json.loads((root / "team-cleanup.json").read_text())["status"] == "complete"


def test_module_finalization_runs_on_all_exits_and_resumes_without_retesting(tmp_path, subtests):
    """成功、失败、异常、取消及重启均执行模块回收；清理失败不能报通过。

    成功、失败、异常、取消及重启均执行模块回收；清理失败不能报通过。

    :aspects: functional
    :risk: no-external-side-effects
    """
    for mode in ("passed", "failed", "exception", "cancelled", "cleanup_error", "cleanup_exception", "resume", "mixed"):
        with subtests.test(mode=mode):
            root = tmp_path / mode
            records = RunRecords(root / "runs.json")
            source = "tests/cases/test_tau_team_cleanup.py"
            from management_portal.test_framework.catalog import ROOT
            selected = [{"case_id": "test_000000000009", "case_title": "回收流程", "module": MODULE,
                         "runner": {"id": "tau"}, "source_sha256": hashlib.sha256((ROOT / source).read_bytes()).hexdigest(),
                         "implementation": {"id": "fixture", "source": source}}]
            group = {"group_id": "module:" + MODULE, "runner": "tau", "module_id": MODULE,
                     "item_indexes": [0], "continue_on_failure": True}
            if mode == "mixed":
                group.update(runner="mixed", module_id="", group_id="serial")
                selected.append({**selected[0], "module": "resident.conversation"})
                group["item_indexes"] = [0, 1]
            run = records.create({"executable": True, "campaign": "tau", "selected": selected,
                                  "execution": {"groups": [group]}}, "alice")
            finalized = []
            class Adapter:
                def execute(self, **_kw):
                    assert mode != "resume"
                    if mode == "exception":
                        raise RuntimeError("provider unavailable")
                    if mode == "cancelled":
                        # Web persists the cancellation while the case runs.
                        records.request_cancel(run["run_id"])
                        return AdapterResult(status="cancelled")
                    return AdapterResult(status="failed" if mode == "failed" else "passed")
                def finalize_group(self, **kw):
                    assert records.get(run["run_id"])["groups"][0]["status"] == "running"
                    finalized.append(kw["module_id"])
                    assert kw["lineage_fixtures"] == {}
                    if mode == "cleanup_exception":
                        raise ConnectionError("discovery offline")
                    return {"status": "error" if mode == "cleanup_error" else "complete", "errors": ["blocked"] if mode == "cleanup_error" else []}
            if mode == "resume":
                records.append_result(run["run_id"], group["group_id"], {
                    "item_index": 0, "status": "passed", "case_id": selected[0]["case_id"],
                })
                records.update(run["run_id"], {"status": "running", "executor": {**run['executor'], "pid": 999_999_999}})
                records.update_group(run["run_id"], group["group_id"], {"cleanup": {"status": "running"}})
            TauCampaignExecutor(records, root / "artifacts", Adapter()).run(run["run_id"])
            record = records.get(run["run_id"])
            assert finalized == ([MODULE, "resident.conversation"] if mode == "mixed" else [MODULE])
            assert record["status"] == {"exception": "error", "cleanup_error": "error", "cleanup_exception": "error", "resume": "passed", "mixed": "passed"}.get(mode, mode)
            assert record["report"]["modules"][0]["cleanup"]["status"] == ("error" if mode.startswith("cleanup_") else "complete")
            if mode == "exception":
                assert "provider unavailable" in record["error"]


def test_module_member_cleanup_preserves_team_executor_and_recovers_receipts(tmp_path, subtests):
    """成员样本独立回收且保留固定图；重试、移动、运行中及身份保护均不扩大删除。

    成员样本独立回收且保留固定图；重试、移动、运行中及身份保护均不扩大删除。

    :design-point:
    :risk: no-external-side-effects
    """
    for mode in ("normal", "by_executor", "before_commit", "after_commit", "false_success", "moved", "active", "foreign", "renamed", "shared_team"):
        with subtests.test(mode=mode):
            root = tmp_path / mode
            titles = {rid: team_marker(RUN, MODULE) + rid for rid in ("sample", "executor")}
            titles["other-run"] = team_marker("testrun_aaaaaaaaaaaaaaaa", MODULE)
            members = {rid: {"resident_id": rid, "lineage_id": "fixed", "title": title,
                             "created_by": "test-actor"}
                       for rid, title in titles.items()}
            g = graph("fixed", title="固定模块", leader_resident_id="executor",
                      nodes=[{"id": rid, "title": t, "status": "idle"} for rid, t in titles.items()])
            if mode == "moved": members["sample"]["lineage_id"] = "elsewhere"
            if mode == "renamed": members["sample"]["title"] = "已改为长期用途"
            if mode == "shared_team":
                g.update(created_by="sample_admin")
            if mode == "foreign": members["sample"]["created_by"] = "alice"
            # The module's TAU Resident created the sample with its own hatg_instruction tool.
            if mode == "by_executor": members["sample"]["created_by"] = "executor"
            if mode == "active": g["nodes"][0]["status"] = "running"
            calls = []
            def instruction(name, arguments, *, key="", version=None):
                if name == "GetLineage": return {"lineage": copy.deepcopy(g)}
                rid = arguments["resident_id"]
                if name == "GetResident":
                    # The HATG DTO intentionally does not expose creation
                    # provenance.
                    if rid not in members:
                        raise KeyError(rid)
                    return {"resident": {k: members[rid][k] for k in ("resident_id", "lineage_id", "title")}}
                assert name == "DeleteResident" and rid == "sample"
                calls.append({"arguments": dict(arguments), "key": key})
                if len(calls) == 1 and mode == "before_commit":
                    raise ConnectionError("lost request")
                if len(calls) == 1 and mode == "false_success":
                    return {"accepted": True}
                del members[rid]
                g["nodes"] = [n for n in g["nodes"] if n["id"] != rid]
                g["revision"] += 1
                if len(calls) == 1 and mode == "after_commit":
                    raise ConnectionError("lost reply")
                return {"deleted_resident": {"resident_id": rid, "deleted": True}}
            def run():
                return TauTeamCleanup(query=lambda **kw: {"resources": [], "total": 0}, instruction=instruction,
                    release_member=lambda lid, rid, key: instruction('DeleteResident', {"lineage_id": lid, 'resident_id': rid}, key=key),
                    resource=lambda rid: {"resource_id": rid, "resource_type": "resident",
                        "attributes": {"created_by": members[rid]["created_by"]}},
                ).run(
                    run_id=RUN, module_id=MODULE, run_dir=root, emit=lambda _: None,
                    protected_lineages={"fixed"}, protected_residents={"executor"},
                    resident_lineages={"fixed"}, lineage_fixtures={},
                )
            first = run()
            # An executor with a stray test marker is never deleted, and the conflict is reported.
            assert first["residents"]["executor"]["status"] == "error"
            if mode in {"before_commit", "after_commit", "false_success"}:
                assert first["residents"]["sample"]["status"] == "error"
                second = run()
                assert second["residents"]["sample"]["status"] == "deleted"
                assert len(calls) == (1 if mode == "after_commit" else 2)
                assert all(c == calls[0] for c in calls)
            elif mode not in {"normal", "shared_team", "by_executor"}:
                assert not calls and first["residents"]["sample"]["status"] == "error"
            else:
                assert len(calls) == 1 and first["residents"]["sample"]["status"] == "deleted"
            assert "executor" in members and "other-run" in members
            assert g["title"] == "固定模块"


def test_undeclared_team_is_reclaimed_but_still_reports_policy_error(tmp_path):
    """意外建队不能因回收成功而掩盖违反默认复用策略。

    意外建队不能因回收成功而掩盖违反默认复用策略。

    :design-point:
    :risk: no-external-side-effects
    """
    graphs = {"unexpected": graph("unexpected")}
    def instruction(name, arguments, *, key="", version=None):
        lid = arguments["lineage_id"]
        if name == "GetLineage": return {"lineage": graphs[lid]}
        assert name == "DeleteLineage"
        del graphs[lid]
        return {"deleted": True}
    result = TauTeamCleanup(
        query=lambda **kw: {"resources": [{"resource_id": "unexpected", "title": graph("unexpected")["title"]}], "total": 1},
        instruction=instruction,
    ).run(run_id=RUN, module_id=MODULE, run_dir=tmp_path, emit=lambda _: None,
          protected_lineages=set(), protected_residents=set(), lineage_fixtures={})
    assert not graphs
    assert result["status"] == "error" and "未声明" in result["errors"][0]


def test_lineage_fixture_contract_defaults_to_reuse_and_survives_planning(tmp_path, subtests):
    """独立图例外须显式说明必要性并冻结到计划；无声明的用例继续使用固定团队。

    独立图例外须显式说明必要性并冻结到计划；无声明的用例继续使用固定团队。

    :design-point:
    :risk: no-external-side-effects
    """
    from management_portal.test_framework.catalog import ROOT, CatalogRepository
    from management_portal.test_framework.models import Implementation, CatalogError, TestCase as Case
    from management_portal.test_framework.planner import RunPlanner
    from management_portal.test_framework.registry import RunnerRegistry
    from management_portal.test_framework.tau import build_prompt, TauConfig
    from types import SimpleNamespace

    raw = {"id": "impl-fixture", "runner": "tau", "source": "tests/cases/tau_cases.json",
           "entrypoint": "tau:fixture", "risk": "external-side-effects"}
    assert "lineage_fixture" not in Implementation.parse(raw, "test").public()
    for invalid in (None, True, {}, [], 1, "", "   "):
        with subtests.test(invalid=invalid), pytest.raises(CatalogError):
            Implementation.parse({**raw, "lineage_fixture": invalid}, "test")
    for changes in ({"runner": "pytest"}, {"risk": "no-external-side-effects"}):
        with subtests.test(changes=changes), pytest.raises(CatalogError):
            Implementation.parse({**raw, "lineage_fixture": "建队生命周期", **changes}, "test")
    source = json.loads((ROOT / raw["source"]).read_text())["cases"]
    compiled = {c.id: c for c in CatalogRepository().query({"case_ids": [c["id"] for c in source]})}
    for definition in source:
        assert Case.parse(definition).implementations == compiled[definition["id"]].implementations
    case = next(Case.parse(c) for c in source if c["key"] == "agent-use.design.hatg-fork-effect")
    class Catalog:
        def query(self, _selection): return [case]
    planner = RunPlanner(Catalog(), RunnerRegistry(tau_available=True))
    plan = planner.preview({"implementation": {"runners": ["tau"]}})
    selected = plan["selected"][0]
    assert selected["implementation"]["lineage_fixture"] == case.implementations[0].lineage_fixture
    from dataclasses import replace
    case = replace(case, implementations=(replace(case.implementations[0], lineage_fixture=""),))
    # A changed catalog must not silently execute a different source contract.
    with pytest.raises(CatalogError, match="目录不一致"):
        planner.preview({"implementation": {"runners": ["tau"]}})
    assert selected["implementation"]["lineage_fixture"]
    target = SimpleNamespace(instance_id="dev", origin="http://dev", page_fingerprint="fp", actor="test-actor")
    prompt = build_prompt(selected, TauConfig(tmp_path / "state.json"), tmp_path, target, RUN)
    assert "本用例允许独立临时 Lineage" in prompt
    assert selected["implementation"]["lineage_fixture"] in prompt
    default = copy.deepcopy(selected)
    default["implementation"].pop("lineage_fixture")
    prompt = build_prompt(default, TauConfig(tmp_path / "state.json"), tmp_path, target, RUN)
    assert "本用例没有独立团队声明" in prompt
    assert "产品正常创建的业务成员遵循产品绑定" in prompt
    assert "DeleteResident" in prompt and "临时 Resident 放在本模块的临时团队中" not in prompt
