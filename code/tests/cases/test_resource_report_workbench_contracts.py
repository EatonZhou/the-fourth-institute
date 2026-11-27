"""Report and Workbench publish the contracts used by their real mutations."""

import pytest

from management_portal.resources.errors import ResourceVersionConflict
from management_portal.resources.mutation_contracts import (
    ResourceMutationValidationError, prepare_mutation_contract, validate_mutation_payload,
)
from management_portal.workbench_projections.mutation_contract import declaration as workbench_contracts
from management_portal.workbench_projections.store import ProjectionError
from tests.cases.historical_report_support import seed_historical_report
from tests.cases.permission_test_support import permissions  # noqa: F401
from tests.cases.test_home_report_persistence import KEY, RID, TARGET, reports  # noqa: F401
from tests.cases.test_report_explanations import explanations  # noqa: F401
from tests.cases.test_workbench_projections import APP, place_workbenches, resources, store  # noqa: F401
from tests.cases.test_workspace_resource_rename import workspace  # noqa: F401


def validate(schema, payload, kind, operation):
    return validate_mutation_payload(prepare_mutation_contract(schema), payload, resource_type=kind, operation=operation)


def test_formal_report_contract_and_fixed_explanation_registration(explanations, monkeypatch):
    e = explanations
    response = e.workspace.call("GetResourceType", {"resource_type": "report"}).raise_for_error()
    from management_portal.home_report_mutation_contract import REGISTER_CONTRACT, UPDATE_CONTRACT

    assert response["contracts"] == {"register": REGISTER_CONTRACT, "update": UPDATE_CONTRACT}
    good = {"explanation_target": e.ref}
    assert validate(response["contracts"]["register"], good, "report", "register") == "write"
    args = {"resource_type": "report", "fields": good}
    result = e.workspace.call("RegisterResource", args, idempotency_key="fixed-report-schema").raise_for_error()
    assert e.store.response(result["resource_id"], "alice")["revision"] == 1

    def unexpected_reservation(*args, **kwargs):
        pytest.fail("invalid registration allocated an identity")

    monkeypatch.setattr(e.workspace.service._registrations, "reserve", unexpected_reservation)
    bad = {**args, "fields": {"explanation_target": {**e.ref, "sha256": True}}}
    error = e.workspace.call("RegisterResource", bad, idempotency_key="invalid-report-hash").error
    assert error["code"] == "instruction.invalid_arguments"
    assert any(item["path"] == "/explanation_target/sha256" for item in error["details"]["errors"])


def test_report_legacy_registration_replays_only_original_committed_request(workspace, reports, monkeypatch):
    store, state = reports
    from management_portal.resources.providers import report

    monkeypatch.setattr(report, "ReportStore", lambda: store)
    workspace.service._index().project_registered = lambda *_: None
    register = workspace.service.register
    ledger = workspace.service._registrations
    payload = {"target": TARGET, "origin_key": KEY, "quality": "medium"}
    ledger.reserve("report", payload, actor="alice", allocate=lambda: RID, operation_id="original")
    incomplete = "report_000000000002"
    ledger.reserve("report", payload, actor="alice", allocate=lambda: incomplete, operation_id="uncommitted")
    seed_historical_report(store, RID, payload)
    ledger.complete(RID)
    ledger.complete(incomplete)  # A ledger receipt alone cannot establish a Report commit.

    def unexpected_reservation(*args, **kwargs):
        pytest.fail("legacy replay attempted to allocate an identity")

    monkeypatch.setattr(ledger, "reserve", unexpected_reservation)
    replay = register("report", payload, actor="alice", operation_id="original")
    assert replay["resource_id"] == RID
    for key, actor, fields in (
        ("", "alice", payload), ("missing", "alice", payload), ("original", "bob", payload),
        ("original", "alice", {**payload, "quality": "high"}), ("uncommitted", "alice", payload),
    ):
        with pytest.raises(ValueError):
            register("report", fields, actor=actor, operation_id=key)
    state["deny_report"] = True
    with pytest.raises(PermissionError):
        register("report", payload, actor="alice", operation_id="original")
    state["deny_report"] = False
    workspace.service.unregister(RID, actor="alice")
    with pytest.raises(KeyError):
        register("report", payload, actor="alice", operation_id="original")


@pytest.mark.parametrize(("payload", "path"), [
    ({}, "/expected_revision"),
    ({"expected_revision": True}, "/expected_revision"),
    ({"expected_revision": 1.0}, "/expected_revision"),
    ({"expected_revision": 1_000_000_000}, "/expected_revision"),
    ({"expected_revision": 1, "content": {"authoring_id": "job"}}, "/content"),
    ({"expected_revision": 1, "content": {"presentation": {"nodes": [{"id": "fixed", "y": 1.1}]}}},
     "/content/presentation/nodes/0/y"),
])
def test_report_update_rejects_invalid_revision_content_and_coordinates(payload, path):
    from management_portal.home_report_mutation_contract import UPDATE_CONTRACT

    with pytest.raises(ResourceMutationValidationError) as rejected:
        validate(UPDATE_CONTRACT, payload, "report", "update")
    assert any(row["path"] == path for row in rejected.value.details["errors"])


def test_report_update_accepts_string_revision_and_keeps_cas(workspace, reports, monkeypatch):
    store, _ = reports
    from management_portal.resources.providers import report

    monkeypatch.setattr(report, "ReportStore", lambda: store)
    seed_historical_report(store, RID, {"target": TARGET, "origin_key": KEY})
    workspace.service.update(RID, {"expected_revision": "1", "content": {"title": "Corrected"}}, actor="alice")
    assert store.response(RID, "alice")["title"] == "Corrected"
    with pytest.raises(ResourceVersionConflict):
        workspace.service.update(RID, {"expected_revision": "1", "content": None}, actor="alice")


def test_workbench_registration_keeps_public_location_and_legacy_path(resources, store):
    register, update = workbench_contracts()
    assert resources.capabilities("workbench")["contracts"] == {"register": register, "update": update}
    created = resources.register("workbench", {"title": "New workbench", "project_ids": " @public ",
                                                "path": "/existing-page", "created_by": "supplied-value"}, actor="alice")
    saved = store.get(created["resource_id"], "alice")
    assert (saved["path"], saved["created_by"], saved["project_ids"]) == ("/existing-page", "alice", [])


def test_workbench_update_publish_keeps_cas_and_permissions(resources, store, permissions, monkeypatch):
    from management_portal.workbench_projections import service

    place_workbenches(permissions)
    monkeypatch.setattr(service.ProjectionService, "check_bindings", lambda *args: {})
    old_revision = store.get(APP, "alice")["revision"]
    with pytest.raises(PermissionError):
        resources.update(APP, {"expected_revision": old_revision, "title": "Denied"}, actor="bob")
    resources.update(APP, {"expected_revision": str(old_revision), "title": "Renamed"}, actor="alice")
    with pytest.raises(ProjectionError) as stale:
        resources.update(APP, {"expected_revision": old_revision, "title": "Stale"}, actor="alice")
    assert stale.value.http_status == 409
    revision = old_revision + 1
    resources.update(APP, {"expected_revision": revision, "published_revision": revision}, actor="alice")
    assert store.get(APP, "alice", published=True)["title"] == "Renamed"


V2 = {"schema": "tfi.projection.v2", "sidebar": [{"id": "home", "title": "Home", "source": "package:main"}]}
APPLICATION = {"repo_id": "repo_0123456789ab", "directory": ".", "commit": "a" * 40, "entries": {"main": "page.html"}}


@pytest.mark.parametrize(("operation", "payload"), [
    ("register", {"project_ids": ["@public"]}),
    ("register", {"title": "Bad build", "application_build": {}}),
    ("register", {"title": "Bad metadata", "existing_metadata": "retained"}),
    ("register", {"title": "Mixed location", "project_ids": "@public, example"}),
    ("register", {"title": "No manifest", "application": APPLICATION}),
    ("register", {"title": "Mixed page", "manifest": V2, "application": APPLICATION, "html": "<p>Legacy</p>"}),
    ("register", {"title": "Too large", "manifest": V2, "application": {**APPLICATION, "max_bytes": 64 * 1024 * 1024 + 1}}),
    ("update", {"expected_revision": 1, "published_revision": 1, "title": "Mixed"}),
    ("update", {"expected_revision": 1, "activate_builtin": True}),
    ("update", {"expected_revision": 1, "activate_builtin": True, "previous_app_id": "", "published_revision": 1}),
    ("update", {"expected_revision": 1.0, "title": "Invalid"}),
    ("update", {"expected_revision": 1, "published_revision": 1.0}),
])
def test_workbench_rejects_invalid_combinations_and_fields(operation, payload):
    register, update = workbench_contracts()
    with pytest.raises(ResourceMutationValidationError):
        validate(register if operation == "register" else update, payload, "workbench", operation)


def test_workbench_contract_accepts_application_and_partial_page_updates():
    register, update = workbench_contracts()
    assert validate(register, {"title": "Package", "project_ids": ["@public"], "manifest": V2,
                               "application": APPLICATION}, "workbench", "register") == "write"
    for payload in (
        {"expected_revision": 1, "application": APPLICATION},
        {"expected_revision": " 1 ", "panels": {"page.html": "<p>Panel</p>"}},
        {"expected_revision": 1, "activate_builtin": True, "previous_app_id": "", "builtin": "studio"},
    ):
        assert validate(update, payload, "workbench", "update") == "write"
