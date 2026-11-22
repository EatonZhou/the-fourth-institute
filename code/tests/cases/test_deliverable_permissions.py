"""The real default scheme shares publication, never a draft's Lineage context."""
from management_portal import permission_system
from management_portal.permission_system.facts import Fact, fact_from_record
from tests.cases.permission_test_support import enable


def test_draft_is_private_and_publication_moves_to_project(monkeypatch):
    permissions = enable(monkeypatch)
    try:
        permissions.users("alice", "bob", category="intern")
        permissions.place(Fact("project", "ResultTest", {}))
        record = {"resource_id": "deliverable_000000000001", "resource_type": "deliverable",
                  "created_by": "alice", "project_id": "ResultTest", "status": "draft"}
        permissions.place(fact_from_record(record, "deliverable"))
        def allowed(actor):
            return permission_system.allows(actor, "ResolveResource", {"address": record["resource_id"]})
        assert allowed("alice")
        assert not allowed("bob")
        record["status"] = "published"
        permissions.place(fact_from_record(record, "deliverable"))
        assert allowed("bob")
        permissions.deny("bob", "ResultTest")
        assert not allowed("bob")
        record["status"] = "withdrawn"
        assert fact_from_record(record, "deliverable").owners == {"project": "ResultTest"}
    finally:
        permission_system.forget_implementation()
