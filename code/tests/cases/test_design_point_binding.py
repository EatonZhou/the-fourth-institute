from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import management_portal.test_framework
from management_portal import wall_backend as wall
from management_portal.wall_documents import Source, parse_points
from management_portal.test_framework.catalog import CatalogRepository
from management_portal.test_framework.catalog_source import derived_case_id, explicit_cases
from management_portal.test_framework.models import CatalogError, TestCase as Case


SOURCE = Source(address="decision_8273271b67b1", title="测试设计", domain="测试框架", kind="design")
POINTS = parse_points(SOURCE, "# 测试设计\n\n## 设计点\n\n- **精确点查询**：仅返回本点主测试。\n- **编辑冲突**：拒绝覆盖其他编辑。\n")


def case_record(number: int, point_ref: str | None = None) -> dict:
    return {
        "id": f"test_{number:012x}", "key": f"binding-fixture-{number}",
        "title": f"设计点绑定验证 {number}", "description": "验证点绑定与实现归属。",
        "importance": "P1", "module": "operations.test-framework", "aspects": ["functional"],
        "is_design_point": True, "design_refs": [SOURCE.address], "design_point_ref": point_ref,
        "lifecycle": "active", "implementations": [],
    }


def point_tests(root: Path, *points: tuple[str, str | None, str]) -> dict[str, str]:
    """Tests bound by docstring to the given points; the case ID of each test name."""
    write_tests(root, "".join(
        f'def {name}():\n    """{name} 的标题\n\n    :importance: P1\n'
        + (f"    :design-point: {point}\n" if point else "") + (f"    :lifecycle: {lifecycle}\n" if lifecycle else "")
        + '    """\n\n\n' for name, point, lifecycle in points))
    return {name: derived_case_id(f"tests/cases/test_binding_fixture.py::{name}") for name, _point, _lifecycle in points}


def write_tests(root: Path, text: str) -> None:
    path = root / "tests/cases/test_binding_fixture.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_design_live(root: Path, cases: list[dict]) -> None:
    live_root = root / "tests/cases/design_live"
    live_root.mkdir(parents=True, exist_ok=True)
    (live_root / "binding.json").write_text(json.dumps({"schema": "tfi.portal-design-live-cases.v1", "cases": cases}))


def implementation(name: str, source: str = "tests/cases/test_binding_fixture.py") -> dict:
    return {"id": f"implementation-{name}", "entrypoint": f"{source}::{name}", "source": source,
            "line": 1, "runner": "pytest", "profiles": []}


def direct_live() -> dict:
    return {"mode": "direct", "risk": "read-only", "target": "/wall?view=test", "adapter": "test-binding-fixture",
            "adapter_version": 1, "direct_steps": [{"capability": "verify-binding"}]}


def test_point_binding_round_trips_wall_generated_identity_and_keeps_document_refs_contracts(subtests, isolated_portal_case):
    """设计点标识往返保留来源引用且稳定用例标识不可替换"""
    with subtests.test(contract='test_point_binding_round_trips_wall_generated_identity_and_keeps_document_refs'), isolated_portal_case() as case_path:
        item = case_record(1, POINTS[0]["id"])
        assert Case.parse(item).public()["design_point_ref"] == POINTS[0]["id"]
        assert Case.parse(item).public()["design_refs"] == [SOURCE.address]
        item.pop("design_point_ref")
        assert Case.parse(item).design_point_ref is None
    with subtests.test(contract='a_docstring_binds_its_test_to_one_point_and_keeps_the_point_document'), isolated_portal_case() as case_path:
        write_tests(case_path, f'def test_first():\n    """精确点查询\n\n    :design-point: {POINTS[0]["id"]}\n    """\n\n\ndef test_second():\n    pass\n')
        cases = {case.key.rpartition("::")[2]: case for case in CatalogRepository(root=case_path).all()}
        assert cases["test_first"].design_point_ref == POINTS[0]["id"]
        assert cases["test_first"].is_design_point and cases["test_first"].design_refs == (SOURCE.address,)
        assert cases["test_second"].design_point_ref is None and not cases["test_second"].is_design_point
    with subtests.test(contract='test_explicit_ownership_rejects_replacing_an_existing_stable_case_id'), isolated_portal_case() as case_path:
        write_tests(case_path, "def test_first():\n    pass\n")
        claimed = {**case_record(1, POINTS[0]["id"]), "implementations": [implementation("test_first")], "live": direct_live()}
        write_design_live(case_path, [claimed])
        (case_path / "tests/cases/case_ids.json").write_text(json.dumps({
            "schema": "tfi.test-case-ids.v1", "ids": {claimed["implementations"][0]["entrypoint"]: case_record(2)["id"]}}))
        _cases, errors = CatalogRepository(root=case_path).projection()
        assert any("稳定 ID" in error["error"] for error in errors)


def test_point_binding_rejects_document_only_and_multiple_targets(subtests, isolated_portal_case):
    """设计点绑定拒绝仅文档地址及多个目标"""
    for value in [SOURCE.address, [POINTS[0]["id"]], {}, 42, "#point", "source#", "source#one#two"]:
        with subtests.test(value=value), isolated_portal_case() as case_path:
            with pytest.raises(CatalogError, match="source#point"):
                Case.parse(case_record(1, value))


def test_point_binding_requires_design_classification_and_matching_source(subtests, isolated_portal_case):
    """设计点绑定要求设计分类与匹配的来源文档"""
    for patch in [{"is_design_point": False}, {"design_refs": ["decision_f2a6a5dd4646"]}]:
        with subtests.test(patch=patch), isolated_portal_case() as case_path:
            with pytest.raises(CatalogError, match="来源文档"):
                Case.parse({**case_record(1, POINTS[0]["id"]), **patch})


def test_point_query_never_includes_sibling_points_or_document_only_tests_contracts(tmp_path, subtests, isolated_portal_case):
    """精确点查询排除相邻点并拒绝重复绑定与陈旧编辑"""
    with subtests.test(contract='test_point_query_never_includes_sibling_points_or_document_only_tests'), isolated_portal_case() as case_path:
        ids = point_tests(case_path, ("test_first", POINTS[0]["id"], ""), ("test_second", POINTS[1]["id"], ""), ("test_third", None, ""))
        catalog = CatalogRepository(root=case_path)
        assert [case.id for case in catalog.query({"design_point_ref": POINTS[0]["id"]})] == [ids["test_first"]]
        assert catalog.query({"design_point_ref": POINTS[0]["id"], "importance": ["P0"]}) == ()
        assert [case.id for case in catalog.query({"has_design_point_ref": False})] == [ids["test_third"]]
        with pytest.raises(CatalogError):
            catalog.query({"design_point_ref": SOURCE.address})
    with subtests.test(contract='test_duplicate_primary_tests_are_rejected_even_when_one_is_retired'), isolated_portal_case() as case_path:
        ids = point_tests(case_path, ("test_first", POINTS[0]["id"], ""), ("test_second", POINTS[0]["id"], "retired"), ("test_third", None, ""))
        catalog = CatalogRepository(root=case_path)
        with pytest.raises(CatalogError, match="重复绑定"):
            catalog.all()
        cases, errors = catalog.projection()
        assert [case.id for case in cases] == [ids["test_third"]]
        assert {error["resource_id"] for error in errors} == {ids["test_first"], ids["test_second"]}
        assert all(POINTS[0]["id"] in error["error"] for error in errors)


def test_explicit_live_sources_require_point_binding_and_playable_definition(tmp_path, monkeypatch, subtests, isolated_portal_case):
    """显式 Live 来源要求精确点绑定与可播放定义"""
    for change in [{"design_point_ref": None}, {"live": None}]:
        with subtests.test(change=change), isolated_portal_case() as case_path:
            write_design_live(case_path, [{**case_record(1, POINTS[0]["id"]), "live": direct_live(), **change}])
            with pytest.raises(CatalogError, match="精确点绑定和可播放"):
                explicit_cases(case_path)


def test_design_live_directory_build_claims_implementations_without_duplicate_autocases_contracts(monkeypatch, tmp_path, subtests, isolated_portal_case):
    """显式 Live 实现不重复生成用例且重复绑定报告冲突"""
    with subtests.test(contract='test_design_live_directory_build_claims_implementations_without_duplicate_autocases'), isolated_portal_case() as case_path:
        write_tests(case_path, "def test_first():\n    pass\n\ndef test_second():\n    pass\n")
        first = {**case_record(1, POINTS[0]["id"]), "implementations": [implementation("test_first")], "live": direct_live()}
        write_design_live(case_path, [first])
        cases = CatalogRepository(root=case_path).all()
        assert len(cases) == 2
        assert sum(len(case.implementations) for case in cases) == 2
        claimed = next(case for case in cases if case.id == first["id"])
        assert claimed.design_point_ref == POINTS[0]["id"]
        assert [item.entrypoint for item in claimed.implementations] == [first["implementations"][0]["entrypoint"]]
    with subtests.test(contract='test_wall_reports_duplicate_binding_without_selecting_an_arbitrary_test'), isolated_portal_case() as case_path:
        with monkeypatch.context() as monkeypatch:
            point_tests(case_path, ("test_first", POINTS[0]["id"], ""), ("test_second", POINTS[0]["id"], ""))
            install_test_api(monkeypatch, CatalogRepository(root=case_path))
            result = wall.point_test_binding(POINTS[0]["id"])
            assert result["status"] == "conflict"
            assert "case_id" not in result
            assert "重复绑定" in result["reason"]


@pytest.fixture
def wall_points(monkeypatch, tmp_path):
    monkeypatch.setattr(wall, "_read_state", lambda: {"projects": {"portal": {}}})
    monkeypatch.setattr(wall, "project", lambda key, actor: {"root": str(tmp_path)})
    monkeypatch.setattr(wall, "index_sources", lambda root: [SOURCE])

    def read_point(key, ref, actor):
        point = next((point for point in POINTS if point["id"] == ref), None)
        if point is None:
            raise KeyError("这个点已被修改或移除，请刷新墙面")
        return {}, copy.deepcopy(point), "", SOURCE

    monkeypatch.setattr(wall, "_point", read_point)
    return POINTS


def install_test_api(monkeypatch, catalog):
    def point_case(ref):
        cases = catalog.query({"design_point_ref": ref})
        if len(cases) > 1:
            raise CatalogError(f"设计点 {ref} 存在重复绑定")
        return cases[0].public() if cases else None

    monkeypatch.setattr(management_portal.test_framework, "api", SimpleNamespace(
        case_definition=lambda case_id: catalog.get(case_id).public(), case_for_design_point=point_case,
    ), raising=False)


def test_wall_resolves_exact_point_and_generates_navigable_deep_link(wall_points):
    """WALL 精确解析设计点并生成可导航深链接"""
    binding = wall.resolve_design_point(POINTS[0]["id"], "fixture-user")
    assert binding["status"] == "valid"
    assert binding["title"] == POINTS[0]["title"]
    assert "%23" in binding["url"]
    assert binding["url"].startswith("/wall?project=portal&block=")


def test_removed_point_is_invalid_and_never_falls_back_to_another_point(wall_points):
    """已移除设计点返回无效且不回退到其他点"""
    renamed = parse_points(SOURCE, "## 设计点\n\n- **已移除的测试点**：历史关联。\n")[0]["id"]
    binding = wall.resolve_design_point(renamed, "fixture-user")
    assert binding["status"] == "invalid"
    assert "url" not in binding


def test_missing_registered_workspace_does_not_block_other_bindings(wall_points, monkeypatch, tmp_path):
    """已登记 Workspace 缺失不阻碍其他有效设计点绑定"""
    from management_portal.wall_documents import index_sources

    missing = tmp_path / "removed-test-workspace"
    monkeypatch.setattr(wall, "_read_state", lambda: {"projects": {"removed": {}, "portal": {}}})
    monkeypatch.setattr(wall, "project", lambda key, actor: {"root": str(missing if key == "removed" else tmp_path)})
    monkeypatch.setattr(wall, "index_sources", lambda root: index_sources(root) if root == missing else [SOURCE])
    assert wall.resolve_design_point(POINTS[0]["id"], "fixture-user")["status"] == "valid"
    monkeypatch.setattr(wall, "_read_state", lambda: {"projects": {"removed": {}}})
    assert wall.resolve_design_point(POINTS[0]["id"], "fixture-user")["status"] == "unavailable"


def test_wall_binding_does_not_read_inaccessible_project_sources(wall_points, monkeypatch):
    """WALL 点绑定不读取无权访问的项目来源"""
    def denied(*args):
        raise PermissionError("private project")

    monkeypatch.setattr(wall, "project", denied)
    monkeypatch.setattr(wall, "index_sources", lambda root: pytest.fail("inaccessible project was read"))
    assert wall.resolve_design_point(POINTS[0]["id"], "fixture-user")["status"] == "unavailable"
    with pytest.raises(PermissionError):
        wall.resolve_design_point(POINTS[0]["id"], "")


def test_ambiguous_wall_sources_report_conflict_instead_of_choosing_first_project(wall_points, monkeypatch):
    """WALL 来源跨项目存在歧义时报告冲突"""
    monkeypatch.setattr(wall, "_read_state", lambda: {"projects": {"portal": {}, "other": {}}})
    binding = wall.resolve_design_point(POINTS[0]["id"], "fixture-user")
    assert binding["status"] == "conflict"
    assert "多个 WALL 工程" in binding["reason"]
    assert "url" not in binding


def test_non_design_wall_points_cannot_be_binding_targets(wall_points, monkeypatch):
    """非设计类型的 WALL 点不能作为绑定目标"""
    monkeypatch.setattr(wall, "_point", lambda key, ref, actor: ({}, {**POINTS[0], "kind": "improvement"}, "", SOURCE))
    binding = wall.resolve_design_point(POINTS[0]["id"], "fixture-user")
    assert binding["status"] == "invalid"
    assert "必须是 WALL 设计点" in binding["reason"]


def test_wall_point_returns_its_only_primary_test_and_reports_unbound_siblings(tmp_path, monkeypatch, wall_points):
    """WALL 点仅返回唯一主测试并报告未绑定相邻点"""
    ids = point_tests(tmp_path, ("test_primary", POINTS[0]["id"], ""), ("test_other", None, ""))
    install_test_api(monkeypatch, CatalogRepository(root=tmp_path))
    bound = wall.test_binding({"block": [POINTS[0]["id"]]}, "fixture-user")
    assert bound["test_binding"]["case_id"] == ids["test_primary"]
    assert bound["test_binding"]["url"] == f"/wall?case={ids['test_primary']}"
    sibling = wall.test_binding({"block": [POINTS[1]["id"]]}, "fixture-user")
    assert sibling["test_binding"]["status"] == "unbound"
    assert wall.test_binding({"case_id": [ids["test_other"]]}, "fixture-user")["status"] == "unbound"
