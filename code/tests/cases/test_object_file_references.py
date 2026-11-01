"""Object-relative references survive moves and enforce the read boundary.

:module: resource.service
:design: decision_4aa13bc21e8e
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal.resources import ResourceService
from management_portal.resources.access import ResourceAccessPolicy
from tests.cases.permission_test_support import permissions, Fact


@pytest.fixture(params=["workspace", "repo", "article"])
def par_files(request, tmp_path, monkeypatch, permissions):
    """Real owners, access policy, PAR scheme and Kernel; no authorization doubles."""
    from management_common.portal_database import portal_database
    from management_portal import article_registry, resource_identity_backend
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.hatg.store import ExecutionStore
    from management_portal.resources import isa
    from management_portal.resources.providers import install_resolvers
    from management_portal.resources.workspace_store import get_workspace_store
    from tests.cases.registry_support import put_registry_document, remove_registry_document

    kind = request.param
    identifier = kind + "_0123456789ab"
    root = tmp_path / kind
    root.mkdir()
    (root / "report.txt").write_text("delivered")
    collection = (resource_identity_backend.REPO_COLLECTION if kind == "repo"
                  else article_registry.ARTICLE_COLLECTION)
    if kind == "workspace":
        get_workspace_store().ensure(identifier, str(root))
    else:
        placement = ({"canonical_checkout": str(root), "checkouts": [{"path": str(root)}]}
                     if kind == "repo" else
                     {"canonical_root": str(root), "roots": [
                         {"path": str(root), "primary_file": "report.txt", "role": "canonical"}]})
        put_registry_document(collection, identifier, {"id": identifier, "status": "active", **placement})
    permissions.users("alice", "bob", category="intern")
    permissions.place(Fact(kind, identifier))
    # The default scheme grants everyone shared reads and public management.
    # Remove those two entries in this isolated copy to exercise no_permit;
    # retain the real scheme, request mapping, engine and owner lookups.
    with permissions.store.transaction() as connection:
        permissions.store.delete(
            connection, where="p_node = %s AND r_node = ANY(%s)",
            parameters=("@principals", ["@shared", "@public"]),
        )
    permissions.grant("alice", identifier, "@viewer")
    service = ResourceService(resolver_loader=install_resolvers)
    monkeypatch.setattr(isa, "_service", lambda: service)
    kernel = InstructionKernel(isa.binding(), store=ExecutionStore(portal_database(schema="file_refs_par")))
    reference = {kind + "_id": identifier, "relative_path": "report.txt"}

    def call(instruction="ReadResource", *, actor="alice", relative="report.txt", **options):
        return kernel.execute(InstructionCall.from_payload({
            "instruction": instruction,
            "arguments": {"address": {**reference, "relative_path": relative}, **options},
        }), InstructionContext(caller=actor, entry="web"))

    def unregister():
        if kind == "workspace":
            from management_portal.resources.workspace_store import now
            store = get_workspace_store()
            with store.database.transaction() as connection:
                store.mark_deleted(identifier, deleted_at=now(), deleted_by="alice", connection=connection)
        else:
            remove_registry_document(collection, identifier)
        permissions.implementation.unregister_resources([identifier])

    return SimpleNamespace(call=call, service=service, root=root, reference=reference,
                           identifier=identifier, permissions=permissions, unregister=unregister)


READ_MODES = [
    pytest.param("ReadResource", {}, id="read"),
    pytest.param("ReadResource", {"preview": True}, id="preview"),
    pytest.param("ReadResource", {"meta_only": True}, id="metadata"),
    pytest.param("ResolveResource", {}, id="resolve"),
]


@pytest.mark.parametrize("instruction,options", READ_MODES)
@pytest.mark.parametrize("reason", ["no_permit", "denied"])
def test_real_par_refusal_precedes_file_access(par_files, monkeypatch, instruction, options, reason):
    from management_portal.resources.providers import folder_files

    files = par_files
    if reason == "denied":
        files.permissions.grant("bob", files.identifier, "@viewer")
        denial = files.permissions.deny("bob", files.identifier)
    inspect = Mock(side_effect=AssertionError("file inspected before authorization"))
    monkeypatch.setattr(folder_files, "describe", inspect)
    monkeypatch.setattr(folder_files, "open_target", inspect)
    for relative in ("report.txt", "absent.txt"):
        result = files.call(instruction, actor="bob", relative=relative, **options)
        assert result.status == "failed", result.to_payload()
        assert result.error["code"] == "instruction.not_found", result.error
        details = {"reason": reason, "target_id": files.identifier,
                   "action": instruction, "a": "@viewer"}
        if reason == "denied":
            details["deny_entry_id"] = denial.entry_id
        assert result.error["details"] == details
        inspect.assert_not_called()
    # The service boundary enforces the same real scheme without a Kernel.
    for method in (files.service.resolve, files.service.read_content):
        with pytest.raises(PermissionError):
            method(files.reference, actor="bob")
    with pytest.raises(PermissionError):
        files.service.read_content_range(files.reference, actor="bob", start=0, end=2)
    inspect.assert_not_called()


@pytest.mark.parametrize("instruction,options", READ_MODES)
@pytest.mark.parametrize("object_state", ["deleted", "never_registered"])
def test_real_par_distinguishes_missing_file_and_object(par_files, monkeypatch, instruction, options, object_state):
    from management_portal.resources.providers import folder_files

    files = par_files
    delivered = files.call(instruction, **options).raise_for_error()
    if instruction == "ReadResource" and not options.get("meta_only"):
        assert delivered["text"] == "delivered"
    missing_file = files.call(instruction, relative="absent.txt", **options)
    assert missing_file.error["code"] == "instruction.not_found"
    assert missing_file.error["details"] == {"reason": "file_not_found"}
    # A nonadmin with a shared read grant may reach the owner lookup
    # even after its object-specific permission entries have been removed.
    files.permissions.grant("alice", "@shared", "@viewer")
    if object_state == "deleted":
        files.unregister()
    else:
        files.reference[next(key for key in files.reference if key != "relative_path")] = (
            files.identifier.rpartition("_")[0] + "_ffffffffffff")
    inspect = Mock(side_effect=AssertionError("deleted object reached its files"))
    monkeypatch.setattr(folder_files, "describe", inspect)
    monkeypatch.setattr(folder_files, "open_target", inspect)
    missing_object = files.call(instruction, **options)
    assert missing_object.error["code"] == "instruction.not_found", missing_object.to_payload()
    assert missing_object.error["details"] == {"reason": "object_not_found"}
    inspect.assert_not_called()


@pytest.mark.parametrize("instruction,options", READ_MODES)
def test_real_par_missing_object_without_grant_keeps_read_denial(par_files, monkeypatch, instruction, options):
    from management_portal.resources.providers import folder_files

    par_files.unregister()
    inspect = Mock(side_effect=AssertionError("unregistered object reached its files"))
    monkeypatch.setattr(folder_files, "describe", inspect)
    monkeypatch.setattr(folder_files, "open_target", inspect)
    result = par_files.call(instruction, actor="bob", **options)
    assert result.error["code"] == "instruction.not_found", result.error
    assert result.error["details"]["reason"] == "no_permit"
    assert result.error["details"]["target_id"] == par_files.identifier
    inspect.assert_not_called()


@pytest.mark.parametrize("instruction,options", READ_MODES)
@pytest.mark.parametrize("name", ["literal\\name.txt", "with\nnewline.txt"])
def test_real_par_reads_native_posix_filenames(par_files, instruction, options, name):
    import os

    if os.name == "nt":
        pytest.skip("These filenames are invalid on Windows")
    (par_files.root / name).write_text("native filename")
    result = par_files.call(instruction, relative=name, **options).raise_for_error()
    if instruction == "ReadResource" and not options.get("meta_only"):
        assert result["text"] == "native filename"


@pytest.fixture(params=["workspace", "repo", "article"])
def files(request, tmp_path, monkeypatch):
    kind = request.param
    identifier = kind + "_0123456789ab"
    root = tmp_path / "original"
    root.mkdir()
    (root / "report.txt").write_text("delivered")
    state = {"root": root, "live": True}
    policy = Mock(spec=ResourceAccessPolicy)
    service = ResourceService(access_policy=policy)

    def resolve(address, **context):
        if not state["live"]:
            raise KeyError(identifier)
        assert address == identifier, "authorize the object before visiting a file"
        return {"root_id": identifier, "resource_type": kind, "target_kind": kind,
                "record": {"resource_id": identifier, "attributes": {"path": str(state["root"])}},
                "checkout_path": str(state["root"]), "article_root": str(state["root"]), "exists": True}

    service.register_resolver(kind, resolve)
    return SimpleNamespace(service=service, state=state, policy=policy, kind=kind, identifier=identifier,
                           reference={kind + "_id": identifier, "relative_path": "report.txt"})


@pytest.fixture
def resource_isa(files, monkeypatch):
    from management_portal.resources import isa

    monkeypatch.setattr(isa, "_service", lambda: files.service)
    return isa


def test_structured_file_reference_survives_move(files, tmp_path):
    assert files.service.read_content(files.reference, actor="alice")[0] == b"delivered"
    moved = tmp_path / "moved"
    files.state["root"].rename(moved)
    files.state["root"] = moved
    assert files.service.read_content(files.reference, actor="alice")[0] == b"delivered"
    assert files.service.read_content(files.identifier + "/file/report.txt", actor="alice")[0] == b"delivered"
    assert files.service.read_content_range(files.reference, actor="alice", start=1, end=3)[0] == b"eli"


def test_file_reference_failures_are_distinct(files):
    missing = {**files.reference, "relative_path": "missing.txt"}
    with pytest.raises(FileNotFoundError):
        files.service.resolve(missing, actor="alice")
    files.policy.authorize_read.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        files.service.resolve(missing, actor="alice")
    files.state["live"] = False
    with pytest.raises(KeyError):
        files.service.resolve(files.reference, actor="alice")


@pytest.mark.parametrize("relative", ["../outside", "a/../outside", "/etc/passwd", "C:/temp/a", "a//b"])
def test_file_reference_rejects_unsafe_paths(files, relative):
    with pytest.raises(ValueError):
        files.service.read_content({**files.reference, "relative_path": relative}, actor="alice")


def test_file_reference_refuses_symlink_escape(files, tmp_path):
    outside = tmp_path / "outside"
    outside.write_text("not readable")
    (files.state["root"] / "escape").symlink_to(outside)
    with pytest.raises(ValueError):
        files.service.read_content({**files.reference, "relative_path": "escape"}, actor="alice")


@pytest.mark.parametrize('name', ['literal\\name', 'with\nnewline'])
def test_file_reference_preserves_legal_posix_filename(files, name):
    import os
    if os.name == 'nt':
        pytest.skip('These filenames are invalid on Windows')
    (files.state['root'] / name).write_text('literal file name')
    assert files.service.read_content({**files.reference, 'relative_path':name}, actor='alice')[0] == b'literal file name'


def test_structured_reference_has_no_path_fallback(files):
    with pytest.raises(ValueError):
        files.service.resolve({**files.reference, "absolute_path": str(files.state["root"] / "report.txt")}, actor="alice")
    with pytest.raises(ValueError):
        files.service.resolve({"workspace_id": "repo_0123456789ab", "relative_path": "report.txt"}, actor="alice")


def test_literal_percent_filename_and_directory_listing(files):
    import json
    from urllib.parse import parse_qs, urlsplit

    relative = "literal%20name.txt"
    (files.state["root"] / relative).write_text("literal percent")
    reference = {**files.reference, "relative_path": relative}
    assert files.service.read_content(reference, actor="alice")[0] == b"literal percent"
    directory = {**files.reference, "relative_path": ""}
    listing = json.loads(files.service.read_content(directory, actor="alice")[0])
    entry = next(item for item in listing["entries"] if item["name"] == relative)
    assert entry["file_reference"] == reference
    assert files.service.read_content(entry["address"], actor="alice")[0] == b"literal percent"
    resolution = files.service.resolve(reference, actor="alice")
    linked = parse_qs(urlsplit(resolution["portal_url"]).query)["address"][0]
    assert files.service.read_content(linked, actor="alice")[0] == b"literal percent"


def test_access_denial_precedes_file_inspection(files, monkeypatch):
    from management_portal.resources.providers import folder_files

    inspect = Mock(side_effect=AssertionError("file inspected before permission"))
    monkeypatch.setattr(folder_files, "open_target", inspect)
    files.policy.authorize_read.side_effect = PermissionError("denied")
    with pytest.raises(PermissionError):
        files.service.read_content(files.reference, actor="alice")
    inspect.assert_not_called()


def test_read_and_resolve_instructions_accept_file_reference(files, resource_isa):
    isa = resource_isa
    inv = SimpleNamespace(argument={"address": files.reference}.get, caller="alice")
    assert isa.read_resource(inv)["text"] == "delivered"
    assert isa.resolve_resource(inv)["target"]["file_reference"] == files.reference


def test_persistent_context_reference_and_offline_migration(files, tmp_path):
    from management_portal.resource_refs import normalize_stored_resource_references, migrate_context_file_references

    address = files.identifier + "/file/report.txt"
    saved = normalize_stored_resource_references([{"address": address, "title": "Delivery report"}])[0]
    assert saved["file_reference"] == files.reference
    context = {"resources": [{"address": address, "custom": "keep"}], "text": "old /absolute/report.txt"}
    migrated = migrate_context_file_references(context)
    assert migrated["resources"][0]["file_reference"] == files.reference
    assert migrated["resources"][0]["custom"] == "keep"
    assert migrated["text"] == context["text"]
    assert "file_reference" not in context["resources"][0]
    assert migrate_context_file_references(migrated) == migrated
    moved = tmp_path / "delivery-moved"
    files.state["root"].rename(moved)
    files.state["root"] = moved
    assert files.service.read_content(saved["file_reference"], actor="alice")[0] == b"delivered"
    files.state["live"] = False
    with pytest.raises(KeyError):
        files.service.read_content(saved["file_reference"], actor="alice")


def test_formal_instruction_authorization_uses_object_identity(files, resource_isa):
    isa = resource_isa
    from management_portal.hatg.catalog.resource import DECLARATIONS

    for name in ("ReadResource", "ResolveResource"):
        declaration = next(item for item in DECLARATIONS if item.name == name)
        arguments = {"address": files.reference}
        declaration.validate_arguments(arguments)
        assert isa.binding().authorization_arguments(declaration, arguments, None) == {"address": files.identifier}


def test_kernel_executes_structured_read_with_normal_permission_slot(files, resource_isa):
    isa = resource_isa
    from management_common.portal_database import portal_database
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.hatg.store import ExecutionStore

    seen = []
    def authorize(declaration, arguments, context):
        seen.append((declaration.name, arguments["address"]))
        assert arguments["address"] == files.identifier

    kernel = InstructionKernel(isa.binding(), store=ExecutionStore(portal_database(schema="file_refs_isa")), authorize=authorize)
    result = kernel.execute(InstructionCall.from_payload({"instruction": "ReadResource", "arguments": {"address": files.reference}}),
                            InstructionContext(caller="alice", entry="web"))
    assert result.raise_for_error()["text"] == "delivered"
    assert seen == [("ReadResource", files.identifier)]


def test_isa_failure_details_and_preview(files, resource_isa):
    isa = resource_isa
    from management_portal.hatg.contracts import InstructionError

    inv = SimpleNamespace(argument={"address": files.reference, "preview": True, "max_bytes": 3}.get, caller="alice")
    assert isa.read_resource(inv)["text"] == "del"
    missing = SimpleNamespace(argument={"address": {**files.reference, "relative_path": "missing"}}.get, caller="alice")
    with pytest.raises(InstructionError) as exc:
        isa.read_resource(missing)
    assert exc.value.details["reason"] == "file_not_found"
    files.state["live"] = False
    with pytest.raises(InstructionError) as exc:
        isa.resolve_resource(missing)
    assert exc.value.details["reason"] == "object_not_found"


def test_registered_providers_follow_current_placement(files, tmp_path, monkeypatch):
    from management_portal import permission_system, resource_identity_backend, article_registry
    from management_portal.resources.providers import install_resolvers
    from management_portal.resources.workspace_store import get_workspace_store
    from tests.cases.registry_support import put_registry_document, remove_registry_document

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    service = ResourceService(resolver_loader=install_resolvers, access_policy=files.policy)
    record = {"id": files.identifier, "status": "active"}
    collection = resource_identity_backend.REPO_COLLECTION if files.kind == "repo" else article_registry.ARTICLE_COLLECTION

    def place(path):
        if files.kind == "workspace":
            get_workspace_store().ensure(files.identifier, str(path), move=True)
        else:
            if files.kind == "repo":
                record.update(canonical_checkout=str(path), checkouts=[{"path": str(path)}])
            else:
                record.update(canonical_root=str(path), roots=[{"path": str(path), "primary_file": "report.txt", "role": "canonical"}])
            put_registry_document(collection, files.identifier, record)

    place(files.state["root"])
    assert service.read_content(files.reference, actor="alice")[0] == b"delivered"
    new_root = tmp_path / "registered-move"
    files.state["root"].rename(new_root)
    place(new_root)
    # Recreate the old location with unrelated content: a stale path must never win.
    files.state["root"].mkdir()
    (files.state["root"] / "report.txt").write_text("unrelated old path")
    assert service.read_content(files.reference, actor="alice")[0] == b"delivered"
    if files.kind == "workspace":
        store = get_workspace_store()
        store.database.write(lambda c: c.execute(f"DELETE FROM {store.table} WHERE workspace_id=%s", (files.identifier,)))
    else:
        remove_registry_document(collection, files.identifier)
    with pytest.raises(KeyError):
        service.read_content(files.reference, actor="alice")

