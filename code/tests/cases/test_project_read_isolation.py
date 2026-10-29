"""Project denies cover discovery, navigation and direct reads.

:module: resource.index-search
"""
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from management_portal.resource_index_backend import IndexStore, ResourceIndexService, resource_record
from management_portal.resources.service import ResourceService
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

from management_portal.society_service import lineage_aggregate

L = "lineage_111111111111"
R = "resident_111111111111"
D = "decision_111111111111"


@pytest.fixture
def restricted(permissions):
    permissions.users("intern", category="intern")
    permissions.place(Fact("project", "Allowed"), Fact("project", "Hidden"),
                      Fact("lineage", L, {"project": "Hidden"}),
                      Fact("resident", R, {"lineage": L}),
                      Fact("decision", D, {"project": "Hidden"}))
    permissions.grant("intern", "Allowed", "@admin")
    permissions.deny("intern", "Hidden", "@admin")
    return permissions


@pytest.fixture
def resources(restricted):
    index = IndexStore()
    index.publish_snapshot("project", [resource_record(resource_id=p, resource_type="project",
                           title=p, adapter="project") for p in ("Allowed", "Hidden")], {})
    index.publish_snapshot("knowledge", [resource_record(resource_id=D, resource_type="decision",
                           title="Hidden knowledge", adapter="knowledge", attributes={"project_ids": ["Hidden"]})], {})
    return ResourceService(index_provider=ResourceIndexService(store=index, adapters=[], start_worker=False))


def test_http_catalog_and_suggestions_hide_denied_projects_and_knowledge(resources, monkeypatch):
    """实际 HTTP 列表与联想在分页前隐藏被拒绝项目及知识资源"""
    from management_portal import serve_management_portal as portal
    monkeypatch.setattr(portal._RESOURCE_HTTP, "resource_service", lambda: resources)
    monkeypatch.setattr(portal._RESOURCE_HTTP, "respond", lambda request, status, value: (status, value))
    for path in ("/api/resource-index/query?limit=1", "/api/resource-index/suggest?q=Hidden"):
        parsed = urlparse(path)
        status, result = portal._PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, SimpleNamespace(headers={}), parsed, "intern", phase="protected")
        assert status == 200
        assert {row["resource_id"] for row in result["resources"]} == ({"Allowed"} if "query" in path else set())
        if "query" in path:
            assert result["total"] == 1


def test_direct_resource_read_hides_denied_knowledge(resources):
    """知道知识条目 ID 也不能绕过项目拒绝读取详情"""
    with pytest.raises((KeyError, PermissionError)):
        resources.get(D, actor="intern")


def test_navigation_does_not_reinsert_hidden_lineages(resources, monkeypatch):
    """导航只列出可读项目，不能因原始 Lineage 定义重新暴露被拒绝项目"""
    from management_portal.society_service import principal_hierarchy_domain, resource_client
    definitions = [{"lineage_id": L, "title": "Hidden", "project_id": "Hidden"}]
    monkeypatch.setattr(lineage_aggregate, "lineage_catalog_store", lambda: SimpleNamespace(listing_records=lambda: definitions))
    monkeypatch.setattr(resource_client, "get_resource_service", lambda: resources)
    result = principal_hierarchy_domain.navigation("intern")
    assert [row["project_id"] for row in result["projects"]] == ["Allowed"]


def test_lineage_and_resident_reads_follow_project_deny(restricted):
    """项目拒绝覆盖其 Lineage 与 Resident 的读取可见性"""
    from management_portal.society_service.lineage_access_policy import actor_can_manage_lineage
    from management_portal.society_service.resident_access import resident_visible_to_actor
    assert not actor_can_manage_lineage({"lineage_id": L}, "intern", operation="read")
    assert not resident_visible_to_actor({"resident_id": R}, "intern")


def test_resource_content_denied_before_reading_bytes(resources):
    """资源正文入口拒绝项目子资源，正文提供者不会被调用"""
    reads = []
    def resolve(address, *, actor):
        return {"record": {"resource_id": D, "resource_type": "decision"}}
    def content(*args, **kwargs):
        reads.append(args)
        return b"secret", "text/plain", {}
    resources.register_resolver("decision", resolve, read_content=content)
    with pytest.raises((KeyError, PermissionError)):
        resources.read_content(D, actor="intern")
    assert reads == []
