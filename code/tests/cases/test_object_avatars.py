"""Resident、Lineage 与 Project 头像的修改接口、读取地址和跨进程一致性。

:module: platform.core
:importance: P1
"""
import base64
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlparse

import pytest
from PIL import Image

from management_portal import avatar_store, resident_avatar
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.society_service.isa import definition, residents
from management_portal.resources import isa as resource_isa

RESIDENT = "resident_00000000000a"
LINEAGE = "lineage_00000000000b"


def invoke(name, arguments, caller="alice"):
    declaration = catalog.declaration(name)
    declaration.validate_arguments(arguments)
    return Invocation(declaration, InstructionCall(instruction=name, arguments=arguments),
                      InstructionContext(caller=caller, entry="web"), "inst_avatar_00000000000000000")


def png(size=(64, 48)):
    stream = io.BytesIO()
    Image.new("RGB", size, (40, 80, 120)).save(stream, "PNG")
    return stream.getvalue()


def uploaded(purpose="avatar"):
    data = png()
    created = resource_isa.create_upload(invoke("CreateUpload", {
        "purpose": purpose, "file_name": "avatar.png", "media_type": "image/png", "size": len(data),
    }))
    resource_isa.complete_upload(created["upload_id"], data, actor="alice")
    return created["upload_id"]


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    monkeypatch.setenv("PORTAL_RESIDENT_AVATAR_ROOT", str(tmp_path / "residents"))
    monkeypatch.setenv("PORTAL_SCOPE_AVATAR_ROOT", str(tmp_path / "scopes"))


def test_a_change_made_by_another_process_is_read_without_restart(tmp_path):
    """另一进程修改 Resident 头像后，本进程下一次读取即得到新头像

    :design: decision_5bc3fdfc7eec
    """
    reader = resident_avatar.ResidentAvatarStore(tmp_path / "shared")
    drawn = reader.get(RESIDENT)
    assert drawn["kind"] == "sprite"
    writer = resident_avatar.ResidentAvatarStore(tmp_path / "shared")
    chosen = (drawn["sprite_index"] + 1) % 42
    assert writer.update(RESIDENT, {"kind": "sprite", "sprite_index": chosen})["sprite_index"] == chosen
    assert reader.get(RESIDENT)["sprite_index"] == chosen
    image = writer.update(RESIDENT, {"kind": "upload", "data_base64": base64.b64encode(png()).decode()})
    assert reader.get(RESIDENT) == image
    assert image["url"] == f"/api/account/avatar/image?resident={RESIDENT}&v={image['version']}"
    assert reader.image(RESIDENT, image["version"]).startswith(b"\x89PNG")
    # Another upload replaces the image; the old version's file goes with it.
    second = writer.update(RESIDENT, {"kind": "upload", "data_base64": base64.b64encode(png((32, 32))).decode()})
    assert [path.name for path in (tmp_path / "shared").glob("*.png")] == [f"{RESIDENT}-{second['version']}.png"]
    assert resident_avatar.projection_fields(second) == {"avatar_url": second["url"]}
    assert resident_avatar.projection_fields(drawn) == {"sprite_index": drawn["sprite_index"], "sprite_rarity": drawn["sprite_rarity"]}


def test_update_resident_changes_only_the_avatar_when_no_title_is_given(monkeypatch):
    """UpdateResident 只传 avatar 时只改头像不改名，两项都缺、头像无效或在上传归上传者时使用他人上传时拒绝

    :design: decision_5bc3fdfc7eec
    """
    rename = Mock()
    monkeypatch.setattr(residents, "lineage_resident_nodes", lambda: SimpleNamespace(rename_lineage_resident=rename))
    monkeypatch.setattr(residents, "protocol", lambda: SimpleNamespace(get_resident=lambda resident_id, caller: {"resident_id": resident_id}))
    result = residents.update_resident(invoke("UpdateResident", {"lineage_id": LINEAGE, "resident_id": RESIDENT, "avatar": {"sprite_index": 36}}))
    assert result["resident"]["avatar"] == avatar_store.sprite(36)
    assert resident_avatar.get_avatar(RESIDENT) == avatar_store.sprite(36)
    rename.assert_not_called()
    result = residents.update_resident(invoke("UpdateResident", {"lineage_id": LINEAGE, "resident_id": RESIDENT, "avatar": {"upload_id": uploaded()}}))
    assert result["resident"]["avatar"]["kind"] == "upload"
    for arguments in ({}, {"avatar": {"sprite_index": 42}}, {"avatar": {"sprite_index": 1, "upload_id": "x"}},
                      {"avatar": {"upload_id": uploaded("post_image")}}):
        with pytest.raises(InstructionError) as error:
            residents.update_resident(invoke("UpdateResident", {"lineage_id": LINEAGE, "resident_id": RESIDENT, **arguments}))
        assert error.value.code == "instruction.invalid_arguments"
    saved = resident_avatar.get_avatar(RESIDENT)
    # Where uploads stay their creator's, someone else's is not theirs to publish as an avatar.
    monkeypatch.setattr("management_portal.permission_system.owners_only", lambda: True)
    with pytest.raises(InstructionError) as error:
        residents.update_resident(invoke("UpdateResident", {"lineage_id": LINEAGE, "resident_id": RESIDENT, "avatar": {"upload_id": uploaded()}}, caller="bob"))
    assert error.value.code == "instruction.invalid_arguments"
    assert resident_avatar.get_avatar(RESIDENT) == saved
    residents.update_resident(invoke("UpdateResident", {"lineage_id": LINEAGE, "resident_id": RESIDENT, "title": "新名字"}))
    assert rename.call_args.args[1] == {"resident_id": RESIDENT, "title": "新名字"}


def test_lineage_and_project_avatars_are_set_and_cleared_through_their_updates(monkeypatch):
    """UpdateLineage 与 UpdateProject 的 changes.avatar 设置或清除头像，只改头像时不改定义与登记

    :design: decision_5bc3fdfc7eec
    """
    monkeypatch.setattr(definition, "lineage_state", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_reads", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_lifecycle", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_graph_edits", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_program_edits", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_branches", lambda: pytest.fail("an avatar-only change wrote the definition"))
    monkeypatch.setattr(definition, "lineage_handoffs", lambda: pytest.fail("an avatar-only change wrote the definition"))
    result = definition.update_lineage(invoke("UpdateLineage", {"lineage_id": LINEAGE, "changes": {"avatar": {"sprite_index": 5}}}))
    assert result == {"lineage": {"lineage_id": LINEAGE, "avatar": avatar_store.sprite(5)}}
    assert avatar_store.get_scope_avatar("lineage", LINEAGE) == avatar_store.sprite(5)
    assert definition.update_lineage(invoke("UpdateLineage", {"lineage_id": LINEAGE, "changes": {"avatar": None}}))["lineage"]["avatar"] is None
    assert avatar_store.get_scope_avatar("lineage", LINEAGE) is None

    monkeypatch.setattr(resource_isa, "_project_record", lambda project_id, caller: {"resource_id": project_id, "project_id": project_id})
    monkeypatch.setattr(resource_isa, "_service", lambda: pytest.fail("an avatar-only change wrote the registry"))
    project = resource_isa.update_project(invoke("UpdateProject", {"project_id": "AI员工", "changes": {"avatar": {"upload_id": uploaded()}}}))["project"]
    assert project["avatar"]["url"].startswith("/api/account/avatar/image?project=AI%E5%91%98%E5%B7%A5&v=")
    assert avatar_store.get_scope_avatar("project", "AI员工") == project["avatar"]
    store = avatar_store.scope_avatar_store()
    assert store.move("project:AI员工", "project:AI团队")
    assert avatar_store.get_scope_avatar("project", "AI员工") is None
    moved = avatar_store.get_scope_avatar("project", "AI团队")
    assert moved["version"] == project["avatar"]["version"]
    assert store.image("project:AI团队", moved["version"]).startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        avatar_store.scope_subject("lineage", "../escape")
    with pytest.raises(ValueError):
        avatar_store.scope_subject("resident", RESIDENT)


def test_avatar_reads_name_lineages_and_projects_by_their_own_fields(monkeypatch):
    """头像读取地址按 lineage、project 字段区分对象，没有头像返回 null，图片地址按对象种类读取

    :design: decision_5bc3fdfc7eec
    """
    from management_portal import serve_management_portal as server

    monkeypatch.setattr(server._ACCOUNT_HTTP, "respond", lambda handler, status, payload: (status, payload))
    monkeypatch.setattr(server._ACCOUNT_HTTP, "serve_bytes", lambda handler, status, data, mime, **kwargs: (status, data, mime))
    get = lambda address: server._ACCOUNT_HTTP.avatar_get(Mock(), urlparse(address), "bob")
    assert get(f"/api/account/avatar?lineage={LINEAGE}") == (200, {"avatar": None})
    store = avatar_store.scope_avatar_store()
    saved = store.update(f"lineage:{LINEAGE}", {"kind": "upload", "data_base64": base64.b64encode(png()).decode()})
    assert get(f"/api/account/avatar?lineage={LINEAGE}") == (200, {"avatar": saved})
    assert get(saved["url"])[:2] == (200, store.image(f"lineage:{LINEAGE}", saved["version"]))
    resident = resident_avatar.resident_avatar_store().update(RESIDENT, {"kind": "upload", "data_base64": base64.b64encode(png()).decode()})
    assert get(resident["url"])[0] == 200
    assert get(f"/api/account/avatar?principal={RESIDENT}") == (200, {"avatar": resident})
    assert get("/api/account/avatar?project=AI员工") == (200, {"avatar": None})
    for address in (f"/api/account/avatar?lineage={LINEAGE}&project=x", "/api/account/avatar?lineage=bad",
                    f"/api/account/avatar/image?team={LINEAGE}&v={saved['version']}",
                    f"/api/account/avatar/image?lineage={LINEAGE}&project=x&v={saved['version']}"):
        assert get(address)[0] == 400
    assert get(f"/api/account/avatar/image?lineage={LINEAGE}&v={'0' * 64}")[0] == 404
    from management_portal.avatar_database import database
    with database().read() as connection:
        record = connection.execute("SELECT subject_kind, subject, version FROM avatars WHERE subject_kind=%s AND subject=%s", ("scope", f"lineage:{LINEAGE}")).fetchone()
    assert record["subject"] == f"lineage:{LINEAGE}" and record["version"] == saved["version"]
