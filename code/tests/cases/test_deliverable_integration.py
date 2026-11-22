"""Real PG/PAR and Resource ISA behind the same HTTP contract as the results UI.

:module: resources.types
:design: decision_56aef2c2393b
"""
import io
import json
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from tests.cases.directory_fixture_support import install_directory_owners
from tests.cases.permission_test_support import Fact, enable
from tests.cases.registry_support import write_projects


@pytest.fixture
def result_app(monkeypatch):
    from management_common.portal_database import portal_database
    from management_portal import http_responses, isa_client, permission_system, resident_results_http
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.hatg.store import ExecutionStore
    from management_portal.resource_index_backend import IndexStore, ResourceIndexService
    from management_portal.resource_index_registries import ProjectRegistryAdapter, ResourceRegistryAdapter
    from management_portal.resources import isa, runtime
    from management_portal.resources.providers import install_mutation_providers, install_resolvers
    from management_portal.resources.service import ResourceService

    install_directory_owners()
    write_projects({"projects": [{"id": "ResultsIntegration", "status": "active", "created_by": "alice"}],
                    "status_vocab": ["active"]})
    permissions = enable(monkeypatch)
    permissions.users("alice")
    permissions.users("charlie")
    permissions.users("bob", category="intern")
    permissions.place(Fact("project", "ResultsIntegration", {}, creator="alice"))
    index = ResourceIndexService(store=IndexStore(),
        adapters=[ProjectRegistryAdapter(), ResourceRegistryAdapter()], start_worker=False)
    service = ResourceService(index_provider=index, resolver_loader=install_resolvers,
                              mutation_provider_loader=install_mutation_providers)
    monkeypatch.setattr(runtime, "get_resource_service", lambda: service)
    monkeypatch.setattr(isa, "_service", lambda: service)
    kernel = InstructionKernel(isa.binding(), store=ExecutionStore(portal_database(schema="deliverable_web_isa")))

    def call(actor, name, args, *, key=None, **kwargs):
        payload = {"instruction": name, "arguments": args}
        if key:
            payload["idempotency_key"] = key
        return kernel.execute(InstructionCall.from_payload(payload),
            InstructionContext(caller=actor, entry="web")).raise_for_error()

    monkeypatch.setattr(isa_client, "call", call)
    monkeypatch.setattr(http_responses, "_serve_json", lambda request, status, result: (status, result))
    routes = resident_results_http.routes(None)

    def request(method, address, body=None, actor="alice"):
        parsed = urlparse(address)
        raw = json.dumps(body).encode()
        handler = SimpleNamespace(headers={"Content-Type": "application/json", "Content-Length": str(len(raw))},
                                  rfile=io.BytesIO(raw))
        route = next(r for r in routes if r.matches(method, parsed.path, "protected"))
        args = (handler, parsed, actor)
        return route.handler(*args, route.template_params(parsed.path)) if route.match == "template" else route.handler(*args)

    yield SimpleNamespace(request=request, call=call, permissions=permissions, service=service)
    permission_system.forget_implementation()


def test_http_isa_publication_revisions_and_current_permissions(result_app):
    app = result_app
    fields = {"project_id": "ResultsIntegration", "title": "Experiment", "summary": "Measured result",
              "body": "Evidence", "documents": [{"name": "raw.txt", "content": "version one"}], "status": "draft"}
    body = {"operation_id": "result-create-0001", "payload": fields}
    status, created = app.request("POST", "/api/results", body)
    assert status == 200, created
    identifier = created["resource_id"]
    assert identifier.startswith("deliverable_")
    assert app.request("POST", "/api/results", body)[1]["resource_id"] == identifier
    assert app.request("GET", "/api/results", actor="bob")[1]["items"] == []
    assert app.request("GET", f"/api/results/{identifier}", actor="bob")[0] == 403

    status, published = app.request("POST", f"/api/results/{identifier}/update",
        {"expected_revision": 1, "payload": {"status": "published"}})
    assert status == 200, published
    status, detail = app.request("GET", f"/api/results/{identifier}?revision=2", actor="bob")
    assert status == 200, detail
    assert detail["snapshot"]["documents"][0]["content"] == "version one"
    assert not detail["can_edit"]
    assert app.request("GET", f"/api/results/{identifier}?revision=1", actor="bob")[0] == 403
    # A Project administrator does not retroactively acquire a private draft.
    assert app.request("GET", f"/api/results/{identifier}?revision=1", actor="charlie")[0] == 403
    found = app.call("bob", "ListDeliverables", {"project_id": "ResultsIntegration"})
    assert [item["resource_id"] for item in found["items"]] == [identifier]
    indexed = app.call("bob", "SearchResources", {"project_id": "ResultsIntegration", "resource_type": "deliverable"})
    assert identifier in {item["resource_id"] for item in indexed["items"]}
    raw = app.call("bob", "ReadResource", {"address": f"{identifier}/revision/2/file/raw.txt"})
    assert raw["text"] == "version one"
    assert app.request("POST", f"/api/results/{identifier}/update",
        {"expected_revision": 1, "payload": {"title": "stale"}})[0] == 409

    status, changed = app.request("POST", f"/api/results/{identifier}/update",
        {"expected_revision": 2, "payload": {"documents": [{"name": "raw.txt", "content": "version two"}]}})
    assert status == 200, changed
    assert app.call("bob", "ReadResource", {"address": f"{identifier}/revision/2/file/raw.txt"})["text"] == "version one"
    assert app.request("POST", f"/api/results/{identifier}/update",
        {"expected_revision": 3, "payload": {"status": "withdrawn", "withdrawal_reason": "New measurement required"}})[0] == 200
    assert app.request("GET", f"/api/results/{identifier}?revision=2", actor="bob")[1]["record"]["status"] == "withdrawn"
    app.permissions.deny("bob", "ResultsIntegration")
    assert app.request("GET", f"/api/results/{identifier}?revision=2", actor="bob")[0] == 403
    assert app.request("GET", "/api/results", actor="bob")[1]["items"] == []
    assert app.call("bob", "SearchResources", {"project_id": "ResultsIntegration", "resource_type": "deliverable"})["items"] == []


def test_http_rejects_author_spoof_and_viewer_creation(result_app):
    app = result_app
    payload = {"project_id": "ResultsIntegration", "title": "Work", "status": "published"}
    status, _ = app.request("POST", "/api/results", {"operation_id": "no-spoof-0001", "payload": {**payload, "author_id": "bob"}})
    assert status == 400
    status, _ = app.request("POST", "/api/results", {"operation_id": "no-viewer-0001", "payload": payload}, actor="bob")
    assert status == 403
