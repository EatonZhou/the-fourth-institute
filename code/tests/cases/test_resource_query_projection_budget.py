"""Discovery enriches descriptors once and detaches results.

:module: resource.index-search
:importance: P1
:aspects: functional, architecture-conformance
:design: decision_cf27d39e39cd, decision_8dc6b3d03ca7
"""
from unittest import mock

import pytest

from management_portal import resource_index_backend
from management_portal.society_service import business_fact_views
from management_portal.resources.collaboration_registry import descriptor_resource_record
from management_portal.resources.service import ResourceService
from tests.cases.test_canonical_resource_facts import canonical, payload, R
from tests.cases.test_domain_tombstone_and_restore import projection


def discovery(canonical, projection):
    descriptor = payload(canonical, "resident")["descriptor"]
    projection.descriptors.put(descriptor)
    index = resource_index_backend.IndexStore()
    index.publish_snapshot("collaboration", [descriptor_resource_record(descriptor)], {})
    assert R in index._records
    service = ResourceService(index_provider=index, collaboration_store=projection.descriptors)
    return index, service


@pytest.mark.parametrize("operation", ["query", "suggest"])
def test_discovery_projects_each_domain_once_and_detaches_results(canonical, projection, operation):
    """资源查询和联想对每个领域只投影一次，返回结果与存储的描述符互不影响

    :aspects: functional, architecture-conformance, performance
    :design-point:
    """
    _, service = discovery(canonical, projection)
    with mock.patch.object(business_fact_views, "project_resources", wraps=business_fact_views.project_resources) as domain:
        if operation == "query":
            result = service.query(actor=R, resource_types=["resident"], include_attributes=True)
        else:
            result = service.suggest(actor=R, q="Resident", resource_type="resident")
        assert [item["resource_id"] for item in result["resources"]] == [R]
        assert domain.call_count == 1
    if operation == "query":
        item = result["resources"][0]
        item["attributes"]["client_edit"] = True
        assert "client_edit" not in projection.descriptors.get(R)["attributes"]
