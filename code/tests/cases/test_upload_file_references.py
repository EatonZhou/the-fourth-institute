"""Uploads use unified file reads with their existing authenticated-user rule.

:module: resource.service
"""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from management_portal import permission_system
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.resources import ResourceService, isa, upload_files
from management_portal.resources.collaboration_registry import CollaborationDescriptorStore
from tests.cases.permission_test_support import permissions, Fact  # noqa: F401

HANDLE = "upload_" + "a" * 24
RESIDENT = "resident_0123456789ab"


@pytest.fixture
def files(tmp_path, monkeypatch, permissions):
    root = tmp_path / "uploads"
    directory = root / HANDLE
    directory.mkdir(parents=True)
    monkeypatch.setattr(isa, "upload_root", lambda: root)
    monkeypatch.setenv("PORTAL_PUBLIC_ORIGIN", "https://portal.example")
    permissions.users("alice", "bob", category="intern")
    descriptors = CollaborationDescriptorStore()
    resident = {"schema": "tfi.collaboration-descriptor.v2", "resource_id": RESIDENT,
                "title": "Reader", "status": "active", "project_ids": [], "relations": [], "attributes": {}}
    descriptors.put(resident)
    permissions.place(Fact("resident", RESIDENT))
    service = ResourceService()
    monkeypatch.setattr(isa, "_service", lambda: service)
    kernel = isa.build_kernel()
    reference = {"upload_id": HANDLE}

    def write(name="report.txt", data=b"hello upload", ready=True):
        (directory / "meta.json").write_text(json.dumps({"upload_id": HANDLE,
            "file_name": name, "ready": ready, "created_by": "alice",
            "size": len(data), "media_type": "text/plain" if name.endswith(".txt") else "application/zip"}))
        (directory / name).write_bytes(data)
        return directory / name

    def call(instruction="ReadResource", actor="bob", address=None, **options):
        return kernel.execute(InstructionCall.from_payload({"instruction": instruction,
            "arguments": {"address": reference if address is None else address, **options}}),
            InstructionContext(caller=actor, entry="web"))

    write()
    return SimpleNamespace(root=root, directory=directory, reference=reference, service=service,
                           call=call, write=write, descriptors=descriptors, resident=resident,
                           permissions=permissions)


@pytest.mark.parametrize("actor", ["alice", "bob", RESIDENT])
def test_real_par_all_active_callers_and_all_address_forms(files, actor):
    from management_common.instance_manifest import current_instance

    url = "/api/uploads/" + HANDLE
    for address in [files.reference, url, "https://portal.example:443" + url,
                    current_instance().internal_web_origin() + url, url + "?download=1"]:
        value = files.call(actor=actor, address=address).raise_for_error()
        assert value["text"] == "hello upload" and value["file_reference"] == files.reference
        target = files.call("ResolveResource", actor, address).raise_for_error()
        assert target["operations"] == ["read"] and target["resource"] == {}
        assert target["target"]["address"] == url
        assert files.service.open_content(address, actor=actor)["content_size"] == 12
        ref = files.service.reference(address, actor=actor)
        assert ref["resource_id"] == "" and ref["file_reference"] == files.reference
        assert "resolved_path" not in ref
    assert isa.read_upload(HANDLE)["ready"] is True  # Mutable creation/consumer metadata stays compatible.


def test_explicit_deployment_origin_alias_is_readable_but_other_sites_are_not(files, monkeypatch):
    from management_common import site_config

    monkeypatch.setattr(site_config, "read_site_config", lambda: {
        "deployment": {"auth": {"public_origin_aliases": ["https://192.0.2.25"]}}})
    result = files.call(actor=RESIDENT, address="https://192.0.2.25/api/uploads/" + HANDLE).raise_for_error()
    assert result["text"] == "hello upload"
    for origin in ("https://192.0.2.26", "http://192.0.2.25", "https://192.0.2.25:444"):
        with pytest.raises(ValueError, match="another Portal origin"):
            upload_files.file_reference(origin + "/api/uploads/" + HANDLE)


@pytest.mark.parametrize("aliases", ["https://example.org", [None], ["https://example.org/path"],
                                     ["https://example.org?x=1"], ["https://example.org#x"]])
def test_invalid_deployment_origin_aliases_fail_closed(files, monkeypatch, aliases):
    from management_common import site_config

    monkeypatch.setattr(site_config, "read_site_config", lambda: {
        "deployment": {"auth": {"public_origin_aliases": aliases}}})
    with pytest.raises(ValueError):
        upload_files.file_reference("https://portal.example/api/uploads/" + HANDLE)


@pytest.mark.parametrize("actor", ["", "nobody", "resident_ffffffffffff", "service:portal"])
def test_anonymous_and_unregistered_principals_rejected_before_files(files, monkeypatch, actor):
    inspect = Mock(side_effect=AssertionError("anonymous read reached files"))
    monkeypatch.setattr(upload_files.secure_paths, "open_directory", inspect)
    for instruction in ("ReadResource", "ResolveResource"):
        if actor:
            result = files.call(instruction, actor)
            assert result.status == "failed", result.to_payload()
        else:
            with pytest.raises(InstructionError, match="经过认证"):
                files.call(instruction, actor)
    with pytest.raises(PermissionError):
        files.service.resolve(files.reference, actor=actor)
    inspect.assert_not_called()


def test_deleted_resident_is_rejected_on_isa_and_ranges(files):
    files.descriptors.put({**files.resident, "status": "deleted"})
    assert files.call(actor=RESIDENT).status == "failed"
    with pytest.raises(PermissionError):
        files.service.read_content_range(files.reference, actor=RESIDENT, start=0, end=2)


def test_metadata_and_binary_preview_never_open_payload(files, monkeypatch):
    files.write("archive.zip", b"PK\x00\xff")
    original = upload_files.secure_paths.open_child
    opened = []

    def open_child(directory, name, *args, **kwargs):
        opened.append(name)
        assert name != "archive.zip", "metadata opened payload"
        return original(directory, name, *args, **kwargs)

    monkeypatch.setattr(upload_files.secure_paths, "open_child", open_child)
    meta = files.call(meta_only=True).raise_for_error()
    assert meta["name"] == "archive.zip" and meta["size"] == 4 and meta["modified_at"]
    assert "text" not in meta and "base64" not in meta
    preview = files.call(preview=True).raise_for_error()
    assert preview["binary"] is True and not preview.get("text") and "base64" not in preview
    assert "meta.json" in opened


def test_text_preview_range_and_large_controlled_download(files):
    data = ("你好abc\n" * 200).encode()
    files.write(data=data)
    value = files.call(preview=True, max_bytes=7).raise_for_error()
    assert value["text"] == "你好a" and value["truncated"] and not value["binary"]
    assert files.service.read_content_range(files.reference, actor=RESIDENT, start=5, end=13)[0] == data[5:14]
    with pytest.raises(ValueError, match="range"):
        files.service.read_content_range(files.reference, actor="bob", start=-1, end=2)
    result = files.call(max_bytes=16).raise_for_error()
    assert result["version"] == hashlib.sha256(data).hexdigest()
    assert result["size"] == len(data) and result["download_url"].startswith("/api/resources/download/")
    token = result["download_url"].rsplit("/", 1)[-1]
    ticket = json.loads((files.root / "downloads" / (token + ".json")).read_text())
    assert ticket["address"] == files.reference and ticket["caller"] == "bob"


@pytest.mark.parametrize("suffix", ["?bad=1", "?download=0", "?download=1&download=1", "#part", "#", "/more", "/../meta.json", "%2fmeta.json"])
def test_invalid_upload_url_cannot_read(files, suffix):
    with pytest.raises(ValueError):
        files.service.resolve("https://portal.example/api/uploads/" + HANDLE + suffix, actor="bob")


@pytest.mark.parametrize("address", [
    "https://outside.example/api/uploads/" + HANDLE,
    "https://portal.example.attacker/api/uploads/" + HANDLE,
    "https://someone@portal.example/api/uploads/" + HANDLE,
    "http://portal.example/api/uploads/" + HANDLE,
    "//portal.example/api/uploads/" + HANDLE,
    {"upload_id": "../../secret"}, {"upload_id": HANDLE, "relative_path": "meta.json"},
])
def test_foreign_origins_and_noncanonical_references(files, address):
    result = files.call(address=address)
    assert result.error["code"] == "instruction.invalid_arguments", result.to_payload()


@pytest.mark.parametrize("state", ["not_ready", "absent", "pending_cleanup", "bad_name", "payload_symlink", "meta_symlink", "directory_symlink"])
def test_unavailable_or_unsafe_uploads(files, tmp_path, state):
    if state == "not_ready":
        files.write(ready=False)
    elif state == "absent":
        (files.directory / "report.txt").unlink()
    elif state == "pending_cleanup":
        from management_common.upload_retention import created, deleted
        created(files.directory, upload_id=HANDLE, created_at="2026-11-04T00:00:00Z")
        deleted(files.directory)
    elif state == "bad_name":
        (files.directory / "meta.json").write_text(json.dumps({"ready": True, "file_name": "../outside"}))
    else:
        path = (files.directory / "report.txt" if state == "payload_symlink" else
                files.directory / "meta.json" if state == "meta_symlink" else files.directory)
        target = tmp_path / "moved"
        path.rename(target)
        path.symlink_to(target, target_is_directory=target.is_dir())
    result = files.call(meta_only=True)
    assert result.status == "failed", result.to_payload()
    for method in (files.service.resolve, files.service.read_content, files.service.open_content):
        with pytest.raises((FileNotFoundError, ValueError)):
            method(files.reference, actor="bob")


def test_upload_domain_does_not_bypass_ordinary_par_and_batch_checks(files):
    from management_portal.resources.providers import install_resolvers
    from management_portal.resources.workspace_store import get_workspace_store

    identifier = "workspace_0123456789ab"
    root = files.root.parent / "workspace"
    root.mkdir()
    (root / "ordinary.txt").write_text("protected")
    get_workspace_store().ensure(identifier, str(root))
    files.permissions.place(Fact("workspace", identifier))
    files.permissions.deny("bob", identifier)
    ref = {"workspace_id": identifier, "relative_path": "ordinary.txt"}
    assert files.call(address=ref).error["details"]["reason"] == "denied"
    service = ResourceService(resolver_loader=install_resolvers)
    with pytest.raises(PermissionError):
        service.read_content(ref, actor="bob")
    declaration = catalog.declaration("ReadResource")
    decisions = permission_system.authorize_reads(declaration,
        [{"address": files.reference}, {"address": identifier}], InstructionContext(caller="bob", entry="web"))
    assert decisions[0] is None and decisions[1].details["reason"] == "denied"
    rejected = permission_system.authorize_reads(declaration, [{"address": files.reference}],
                                                 InstructionContext(caller="nobody", entry="web"))
    assert rejected[0] is not None
