"""Other domain providers expose the schema used by ResourceService writes."""
from copy import deepcopy
from importlib import import_module

import pytest

from management_portal.resources import ResourceService
from management_portal.resources.mutation_contracts import (
    ResourceMutationValidationError, prepare_mutation_contract, validate_mutation_payload,
)
from tests.cases.test_project_registration_ack import project_registration
from tests.cases.test_experiment_resource_provider import experiment_service
from tests.cases.test_d066_mailbox_todo_resource import active_users


KINDS = ("device", "project", "repo", "workspace", "experiment", "mailbox_todo", "article")


def module(kind):
    return import_module("management_portal.resources.providers." + kind)


def contract(kind, operation):
    return prepare_mutation_contract(getattr(module(kind), operation.upper() + "_CONTRACT"))


@pytest.mark.parametrize("kind", KINDS)
def test_installed_contract_is_detached_and_has_public_field_descriptions(kind):
    service = ResourceService()
    options = {"unregister_workspace": lambda *a, **k: None} if kind == "workspace" else {}
    module(kind).install_mutation_provider(service, **options)
    provider = service._mutation_providers.provider_for(kind)
    for operation in ("register", "update"):
        declaration = getattr(provider, operation + "_contract")
        if getattr(provider, operation) is None:
            assert declaration is None
            continue
        assert declaration == contract(kind, operation)
        assert declaration is not getattr(module(kind), operation.upper() + "_CONTRACT")
        def check_fields(node):
            if isinstance(node, dict):
                for name, schema in node.get("properties", {}).items():
                    assert schema.get("description"), (kind, operation, name)
                for value in node.values():
                    check_fields(value)
            elif isinstance(node, list):
                for value in node:
                    check_fields(value)
        check_fields(declaration)


@pytest.mark.parametrize("kind,operation,payload", [
    ("device", "register", {"name": "server", "device_kind": "gpu_server", "monitor": {"transport": "local"},
                             "gpus": [{"gpu_index": "0", "gpu_uuid": "GPU-a", "model": "test"}]}),
    ("device", "update", {"access_methods": {"ssh": {"host": "host", "port": "2222"}}}),
    ("project", "register", {"id": "project-test", "project_type": "RESEARCH", "tags": "a，b c", "extra": True}),
    ("project", "update", {"field": "tags", "value": ["a", "b"]}),
    ("project", "update", {"field": "description"}),
    ("repo", "register", {"path": "/automatic/repo"}),
    ("repo", "update", {"url": "https://example.invalid/repo.git", "extra": True}),
    ("repo", "update", {"operation": "set_policy", "push_enabled": "yes", "url": 12}),
    ("workspace", "update", {"expected_version": "row-version", "summary": ""}),
    ("experiment", "register", {"project_id": "test", "title": "Title", "importance": 0, "body": "b", "summary": "s", "extra": True}),
    ("experiment", "update", {"importance": 10, "body": None, "summary": "ignored"}),
    ("mailbox_todo", "register", {"user_id": "owner", "text": "next", "date": None}),
    ("mailbox_todo", "update", {"expected_version": 1, "order": {"direction": "up"}}),
    ("mailbox_todo", "update", {"expected_version": 1, "indent": 4, "completed": False}),
    ("article", "register", {"path": "/automatic/article", "compiler": "ignored-for-markdown"}),
])
def test_existing_payload_branches_are_accepted_without_mutation(kind, operation, payload):
    before = deepcopy(payload)
    assert validate_mutation_payload(contract(kind, operation), payload, resource_type=kind, operation=operation) == "write"
    assert payload == before


@pytest.mark.parametrize("kind,operation,payload,path", [
    ("device", "register", {}, "/name"),
    ("device", "register", {"name": "x", "device_kind": "gpu_server", "gpus": []}, "/gpus"),
    ("device", "register", {"name": "x", "gpus": []}, "/gpus"),
    ("device", "update", {"access_methods": []}, "/access_methods"),
    ("device", "update", {"access_methods": {"ssh": {"port": 65536}}}, "/access_methods/ssh/port"),
    ("project", "register", {"id": "test", "language": "fr"}, "/language"),
    ("project", "update", {"field": "path", "value": "/elsewhere"}, "/field"),
    ("repo", "register", {"path": "/repo", "source_kind": "other"}, "/source_kind"),
    ("repo", "update", {"operation": "other"}, "/operation"),
    ("repo", "update", {"operation": "restore_registration"}, "/evidence_address"),
    ("workspace", "update", {"title": "t"}, "/expected_version"),
    ("workspace", "update", {"expected_version": 1, "title": "t"}, "/expected_version"),
    ("workspace", "update", {"expected_version": "v", "title": None}, "/title"),
    ("workspace", "update", {"expected_version": "v", "summary": "x" * 2001}, "/summary"),
    ("experiment", "register", {"project_id": "p"}, "/title"),
    ("experiment", "update", {"importance": 11}, "/importance"),
    ("experiment", "update", {"path": "/elsewhere"}, "/path"),
    ("mailbox_todo", "register", {"text": "x"}, "/user_id"),
    ("mailbox_todo", "update", {"expected_version": 1, "indent": 5}, "/indent"),
    ("mailbox_todo", "update", {"expected_version": 1, "completed": "yes"}, "/completed"),
    ("mailbox_todo", "update", {"expected_version": 1, "order": {"direction": "left"}}, "/order/direction"),
    ("article", "register", {}, "/path"),
])
def test_invalid_fields_have_structured_paths_and_no_payload_echo(kind, operation, payload, path):
    with pytest.raises(ResourceMutationValidationError) as error:
        validate_mutation_payload(contract(kind, operation), payload, resource_type=kind, operation=operation)
    assert error.value.code == "invalid_argument"
    assert path in [item["path"] for item in error.value.details["errors"]]
    assert error.value.details["resource_type"] == kind
    assert error.value.details["operation"] == operation


@pytest.mark.parametrize("kind,field", [("experiment", "importance"), ("mailbox_todo", "expected_version"), ("mailbox_todo", "indent")])
@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_integer_fields_keep_strict_json_types(kind, field, value):
    payload = {"expected_version": 1, "text": "next"} if kind == "mailbox_todo" else {}
    with pytest.raises(ResourceMutationValidationError):
        validate_mutation_payload(contract(kind, "update"), {**payload, field: value}, resource_type=kind, operation="update")


@pytest.mark.parametrize("payload", [
    {"expected_version": 1},
    {"expected_version": 1, "order": {"direction": "up"}, "text": "combined"},
    {"expected_version": 1, "order": {"direction": "up", "before_id": "mailbox_todo_0123456789ab"}},
])
def test_todo_order_is_separate_from_content_changes(payload):
    with pytest.raises(ResourceMutationValidationError):
        validate_mutation_payload(contract("mailbox_todo", "update"), payload, resource_type="mailbox_todo", operation="update")


def service_for(kind):
    from unittest.mock import Mock
    index = Mock()
    index.change_targets.return_value = []
    index.queue_change.return_value = []
    service = ResourceService(index_provider=index)
    module(kind).install_mutation_provider(service)
    return service


def test_device_service_preserves_numeric_conversion_and_rejects_before_owner(monkeypatch):
    from unittest.mock import Mock
    from management_portal import device_identity_backend as owner
    service = service_for("device")
    result = service.register("device", {"name": "device", "access_methods": {
        "ssh": {"host": "example.invalid", "port": "2222"}}}, actor="resident_aaaaaaaaaaaa")
    assert result["access_methods"]["ssh"]["port"] == 2222
    changed = service.update(result["resource_id"], {"name": "changed"}, actor="resident_aaaaaaaaaaaa")
    assert changed["name"] == "changed"
    callback = Mock(side_effect=AssertionError("invalid input reached owner"))
    monkeypatch.setattr(owner, "register_device", callback)
    with pytest.raises(ResourceMutationValidationError):
        service.register("device", {"name": "x", "access_methods": []}, actor="resident_aaaaaaaaaaaa")
    callback.assert_not_called()


def test_repo_automatic_path_and_default_remote_flow_through_real_service(tmp_path):
    import subprocess
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    service = service_for("repo")
    result = service.register("repo", {"path": str(root)}, actor="resource-auto-registration", automatic=True)
    changed = service.update(result["resource_id"], {"url": "https://example.invalid/contract.git"}, actor="resident_aaaaaaaaaaaa")
    assert changed["resource_id"] == result["resource_id"]
    url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], check=True, capture_output=True, text=True)
    assert url.stdout.strip() == "https://example.invalid/contract.git"


def test_article_automatic_path_keeps_placement_check_and_ignored_compiler(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from management_portal import article_registration, resource_identity_backend
    root = tmp_path / "article"
    root.mkdir()
    (root / "main.md").write_text("# Article\n")
    service = service_for("article")
    monkeypatch.setattr(resource_identity_backend, "project_ids_for_path", lambda path: ["contract-project"])
    registered = Mock(return_value={"id": "article_0123456789ab", "path": str(root)})
    monkeypatch.setattr(article_registration, "register_article", registered)
    result = service.register("article", {"path": str(root), "compiler": "ignored-markdown"}, automatic=True,
                              actor="resource-auto-registration")
    assert result["resource_id"] == "article_0123456789ab"
    assert registered.call_args.kwargs == {"project_ids": [], "primary_file": "", "compiler": "ignored-markdown"}
    with pytest.raises(ValueError, match="project_ids"):
        service.register("article", {"path": str(root)}, actor="resident_aaaaaaaaaaaa")
    assert registered.call_count == 1


@pytest.mark.parametrize("value", [None, "", " git_checkout "])
def test_repo_source_kind_only_defaults_when_omitted(value):
    with pytest.raises(ResourceMutationValidationError):
        validate_mutation_payload(contract("repo", "register"), {"path": "/tmp/repo", "source_kind": value},
                                  resource_type="repo", operation="register")


def test_todo_length_is_measured_after_trimming():
    schema = contract("mailbox_todo", "register")
    validate_mutation_payload(schema, {"user_id": "owner", "text": "  " + "x" * 100 + "  "},
                              resource_type="mailbox_todo", operation="register")
    with pytest.raises(ResourceMutationValidationError):
        validate_mutation_payload(schema, {"user_id": "owner", "text": "x" * 101},
                                  resource_type="mailbox_todo", operation="register")


def test_service_errors_do_not_echo_submitted_text(monkeypatch):
    service = service_for("device")
    secret = "submitted-private-value-never-in-errors"
    with pytest.raises(ResourceMutationValidationError) as failure:
        service.register("device", {"name": "x", "device_kind": secret}, actor="resident_aaaaaaaaaaaa")
    assert secret not in str(failure.value)
    assert secret not in str(failure.value.details)


def test_project_unicode_id_alias_tags_and_missing_value_keep_domain_semantics(project_registration):
    service, *_ = project_registration
    payload = {"id": "Δοκιμή", "project_type": " RESEARCH ", "tags": "a，b c", "future_field": True}
    result = service.register("project", payload, actor="resident_0123456789ab")
    assert result["resource_id"] == payload["id"]
    changed = service.update(payload["id"], {"field": "tags", "value": "b, a，b"}, actor="resident_0123456789ab")
    assert changed["tags"] == ["b", "a"]
    changed = service.update(payload["id"], {"field": "description"}, actor="resident_0123456789ab")
    assert changed["description"] == ""
    assert changed["project_type"] == "academic"


def test_experiment_body_precedence_trimmed_limits_and_domain_checks(experiment_service):
    service, root = experiment_service
    result = service.register("experiment", {"project_id": "OSForAgent", "title": " " + "t" * 200 + " ",
        "body": "chosen", "summary": "ignored", "status": " " + "s" * 80 + " "}, actor="resident_aaaaaaaaaaaa")
    path = next(root.glob("knowledge_base/experiment/*/experiment.md"))
    assert "chosen" in path.read_text() and "ignored" not in path.read_text()
    service.update(result["resource_id"], {"body": None, "summary": "ignored"}, actor="resident_aaaaaaaaaaaa")
    assert "None" in path.read_text() and "ignored" not in path.read_text()
    before = path.read_bytes()
    for changes in ({"status": "s" * 81}, {"status": "a\nb"}, {"body": "x" * 2_000_001}):
        with pytest.raises(ResourceMutationValidationError):
            service.update(result["resource_id"], changes, actor="resident_aaaaaaaaaaaa")
        assert path.read_bytes() == before


def test_article_real_automatic_registration_and_existing_placement_guard(tmp_path):
    from tests.cases.registry_support import write_projects
    project_root = tmp_path / "Project"
    article_root = project_root / "article"
    article_root.mkdir(parents=True)
    (article_root / "main.md").write_text("# Contract Article\nbody\n")
    write_projects({"projects": [{"id": "ContractProject", "path": str(project_root), "status": "active"}]})
    service = service_for("article")
    result = service.register("article", {"path": str(article_root), "compiler": "ignored-for-markdown"},
                              automatic=True, actor="resource-auto-registration")
    assert result["resource_id"].startswith("article_")
    registered = service.register("article", {"path": str(article_root), "project_ids": ["ContractProject"]},
                                  actor="resident_aaaaaaaaaaaa")
    assert registered["resource_id"] == result["resource_id"]
    with pytest.raises(ValueError, match="project_ids"):
        service.register("article", {"path": str(article_root), "project_ids": ["OtherProject"]}, actor="resident_aaaaaaaaaaaa")


def test_todo_service_cross_date_order_and_stale_version(active_users):
    from management_portal.resources.runtime import create_local_resource_service
    from management_portal.resources.errors import ResourceVersionConflict
    service = create_local_resource_service()
    a = service.register("mailbox_todo", {"user_id": "alice", "text": "a", "date": "2026-11-27"}, actor="alice")
    b = service.register("mailbox_todo", {"user_id": "alice", "text": "b", "date": "2026-11-28"}, actor="alice")
    changed = service.update(a["resource_id"], {"expected_version": 1, "order": {"after_id": b["resource_id"]}}, actor="alice")
    assert changed["attributes"]["date"] == "2026-11-28" and changed["version"] == 2
    with pytest.raises(ResourceVersionConflict):
        service.update(a["resource_id"], {"expected_version": 1, "text": "stale"}, actor="alice")
    with pytest.raises(KeyError):
        service.update(a["resource_id"], {"expected_version": 2, "text": "foreign"}, actor="bob")


def test_recovery_replacements_require_array_only_in_recovery_branch():
    schema = contract("repo", "update")
    validate_mutation_payload(schema, {"operation": "set_policy", "replacements": None}, resource_type="repo", operation="update")
    with pytest.raises(ResourceMutationValidationError) as error:
        validate_mutation_payload(schema, {"operation": "restore_registration", "replacements": None,
            "reason": "lost registration", "evidence_address": "workspace_0123456789ab/file/evidence.json",
            "evidence_sha256": "0" * 64, "checkout_moves": [{"from": "/old", "to": "/new"}]},
            resource_type="repo", operation="update")
    assert "/replacements" in [e["path"] for e in error.value.details["errors"]]
