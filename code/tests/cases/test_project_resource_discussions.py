"""Project background is live, bounded, actor-scoped and never a permission grant."""
from copy import deepcopy
from urllib.parse import urlparse

import pytest

from management_portal import project_resource_discussions as discussions
from management_portal.portal_ui_state import PortalUIStateStore
from tests.cases.permission_test_support import Fact, permissions


class Resources:
    def __init__(self):
        self.records = {
            "A": {"resource_id": "A", "resource_type": "project", "title": "Alpha", "summary": "Goal A"},
            "B": {"resource_id": "B", "resource_type": "project", "title": "Beta"},
            "article_123456789abc": {"resource_id": "article_123456789abc", "resource_type": "article", "title": "Paper", "projects": ["A"]},
        }
        self.denied = set()
        self.calls = []
        self.summary = "Shared conclusions"
        self.on_query = lambda: None

    def get(self, resource_id, *, actor):
        self.calls.append(("get", actor, resource_id))
        assert actor == "alice"
        if resource_id in self.denied:
            raise PermissionError(resource_id)
        return deepcopy(self.records[resource_id])

    def action(self, resource_id, action, *, actor):
        assert action == "summary.read"
        self.calls.append((action, actor, resource_id))
        return {"text": self.summary}

    def resolve(self, resource_id, *, actor):
        return {"record": self.get(resource_id, actor=actor), "exists": True}

    def query(self, *, actor, projects, limit):
        self.calls.append(("query", actor, projects, limit))
        self.on_query()
        # Includes stale and denied index entries: authority must filter both.
        return {"resources": [{"resource_id": key} for key in self.records if key not in ("A", "B")], "total": len(self.records) - 2}


RID = "article_123456789abc"


def test_background_reads_authorized_current_project_and_bounded_index():
    service = Resources()
    service.records["article_000000000002"] = {"resource_id": "article_000000000002", "projects": ["A"], "title": "private"}
    service.denied.add("article_000000000002")
    service.records["repo_000000000003"] = {"resource_id": "repo_000000000003", "projects": ["B"], "title": "other project"}
    result = discussions.context(service, "alice", service.records[RID])
    assert result["selected_project_id"] == "A" and not result["selection_required"]
    assert "Shared conclusions" in result["context_text"] and "Goal A" in result["context_text"]
    assert "private" not in result["context_text"] and "other project" not in result["context_text"]
    assert ("query", "alice", ["A"], 30) in service.calls
    service.summary = "New shared conclusion"
    assert "New shared conclusion" in discussions.context(service, "alice", service.records[RID])["context_text"]


def test_multiple_projects_require_explicit_choice_or_resource_only():
    service = Resources()
    service.records[RID]["projects"] = ["A", "B"]
    result = discussions.context(service, "alice", service.records[RID])
    assert result["selection_required"] and result["context_text"] == ""
    with pytest.raises(ValueError, match="请选择"):
        discussions.context(service, "alice", service.records[RID], require_selection=True)
    assert discussions.context(service, "alice", service.records[RID], selected="@none", require_selection=True)["context_text"] == ""
    chosen = discussions.context(service, "alice", service.records[RID], selected="B", require_selection=True)
    assert chosen["selected_project_id"] == "B"
    assert "Goal A" not in chosen["context_text"]
    assert ("query", "alice", ["B"], 30) in service.calls


@pytest.mark.parametrize("change", ["denied", "unlinked", "removed"])
def test_project_revocation_and_unlink_fail_before_sending(change):
    service = Resources()
    snapshot = deepcopy(service.records[RID])
    if change == "denied":
        service.denied.add("A")
    elif change == "unlinked":
        service.on_query = lambda: service.records[RID].update(projects=[])
    else:
        del service.records["A"]
    with pytest.raises(PermissionError):
        discussions.context(service, "alice", snapshot, selected="A", require_selection=True)


def test_readable_resource_does_not_grant_project_or_hide_service_failure():
    service = Resources()
    service.denied.add("A")
    result = discussions.context(service, "alice", service.records[RID])
    assert result["projects"] == [] and result["context_text"] == ""
    assert not any(call[0] == "summary.read" for call in service.calls)
    service.denied.clear()
    def unavailable():
        raise RuntimeError("owner unavailable")
    service.on_query = unavailable
    with pytest.raises(RuntimeError, match="owner unavailable"):
        discussions.context(service, "alice", service.records[RID])


def test_actual_binding_store_is_private_and_association_follows_live_membership(monkeypatch):
    service = Resources()
    store = PortalUIStateStore()
    namespace = discussions.resident_handoff.INVOCATION_ASSOCIATION_NAMESPACE
    own = {"resident_id": "resident_123456789abc", "metadata": {"resource_id": RID}}
    store.put("alice", namespace, f"resource-chat:alice:{RID}", own)
    store.put("bob", namespace, f"resource-chat:bob:{RID}", {**own, "resident_id": "resident_bbbbbbbbbbbb"})
    before = deepcopy(store.list("alice", namespace))
    monkeypatch.setattr(discussions.resident_handoff, "get_portal_ui_state_store", lambda: store)
    resolved = []
    def resolve(**kwargs):
        resolved.append(kwargs)
        assert kwargs["actor"] == "alice" and kwargs["create"] is False
        return {"resident_id": own["resident_id"], "lineage_id": "lineage_123456789abc"}
    monkeypatch.setattr(discussions.discussion_residents, "resolve", resolve)
    page = discussions.list_discussions(service, "alice", "A")
    assert [item["resident_id"] for item in page["items"]] == [own["resident_id"]]
    assert set(page["items"][0]) == {"resource_id", "resource_type", "title", "url", "resident_id", "lineage_id"}
    assert len(resolved) == 1
    service.records[RID]["projects"] = ["B"]
    assert discussions.list_discussions(service, "alice", "A")["items"] == []
    assert discussions.list_discussions(service, "alice", "B")["items"][0]["resident_id"] == own["resident_id"]
    service.denied.add(RID)
    assert discussions.list_discussions(service, "alice", "B")["items"] == []
    assert store.list("alice", namespace) == before, "projection must not mutate identities or membership"
    service.denied.add("B")
    with pytest.raises(PermissionError):
        discussions.list_discussions(service, "alice", "B")


def test_project_route_uses_authenticated_actor_and_returns_permission_failure(monkeypatch):
    from management_portal import http_projects, http_responses, resources
    service = Resources()
    monkeypatch.setattr(resources, "get_resource_service", lambda: service)
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, payload: (status, payload))
    calls = []
    def listing(source, actor, project_id, *, offset):
        calls.append((source, actor, project_id, offset))
        raise PermissionError("revoked")
    monkeypatch.setattr(discussions, "list_discussions", listing)
    route = next(route for route in http_projects.routes(None) if route.path == "/projects/api/discussions")
    status, body = route.handler(None, urlparse("/projects/api/discussions?project_id=A&actor=bob&offset=50"), "alice")
    assert status == 403 and body == {"error": "revoked"}
    assert calls == [(service, "alice", "A", 50)]


def test_real_resource_authorization_retains_revocation_and_uses_live_provider_links(monkeypatch, permissions):
    from management_portal import portal_features
    from management_portal.resources import ResourceService
    data = Resources()
    permissions.users("alice")
    permissions.place(Fact("project", "A"), Fact("project", "B"), Fact("article", RID))
    permissions.grant("alice", RID, "@viewer")
    original = portal_features.contributions
    monkeypatch.setattr(portal_features, "contributions", lambda hook:
        (("projects", {"project": lambda identity: data.records.get(identity)}),)
        if hook == "resource.authoritative_records" else original(hook))

    class Index:
        def get_resource(self, resource_id, **kwargs):
            return deepcopy(data.records[resource_id])

        def query(self, **kwargs):
            return {"resources": [{"resource_id": RID}], "total": 1}

    service = ResourceService(index_provider=Index(), action_provider_loader=lambda _: None)
    service.register_action_provider("project", {"summary.read": lambda rid, payload, **kw: {"text": data.summary}})
    live = deepcopy(data.records[RID])
    service.register_resolver("article", lambda address, **kw: {
        "root_id": address, "resource_type": "article", "target_kind": "article", "record": deepcopy(live), "exists": True})
    assert "Shared conclusions" in discussions.context(service, "alice", live)["context_text"]
    # Discovery still says A; the authoritative provider now says B.
    live["projects"] = ["B"]
    with pytest.raises(PermissionError, match="解除关联"):
        discussions.context(service, "alice", data.records[RID], selected="A")
    live["projects"] = ["A"]
    permissions.deny("alice", "A", "@viewer")
    assert service.get(RID, actor="alice")["resource_id"] == RID
    denied = discussions.context(service, "alice", live)
    assert denied["projects"] == [] and denied["context_text"] == ""
    with pytest.raises(PermissionError):
        discussions.context(service, "alice", live, selected="A")
