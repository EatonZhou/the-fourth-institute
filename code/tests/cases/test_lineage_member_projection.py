from __future__ import annotations

import copy

import pytest

from management_portal.society_service import lineage_domain, resident_catalog, resident_domain
from management_portal.society_service.state_store import LineageDataStore
from management_portal.society_service.lineage_storage import canonical_lineage_folder
from tests.cases.test_resident_folder_creation_contract import future_creation_store, _new_session_payload


def _family():
    lineage = lineage_domain.create_lineage({"title": "Complete membership view"}, "sample_admin")
    root = lineage_domain.create_lineage_resident(lineage["lineage_id"], _new_session_payload("Root"), "sample_admin")["resident"]
    fork = resident_domain.create_resident_full_context_fork(root["resident_id"], "sample_admin", defer_backend_start=True)
    return lineage["lineage_id"], root["resident_id"], fork["resident_id"]




def test_identity_and_graph_membership_roll_back_together(future_creation_store):
    """Resident 身份与图成员关系在失败时一同回滚"""
    lineage_id, root_id, _ = _family()
    catalog = resident_catalog.get_catalog()
    before = catalog.state_store.lineage_definition(lineage_id)
    new = copy.deepcopy(catalog.get(root_id))
    new["resident_id"] = "resident_123456789abc"
    owned = new["owned_workspace"]
    owned["workspace_id"] = "workspace_123456789abc"
    new["owned_workspace_id"] = owned["workspace_id"]
    owned["path"] = str(canonical_lineage_folder(lineage_id) / "residents" / new["resident_id"] / "workspace")
    def failed_commit():
        catalog.state_store.put_resident_record(new["resident_id"], new)
        assert catalog.state_store.database.write(lambda connection: connection.execute(
            'SELECT 1 FROM lineage_nodes WHERE lineage_id=%s AND node_id=%s', (lineage_id, new["resident_id"]),
        ).fetchone())
        raise RuntimeError("rollback")
    with pytest.raises(RuntimeError, match="rollback"):
        catalog.state_store.transaction(failed_commit)
    assert catalog.state_store.lineage_definition(lineage_id) == before
    with pytest.raises(KeyError):
        catalog.get(new["resident_id"])


def test_graph_delete_removes_identity_and_cannot_rematerialize(future_creation_store):
    """删除图节点同时删除身份且重开存储后不恢复"""
    from tests.cases.isa_test_support import reset_kernels, run

    lineage_id, root_id, fork_id = _family()
    store = resident_catalog.get_catalog().state_store
    before = store.lineage_definition(lineage_id)
    reset_kernels()
    try:
        result = run("DeleteResident", {"lineage_id": lineage_id, "resident_id": fork_id}, key="baseline-lifecycle-delete",
                     version={lineage_id: before["revision"]}).raise_for_error()
    finally:
        reset_kernels()
    assert result["resident_id"] == fork_id
    with pytest.raises(KeyError):
        resident_catalog.get_catalog().get(fork_id)
    reopened = LineageDataStore()
    assert {node["id"] for node in reopened.lineage_definition(lineage_id)["nodes"]} == {root_id}
    assert {record["resident_id"] for record in reopened.residents_for_lineage(lineage_id)} == {root_id}


def test_failed_graph_delete_keeps_identity_and_membership(future_creation_store, monkeypatch):
    """删除图节点失败时保留身份与成员关系"""
    lineage_id, root_id, fork_id = _family()
    store = resident_catalog.get_catalog().state_store
    before = store.lineage_definition(lineage_id)
    def fail(*_args):
        raise RuntimeError("resident lifecycle refused deletion")
    monkeypatch.setattr(lineage_domain.resident_domain, "delete_resident_identities", fail)
    with pytest.raises(RuntimeError, match="lifecycle refused"):
        lineage_domain.delete_lineage_graph_objects(lineage_id, {"resident_ids": [fork_id], "expected_revision": before["revision"]}, "sample_admin")
    assert store.lineage_definition(lineage_id) == before
    assert {record["resident_id"] for record in store.residents_for_lineage(lineage_id)} == {root_id, fork_id}
