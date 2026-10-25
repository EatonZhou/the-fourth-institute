"""User Mailbox authority: sections, durable pins, scoped clearing and streaming.

:module: workbench.chat
:importance: P2
:design: decision_15f7df737ed5
"""
from types import SimpleNamespace

import pytest

from management_portal import agent_conversation_index_backend as index
from management_portal import conversation_read_state as read_state
from management_portal import mailbox_sources
from management_portal import web_isa
from management_portal.hatg.contracts import InstructionError


def item(key, kind="resident", at="2026-10-13T00:00:00Z"):
    return {"key": key, "kind": kind, "updated_at": at}


def unread_flags(actor):
    return {key: row["unread"] for key, row in read_state.load(actor)["rows"].items()}


def invocation(**arguments):
    return SimpleNamespace(caller="CASE-A", argument=arguments.get)


def test_personal_todos_validate_text_and_date_and_stay_private(isolated_portal_case):
    """个人待办校验文本日期且仅允许所属用户操作

    :design-point:"""
    from management_portal import mailbox_todos as todos

    with isolated_portal_case():
        created = todos.create("CASE-A", "😀" * 100, date="2026-10-13")["item"]
        identifier = created["todo_id"]
        assert todos.list_items("CASE-A")["pending_count"] == 1
        assert todos.list_items("sample_admin")["items"] == []
        for text in ("", "  ", "中" * 101, "😀" * 101):
            with pytest.raises(InstructionError, match="1–100"):
                todos.create("CASE-A", text)
        with pytest.raises(InstructionError, match="YYYY-MM-DD"):
            todos.create("CASE-A", "无效日期", date="2026-02-30")
        for operation in (lambda: todos.update("sample_admin", identifier, {"text": "改写"}),
                          lambda: todos.delete("sample_admin", identifier),
                          lambda: todos.move("sample_admin", identifier, "up")):
            with pytest.raises(InstructionError, match="待办不存在"):
                operation()
        todos.update("CASE-A", identifier, {"text": "更新过的待办", "date": "2026-10-12", "completed": True})
        assert todos.list_items("CASE-A")["pending_count"] == 0
        saved = todos.list_items("CASE-A")["items"][0]
        assert (saved["text"], saved["date"], saved["completed"]) == ("更新过的待办", "2026-10-12", True)
        with pytest.raises(InstructionError):
            todos.update("CASE-A", identifier, {"created_by": "sample_admin"})
        todos.delete("CASE-A", identifier)
        assert todos.list_items("CASE-A")["items"] == []


def test_personal_todos_preserve_concurrent_additions_and_manual_order(isolated_portal_case):
    """个人待办并发新增不丢失且保留手工排序

    :design-point:"""
    from concurrent.futures import ThreadPoolExecutor
    from management_portal import mailbox_todos as todos

    with isolated_portal_case():
        with ThreadPoolExecutor(max_workers=4) as pool:
            created = list(pool.map(lambda n: todos.create("CASE-A", f"待办 {n}", date="2026-10-13")["item"], range(8)))
        items = todos.list_items("CASE-A")["items"]
        assert len(items) == len({item["position"] for item in items}) == 8
        first, second = items[:2]
        todos.move("CASE-A", second["todo_id"], "up")
        assert todos.list_items("CASE-A")["items"][0]["todo_id"] == second["todo_id"]
        todos.update("CASE-A", second["todo_id"], {"completed": True})
        assert todos.list_items("CASE-A")["items"][0]["todo_id"] == second["todo_id"]
        todos.move("CASE-A", second["todo_id"], "up")
        assert todos.list_items("CASE-A")["items"][0]["completed"]
        todos.create("CASE-A", "更早的日期", date="2026-10-12")
        todos.create("CASE-A", "未来日期", date="2026-10-14")
        ordered = todos.list_items("CASE-A")["items"]
        assert ordered[0]["date"] == "2026-10-14" and ordered[-1]["date"] == "2026-10-12"


def test_personal_todos_drag_placement_and_indent_are_atomic_and_user_scoped(isolated_portal_case):
    """个人待办拖放与缩进原子更新并按用户隔离

    :design-point:"""
    from management_portal import mailbox_todos as todos

    with isolated_portal_case():
        a, b, c = [todos.create("CASE-A", text, date="2026-10-13")["item"] for text in ("父项", "子项", "另一项")]
        yesterday = todos.create("CASE-A", "昨天", date="2026-10-12")["item"]
        foreign = todos.create("sample_admin", "私有目标")["item"]
        assert a["indent"] == 0
        todos.update("CASE-A", b["todo_id"], {"indent": 2, "completed": True})
        todos.move("CASE-A", c["todo_id"], before_todo_id=a["todo_id"])
        assert [item["text"] for item in todos.list_items("CASE-A")["items"]] == ["另一项", "父项", "子项", "昨天"]
        moved = todos.move("CASE-A", b["todo_id"], after_todo_id=yesterday["todo_id"])["item"]
        assert (moved["date"], moved["indent"], moved["completed"]) == ("2026-10-12", 2, True)
        assert [item["text"] for item in todos.list_items("CASE-A")["items"]][-2:] == ["昨天", "子项"]
        for indent in (-1, 5, True, "1"):
            with pytest.raises(InstructionError):
                todos.update("CASE-A", b["todo_id"], {"indent": indent})
        before = todos.list_items("CASE-A")
        for placement in ({}, {"before_todo_id": a["todo_id"], "direction": "up"},
                          {"before_todo_id": c["todo_id"]}, {"before_todo_id": "missing"},
                          {"after_todo_id": foreign["todo_id"]}):
            with pytest.raises(InstructionError):
                todos.move("CASE-A", c["todo_id"], **placement)
        assert todos.list_items("CASE-A") == before


def test_resident_can_add_user_todo_via_isa_with_idempotency(monkeypatch, isolated_portal_case):
    """Resident 可幂等调用 CreateMailboxTodo 添加用户待办

    :design-point:"""
    from management_portal import mailbox_todos as todos
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import InstructionKernel
    from management_portal.hatg.store import ExecutionStore
    from management_common.portal_database import portal_database

    resident = "resident_111111111111"
    with isolated_portal_case():
        monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
            _PORTAL_ACCESS=SimpleNamespace(user=lambda name: {"configured": name == "CASE-A", "status": "active"}),
            _load_users=lambda: {"CASE-A": {}},
        ))
        kernel = InstructionKernel(web_isa.binding(), store=ExecutionStore(portal_database(schema="todo_isa_test")), authorize=lambda *args: None)
        context = InstructionContext(caller=resident, entry="web")
        call = InstructionCall.from_payload({"instruction": "CreateMailboxTodo", "arguments": {"user_id": "CASE-A", "text": "请检查新版本"}, "idempotency_key": "resident-todo"})
        first, replay = kernel.execute(call, context), kernel.execute(call, context)
        assert first.status == replay.status == "committed", first.error
        assert replay.replayed
        assert replay.result["item"]["todo_id"] == first.result["item"]["todo_id"]
        assert todos.list_items("CASE-A")["items"][0]["created_by"] == resident
        assert len(todos.list_items("CASE-A")["items"]) == 1
        for arguments in ({"text": "未指定 User"}, {"user_id": "missing", "text": "未知 User"}, {"user_id": "CASE-A", "text": "长" * 101}):
            result = kernel.execute(InstructionCall.from_payload({"instruction": "CreateMailboxTodo", "arguments": arguments}), context)
            assert result.status == "failed"
        with pytest.raises(InstructionError):
            todos.list_items(resident)
        with pytest.raises(InstructionError):
            todos.create("sample_admin", "不能替他人添加", user_id="CASE-A")


@pytest.mark.parametrize("old_name,new_name,mutates,streaming", [
    ("ListInbox", "GetMailbox", False, False),
    ("ListMailbox", "GetMailbox", False, False),
    ("MarkInboxItemRead", "MarkMailboxItemRead", True, False),
    ("ClearInbox", "ClearMailbox", True, False),
    ("PinInboxItem", "PinMailboxItem", True, False),
    ("WatchInbox", "WatchMailbox", False, True),
])
def test_user_mailbox_commands_replace_inbox_without_aliases(old_name, new_name, mutates, streaming):
    """用户 Mailbox 指令替代 Inbox 旧名且不保留别名

    :design-point:"""
    from management_portal.hatg import routing
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.hatg.contracts import InstructionContext

    catalog = instruction_catalog()
    declaration = catalog[new_name]
    assert declaration.owner == "web"
    assert (declaration.mutates, declaration.streaming) == (mutates, streaming)
    binding = web_isa.binding()
    assert new_name in (binding.streams if streaming else binding.handlers)
    assert old_name not in catalog
    assert old_name not in binding.handlers and old_name not in binding.streams
    result = routing.execute({"instruction": old_name, "arguments": {}},
                             InstructionContext(caller="CASE-A", entry="web"))
    assert result.status == "failed"
    assert result.error["code"] == "instruction.unknown"


def test_user_and_resident_mailboxes_keep_distinct_instructions():
    """GetMailbox 按参数读取 User 或 Resident 的 Mailbox

    resident_id 与 user_id 必须且只能给一个：带 resident_id 由 SocietyService 读取 Resident 的待处理队列，
    带 user_id 由 Portal Web 读取这位 User 的对话与通知；related_resident_id 只用于 User Mailbox。

    :design-point:"""
    from management_portal.hatg.catalog import instruction_catalog
    from management_portal.society_service.isa import execution

    declaration = instruction_catalog()["GetMailbox"]
    assert declaration.owner == "web" and declaration.one_of == (("resident_id",), ("user_id",))
    assert declaration.owner_for({"resident_id": "resident_aaaaaaaaaaaa"}) == "lineage"
    assert declaration.owner_for({"user_id": "CASE-A", "related_resident_id": "resident_aaaaaaaaaaaa"}) == "web"
    assert web_isa.binding().handlers["GetMailbox"] is web_isa.get_user_mailbox
    assert execution.HANDLERS["GetMailbox"] is execution.get_mailbox
    with pytest.raises(InstructionError) as refused:
        execution.get_mailbox(SimpleNamespace(argument={"resident_id": "resident_aaaaaaaaaaaa",
                                                         "related_resident_id": "resident_bbbbbbbbbbbb"}.get))
    assert refused.value.code == "instruction.invalid_arguments"
    assert "MarkMailboxItemRead" in web_isa.binding().handlers


def test_sections_and_pins_share_read_authority_without_changing_unread(isolated_portal_case):
    """Mailbox 分区与置顶共用阅读状态且置顶不改变未读

    :design-point:"""
    with isolated_portal_case():
        entries = [item("resident:old", at="2026-09-01T00:00:00Z"),
                   item("resident:new"), item("mention:post", "notice-board-mention"),
                   item("notification:notice", "lineage-notification"), item("chatroom:room", "chatroom")]
        for entry in entries:
            if entry["key"].startswith("resident:"):
                read_state.set_read("CASE-A", entry["key"], unread_value=True)
        before = index.decorate_entries(entries, read_state.load("CASE-A"))
        read_state.set_pinned("CASE-A", "resident:old", True)
        after = index.decorate_entries(entries, read_state.load("CASE-A"))
        assert after["entries"][0]["key"] == "resident:old"
        assert before["unread_count"] == after["unread_count"]
        assert after["sections"]["conversation"]["count"] == 3
        assert after["sections"]["notification"]["count"] == 2
        assert before["content_signature"] != after["content_signature"]
        read_state.dismiss("CASE-A", {entry["key"]: entry["updated_at"] for entry in after["entries"] if not entry["pinned"]})
        retained = index.decorate_entries(entries, read_state.load("CASE-A"))["entries"]
        assert [entry["key"] for entry in retained] == ["resident:old"]
        assert retained[0]["unread"]
        read_state.read_through("CASE-A", [entry["key"] for entry in retained])
        assert not index.decorate_entries(entries, read_state.load("CASE-A"))["entries"][0]["unread"]
        assert not any(entry["pinned"] for entry in index.decorate_entries(entries, read_state.load("sample_admin"))["entries"])


def test_pin_limit_is_atomic_and_disappeared_targets_release_their_slot(isolated_portal_case):
    """置顶数量上限原子执行且消失条目释放名额

    :importance: P1
    :design-point:
    """
    with isolated_portal_case():
        for position in range(50):
            read_state.set_pinned("CASE-A", f"resident:{position}", True)
        assert not read_state.set_pinned("CASE-A", "resident:0", True)
        with pytest.raises(ValueError, match="50"):
            read_state.set_pinned("CASE-A", "resident:overflow", True)
        read_state.retain_pins("CASE-A", {"resident:0"})
        read_state.set_pinned("CASE-A", "resident:overflow", True)
        assert read_state.load("CASE-A")["pinned_keys"] == ["resident:0", "resident:overflow"]


def test_old_pins_are_in_page_one_and_not_repeated_on_later_pages(monkeypatch, isolated_portal_case):
    """旧置顶条目进入首页且不会在后续页面重复

    :design-point:"""
    from management_portal import serve_management_portal as portal

    with isolated_portal_case():
        old = item("resident:resident_111111111111", at="2026-09-01T00:00:00Z")
        new = item("resident:resident_222222222222")
        read_state.set_pinned("CASE-A", old["key"], True)
        monkeypatch.setattr(index, "build_entries_page", lambda actor, **kwargs: {
            "entries": [old] if kwargs["page_token"] else [new], "next_page_token": "next" if not kwargs["page_token"] else "",
            "paged": True, "page_size": 1,
        })
        monkeypatch.setattr(portal, "_agent_conversation_entries", lambda actor, resident_ids: [old])
        monkeypatch.setattr(portal, "_mailbox_source_entries", lambda actor: [])
        first = portal._agent_conversation_payload("CASE-A", page_size=1)
        assert [entry["key"] for entry in first["entries"]] == [old["key"], new["key"]]
        assert portal._agent_conversation_payload("CASE-A", page_size=1, page_token="next")["entries"] == []
        monkeypatch.setattr(portal, "_agent_conversation_entries", lambda actor, resident_ids: [])
        portal._agent_conversation_payload("CASE-A", page_size=1)
        assert read_state.load("CASE-A")["pinned_keys"] == []


def test_pin_instruction_rejects_notifications_and_missing_items(monkeypatch):
    """置顶指令拒绝通知与不存在的条目

    :design-point:"""
    monkeypatch.setattr(web_isa, "_inbox_entry", lambda *_: item("mention:p", "notice-board-mention"))
    with pytest.raises(InstructionError, match="通知不能置顶"):
        web_isa.pin_inbox_item(invocation(key="mention:p", pinned=True))
    monkeypatch.setattr(web_isa, "_inbox_entry", lambda *_: None)
    with pytest.raises(InstructionError, match="不存在"):
        web_isa.pin_inbox_item(invocation(key="resident:deleted", pinned=True))


def test_pin_source_failure_does_not_remove_pins(monkeypatch, isolated_portal_case):
    """置顶来源读取失败时保留已有置顶

    :design-point:"""
    from management_portal import serve_management_portal as portal

    with isolated_portal_case():
        read_state.set_pinned("CASE-A", "chatroom:room", True)
        monkeypatch.setattr(index, "build_entries_page", lambda *a, **kw: {"entries": [], "next_page_token": "", "paged": True})
        monkeypatch.setattr(portal, "_mailbox_source_entries", lambda actor: (_ for _ in ()).throw(RuntimeError("source unavailable")))
        with pytest.raises(RuntimeError, match="source unavailable"):
            portal._agent_conversation_payload("CASE-A")
        assert read_state.load("CASE-A")["pinned_keys"] == ["chatroom:room"]


def test_node_menu_can_pin_a_visible_resident_without_conversation_or_read_side_effects(monkeypatch, isolated_portal_case):
    """节点 Menu 固定未交谈的 Resident，并保持未读状态

    验证未交谈或已清理的可见节点可固定到个人 Mailbox；固定跨刷新保持，取消固定移除无活动条目，访问限制与源服务故障不会破坏已有固定或已读记录。

    :design-point:
    """
    from management_portal import conversation_index_projection as projection
    from management_portal import serve_management_portal as portal
    from management_portal.society_service import resident_protocol

    resident_id = "resident_111111111111"
    key = f"resident:{resident_id}"
    visible = True
    activity = []
    reads = []

    def resident(exact, actor):
        reads.append((exact, actor))
        assert exact == resident_id
        if actor != "CASE-A" or not visible:
            raise PermissionError("not visible")
        return resident_protocol._resident_view(SimpleNamespace(
            resident_id=exact,
            record={"resident_id": exact, "title": "未交谈的节点", "lineage_id": "lineage_222222222222", "lineage_title": "科研团队"},
        ))

    def inbox_entry(actor, exact_key):
        return next((entry for entry in index.decorate_entries(
            index.build_entries(actor, resident_ids=[resident_id]), read_state.load(actor),
        )["entries"] if entry["key"] == exact_key), None)

    with isolated_portal_case():
        monkeypatch.setattr(resident_protocol, "_workspace_views", lambda *_: [])
        monkeypatch.setattr(resident_protocol.resident_backend, "execution_account_projection", lambda *_: {})
        monkeypatch.setattr(projection, "get_society_service", lambda: SimpleNamespace(resident=SimpleNamespace(
            get_resident=resident,
            get_resident_state=lambda *args: {"cursor": 7, "state": {"status": "idle", "intake_state": "open"}},
        )))
        monkeypatch.setattr(projection, "collect_entries", lambda **kwargs: list(activity))
        monkeypatch.setattr(projection, "get_avatar", lambda *_: {})
        monkeypatch.setattr(web_isa, "_inbox_entry", inbox_entry)
        monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
            _forget_cached_inbox=lambda *_: None,
            _publish_conversation_read_changes=lambda *a, **kw: None,
        ))
        assert inbox_entry("CASE-A", key) is None
        pinned = web_isa.pin_inbox_item(invocation(key=key, pinned=True))["item"]
        assert pinned["pinned"] and not pinned["unread"]
        assert pinned["title"] == "未交谈的节点"
        assert pinned["subtitle"] == "科研团队"
        assert pinned["resident_id"] == resident_id and pinned["cursor"] == 7
        assert "lineage_222222222222" in pinned["url"]
        assert unread_flags("CASE-A") == {key: False}
        assert index.build_entries("sample_admin", resident_ids=[resident_id]) == []
        assert index.build_entries("CASE-A", resident_ids=[]) == []

        # Refreshing the first Mailbox page retains this pin even without activity.
        monkeypatch.setattr(index, "build_entries_page", lambda *a, **kw: {"entries": [], "paged": True})
        monkeypatch.setattr(index, "unread_resident_entries", lambda *a: [])
        monkeypatch.setattr(portal, "_mailbox_source_entries", lambda *_: [])
        assert portal._agent_conversation_payload("CASE-A")["entries"][0]["key"] == key
        with monkeypatch.context() as unavailable:
            unavailable.setattr(projection, "resident_pin_entry", lambda *a: (_ for _ in ()).throw(RuntimeError("source unavailable")))
            with pytest.raises(RuntimeError, match="source unavailable"):
                portal._agent_conversation_payload("CASE-A")
            assert read_state.load("CASE-A")["pinned_keys"] == [key]
        assert web_isa.pin_inbox_item(invocation(key=key, pinned=False))["item"] == {}
        assert index.build_entries("CASE-A", resident_ids=[resident_id]) == []

        # A conversation cleared from Mailbox can be pinned again without marking it read.
        activity.append({**item(key), "resident_id": resident_id})
        read_state.set_read("CASE-A", key, unread_value=True)
        read_state.dismiss("CASE-A", {key: activity[0]["updated_at"]})
        assert inbox_entry("CASE-A", key) is None
        read_state.set_read("CASE-A", key, unread_value=True)
        assert web_isa.pin_inbox_item(invocation(key=key, pinned=True))["item"]["pinned"]
        assert unread_flags("CASE-A") == {key: True}
        assert not read_state.row(read_state.load("CASE-A"), key)["dismissed_through"]
        web_isa.pin_inbox_item(invocation(key=key, pinned=False))
        activity.clear()

        visible = False
        with pytest.raises(InstructionError, match="不存在"):
            web_isa.pin_inbox_item(invocation(key=key, pinned=True))
        assert read_state.load("CASE-A")["pinned_keys"] == []
        assert reads and all(actor == "CASE-A" for _, actor in reads)


@pytest.mark.parametrize("mode", ["unread", "all"])
def test_clear_instruction_only_changes_selected_section(monkeypatch, mode):
    """清空 Mailbox 仅处理选定分区并保留整区清空时的置顶

    :design-point:"""
    calls = []
    entries = [{**entry, "unread": True} for entry in (
        item("resident:a"), {**item("resident:pin"), "pinned": True}, item("notice-board:n", "notice-board-mention"),
    )]
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda actor: entries if mode == "all" else pytest.fail("unread must not page history"))
    monkeypatch.setattr(read_state, "load", lambda actor: {"rows": {
        entry["key"]: {"unread": True, "pinned": bool(entry.get("pinned")), "read_revision": 1, "dismissed_through": ""}
        for entry in entries
    }})
    monkeypatch.setattr(mailbox_sources, "sources", lambda: [])
    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
        _clear_agent_conversations=lambda actor, **kwargs: calls.append(kwargs) or {
            "cleared_count": len(kwargs["entries"]), "items": []},
    ))
    monkeypatch.setattr(web_isa, "_access", lambda: SimpleNamespace(user=lambda _user: {"configured": True}))
    web_isa.clear_user_mailbox(invocation(user_id="CASE-A", mode=mode, section="notification"))
    assert [entry["key"] for entry in calls[0]["entries"]] == ["notice-board:n"]
    web_isa.clear_user_mailbox(invocation(user_id="CASE-A", mode=mode, section="conversation"))
    assert [entry["key"] for entry in calls[1]["entries"]] == (["resident:a"] if mode == "all" else ["resident:a", "resident:pin"])
    web_isa.clear_user_mailbox(invocation(user_id="CASE-A", mode=mode))
    assert [entry["key"] for entry in calls[2]["entries"]] == (
        ["resident:a", "notice-board:n"] if mode == "all" else ["resident:a", "resident:pin", "notice-board:n"]
    )


def test_watch_interprets_the_browser_contract_without_exposing_other_sources():
    """Mailbox 监听从浏览器变更提取键且过滤无关事件

    :design-point:"""
    keys, snapshot = web_isa.inbox_change_keys({
        "resident_events": [{"resident_id": "resident_a", "event_type": "resident.message.appended"},
                            {"resident_id": "resident_b", "event_type": "resident.activity.changed"}],
        "projection_changes": {"agent-conversation-index": {"current": ["unread"]}},
        "projection_details": {"agent-conversation-index": {"current": {"keys": ["notice:n"]}}},
    })
    assert keys == ["notice:n", "resident:resident_a"]
    assert not snapshot
    assert web_isa.inbox_change_keys({"projection_changes": {"agent-conversation-index": {"current": []}}}) == ([], True)


def test_watch_uses_authorized_browser_changes_and_resumable_checkpoints(monkeypatch):
    """Mailbox 监听按当前授权推送且支持游标恢复

    :design-point:"""
    from management_portal import notifications
    from management_portal.notifications.bus import NotificationHub

    hub = NotificationHub()
    monkeypatch.setattr(notifications, "get_notification_hub", lambda: hub)
    calls = []

    def authorized_payload(actor, batch, domains):
        calls.append((actor, domains))
        return {"resident_events": [notification.payload["event"] for notification in batch
                                    if notification.payload["actor"] == actor]}

    monkeypatch.setattr(web_isa, "web", lambda: SimpleNamespace(
        _portal_change_payload=authorized_payload, _PORTAL_ACCESS=SimpleNamespace(user=lambda name: {"configured": True})))
    monkeypatch.setattr(web_isa, "_inbox_entries", lambda *_: pytest.fail("WatchMailbox must not poll GetMailbox"))
    stream = web_isa.watch_inbox(invocation(user_id="CASE-A", timeout_seconds=1))
    first = next(stream)[0]
    assert first["snapshot_required"] and first["keys"] == []
    assert first["cursor"].startswith("mailbox-watch-v1:")
    stream.close()
    invalid_cursor = first["cursor"].rsplit(":", 1)[0] + ":invalid"
    with pytest.raises(InstructionError, match="无效的 WatchMailbox 游标"):
        next(web_isa.watch_inbox(invocation(user_id="CASE-A", after_cursor=invalid_cursor, timeout_seconds=1)))
    hub.publish("portal.resident.release.resident_private.events", {
        "actor": "sample_admin", "event": {"resident_id": "resident_private", "event_type": "resident.message.appended"}})
    hub.publish("portal.resident.release.resident_visible.events", {
        "actor": "CASE-A", "event": {"resident_id": "resident_visible", "event_type": "resident.message.appended"}})
    resumed = web_isa.watch_inbox(invocation(user_id="CASE-A", after_cursor=first["cursor"], timeout_seconds=1))
    change = next(resumed)[0]
    resumed.close()
    assert change["keys"] == ["resident:resident_visible"]
    assert not change["snapshot_required"]
    assert change["cursor"] != first["cursor"]
    assert calls == [("CASE-A", {"resident", "projection"})]
    # A restart or evicted cursor tells the consumer to re-read, never silently
    # advancing past changes it has not seen.
    monkeypatch.setattr(notifications, "get_notification_hub", lambda: NotificationHub())
    restarted = web_isa.watch_inbox(invocation(user_id="CASE-A", after_cursor=change["cursor"], timeout_seconds=1))
    assert next(restarted)[0]["snapshot_required"]
    restarted.close()
    gap_hub = NotificationHub(max_items=32)
    for position in range(40):
        gap_hub.publish("test", {"position": position})
    monkeypatch.setattr(notifications, "get_notification_hub", lambda: gap_hub)
    gapped = web_isa.watch_inbox(invocation(user_id="CASE-A", after_cursor=first["cursor"], timeout_seconds=1))
    assert next(gapped)[0]["snapshot_required"]
    gapped.close()
