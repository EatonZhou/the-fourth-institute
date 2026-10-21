"""attach_to_next: an info message waits for the next request instead of starting a turn (concept_43c793b4f3ab)."""

from __future__ import annotations

from management_portal.society_service import resident_conversation_stream, resident_held_infos, resident_execution_work
from management_portal.society_service import resident_domain as backend
from tests.cases.test_resident_execution_protocol import input_delivery, resident_record  # noqa: F401


def test_info_message_is_held_without_starting_a_turn(input_delivery):
    """attach_to_next 的 info 消息排队暂存，不启动新轮次"""
    resident_id = input_delivery["resident_id"]

    result = backend.deliver_resident_message_content(
        resident_id, "CASE-A", "共享接口 get_user 改名为 get_principal。",
        activation_policy="attach_to_next", message_id="msg_00000000000000a1",
    )

    assert result["message_id"] == "msg_00000000000000a1"
    assert resident_execution_work.read(input_delivery) == {}
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, "msg_00000000000000a1")["state"] == "queued"
    assert [item["message_id"] for item in resident_held_infos.peek(input_delivery)] == ["msg_00000000000000a1"]


def test_repeating_an_info_delivery_holds_it_once(input_delivery):
    """重复投递同一条 info 消息只暂存一次"""
    for _ in range(2):
        backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "同一条更新。",
            activation_policy="attach_to_next", message_id="msg_00000000000000a2",
        )

    assert [item["message_id"] for item in resident_held_infos.peek(input_delivery)] == ["msg_00000000000000a2"]


def test_next_request_carries_held_infos_once_and_closes_them(input_delivery):
    """下一条请求按顺序带上暂存的 info，带过一次后标为完成且不再附带"""
    resident_id = input_delivery["resident_id"]
    for index, text in enumerate(("权限内核新增 Operation 资源。", "页面改用新的授权检查。"), start=1):
        backend.deliver_resident_message_content(
            resident_id, "CASE-A", text, activation_policy="attach_to_next", message_id=f"msg_00000000000000b{index}",
        )

    prompt, carried = resident_held_infos.attach(input_delivery, "请继续实现 admin 页面。")

    assert prompt.startswith(resident_held_infos.INFO_HEADER)
    assert prompt.index("权限内核新增 Operation 资源。") < prompt.index("页面改用新的授权检查。") < prompt.index("请继续实现 admin 页面。")
    assert [item["message_id"] for item in carried] == ["msg_00000000000000b1", "msg_00000000000000b2"]

    resident_held_infos.consume(input_delivery, carried)
    resident_held_infos.consume(input_delivery, carried)

    repository = resident_conversation_stream.get_repository()
    assert resident_held_infos.peek(input_delivery) == []
    assert resident_held_infos.attach(input_delivery, "下一条请求。") == ("下一条请求。", [])
    for message_id in ("msg_00000000000000b1", "msg_00000000000000b2"):
        assert repository.input_state(resident_id, message_id)["state"] == "completed"
        assert repository.message(resident_id, message_id)["activated_at"]


def test_info_message_rejects_request_context(input_delivery):
    """info 消息带运行说明时被拒绝，也不暂存"""
    import pytest

    with pytest.raises(ValueError, match="info 消息只包含消息内容"):
        backend.deliver_resident_message_content(
            input_delivery["resident_id"], "CASE-A", "带运行说明的 info",
            activation_policy="attach_to_next", runtime_context="额外说明",
        )
    assert resident_held_infos.peek(input_delivery) == []


def test_snapshot_marks_info_messages_still_waiting(input_delivery):
    """对话快照标出仍在等待下一条请求的 info 消息"""
    resident_id = input_delivery["resident_id"]
    backend.deliver_resident_message_content(
        resident_id, "CASE-A", "记忆库新增了登录约定。", activation_policy="attach_to_next", message_id="msg_00000000000000a7",
    )

    snapshot = resident_held_infos.project_snapshot(input_delivery, {"messages": [
        {"message_id": "msg_00000000000000a7"}, {"message_id": "msg_unrelated00000000"},
    ]})

    held, other = snapshot["messages"]
    assert held["held_info"] is True and "held_info" not in other
    assert [item["message_id"] for item in snapshot["held_infos"]] == ["msg_00000000000000a7"]
    assert resident_held_infos.project_snapshot({"resident_id": "resident_dddddddddddd"}, {"messages": []}) == {"messages": []}


def test_delivered_infos_show_inside_the_request_they_came_with(input_delivery):
    """已送达的 info 折叠显示在随行请求里

    模型把 info 和随行请求当作一次输入读到；对话中 info 不再单独成条，作为请求的 attached_infos 显示并保留原发送者和时间；请求不在本页时 info 留在原处并标注随行。

    The model reads infos and their request as one input; the Conversation shows them inside it, collapsed.

    :module: resident.conversation
    :design: concept_43c793b4f3ab
    """

    resident_id = input_delivery["resident_id"]
    for index, text in enumerate(("记忆库更正了发布界限。", "临时放行没有固定有效期。"), start=1):
        backend.deliver_resident_message_content(
            resident_id, "CASE-A", text, activation_policy="attach_to_next", message_id=f"msg_00000000000000c{index}",
        )
    _, carried = resident_held_infos.attach(input_delivery, "我们的工作进行到哪一步？")
    resident_held_infos.consume(input_delivery, carried, request_message_id="msg_00000000000000d1")

    # Each delivered info says, in the trace, which request it went with.
    stored = resident_conversation_stream.get_repository().message(resident_id, "msg_00000000000000c1")
    assert stored["released_with"] == "msg_00000000000000d1"

    def info(index: int) -> dict:
        return {"message_id": f"msg_00000000000000c{index}", "role": "user", "author_id": "steward",
                "created_at": f"2026-10-04T10:00:0{index}Z", "content_parts": [{"type": "text", "text": f"info {index}"}],
                "released_with": "msg_00000000000000d1"}

    snapshot = resident_held_infos.project_snapshot(input_delivery, {"messages": [
        {"message_id": "msg_00000000000000e0", "role": "agent"},
        {"message_id": "msg_00000000000000d1", "role": "user"},
        info(1),
        info(2),
        {"message_id": "msg_00000000000000e1", "role": "agent"},
    ]})

    assert [item["message_id"] for item in snapshot["messages"]] == [
        "msg_00000000000000e0", "msg_00000000000000d1", "msg_00000000000000e1",
    ]
    request = snapshot["messages"][1]
    assert [item["message_id"] for item in request["attached_infos"]] == ["msg_00000000000000c1", "msg_00000000000000c2"]
    assert request["attached_infos"][0] == {
        key: value for key, value in info(1).items() if key not in {"role", "released_with"}
    }
    assert "attached_infos_steward" not in request and "held_infos" not in snapshot
    # Without its request on the page, an info stays where it is, marked as delivered with it.
    alone = resident_held_infos.project_page(input_delivery, [
        {"message_id": "msg_00000000000000c1", "role": "user", "released_with": "msg_00000000000000d1"},
    ])
    assert alone == [{"message_id": "msg_00000000000000c1", "role": "user", "released_with": "msg_00000000000000d1"}]
