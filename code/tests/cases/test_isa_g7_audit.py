"""G7 resource and user contracts, with isolated stores and no deployment actions.

:module: spec.design-governance
"""
from types import SimpleNamespace
import io

import pytest
from PIL import Image

from management_portal import web_isa
from management_portal.hatg import catalog
from management_portal.hatg.contracts import InstructionCall, InstructionContext, InstructionError
from management_portal.hatg.kernel import Invocation
from management_portal.resources import isa


def invoke(name, arguments, *, expected_version=None, caller="alice"):
    declaration = catalog.declaration(name)
    declaration.validate_arguments(arguments)
    return Invocation(declaration, InstructionCall(
        instruction=name, arguments=arguments, expected_version=expected_version,
    ), InstructionContext(caller=caller, entry="web"), "inst_g7_test")


def test_search_exposes_project_membership_and_status_without_attributes(monkeypatch):
    """SearchResources 结果直接给出所属 Project 和状态，不返回原始 attributes"""
    def query(**arguments):
        assert arguments["include_attributes"] is True
        return {"resources": [{"resource_id": "decision_aaaaaaaaaaaa", "title": "Design",
                               "attributes": {"project_ids": ["AI员工"], "status": "open"}}], "total": 1}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(query=query))
    item = isa.search_resources(invoke("SearchResources", {"project_id": "AI员工"}))["items"][0]
    assert item["project"] == "AI员工" and item["status"] == "open"
    assert "attributes" not in item


def test_search_pages_with_cursor_until_the_last_page(monkeypatch):
    """SearchResources 用 cursor 翻页：还有下一页时返回 next_cursor，最后一页没有

    :design: decision_898b8911a2ff
    """
    records = [{"resource_id": f"decision_{index:012x}", "title": str(index)} for index in range(5)]
    seen = []

    def query(**arguments):
        seen.append(arguments["offset"])
        start = arguments["offset"]
        return {"resources": records[start:start + arguments["limit"]], "total": len(records)}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(query=query))
    titles, cursor = [], None
    while True:
        page = isa.search_resources(invoke("SearchResources", {"limit": 2, **({"cursor": cursor} if cursor else {})}))
        titles += [item["title"] for item in page["items"]]
        cursor = page.get("next_cursor")
        if not cursor:
            break
    assert titles == ["0", "1", "2", "3", "4"] and seen == [0, 2, 4]
    with pytest.raises(InstructionError) as error:
        isa.search_resources(invoke("SearchResources", {"cursor": "not-a-cursor"}))
    assert error.value.code == "instruction.invalid_arguments"


@pytest.mark.parametrize("denied", [False, True])
def test_resolve_missing_or_unreadable_internal_target_is_not_found(monkeypatch, denied):
    """ResolveResource 解析不存在或无权读取的内部文件时都报告不存在"""
    def resolve(*args, **kwargs):
        if denied:
            raise PermissionError("private")
        return {"exists": False, "root_id": "workspace_aaaaaaaaaaaa", "record": {}}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(resolve=resolve))
    with pytest.raises(InstructionError) as error:
        isa.resolve_resource(invoke("ResolveResource", {"address": "workspace_aaaaaaaaaaaa/file/missing.md"}))
    assert error.value.code == "instruction.not_found"


def test_read_version_can_be_reused_for_knowledge_updates(monkeypatch):
    """ReadResource 返回版本可用于知识更新"""
    resource_id = "decision_aaaaaaaaaaaa"
    seen = []
    def update(identifier, changes, **kwargs):
        seen.append((identifier, changes))
        assert changes["expected_version"] == "4"
        return {"resource_id": identifier, "version": {"number": 5}}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(
        read_content=lambda *a, **k: (b"Design", "text/markdown", {
            "record": {"version": {"number": 4}}}), update=update,
    ))
    read = isa.read_resource(invoke("ReadResource", {"address": resource_id}))
    result = isa.update_resource(invoke("UpdateResource", {
        "resource_id": resource_id, "changes": {"text": "New Design"},
    }, expected_version=read["version"]))
    assert result["version"] == "5"
    assert seen == [(resource_id, {"text": "New Design", "expected_version": "4"})]


def test_real_knowledge_provider_reuses_read_token_and_rejects_stale_writes(tmp_path, monkeypatch):
    """知识条目用读取得到的版本更新成功，再用旧版本写入返回冲突且文件不变"""
    from knowledge_board_lan import knowledge_backend
    from management_portal.resources.providers.knowledge import _update_knowledge
    from tests.cases.test_knowledge_versioning import KNOWLEDGE_ID, _markdown

    primary = tmp_path / "item.md"
    primary.write_text(_markdown(), encoding="utf-8")
    record = {"address": KNOWLEDGE_ID, "kb_id": KNOWLEDGE_ID, "type": "thinking",
              "path": str(primary), "selected_path": str(primary), "selected_file_id": "file-primary"}
    monkeypatch.setattr(knowledge_backend, "resolve_item_address", lambda *a, **k: record)
    def read(*a, **k):
        return primary.read_bytes(), "text/markdown", {"record": {
            "version": knowledge_backend.knowledge_version_record(primary.read_text(encoding="utf-8")),
        }}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(read_content=read,
        update=lambda identifier, changes, **k: _update_knowledge(identifier, changes, actor=k["actor"])))
    token = isa.read_resource(invoke("ReadResource", {"address": KNOWLEDGE_ID}))["version"]
    assert token == "3"
    updated = isa.update_resource(invoke("UpdateResource", {"resource_id": KNOWLEDGE_ID,
        "changes": {"text": _markdown() + "\nChanged"}}, expected_version=token))
    assert updated["version"] == "4"
    before = primary.read_bytes()
    with pytest.raises(InstructionError) as error:
        isa.update_resource(invoke("UpdateResource", {"resource_id": KNOWLEDGE_ID,
            "changes": {"text": "stale overwrite"}}, expected_version=token))
    assert error.value.code == "instruction.version_conflict"
    assert primary.read_bytes() == before


def test_internal_files_keep_content_digest_even_when_root_has_a_version(monkeypatch):
    """读取 Workspace 内部文件时 version 为内容摘要，不采用根资源的版本号"""
    import hashlib
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(
        read_content=lambda *a, **k: (b"file", "text/plain", {"record": {"version": {"number": 7}}}),
    ))
    result = isa.read_resource(invoke("ReadResource", {"address": "workspace_aaaaaaaaaaaa/file/report.md"}))
    assert result["version"] == hashlib.sha256(b"file").hexdigest()


@pytest.mark.parametrize("resource_id", ["repo_aaaaaaaaaaaa", "experiment_aaaaaaaaaaaa", "workbench_aaaaaaaaaaaa"])
def test_unsupported_resource_version_checks_fail_before_mutation(monkeypatch, resource_id):
    """不支持版本检查的资源带 expected_version 更新时在修改前以参数错误拒绝"""
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(
        update=lambda *a, **k: pytest.fail("unsupported version must not be ignored"),
    ))
    with pytest.raises(InstructionError) as error:
        isa.update_resource(invoke("UpdateResource", {
            "resource_id": resource_id, "changes": {"operation": "set_policy"},
        }, expected_version="stale"))
    assert error.value.code == "instruction.invalid_arguments"


def test_unversioned_resource_updates_remain_available(monkeypatch):
    """不带版本的资源更新照常执行，返回空 version"""
    seen = []
    def update(identifier, changes, **kwargs):
        seen.append(changes)
        return {"resource_id": identifier}
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(update=update))
    result = isa.update_resource(invoke("UpdateResource", {"resource_id": "repo_aaaaaaaaaaaa",
        "changes": {"operation": "set_policy", "push_enabled": True}}))
    assert result["version"] == ""
    assert seen == [{"operation": "set_policy", "push_enabled": True}]


def test_resource_type_only_lists_executable_declared_actions(monkeypatch):
    """GetResourceType 只列出已声明且可执行的操作及其说明"""
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(
        capabilities=lambda kind: {"actions": ["ssh.test", "undeclared"]},
        resource_types=SimpleNamespace(
            get=lambda kind: {"type": kind, "installed": True, "enabled": True, "version": 1},
            identity=SimpleNamespace(declaration=lambda kind: {"type": kind}),
        ),
        _index=lambda: SimpleNamespace(type_counts=lambda: {}),
    ))
    actions = isa.get_resource_type(invoke("GetResourceType", {"resource_type": "device"}))["actions"]
    assert [a["action"] for a in actions] == ["ssh.test"]
    assert actions[0]["description"] and actions[0]["mutates"] is False


def test_avatar_size_is_rejected_before_upload_is_created(tmp_path, monkeypatch):
    """头像超过大小上限时 CreateUpload 在创建上传前拒绝"""
    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    with pytest.raises(InstructionError) as error:
        isa.create_upload(invoke("CreateUpload", {"purpose": "avatar", "file_name": "avatar.png",
            "media_type": "image/png", "size": 3 * 1024 * 1024}))
    assert error.value.code == "instruction.invalid_arguments"
    assert not (tmp_path / "uploads").exists()


def test_avatar_rejects_gif_in_both_data_and_source_channels(tmp_path, monkeypatch):
    """头像上传和从资源地址取图两种方式都拒绝 GIF"""
    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16)).save(buffer, format="GIF")
    data = buffer.getvalue()
    upload = isa.create_upload(invoke("CreateUpload", {"purpose": "avatar", "file_name": "avatar.gif",
        "media_type": "image/gif", "size": len(data)}))
    with pytest.raises(InstructionError):
        isa.complete_upload(upload["upload_id"], data, actor="alice")
    assert not isa.read_upload(upload["upload_id"])["ready"]
    monkeypatch.setattr(isa, "_service", lambda: SimpleNamespace(
        read_content=lambda *a, **k: (data, "image/gif", {}),
    ))
    with pytest.raises(InstructionError):
        isa.create_upload(invoke("CreateUpload", {"purpose": "avatar",
            "source_address": "workspace_aaaaaaaaaaaa/file/avatar.gif"}))
    assert isa._checked_media_type("post_image", data, "image/gif") == "image/gif"


def test_get_and_list_user_include_public_avatar(monkeypatch):
    """GetUser 和 ListUsers 返回用户的公开头像"""
    avatar = {"kind": "sprite", "sprite_index": 3}
    access = SimpleNamespace(user=lambda name: {"configured": True, "status": "active", "email": "private"},
        list_users=lambda keys: [{"username": "alice", "status": "active"}], _revision=lambda: 1)
    monkeypatch.setattr(web_isa, "_access", lambda: access)
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(_load_users=lambda: {"alice": {}}))
    monkeypatch.setattr("management_portal.user_avatar.user_avatar_store", lambda: SimpleNamespace(get=lambda name: avatar))
    user = web_isa.get_user(invoke("GetUser", {"user_id": "alice"}, caller="bob"))["user"]
    assert user["avatar"] == avatar and user["email"] == "private"
    assert web_isa.list_users(invoke("ListUsers", {}))["users"][0]["avatar"] == avatar


def test_user_rows_ask_update_user_for_their_edit_button(monkeypatch):
    """ListUsers 与管理页的用户列表逐行给出 allows.UpdateUser

    每行按调用者对该用户的 UpdateUser 判定，页面据此启用编辑按钮。

    :design: decision_6eac174d85bf
    """
    from management_portal import permission_system

    asked = []

    def permitted_ids(actor, instruction, parameter, ids):
        asked.append((actor, instruction, parameter, list(ids)))
        return {"alice"}

    monkeypatch.setattr(permission_system, "permitted_ids", permitted_ids)
    rows = web_isa.with_user_allows("alice", [{"username": "alice"}, {"username": "bob"}])
    assert [row["allows"] for row in rows] == [{"UpdateUser": True}, {"UpdateUser": False}]
    assert asked == [("alice", "UpdateUser", "user_id", ["alice", "bob"])]


def test_rejected_account_change_does_not_write_avatar(monkeypatch):
    """UpdateUser 的账号变更被拒绝时不写入同一请求中的头像

    :module: platform.core"""
    def reject(*args):
        raise ValueError("invalid account transition")
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
        _require_authenticated_actor=lambda actor: actor, _admin_access_action=reject))
    monkeypatch.setattr(web_isa, "_set_avatar", lambda *a: pytest.fail("rejected request wrote avatar"))
    with pytest.raises(ValueError, match="invalid account transition"):
        web_isa.update_user(invoke("UpdateUser", {"user_id":"alice",
            "changes": {"status": "disabled", "avatar": {"sprite_index": 2}}}))


def test_admitted_avatar_update_targets_only_the_requested_user(tmp_path, monkeypatch):
    """UpdateUser 修改头像只作用于请求中的用户

    :module: platform.core"""
    from management_portal.user_avatar import UserAvatarStore
    store = UserAvatarStore(tmp_path / "avatars")
    monkeypatch.setattr("management_portal.user_avatar.user_avatar_store", lambda: store)
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(_require_authenticated_actor=lambda actor: actor))
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda name: {"username": name}))
    result = web_isa.update_user(invoke("UpdateUser", {"user_id":"alice", "changes": {"avatar": {"sprite_index": 3}}}))
    assert result["user"]["avatar"]["sprite_index"] == 3
    peer = web_isa.update_user(invoke("UpdateUser", {"user_id":"bob", "changes": {"avatar": {"sprite_index": 4}}}))
    assert peer["user"]["avatar"]["sprite_index"] == 4
    assert store.get("alice")["sprite_index"] == 3


def test_invalid_recovery_email_does_not_write_avatar(monkeypatch):
    """找回邮箱无效时 UpdateUser 不写入同一请求中的头像"""
    def reject(*a, **k):
        raise ValueError("invalid email")
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
        _require_authenticated_actor=lambda actor: actor, _admin_access_action=reject))
    monkeypatch.setattr(web_isa, "_set_avatar", lambda *a: pytest.fail("invalid request wrote avatar"))
    with pytest.raises(ValueError, match="invalid email"):
        web_isa.update_user(invoke("UpdateUser", {"user_id":"alice",
            "changes": {"recovery_email": "bad", "avatar": {"sprite_index": 2}}}))


def test_selected_disk_is_in_declared_result_array(monkeypatch):
    """GetDeviceStatus kind=disk 按 device_id 查询时结果仍放在声明的 devices 数组中"""
    from management_portal import agent_status_backend
    disk = {"resource_id": "device_aaaaaaaaaaaa", "status": "ok", "filesystems": [{"used_bytes": 10}]}
    asked = []
    monkeypatch.setattr(agent_status_backend, "get_disk_resource_status", lambda **k: asked.append(k) or disk)
    result = web_isa.get_device_status(invoke("GetDeviceStatus", {"kind": "disk", "device_id": disk["resource_id"]}))
    assert result["devices"] == [disk]
    assert "disks" not in result
    assert asked == [{"force_refresh": False, "resource_id": disk["resource_id"]}]


def test_failed_publish_preserves_remote_push_result(monkeypatch):
    """PublishRelease 失败时错误详情保留各远端的推送结果"""
    from scripts import publish_portal_release as publisher
    push = {"ok": False, "remotes": {"github": {"ok": False, "error": "offline"}}}
    monkeypatch.setattr(web_isa, "_instance", lambda: "dev")
    monkeypatch.setattr(publisher, "publish", lambda options: {"ok": False, "error": "failed", "push": push})
    with pytest.raises(InstructionError) as error:
        web_isa.publish_release(invoke("PublishRelease", {"message": "test", "paths": ["SPEC.md"]}))
    assert error.value.details["push"] == push
