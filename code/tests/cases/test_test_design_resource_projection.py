from types import SimpleNamespace


from management_portal.resources.providers import test as test_provider
from management_portal.test_framework.resource_index import TestCatalogAdapter as CatalogAdapter


def test_test_resource_projects_exact_point_without_inferring_from_document(subtests, isolated_portal_case):
    """Test 资源记录只投影声明的设计点，不从设计文档推断"""
    for point_ref in ["", "decision_0123456789ab#binding-contract"]:
        with subtests.test(point_ref=point_ref), isolated_portal_case() as case_path:
            case = projected_case(point_ref)
            record = test_provider.test_record(case)
            assert record["design_point_ref"] == point_ref
            assert record["design_refs"] == ["decision_0123456789ab"]


def test_test_index_preserves_binding_and_indexes_the_exact_point(monkeypatch, subtests, isolated_portal_case):
    """Test 资源索引保留设计点绑定，并可按精确设计点检索"""
    for point_ref in ["", "decision_0123456789ab#binding-contract"]:
        with subtests.test(point_ref=point_ref), isolated_portal_case() as case_path:
            with monkeypatch.context() as monkeypatch:
                case = projected_case(point_ref)
                repository = SimpleNamespace(projection=lambda: ([case], []))
                monkeypatch.setattr(test_provider, "catalog_repository", lambda: repository)
                records, details = CatalogAdapter().scan()
                assert details["resource_errors"] == []
                assert len(records) == 1
                assert records[0]["attributes"]["design_point_ref"] == point_ref
                if point_ref:
                    assert point_ref in records[0]["search_terms"]


def test_test_resources_belong_to_the_configured_portal_projects(monkeypatch, tmp_path):
    """Test 资源归属本站登记的 Portal 开发 Project

    记录与索引的 project_ids 取自 deployment.development_projection.project_ids；未登记时为空，不写死某个站点的 Project。
    """
    import json
    case = projected_case("")
    repository = SimpleNamespace(projection=lambda: ([case], []))
    monkeypatch.setattr(test_provider, "catalog_repository", lambda: repository)
    site = tmp_path / "site.json"
    for projects in (["portal-project"], None):
        site.write_text(json.dumps({"deployment": {"development_projection": {"project_ids": projects}} if projects else {}}))
        monkeypatch.setenv("PORTAL_SITE_CONFIG", str(site))
        records, _details = CatalogAdapter().scan()
        assert test_provider.test_record(case)["project_ids"] == records[0]["attributes"]["project_ids"] == (projects or [])


def projected_case(point_ref):
    item = {
        "id": "test_0123456789ab",
        "key": "test.design-resource-projection",
        "title": "设计点资源投影",
        "description": "保持绑定与文档参考的不同语义",
        "module": "resource.service",
        "module_path": ["资源", "资源服务"],
        "importance": "P1",
        "aspects": ["functional"],
        "is_design_point": bool(point_ref),
        "design_refs": ["decision_0123456789ab"],
        "design_point_ref": point_ref,
        "lifecycle": "active",
        "implementations": [],
    }
    return SimpleNamespace(id=item["id"], public=lambda: item)
