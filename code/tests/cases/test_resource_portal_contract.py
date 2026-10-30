from __future__ import annotations

import json
from unittest import mock

import pytest

from management_portal.resources.portal import (
    RESOURCE_PORTAL_ROUTES,
    canonical_portal_location,
    portal_route_contract,
    portal_route_script,
    portal_url_for_address,
    portal_url_for_resource,
)
from management_portal.resource_index_records import resource_record
from management_portal.resources import ResourceService
from management_portal.resources.access import ResourceAccessPolicy


@pytest.mark.parametrize("kind", sorted(set(RESOURCE_PORTAL_ROUTES) - {"project"}))
def test_index_and_browser_contract_use_the_same_resource_destination(kind):
    """资源索引与浏览器路由契约为同一资源 ID 生成相同的页面地址"""
    resource_id = f"{kind}_0123456789ab"
    declared = next(route for route in portal_route_contract()["routes"] if route["resource_type"] == kind)
    expected = f"{declared['path']}?{declared['parameter']}={resource_id}"
    assert portal_url_for_address(resource_id) == expected
    assert portal_url_for_resource(resource_id, kind) == expected
    record = resource_record(resource_id=resource_id, resource_type=kind, title="sample", adapter="fixture", url="/obsolete")
    assert record["url"] == expected


def test_published_script_contains_the_authoritative_contract_contracts(subtests, isolated_portal_case):
    """发布的路由脚本内容等于权威路由契约，评审和检索地址带上下文参数"""
    with subtests.test(contract='test_published_script_contains_the_authoritative_contract'), isolated_portal_case() as case_path:
        script = portal_route_script().decode()
        prefix = 'window.PortalResourceRoutes = Object.freeze('
        assert script.startswith(prefix) and script.endswith(');\n')
        assert json.loads(script[len(prefix):-3]) == portal_route_contract()
        assert portal_route_contract()["navigation_resource_types"] == ["family", "lineage", "resident", "test", "workbench", "workspace"]
        assert portal_route_contract()["navigation_references"] == [{
            "reference_type": "testrun",
            "pattern": r"testrun_[0-9a-f]{16}",
            "path": "/wall",
            "parameter": "run",
            "title": "Test Run",
        }]
        assert "run" in portal_route_contract()["identity_parameters"]
    with subtests.test(contract='test_resource_context_preserves_paper_reviews_and_scoped_searches'), isolated_portal_case() as case_path:
        assert portal_url_for_resource('review_0123456789ab', 'review', record={'attributes': {'paper_id': 'paper_aaaaaaaaaaaa'}}) == '/paper?paper=paper_aaaaaaaaaaaa'
        assert portal_url_for_resource('search_0123456789ab', 'search', record={'surface': 'knowledge', 'resource_scope': 'skill'}) == '/resource/skill?semantic=search_0123456789ab'
        assert portal_url_for_resource('search_0123456789ab', 'search', record={'attributes': {'surface': 'wall', 'project': 'A & B'}}) == '/wall?project=A%20%26%20B&semantic=search_0123456789ab'
        assert portal_url_for_resource('A & B', 'project') == '/projects?project=A%20%26%20B'
    with subtests.test(contract='test_nested_destination_keeps_the_resolved_child_url'), isolated_portal_case() as case_path:
        address = 'repo_0123456789ab/file/README.md'
        location = '/api/resources/content?address=repo_0123456789ab%2Ffile%2FREADME.md'
        assert canonical_portal_location(address, location) == location
    with subtests.test(contract='test_invalid_or_mismatched_resource_identity_has_no_generated_destination'), isolated_portal_case() as case_path:
        assert portal_url_for_address('paper_0123456789ab', 'resident') == ''
        assert portal_url_for_address('madeup_0123456789ab') == ''
        assert portal_url_for_address('paper_invalid') == ''


def test_redirect_validation_rejects_external_or_recursive_destinations(subtests, isolated_portal_case):
    """资源跳转校验拒绝外部地址、递归的资源入口和含换行的地址"""
    for candidate in [
    "https://example.invalid/", "//example.invalid/", "/\\example.invalid/",
    "/r/repo_0123456789ab", "/repo_0123456789ab", "/resource?address=repo_0123456789ab",
    "/resource.html?address=repo_0123456789ab", "/resource/repo\r\nLocation: bad",
]:
        with subtests.test(candidate=candidate), isolated_portal_case() as case_path:
            assert canonical_portal_location("repo_0123456789ab", candidate) == ""


def test_service_resolution_and_reference_share_the_contextual_destination(subtests, isolated_portal_case):
    """ResourceService 的解析与引用对评审和检索给出相同的上下文地址"""
    for kind, metadata, expected in [
    ("review", {"paper_id": "paper_aaaaaaaaaaaa"}, "/paper?paper=paper_aaaaaaaaaaaa"),
    ("search", {"surface": "knowledge", "resource_scope": "repo"}, "/resource/repo?semantic=search_0123456789ab"),
]:
        with subtests.test(kind=kind, metadata=metadata, expected=expected), isolated_portal_case() as case_path:
            resource_id = f"{kind}_0123456789ab"
            index = mock.Mock()
            index.get_resource.return_value = {
                "resource_id": resource_id,
                "resource_type": kind,
                "attributes": metadata,
                "url": "/obsolete",
            }
            policy = mock.Mock(spec=ResourceAccessPolicy)
            service = ResourceService(index_provider=index, access_policy=policy)
            service.register_resolver(kind, lambda address, **context: {
                "address": address,
                "root_id": resource_id,
                "resource_type": kind,
                "target_kind": kind,
                "record": {"resource_id": resource_id, **metadata},
                "portal_url": "/obsolete",
                "exists": True,
            })

            assert service.resolve(resource_id, actor="CASE-A")["portal_url"] == expected
            policy.authorize_read.assert_called_once()
            assert policy.authorize_read.call_args.kwargs["actor"] == "CASE-A"
            policy.reset_mock()
            assert service.reference(resource_id, actor="CASE-A")["url"] == expected
            assert policy.authorize_read.called
            assert all(call.kwargs["actor"] == "CASE-A" for call in policy.authorize_read.call_args_list)
