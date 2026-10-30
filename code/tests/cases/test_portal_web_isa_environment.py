"""Portal Web environment instructions (decision_4527448ad07f "运行环境").

:module: isa.instructions
:importance: P2
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from management_portal import mailbox_sources
from management_portal import web_isa
from management_portal.hatg.contracts import InstructionError


def invocation(arguments: dict, name: str = "") -> SimpleNamespace:
    return SimpleNamespace(argument=arguments.get, caller="sample_admin", name=name)


@pytest.fixture(autouse=True)
def no_operator_deployment(monkeypatch):
    from scripts import tfi_development_runtime as paired
    monkeypatch.setattr(paired, "configured_root", lambda: None)


def test_paired_restart_queues_the_selected_web_without_legacy_services(monkeypatch, tmp_path):
    from scripts import tfi_development_runtime as paired
    monkeypatch.setattr(paired, "configured_root", lambda: tmp_path)
    requests = []
    monkeypatch.setattr(paired, "request_restart", lambda root, instance, components:
                        requests.append((root, instance, components)) or
                        {"request": {"request_id": "paired-job", "components": components}})
    assert web_isa.restart_portal(invocation({"instance": "release", "components": ["web"]}))["request_id"] == "paired-job"
    assert requests == [(tmp_path, "release", ["web"])]


def test_standalone_restart_is_refused_without_running_commands(monkeypatch):
    """没有 Dev/Release 配对时 RestartPortal 报告不支持，不执行任何命令"""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("standalone restart ran a command"))
    for instance in ("dev", "release"):
        with pytest.raises(InstructionError, match="install_tfi_release"):
            web_isa.restart_portal(invocation({"instance": instance}))


def test_paired_publish_from_release_preserves_pending_activation_receipt(monkeypatch, tmp_path):
    """PublishRelease 从仓库根目录调用 tfi_dev 发布，路径带 code/ 前缀，并返回待确认的激活回执"""
    from scripts import tfi_development_runtime as paired, tfi_dev
    monkeypatch.setattr(paired, "configured_root", lambda: tmp_path)
    monkeypatch.setattr(web_isa, "_instance", lambda: "release")
    health = {"status": "pending", "receipt": str(tmp_path / "result.json")}
    calls = []
    monkeypatch.setattr(tfi_dev, "publish", lambda root, message, **options: calls.append((root, message, options)) or
                        {"ok": True, "commit": "abc", "paths": list(options["paths"]), "health": health})
    result = web_isa.publish_release(invocation({"message": "fix", "paths": ["SPEC.md"]}))
    assert result["health"] == health
    assert result["paths"] == ["code/SPEC.md"]
    assert calls == [(web_isa.REPO_ROOT.parent, "fix", {"paths": ("code/SPEC.md",), "all_files": False})]


def test_clear_unread_updates_read_boundary_without_listing_inbox(monkeypatch):
    """清除未读只按未读的阅读状态行更新已读位置，不扫描整个收件箱"""
    from management_portal import conversation_read_state

    calls = []
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda _user: {"configured": True}))
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda _actor: (_ for _ in ()).throw(
        AssertionError("clear unread must not scan the inbox")
    ))
    monkeypatch.setattr(mailbox_sources, "sources", lambda: [])
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        _clear_agent_conversations=lambda actor, **kwargs: (
            calls.append((actor, kwargs)) or {"clear_mode": "unread", "cleared_count": 1, "items": []}
        ),
    ))
    key = "resident:resident_aaaaaaaaaaaa"
    monkeypatch.setattr(conversation_read_state, "load", lambda actor: {"rows": {
        key: {"unread": True, "pinned": False, "read_revision": 3, "dismissed_through": ""},
        "resident:resident_bbbbbbbbbbbb": {"unread": False, "pinned": False, "read_revision": 4, "dismissed_through": ""},
    }})

    assert web_isa.clear_user_mailbox(invocation({"user_id": "sample_admin", "mode": "unread"})) == {
        "status": "cleared", "cleared": 1, "items": []}
    assert calls == [("sample_admin", {"mode": "unread", "entries": [{"key": key, "unread": True}]})]


def test_resident_favorites_sit_beside_lineage_and_project_favorites(monkeypatch, isolated_portal_case):
    """UpdateUserNavigation 收藏的 Resident 与 Lineage 收藏并列，非法 ID 被拒绝

    :module: platform.core"""
    from management_portal import presentation_store

    favorites = ["lineage:lineage_aaaaaaaaaaaa"]

    def set_favorite(caller, address, favorite):
        favorites[:] = [item for item in favorites if item != address] + ([address] if favorite else [])

    with isolated_portal_case():
        monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
            _lineage_favorite_payload=lambda caller: {"favorites": list(favorites)},
            _set_lineage_favorite=set_favorite,
            _PORTAL_ACCESS=SimpleNamespace(user=lambda name: {"configured": True}),
        ))

        def update(object_id, favorite):
            arguments = {"user_id": "sample_admin", "object_id": object_id, "favorite": favorite}
            return web_isa.update_user_navigation(invocation(arguments))["items"]

        assert update("resident_bbbbbbbbbbbb", True) == ["lineage_aaaaaaaaaaaa", "resident_bbbbbbbbbbbb"]
        assert presentation_store.favorite_ids("sample_admin", "resident") == ["resident_bbbbbbbbbbbb"]
        assert update("resident_bbbbbbbbbbbb", False) == ["lineage_aaaaaaaaaaaa"]
        assert update("todo_cccccccccccc", True) == ["lineage_aaaaaaaaaaaa", "todo_cccccccccccc"]
        with pytest.raises(InstructionError):
            update("resident_bad", True)


def test_web_objects_report_their_version_tokens(monkeypatch):
    """Web 对象报告各自的版本号，收件箱条目和个人导航不提供版本号"""
    from management_portal import notice_board_web_isa

    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(_revision=lambda: 7))
    monkeypatch.setattr(notice_board_web_isa, "_board", lambda: SimpleNamespace(
        get_post=lambda caller, post_id: {"post": {"post_id": post_id, "notification_revision": 3}}))
    assert web_isa.version_of(invocation({"user_id": "alice"}, "UpdateUser"), "alice") == 7
    # A Telepus contact is versioned by its User record (the feature's version_of).
    assert web_isa.version_of(invocation({"user_id": "alice"}, "BindTelepusContactForUser"), "alice") == 7
    # Personal navigation, also named by user_id, has no version token.
    assert web_isa.version_of(invocation({"user_id": "alice"}, "UpdateUserNavigation"), "alice") is None
    # A post instruction asks the Notice Board for the post's version.
    assert web_isa.version_of(invocation({"post_id": "post_0123456789ab"}, "UpdatePost"), "post_0123456789ab") == 3
    # The inbox handler checks the displayed completion during its target read.
    assert web_isa.version_of(invocation({"key": "resident:resident_aaaaaaaaaaaa"}, "MarkMailboxItemRead"), "resident:resident_aaaaaaaaaaaa") is None
    # Personal navigation has no version token: expected_version is accepted and not compared.
    assert web_isa.version_of(invocation({"object_id": "resident_aaaaaaaaaaaa"}), "resident_aaaaaaaaaaaa") is None


def test_list_inbox_pages_through_the_conversation_index_within_its_page_limit(monkeypatch):
    """GetMailbox 读取 User Mailbox 时按分页上限逐页读取会话索引，各页合成一个列表排序并汇总未读数

    第一页带上全部通知，其中较早的通知排在第二页较新的 Resident 之后；
    置顶在前，同一时间按 key 升序，结果不随页大小变化。"""
    pages = {
        "": ([("pinned", "2026-10-01T00:00:00Z", True), ("b", "2026-10-16T10:00:00Z", False),
              ("a", "2026-10-16T10:00:00Z", False), ("old-notice", "2026-10-03T00:00:00Z", False)], "t1"),
        "t1": ([("c", "2026-10-10T00:00:00Z", False)], ""),
    }
    calls = []

    def payload(caller, *, resident_id="", page_size, page_token):
        assert 1 <= page_size <= 100
        calls.append(page_token)
        rows, token = pages[page_token]
        return {"entries": [{"key": key, "updated_at": at, "pinned": pinned, "unread": key != "b"}
                            for key, at, pinned in rows], "next_page_token": token}

    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(_agent_conversation_payload=payload))
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda _user: {"configured": True}))
    result = web_isa.get_user_mailbox(invocation({"user_id": "sample_admin"}, "GetMailbox"))
    assert [item["key"] for item in result["items"]] == ["pinned", "a", "b", "c", "old-notice"]
    assert result["unread_count"] == 4
    assert calls == ["", "t1"]


def test_list_inbox_names_the_message_ids_inside_the_read_version(monkeypatch):
    """GetMailbox 的 User Mailbox 条目给出读版本对应的回复和输入消息 ID，Chatroom 条目不给消息 ID

    :module: platform.core"""
    from management_portal.conversation_completion import completion_signature

    signature = completion_signature({"schema_version": 1, "request_actor": "Sample-Editor", "completion_id": "msg_reply",
                                      "input_message_id": "msg_input", "completed_at": "2026-10-13T03:00:00Z"})
    terminal = completion_signature({"schema_version": 1, "request_actor": "Sample-Editor",
                                     "completion_id": "resident-input-terminal:msg_x:failed", "input_message_id": "msg_x"})
    entries = [{"key": "resident:a", "resident_id": "a", "completion_signature": signature, "read_revision": 11},
               {"key": "resident:b", "resident_id": "b", "completion_signature": terminal},
               {"key": "notice", "completion_signature": ""},
               # A Chatroom message is read with ListChatroomMessages, never GetMessage.
               {"key": "chatroom:c", "completion_signature": signature}]
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda caller, resident_id="": entries)
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda _user: {"configured": True}))
    items = {item["key"]: item for item in web_isa.get_user_mailbox(invocation({"user_id": "sample_admin"}, "GetMailbox"))["items"]}
    reply, terminal_item, notice, chatroom = (items[key] for key in ("resident:a", "resident:b", "notice", "chatroom:c"))
    assert (reply["latest_message_id"], reply["input_message_id"], reply["version"]) == ("msg_reply", "msg_input", "11")
    assert "latest_message_id" not in terminal_item and terminal_item["input_message_id"] == "msg_x"
    assert "latest_message_id" not in notice and "input_message_id" not in notice
    assert "latest_message_id" not in chatroom and chatroom["version"] == "0"


def test_mark_resident_read_checks_access_and_uses_persisted_boolean_without_scanning_inbox(monkeypatch):
    """Resident 已读标记校验访问并按看到的修订号修改阅读状态

    手动标记通过 Resident 服务校验访问，把调用者给出的 expected_version 交给阅读状态比较，不扫描消息列表；拒绝无权访问者。
    """
    key = "resident:resident_aaaaaaaaaaaa"
    seen = []
    item = {"key": key, "unread": False, "read_revision": 6}

    def apply(caller, exact, *, unread, expected_revision):
        seen.append((caller, exact, unread, expected_revision))
        return item

    monkeypatch.setattr(web_isa, "_inbox_entries", lambda *args: pytest.fail("marking a Resident read must not scan the index"))
    def check_access(resident_id, caller):
        seen.append((resident_id, caller))
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        resident_api=SimpleNamespace(resolve_resident_identity=check_access),
        _set_agent_conversation_read_state=apply,
    ))
    call = SimpleNamespace(
        caller="sample_admin", name="MarkMailboxItemRead",
        argument={"key": key, "unread": False}.get,
        expected_version_for=lambda _key: "5",
    )
    result = web_isa.mark_inbox_item_read(call)
    assert result == {"item": item}
    assert seen == [("resident_aaaaaaaaaaaa", "sample_admin"), ("sample_admin", key, False, "5")]
    def deny_access(*args):
        raise PermissionError("no access")
    denied_web = web_isa.web("web_clients")
    denied_web.resident_api.resolve_resident_identity = deny_access
    monkeypatch.setattr(web_isa, "web", lambda _module: denied_web)
    with pytest.raises(PermissionError):
        web_isa.mark_inbox_item_read(call)
    assert len(seen) == 2


def test_mark_notification_read_stops_at_the_page_that_contains_it(monkeypatch):
    """标记通知已读时先查调用者自己的行，较早没有行的通知读到包含它的那一页即停止

    通知和 @ 创建时就写入收件人的行，有行时不读 Mailbox 页；聊天室按成员资格判断，非成员返回不存在；
    较早没有行的通知读到包含它的那一页即停止，不重建其他页。

    :module: platform.core"""
    from management_portal import conversation_read_state

    key = "notification:notification_aaaaaaaaaaaa"
    entry = {"key": key, "read_revision": 3}
    calls, rows = [], {}
    def payload(caller, *, page_size, page_token):
        calls.append(page_token)
        assert page_token == "", "Reading one displayed notification must not rebuild unrelated Resident pages"
        return {"entries": [entry], "next_page_token": "many-more-residents"}
    monkeypatch.setattr(conversation_read_state, "read_rows",
                        lambda caller, keys: {exact: rows[exact] for exact in keys if exact in rows})
    monkeypatch.setattr(mailbox_sources, "sources", lambda: [mailbox_sources.MailboxSource(
        "chatroom:", lambda caller: [], conversation=True, set_unread=lambda *a, **kw: {},
        contains=lambda chatroom_id, caller: chatroom_id == "chatroom_aaaaaaaaaaaa")])
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        _agent_conversation_payload=payload,
        _set_agent_conversation_read_state=lambda caller, exact, *, unread, expected_revision: {
            "key": exact, "unread": unread, "read_revision": 4},
    ))

    def call(target):
        return SimpleNamespace(caller="sample_admin", argument={"key": target, "unread": False}.get,
                               expected_version_for=lambda _target: "3")

    assert web_isa.mark_inbox_item_read(call(key))["item"]["unread"] is False
    assert calls == [""]
    rows[key] = {"read_revision": 3}
    assert web_isa.mark_inbox_item_read(call(key))["item"]["unread"] is False
    assert web_isa.mark_inbox_item_read(call("chatroom:chatroom_aaaaaaaaaaaa"))["item"]["unread"] is False
    with pytest.raises(InstructionError) as missing:
        web_isa.mark_inbox_item_read(call("chatroom:chatroom_bbbbbbbbbbbb"))
    assert missing.value.code == "instruction.not_found"
    assert calls == [""]


def test_resident_chat_read_checks_identity_before_updating_persisted_flag(monkeypatch):
    """打开 Resident Chat 时 MarkMailboxItemRead 先核对当前身份再写入已读；访问拒绝不会写入已读状态。

    :module: platform.core
    """
    resident_id = "resident_aaaaaaaaaaaa"
    key = "resident:" + resident_id
    checks, writes = [], []
    resident_api = SimpleNamespace(resolve_resident_identity=lambda rid, actor: checks.append((rid, actor)))
    monkeypatch.setattr(web_isa, "web", lambda _module: SimpleNamespace(
        resident_api=resident_api,
        _set_agent_conversation_read_state=lambda caller, exact, *, unread, expected_revision: (
            writes.append((caller, exact, unread, expected_revision))
            or {"key": exact, "unread": unread, "read_revision": 2}),
    ))
    call = SimpleNamespace(caller="sample_admin", argument={"key": key}.get, expected_version_for=lambda _target: None)
    assert web_isa.mark_inbox_item_read(call)["item"] == {"key": key, "unread": False, "read_revision": 2}
    assert checks == [(resident_id, "sample_admin")]
    assert writes == [("sample_admin", key, False, None)]
    writes.clear()
    resident_api.resolve_resident_identity = lambda *args: (_ for _ in ()).throw(PermissionError("forbidden"))
    with pytest.raises(PermissionError):
        web_isa.mark_inbox_item_read(call)
    assert writes == []


def test_gpu_status_lists_the_servers_or_the_selected_device(monkeypatch):
    """GetDeviceStatus kind=gpu 默认列出服务器，指定设备时只返回该设备，未知设备返回空列表"""
    from management_portal import agent_status_backend

    server = {"device_id": "device_server", "status": "ok", "gpus": [{"device_id": "device_gpu0"}]}
    replies = {
        "": {"status": "ok", "servers": [server], "gpus": [{"device_id": "device_gpu0"}]},
        "device_gpu0": {"status": "empty", "device_id": "device_gpu0"},
        "device_missing": {"status": "error", "device_id": "device_missing", "known_device_ids": ["device_server"]},
    }
    monkeypatch.setattr(agent_status_backend, "get_gpu_device_status",
                        lambda *, force_refresh=False, device_id="": dict(replies[device_id]))
    assert web_isa.get_device_status(invocation({"kind": "gpu"}))["devices"] == [server]
    assert web_isa.get_device_status(invocation({"kind": "gpu", "device_id": "device_gpu0"}))["devices"] == [
        {"status": "empty", "device_id": "device_gpu0"}]
    assert web_isa.get_device_status(invocation({"kind": "gpu", "device_id": "device_missing"}))["devices"] == []


def test_disk_status_lists_the_disks_or_the_selected_disk(monkeypatch):
    """GetDeviceStatus kind=disk 默认在 devices 中列出全部磁盘，指定 device_id 时只返回该磁盘"""
    from management_portal import agent_status_backend

    disk = {"resource_id": "device_disk0", "status": "ok"}
    asked = []

    def disk_status(*, force_refresh=False, resource_id=""):
        asked.append((force_refresh, resource_id))
        return {"status": "ok", "resources": [disk]} if not resource_id else dict(disk)

    monkeypatch.setattr(agent_status_backend, "get_disk_resource_status", disk_status)
    monkeypatch.setattr(agent_status_backend, "get_gpu_device_status",
                        lambda **_kwargs: pytest.fail("a disk read asked for GPUs"))
    every = web_isa.get_device_status(invocation({"kind": "disk", "refresh": True}))
    assert every["devices"] == [disk] and every["resources"] == [disk]
    assert web_isa.get_device_status(invocation({"kind": "disk", "device_id": "device_disk0"}))["devices"] == [disk]
    assert asked == [(True, ""), (False, "device_disk0")]


def test_publish_release_reports_the_push_to_the_remote(monkeypatch):
    """PublishRelease 结果报告提交、待激活回执和推送结果"""
    from scripts import tfi_dev

    push = {"ok": True, "remote": "origin"}
    health = {"status": "pending", "receipt": "/runtime/receipt.json"}
    monkeypatch.setattr(web_isa, "_instance", lambda: "dev", raising=False)
    monkeypatch.setattr(tfi_dev, "publish", lambda *args, **options: {
        "ok": True, "commit": "abc123", "paths": ["code/SPEC.md"], "health": health, "push": push})
    result = web_isa.publish_release(invocation({"message": "m", "paths": ["SPEC.md"]}))
    assert result == {"commit": "abc123", "paths": ["code/SPEC.md"], "health": health, "push": push}
