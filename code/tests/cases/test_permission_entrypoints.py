from management_portal.society_service import resident_lineage
from management_portal.society_service import resident_conversation_projection_domain
from management_portal.society_service import resident_directory_domain
from management_portal.society_service import resident_runtime_domain
from management_portal.society_service import resident_workspace_domain
import hashlib
from types import SimpleNamespace
from unittest import mock

import pytest

from management_portal import resources
from management_portal.resources.service import ResourceService
from management_portal.workbench_projections import native, service, studio
from management_portal.workbench_projections.store import ProjectionError, Store


APP = "workbench_111111111111"
LINEAGE = "lineage_111111111111"
R = "resident_111111111111"
D = "article_111111111111"


@pytest.fixture
def checkpoint_entries(tmp_path):
    store = Store()
    app = store.create(APP, {
        "title": "权限测试", "html": "<h1>权限测试</h1>",
        "bindings": {"main": {"lineage_id": LINEAGE, "residents": {"designer": R}}},
    }, "alice")
    yield SimpleNamespace(store=store, app=app)


@pytest.mark.parametrize("resource_type,identifier", [
    ("project", "new-project"), ("experiment", "experiment_111111111111"),
])
def test_a_resource_registration_notifies_the_index_without_account_lookups(resource_type, identifier):
    """资源登记不查询账户记录且通知索引登记结果"""
    access = mock.Mock()
    index = mock.Mock()
    registry = ResourceService(portal_access=access, index_provider=index)
    registry.register_mutation_provider(resource_type, register=lambda payload, **context: {
        "resource_id": identifier, **payload,
    })
    result = registry.register(resource_type, {"title": "New resource", "project_ids": ["Fixture"]}, actor="bob")
    assert result["resource_id"] == identifier
    index.project_registered.assert_called_once_with(resource_type, result)
    assert access.mock_calls == []




def test_workbench_job_uses_persisted_resource_and_keeps_author(checkpoint_entries):
    """工作台任务保留原作者且拒绝更换所属工作台与 Resident"""
    fixture = checkpoint_entries
    store = fixture.store
    job = {"id": "generation", "workbench_id": APP, "resident_id": R, "status": "running"}
    store.job(job["id"], "alice", job)
    assert store.job(job["id"], "bob")["created_by"] == "alice"
    saved = store.job(job["id"], "bob", {**job, "status": "completed", "created_by": "bob"})
    assert saved["created_by"] == "alice"
    with pytest.raises(ProjectionError, match="所属工作台"):
        store.job(job["id"], "bob", {**job, "workbench_id": D})
    assert store.resident_job(job["id"], R)["created_by"] == "alice"
    with pytest.raises(PermissionError):
        store.resident_job(job["id"], "resident_222222222222")
