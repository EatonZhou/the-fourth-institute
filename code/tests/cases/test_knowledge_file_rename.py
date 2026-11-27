"""Primary filename changes preserve Knowledge identity and resumable ownership.

:module: workflow.knowledge
:design: decision_3ece26686464
"""
from pathlib import Path
from unittest.mock import Mock

import pytest

from knowledge_board_lan import knowledge_source as source
from management_portal.resources import knowledge_move as move
from management_portal.resources.directory_registrations import directory_writer, locations
from management_common.instance_locations import stored_location
from management_portal.resources.errors import ResourceVersionConflict
from tests.cases.test_knowledge_file_create import file_service  # noqa: F401
from tests.cases.test_knowledge_move_action import move_service, ACTOR  # noqa: F401
from tests.cases.permission_test_support import permissions  # noqa: F401


def invoke(service, rid, **changes):
    payload = {"filename": "新的文件.md", "expected_version": 3, **changes}
    return service.update_knowledge_file_name(
        rid, payload["filename"], payload["expected_version"],
        actor=ACTOR, operation_id=payload.get("operation_id", ""),
    )


def test_primary_identity_content_companions_permissions_and_replay(file_service, permissions):
    service, index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    before = old.read_text()
    companion = old.parent / rid / "image.png"
    identity = companion.stat().st_ino
    result = invoke(service, rid, operation_id="rename-one")
    assert result["status"] == "completed", result
    assert result["version"]["number"] == 4
    new = old.with_name("新的文件.md")
    assert not old.exists() and new.is_file()
    assert new.read_text().split("---", 2)[2] == before.split("---", 2)[2]
    assert source._yaml_front_matter(new.read_text())["version"]["created_by"] == source._yaml_front_matter(before)["version"]["created_by"]
    assert companion.stat().st_ino == identity
    assert Path(service.resolve(rid, actor=ACTOR)["resolved_path"]) == new
    assert Path(service.resolve(f"{rid}/file/{rid}/image.png", actor=ACTOR)["resolved_path"]) == companion
    assert invoke(service, rid, operation_id="rename-one") == result
    same = invoke(service, rid, expected_version=4)
    assert same["status"] == "completed" and same["version"]["number"] == 4
    with pytest.raises(ResourceVersionConflict):
        invoke(service, rid, filename="other.md")
    with pytest.raises(ValueError, match="different arguments"):
        invoke(service, rid, filename="other.md", operation_id="rename-one")


@pytest.mark.parametrize("filename", ["../bad.md", ".hidden.md", "bad.index.md", "a/b.md", "a\\b.md", "x.txt", "NUL.md", " bad.md", "x\x00.md"])
def test_invalid_names_leave_files_and_version_unchanged(file_service, permissions, filename):
    service, _index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    old = Path(records[0]["path"])
    before = old.read_bytes()
    with pytest.raises(ValueError):
        invoke(service, items[0]["resource_id"], filename=filename)
    assert old.read_bytes() == before


def test_denied_and_occupied_targets_never_overwrite(file_service, permissions, tmp_path):
    service, _index, items, records = file_service
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    before = old.read_bytes()
    with pytest.raises(PermissionError):
        invoke(service, rid)
    permissions.grant(ACTOR, "AlignMe")
    target = old.with_name("新的文件.md")
    target.write_text("someone else's file")
    with pytest.raises(FileExistsError):
        invoke(service, rid)
    target.unlink()
    target.symlink_to(tmp_path / "absent")
    with pytest.raises((FileExistsError, ValueError)):
        invoke(service, rid)
    assert old.read_bytes() == before


class Crash(BaseException):
    pass


@pytest.mark.parametrize("point", ["publish", "commit", "projection"])
def test_interruption_retries_original_operation_without_duplicate_version(file_service, permissions, monkeypatch, point):
    service, index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    with monkeypatch.context() as patch:
        if point == "projection":
            patch.setattr(index, "project_relocated", Mock(side_effect=OSError("injected index failure")))
            pending = invoke(service, rid, operation_id="recover")
            assert pending["status"] == "partial" and pending["committed"]
        else:
            original = move.rename_directory_no_replace if point == "publish" else move._commit
            def crash(*args):
                original(*args)
                raise Crash()
            patch.setattr(move, "rename_directory_no_replace" if point == "publish" else "_commit", crash)
            with pytest.raises(Crash):
                invoke(service, rid, operation_id="recover")
    assert old.exists()
    result = invoke(service, rid, operation_id="recover")
    assert result["status"] == "completed", result
    assert result["version"]["number"] == 4 and not old.exists()
    assert Path(service.resolve(rid, actor=ACTOR)["resolved_path"]).name == "新的文件.md"


def test_directory_package_primary_stays_authoritative_after_rename_and_scan(file_service, permissions):
    service, index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    package = old.parent / "package"
    package.mkdir()
    primary = package / "README.md"
    old.rename(primary)
    (package / "aaa.md").write_text("companion without an identity")
    with directory_writer("knowledge") as writer:
        doc = writer.get("knowledge", rid)
        doc["directories"] = [{"path": stored_location(package), "owner_id": rid}]
        writer.put("knowledge", rid, doc)
    index.project_relocated([{**records[0], "path": str(primary), "content_root": str(package)}])
    result = invoke(service, rid, filename="zzz.md")
    assert result["status"] == "completed", result
    assert source._choose_primary_file(package, list(package.glob("*.md"))) == package / "zzz.md"
    assert move._paths(move.get_registry_store().get("knowledge", rid), rid)[0] == package / "zzz.md"
    assert Path(service.resolve(rid, actor=ACTOR)["resolved_path"]) == package / "zzz.md"
    assert service.resolve(rid, actor=ACTOR)["detail"]["file_count"] == 2
    assert (package / "aaa.md").read_text() == "companion without an identity"
    permissions.grant(ACTOR, "Destination")
    moved = service.move_knowledge_items("Destination", [{"resource_id": rid, "expected_version": 4}], actor=ACTOR)
    assert moved["status"] == "completed", moved
    relocated = Path(service.resolve(rid, actor=ACTOR)["resolved_path"])
    assert relocated.name == "zzz.md" and "Destination" in relocated.parts
    assert source._choose_primary_file(relocated.parent, list(relocated.parent.glob("*.md"))) == relocated


def test_concurrent_versions_allow_only_one_rename(file_service, permissions):
    from concurrent.futures import ThreadPoolExecutor
    service, _index, items, _records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    def rename(name):
        try:
            return invoke(service, rid, filename=name)
        except ResourceVersionConflict as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rename, ["one.md", "two.md"]))
    assert len([r for r in results if isinstance(r, dict) and r["status"] == "completed"]) == 1
    assert len([r for r in results if isinstance(r, ResourceVersionConflict)]) == 1


def test_registration_failure_leaves_source_and_recovers(file_service, permissions, monkeypatch):
    service, _index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    before = old.read_bytes()
    document = move.get_registry_store().get("knowledge", rid)
    with monkeypatch.context() as patch:
        patch.setattr(move.permission_system, "sync_resource", Mock(side_effect=RuntimeError("injected transaction failure")))
        pending = invoke(service, rid, operation_id="rollback")
    assert pending["status"] == "partial" and not pending["committed"]
    assert move.get_registry_store().get("knowledge", rid) == document
    assert old.read_bytes() == before
    result = invoke(service, rid, operation_id="rollback")
    assert result["status"] == "completed" and result["version"]["number"] == 4


def test_detail_capability_and_revoked_retry(file_service, permissions, monkeypatch):
    from management_portal import resource_views
    service, index, items, records = file_service
    rid = items[0]["resource_id"]
    permissions.grant(ACTOR, "AlignMe")
    assert resource_views.knowledge_detail(rid, service=service, actor=ACTOR)["can_edit"] is True
    with monkeypatch.context() as patch:
        patch.setattr(index, "project_relocated", Mock(side_effect=OSError("injected projection failure")))
        assert invoke(service, rid, operation_id="revoke")["status"] == "partial"
    real = move.permission_system.permitted_ids
    def deny_write(actor, instruction, parameter, ids):
        return set() if instruction == "UpdateKnowledgeFileName" else real(actor, instruction, parameter, ids)
    monkeypatch.setattr(move.permission_system, "permitted_ids", deny_write)
    assert resource_views.knowledge_detail(rid, service=service, actor=ACTOR)["can_edit"] is False
    with pytest.raises(PermissionError):
        invoke(service, rid, operation_id="revoke")
    assert Path(records[0]["path"]).exists()  # no unauthorized retry cleanup


def test_public_isa_and_final_inventory_failure_recover(file_service, permissions, monkeypatch):
    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.resources import isa

    service, index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    package = old.parent / "package"
    package.mkdir()
    primary = package / "README.md"
    old.rename(primary)
    with directory_writer("knowledge") as writer:
        doc = writer.get("knowledge", rid)
        doc["directories"] = [{"path": stored_location(package), "owner_id": rid}]
        writer.put("knowledge", rid, doc)
    index.project_relocated([{**records[0], "path": str(primary), "content_root": str(package)}])
    arguments = {"resource_id": rid, "filename": "final.md", "expected_version": 3, "operation_id": "inventory"}
    invocation = Invocation(catalog.declaration("UpdateKnowledgeFileName"),
        InstructionCall(instruction="UpdateKnowledgeFileName", arguments=arguments),
        InstructionContext(caller=ACTOR, entry="harness"), "inst_res_0123456789abcdefabcd")
    original = index.project_relocated
    calls = []
    def fail_final(batch):
        calls.append(batch)
        if len(calls) == 2:
            raise OSError("injected final inventory failure")
        return original(batch)
    with monkeypatch.context() as patch:
        patch.setattr(index, "project_relocated", fail_final)
        pending = isa.update_knowledge_file_name(invocation)
    assert pending["status"] == "partial" and not primary.exists()
    result = isa.update_knowledge_file_name(invocation)
    assert result["status"] == "completed" and result["version"]["number"] == 4
    assert service.resolve(rid, actor=ACTOR)["detail"]["file_count"] == 1


def test_changed_source_aborts_preparation_and_requires_new_request(file_service, permissions, monkeypatch):
    service, _index, items, records = file_service
    permissions.grant(ACTOR, "AlignMe")
    rid = items[0]["resource_id"]
    old = Path(records[0]["path"])
    with monkeypatch.context() as patch:
        patch.setattr(move, "_commit", Mock(side_effect=OSError("injected before commit")))
        assert invoke(service, rid, operation_id="abort")["status"] == "partial"
    old.write_text(old.read_text() + "\nExternal update\n")
    result = invoke(service, rid, operation_id="abort")
    assert result["status"] == "partial" and result["retryable"] is False
    assert not result["committed"] and old.exists()
    assert not old.with_name("新的文件.md").exists()
    new = invoke(service, rid, operation_id="fresh")
    assert new["status"] == "completed"
    assert Path(new["path"]).read_text().endswith("External update\n")
