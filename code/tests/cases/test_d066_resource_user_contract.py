"""Plan d066: separate upload modes and account authority without changing owners.

:module: isa.instructions
:importance: P1
"""
from types import SimpleNamespace

import pytest

from management_portal import web_isa
from management_portal.hatg.catalog import declaration, instruction_catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.resources import isa


def invoke(name, arguments, caller="alice"):
    declared = declaration(name)
    declared.validate_arguments(arguments)
    return Invocation(declared, InstructionCall(instruction=name, arguments=arguments),
                      InstructionContext(caller=caller, entry="web"), "inst_res_00000000000000000000")


@pytest.mark.parametrize("name,arguments", [
    ("GetPermissionSchemeActivationPreview", {"scheme_id": "default"}),
    ("GetPermissionSchemeCreatePreview", {"scheme_id": "custom", "source_scheme_id": "default"}),
    ("GetPermissionSchemeUpdatePreview", {"scheme_id": "custom", "rules": {}, "diff": True}),
])
def test_permission_preview_does_not_initialize_resource_service(monkeypatch, name, arguments):
    from management_portal import permission_system, society_service
    from management_portal.permission_system import facts
    from management_portal.resources import runtime

    monkeypatch.setattr(isa, "_service", lambda: pytest.fail("readonly preview initialized ResourceService"))
    seen = []

    def collect(service=None, *, read_only=False, resident_workspaces=None, index_snapshot=None):
        assert service is None and read_only is True
        assert resident_workspaces() == {"resident_123456789abc": "workspace_123456789abc"}
        assert index_snapshot() == {"records": []}
        return ["current owner facts"]

    monkeypatch.setattr(facts, "collect_portal_facts", collect)
    monkeypatch.setattr(runtime, "readonly_index_snapshot", lambda: {"records": []})
    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        projection=SimpleNamespace(list_permission_resident_workspaces=lambda: {
            "resident_123456789abc": "workspace_123456789abc"})))

    def manage(name, arguments, *, actor, collect_facts, expected_version):
        seen.append((name, arguments, actor, callable(collect_facts)))
        assert collect_facts() == ["current owner facts"]
        return {"valid": True}

    monkeypatch.setattr(permission_system, "manage", manage)
    invocation = invoke(name, arguments)
    assert isa.permission_instruction(invocation) == {"valid": True}
    assert seen == [(name, arguments, "alice", True)]


def test_instruction_permission_check_uses_society_reader_not_subject(monkeypatch):
    from management_portal import permission_system, society_service

    calls = []
    public = {"lineage_id": "lineage_123456789abc", "operations": []}
    derived = {**public, "_permission_operations": [{
        "element": "ApplyLineageChanges:lineage_id", "resource_id": public["lineage_id"]}]}

    def read(arguments, *, actor):
        calls.append((arguments, actor))
        return derived

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        lineage=SimpleNamespace(describe_lineage_change_permissions=read)))
    monkeypatch.setattr(isa, "_service", lambda: pytest.fail("instruction check initialized ResourceService"))

    def manage(name, arguments, *, actor, collect_facts, expected_version, derive_arguments=None):
        assert name == "CheckInstructionPermission"
        assert actor == "reader" and arguments["principal_id"] == "subject"
        assert derive_arguments is not None
        return derive_arguments(arguments["instruction"], arguments["arguments"],
                                actor=actor, principal_id=arguments["principal_id"])

    monkeypatch.setattr(permission_system, "manage", manage)
    result = isa.permission_instruction(invoke("CheckInstructionPermission", {
        "principal_id": "subject", "instruction": "ApplyLineageChanges", "arguments": public}, "reader"))
    assert result == derived
    assert calls == [(public, "reader")]

    def denied(arguments, *, actor):
        assert actor == "reader"
        raise PermissionError("reader revoked")

    monkeypatch.setattr(society_service, "get_society_service", lambda: SimpleNamespace(
        lineage=SimpleNamespace(describe_lineage_change_permissions=denied)))
    with pytest.raises(PermissionError, match="reader revoked"):
        isa.permission_instruction(invoke("CheckInstructionPermission", {
            "principal_id": "subject", "instruction": "ApplyLineageChanges", "arguments": public}, "reader"))


def test_upload_modes_have_distinct_inputs_and_preserve_real_caller(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    monkeypatch.setattr(isa, "upload_root", lambda: root)
    calls = []
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(read_content=lambda address, **kw:
        calls.append((address, kw["actor"])) or (b"hello", "text/plain", {"filename": "source.txt"})))
    copied = isa.create_upload_from_file(invoke("CreateUploadFromFile", {
        "purpose": "conversation_attachment", "source_address": "workspace_aaaaaaaaaaaa/file/source.txt"}, "bob"))
    assert copied["ready"] is True and "upload_url" not in copied
    assert calls == [("workspace_aaaaaaaaaaaa/file/source.txt", "bob")]
    assert isa.read_upload(copied["upload_id"])["created_by"] == "bob"
    pending = isa.create_upload(invoke("CreateUpload", {
        "purpose": "conversation_attachment", "file_name": "new.txt", "media_type": "text/plain", "size": 5}))
    assert pending["ready"] is False and pending["upload_url"]
    assert len(calls) == 1
    with pytest.raises(InstructionError):
        invoke("CreateUpload", {"purpose": "conversation_attachment", "source_address": "old"})
    with pytest.raises(InstructionError):
        invoke("CreateUploadFromFile", {"purpose": "conversation_attachment"})


def test_upload_application_purposes_are_installed_owner_contributions(tmp_path, monkeypatch):
    from management_portal import portal_features

    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    for name in ("CreateUpload", "CreateUploadFromFile"):
        assert not next(p for p in declaration(name).parameters if p.name == "purpose").enum
    monkeypatch.setattr(portal_features, "contributions", lambda hook: ())
    for purpose in ("paper_pdf", "undeclared_purpose"):
        with pytest.raises(InstructionError):
            isa.create_upload(invoke("CreateUpload", {"purpose": purpose, "file_name": "a.pdf",
                "media_type": "application/pdf", "size": 4}))
    assert not (tmp_path / "uploads").exists()
    monkeypatch.setattr(isa, "_service", lambda: pytest.fail("unknown purpose read the source"))
    with pytest.raises(InstructionError):
        isa.create_upload_from_file(invoke("CreateUploadFromFile", {
            "purpose": "paper_pdf", "source_address": "workspace_aaaaaaaaaaaa/file/a.pdf"}))


def test_installed_application_upload_policy_checks_bytes_for_both_modes(tmp_path, monkeypatch):
    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    args = {"purpose": "paper_pdf", "file_name": "a.pdf", "media_type": "application/pdf", "size": 3}
    upload = isa.create_upload(invoke("CreateUpload", args))
    with pytest.raises(InstructionError):
        isa.complete_upload(upload["upload_id"], b"bad", actor="alice")
    assert isa.read_upload(upload["upload_id"])["ready"] is False
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(read_content=lambda *a, **kw:
        (b"bad", "application/pdf", {"filename": "a.pdf"})))
    with pytest.raises(InstructionError):
        isa.create_upload_from_file(invoke("CreateUploadFromFile", {
            "purpose": "paper_pdf", "source_address": "workspace_aaaaaaaaaaaa/file/a.pdf"}))


def test_upload_completion_rechecks_installed_purpose_before_writing(tmp_path, monkeypatch):
    from pathlib import Path
    from management_portal import portal_features

    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    upload = isa.create_upload(invoke("CreateUpload", {"purpose": "paper_pdf", "file_name": "a.pdf",
        "media_type": "application/pdf", "size": 5}))
    monkeypatch.setattr(portal_features, "contributions", lambda hook: ())
    with pytest.raises(InstructionError):
        isa.complete_upload(upload["upload_id"], b"%PDF-", actor="alice")
    meta = isa.read_upload(upload["upload_id"])
    assert meta["ready"] is False and not Path(meta["path"]).exists()


def test_notice_board_upload_purpose_is_absent_without_its_application(tmp_path, monkeypatch):
    from management_portal import portal_features

    monkeypatch.setattr(isa, "upload_root", lambda: tmp_path / "uploads")
    monkeypatch.setattr(portal_features, "contributions", lambda hook: ())
    with pytest.raises(InstructionError):
        isa.create_upload(invoke("CreateUpload", {"purpose": "post_image", "file_name": "a.png",
            "media_type": "image/png", "size": 3}))
    assert not (tmp_path / "uploads").exists()


@pytest.mark.parametrize("change", [{"status": "disabled"}, {"category": "portal_admin"}])
def test_profile_update_rejects_account_authority_before_touching_owner(change, monkeypatch):
    monkeypatch.setattr(web_isa, "web", lambda *args: pytest.fail("profile route reached account owner"))
    with pytest.raises(InstructionError) as failure:
        web_isa.update_user(SimpleNamespace(caller="alice", argument={"user_id": "bob", "changes": change}.get))
    assert failure.value.code == "instruction.invalid_arguments"


def test_new_account_instructions_keep_user_version_and_strict_fields(monkeypatch):
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(_revision=lambda: 42))
    for name, field, value in (("UpdateUserStatus", "status", "disabled"),
                               ("UpdateUserCategory", "category", "intern")):
        inv = invoke(name, {"user_id": "bob", field: value})
        assert web_isa.version_of(inv, "bob") == 42
        assert name in web_isa.HANDLERS
        with pytest.raises(InstructionError):
            invoke(name, {"user_id": "bob", field: value, "changes": {"display_name": "hidden"}})


def test_resource_type_and_favorite_names_have_no_old_alias():
    catalog = instruction_catalog()
    assert "UpdateResourceType" not in catalog
    assert "UpdateUserNavigation" not in catalog
    assert "SetResourceTypeEnabled" in isa.HANDLERS
    assert "SetUserNavigationFavorite" in web_isa.HANDLERS
    invoke("SetResourceTypeEnabled", {"resource_type": "device", "enabled": False})
    invoke("SetUserNavigationFavorite", {"user_id": "alice", "object_id": "resident_aaaaaaaaaaaa", "favorite": True})


def test_single_resident_move_uses_objects_contract_and_explicit_empty_source():
    assert "MoveResident" not in instruction_catalog()
    invoke("MoveLineageObjects", {"lineage_id": "lineage_aaaaaaaaaaaa",
        "target_lineage_id": "lineage_bbbbbbbbbbbb", "object_ids": ["resident_cccccccccccc"],
        "allow_empty_source": True})
    with pytest.raises(InstructionError):
        invoke("MoveLineageObjects", {"lineage_id": "lineage_aaaaaaaaaaaa",
            "object_ids": ["resident_cccccccccccc"], "allow_empty_source": "true"})


def test_account_owner_changes_keep_admin_guard_and_session_revocation(monkeypatch):
    from management_portal import permission_system
    from management_portal.permission_system.categories import accounts
    from tests.cases.permission_test_support import Permissions, portal_admin

    monkeypatch.setattr(permission_system, "configured_mode", lambda: permission_system.OPEN)
    Permissions.account("alice")
    Permissions.account("bob")
    portal_admin("alice")
    store = accounts()
    calls = []

    def account_action(caller, payload):
        calls.append((caller, payload))
        return {"user": store.upsert_user(payload["username"], actor=caller,
            status=payload.get("status"), revoke_sessions=bool(payload.get("revoke_sessions")))}

    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        _PORTAL_ACCESS=store, _require_authenticated_actor=lambda _caller: None,
        _admin_access_action=account_action))
    with pytest.raises(InstructionError) as no_admin:
        web_isa.update_user_status(invoke("UpdateUserStatus", {"user_id": "alice", "status": "disabled"}, "bob"))
    assert no_admin.value.code == "instruction.forbidden" and not calls
    changed = web_isa.update_user_category(invoke("UpdateUserCategory", {"user_id": "bob", "category": "intern"}))
    assert changed["category_changed"] is True and accounts().user("bob")["category"] == "intern"
    web_isa.update_user_status(invoke("UpdateUserStatus", {"user_id": "bob", "status": "disabled"}))
    assert accounts().user("bob")["status"] == "disabled"
    assert calls[-1][1]["revoke_sessions"] is True
    with pytest.raises(InstructionError):
        web_isa.update_user_status(invoke("UpdateUserStatus", {"user_id": "alice", "status": "disabled"}))
    assert accounts().user("alice")["status"] == "active"
    with pytest.raises(InstructionError):
        web_isa.update_user_category(invoke("UpdateUserCategory", {"user_id": "alice", "category": "member"}))
    assert accounts().user("alice")["category"] == "portal_admin"
