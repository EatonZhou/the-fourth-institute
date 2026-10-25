"""Family facade: messages wait for the Family Router and relays close them (decision_44cf587c1721).

:module: resident.conversation
"""

from __future__ import annotations
from tests.cases.family_test_support import set_role

import pytest

from management_portal.society_service import family_facade, resident_conversation_stream, resident_execution_work
from management_portal.society_service import resident_directory_domain
from management_portal.society_service import resident_domain as backend
from management_portal.society_service.resident_workspace_domain import _find_resident
from tests.cases.test_resident_execution_protocol import VALID_PNG_BYTES as PNG
from tests.cases.test_resident_execution_protocol import input_delivery, resident_record  # noqa: F401
from tests.cases.permission_test_support import Fact, permissions  # noqa: F401

MEMBER = "resident_bbbbbbbbbbbb"


def _register_test_member(facade, title):
    from management_portal.society_service import resident_lineage
    from management_portal.society_service.state_store import get_lineage_data_store
    from tests.cases.society_service_test_support import publish_registered_lineage_members

    lineage = get_lineage_data_store().lineage_definition(resident_lineage.lineage_id(facade))
    publish_registered_lineage_members({
        **lineage,
        "created_at": "2026-10-05T00:00:00Z", "updated_at": "2026-10-05T00:00:00Z",
        "nodes": [{"id": MEMBER, "title": title}],
    }, actor="CASE-A")


@pytest.fixture
def facade(input_delivery):
    resident_id = input_delivery["resident_id"]
    set_role(resident_id, family_id="fbi_family0001", actor="CASE-A")
    return _find_resident(resident_id)



def test_facade_message_waits_without_starting_a_turn(facade):
    """门面收到消息只排队不开始执行

    发给门面的消息进入 queued，不建立执行，等待 Family Router；消息到达时就出现在门面对话中，转交给成员后不再重复写入，时间不变。

    :design: decision_44cf587c1721
    """
    resident_id = facade["resident_id"]

    backend.deliver_resident_message_content(resident_id, "CASE-A", "admin 页面的权限检查怎么接？",
                                             message_id="msg_00000000000000f1")

    assert family_facade.is_facade(facade)
    assert resident_execution_work.read(facade) == {}
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, "msg_00000000000000f1")["state"] == "queued"
    # CASE-A 10-05: the sender sees the request at once, while the Router still chooses a member.
    shown_at = repository.message(resident_id, "msg_00000000000000f1")["activated_at"]
    assert shown_at
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == ["msg_00000000000000f1"]
    family_facade.complete_relay((resident_id, "msg_00000000000000f1"), MEMBER, "msg_00000000000000e1")
    assert repository.message(resident_id, "msg_00000000000000f1")["activated_at"] == shown_at
    assert [item["message_id"] for item in repository.read_messages(resident_id)] == ["msg_00000000000000f1"]


def test_facade_rejects_injection_and_infos_carrying_more_than_text(facade):
    """门面拒收插入当前一轮的消息和带附件的 info

    门面只接收普通消息和 info 消息；inject_active_turn 被拒绝；info 与发给任何 Resident 的 info 一样只包含消息内容，带说明、附件或引用时被拒绝。

    :design: decision_44cf587c1721
    """
    with pytest.raises(ValueError, match="只接收普通消息和 info 消息"):
        backend.deliver_resident_message_content(facade["resident_id"], "CASE-A", "插入", activation_policy="inject_active_turn")
    # An info carries only what it says, as on any Resident.
    with pytest.raises(ValueError, match="info 消息只包含消息内容"):
        backend.deliver_resident_message_content(facade["resident_id"], "CASE-A", "带说明", runtime_context="x",
                                                 activation_policy="attach_to_next")


def _upload(name: str, media_type: str, data: bytes, owner: str = "CASE-A") -> str:
    """A finished Chat attachment, uploaded the way the composer uploads it."""

    from management_portal.hatg import catalog
    from management_portal.hatg.contracts import InstructionCall, InstructionContext
    from management_portal.hatg.kernel import Invocation
    from management_portal.resources import isa as resource_isa

    arguments = {"purpose": "conversation_attachment", "file_name": name, "media_type": media_type, "size": len(data)}
    invocation = Invocation(catalog.declaration("CreateUpload"), InstructionCall(instruction="CreateUpload", arguments=arguments),
                            InstructionContext(caller=owner, entry="web"), "inst_res_00000000000000000000")
    upload_id = resource_isa.create_upload(invocation)["upload_id"]
    resource_isa.complete_upload(upload_id, data, actor=owner)
    return upload_id


def test_a_facade_request_carries_its_images_and_files_to_the_member(facade, monkeypatch, tmp_path, permissions):
    """发给门面的请求可以带图片和文件，并原样交给成员

    发给门面的请求和发给任何 Resident 一样可以带图片和文件：门面收到时按发送者检查每个附件（权限系统开启时别人的上传被拒绝），保存原消息，不开始工作；Router 以门面身份把消息原样转交成员时，成员按原发送者的权限读取这些附件，图片进入成员的输入并复制到它的会话目录，文件以路径引用给它；转交时改动了消息的，按转交者自己的权限读取，别人的上传被拒绝。

    CASE-A 10-06: a message to a facade carries images and files like a message to any Resident.

    The facade checks the sender's right to each attachment when the message
    arrives; the member reads them as if the sender had sent it the message,
    as long as the Router relays the message unchanged.

    :importance: P1
    :aspects: functional, behavior
    :design: decision_44cf587c1721, decision_c524c775e0be
    """

    from management_portal.society_service import resident_protocol
    from management_portal.society_service.resident_workspace_domain import _save_resident_record

    monkeypatch.setenv("PORTAL_UPLOAD_ROOT", str(tmp_path / "uploads"))
    resident_id = facade["resident_id"]
    image = _upload("shot.png", "image/png", PNG)
    notes = _upload("notes.txt", "text/plain", b"steps to reproduce")
    parts = [
        {"type": "text", "text": "登录页报错，截图和日志在附件里。"},
        {"type": "image", "media_type": "image", "upload_id": image, "mime_type": "image/png", "name": "shot.png"},
        {"type": "file", "media_type": "file", "upload_id": notes, "mime_type": "text/plain", "name": "notes.txt"},
    ]

    def send(message_id, content_parts, actor="CASE-A", **options):
        return resident_protocol.deliver_resident_input({
            "resident_id": resident_id, **options,
            "message": {"message_id": message_id, "role": "user", "content_parts": content_parts},
        }, actor, delivery_context=resident_protocol.ResidentDeliveryContext(business_ref=mark) if actor != "CASE-A" else None)

    mark = ""
    send("msg_00000000000000a1", parts)
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, "msg_00000000000000a1")["state"] == "queued"
    assert repository.message(resident_id, "msg_00000000000000a1")["content_parts"] == parts
    assert resident_execution_work.read(_find_resident(resident_id)) == {}
    # Someone else's upload is not the sender's to send.
    foreign = _upload("other.png", "image/png", PNG, owner="sample_admin")
    with pytest.raises(PermissionError, match="只能发送自己上传的附件"):
        send("msg_00000000000000a2", [parts[0], {**parts[1], "upload_id": foreign}])
    with pytest.raises(ValueError, match="info 消息只包含消息内容"):
        send("msg_00000000000000a3", parts, activation_policy="attach_to_next")

    # The Router, running as the facade, relays the message to a member; this
    # Resident stands in for that member from here on.
    router = "resident_cccccccccccc"
    member = _find_resident(resident_id)
    from management_portal.society_service.state_store import get_lineage_data_store
    get_lineage_data_store().database.write(lambda c: c.execute(
        "UPDATE family_residents SET role='member' WHERE resident_id=%s", (resident_id,)))
    mark = family_facade.relay_mark(resident_id, "msg_00000000000000a1")
    monkeypatch.setattr(family_facade, "validate_relay",
                        lambda business_ref, actor: (resident_id, "msg_00000000000000a1") if business_ref == mark else None)
    monkeypatch.setattr(family_facade, "relay_delegates", lambda source, member_id: source is not None)

    # A relay that alters the message is read with the relayer's own access.
    with pytest.raises(PermissionError, match="只能发送自己上传的附件"):
        send("msg_00000000000000b0", [{"type": "text", "text": "改过的请求"}, *parts[1:]], actor=router)
    send("msg_00000000000000b1", parts, actor=router)

    job = resident_execution_work.read(_find_resident(resident_id))
    assert job["user_message_id"] == "msg_00000000000000b1"
    staged = job["input_media"]
    assert [item["name"] for item in staged] == ["shot.png"]
    assert open(staged[0]["source"], "rb").read() == PNG
    assert staged[0]["original_source"].endswith("/shot.png")
    from management_portal.society_service.resident_execution_domain import _prepare_resident_prompt
    job = _prepare_resident_prompt(_find_resident(resident_id), job)
    from management_portal.execution.message_projection import unwrap_message
    assert unwrap_message(job["actual_sent_prompt"]).endswith(f"/{notes}/notes.txt]")


def test_a_residents_state_says_whether_messages_to_it_may_carry_images_and_files(input_delivery, monkeypatch):
    """Resident 状态写明发给它的消息能否带图片和文件

    对话控制中的 accepts_images、accepts_files 来自 Resident 的执行提供方：能读图片、能用本地工具读文件的为 true，否则为 false；Family Router 据此只把带图片或文件的请求交给能读取的成员。

    The Family Router reads this to give a request with images or files only to a member that reads them.

    :importance: P1
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import resident_execution_provider, resident_protocol, resident_workspace_domain

    target = resident_workspace_domain.resolve_resident_runtime_target(input_delivery["resident_id"], "CASE-A", require_visible=True)
    controls = resident_protocol._conversation_controls(target, "CASE-A", {})
    assert (controls["accepts_images"], controls["accepts_files"]) == (True, True)
    monkeypatch.setattr(resident_execution_provider, "provider_for",
                        lambda record: resident_execution_provider.ProviderApiExecutionProvider())
    controls = resident_protocol._conversation_controls(target, "CASE-A", {})
    assert (controls["accepts_images"], controls["accepts_files"]) == (False, False)


def test_a_facade_info_goes_to_the_steward_and_shows_with_the_next_request(facade, monkeypatch):
    """发给门面的 info 到达即转给 steward，并随门面的下一条请求显示

    门面收到 info 后立即以门面身份转给本 Family 的 steward，原发送者写在说明里（重试不重复）；Router 不处理 info；门面对话中 info 先显示为已交给 steward，下一条请求转交后折叠在该请求里。

    Whom an info concerns depends on what it says: the steward gets it at once and decides.

    :design: decision_44cf587c1721, concept_43c793b4f3ab
    """

    from management_portal.society_service import lineage_message_events, resident_execution_domain, resident_held_infos

    steward = "resident_cccccccccccc"
    forwarded = []
    monkeypatch.setattr(family_facade, "family_steward_id", lambda record: steward)
    monkeypatch.setattr(
        resident_execution_domain, "deliver_resident_message_content",
        lambda resident_id, actor, content, **options: forwarded.append((resident_id, actor, content, options)) or {},
    )
    resident_id, info_id, request_id = facade["resident_id"], "msg_00000000000000f7", "msg_00000000000000f8"
    for _attempt in range(2):  # a retried delivery forwards to the same copy
        backend.deliver_resident_message_content(
            resident_id, "CASE-A", "登录页改到周五上线。", activation_policy="attach_to_next", message_id=info_id,
        )

    # Like every internal note it comes from the facade; the context names the sender.
    assert [(target, actor, content) for target, actor, content, _options in forwarded] == [
        (steward, resident_id, "登录页改到周五上线。"), (steward, resident_id, "登录页改到周五上线。"),
    ]
    assert {options["message_id"] for *_rest, options in forwarded} == {
        family_facade.info_forward_message_id(resident_id, info_id),
    }
    assert f"这是 CASE-A 发给 Family 门面 {resident_id} 的 info 消息" in forwarded[0][3]["runtime_context"]
    assert forwarded[0][3]["business_ref"] == f"{resident_id}/info/{info_id}"
    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(resident_id, info_id)["state"] == "completed"
    assert resident_execution_work.read(facade) == {}
    # The Router never routes an info.
    assert lineage_message_events._facade_message_waiting(repository, _find_resident(resident_id), info_id) is False

    def shown():
        return resident_held_infos.project_snapshot(
            facade, {"messages": list(repository.snapshot(resident_id)["messages"])}, handed_to_steward=True,
        )["messages"]

    waiting = next(item for item in shown() if item["message_id"] == info_id)
    assert waiting["held_info"] is True and waiting["held_info_steward"] is True
    backend.deliver_resident_message_content(resident_id, "CASE-A", "登录页现在要改什么？", message_id=request_id)
    assert repository.input_state(resident_id, request_id)["state"] == "queued"
    family_facade.complete_relay((resident_id, request_id), MEMBER, "msg_00000000000000e8")
    messages = shown()
    assert info_id not in [item["message_id"] for item in messages]
    request = next(item for item in messages if item["message_id"] == request_id)
    assert [item["message_id"] for item in request["attached_infos"]] == [info_id]
    assert request["attached_infos_steward"] is True


def test_relay_closes_the_facade_message_and_projects_the_member(facade, monkeypatch):
    """转交后门面消息记为已转交并投影成员进度

    Router 转交后门面消息记为已完成并关联成员消息；投影带上承接的成员；还在等 Router 的请求标为 routing。

    :design: decision_44cf587c1721
    """
    monkeypatch.setattr(family_facade, "family_members", lambda _: [MEMBER, "resident_ffffffffffff"])
    resident_id = facade["resident_id"]
    backend.deliver_resident_message_content(resident_id, "CASE-A", "请评审 Operation 资源。", message_id="msg_00000000000000f2")
    mark = family_facade.relay_mark(resident_id, "msg_00000000000000f2")

    source = family_facade.validate_relay(mark, "CASE-A")
    family_facade.complete_relay(source, MEMBER, "msg_00000000000000e2")
    family_facade.complete_relay(source, MEMBER, "msg_00000000000000e2")

    repository = resident_conversation_stream.get_repository()
    assert source == (resident_id, "msg_00000000000000f2")
    assert repository.input_state(resident_id, "msg_00000000000000f2")["state"] == "completed"
    assert repository.message(resident_id, "msg_00000000000000f2")["activated_at"]
    assert family_facade.relay_links(facade) == {
        "msg_00000000000000f2": {
            "resident_id": MEMBER,
            "message_id": "msg_00000000000000e2",
            "relayed_at": family_facade.relay_links(facade)["msg_00000000000000f2"]["relayed_at"],
        },
    }
    snapshot = family_facade.project_snapshot(facade, {"messages": [
        {"message_id": "msg_00000000000000f2", "role": "user", "content_parts": []},
        {"message_id": "msg_unrelated00000000", "role": "user", "content_parts": []},
        {"message_id": "msg_waiting000000000", "role": "user", "content_parts": []},
    ], "input_states": [{"message_id": "msg_waiting000000000", "state": "queued"}]})
    relayed, unrelated, waiting = snapshot["messages"]
    assert relayed["relay"]["resident_id"] == MEMBER
    assert relayed["relay"]["message_id"] == "msg_00000000000000e2"
    assert "replies" not in relayed["relay"]
    assert "relay" not in unrelated
    # A request still waiting for the Router shows that under itself.
    assert waiting["relay"] == {"state": family_facade.ROUTING_STATE}
    # The facade's own state shows the Router still choosing a member for the waiting message.
    assert snapshot["state"]["work_phase"] == family_facade.ROUTING_PHASE
    assert snapshot["family_facade"]["family_id"] == "fbi_family0001"


@pytest.mark.parametrize("scope_kind", ["lineage_scope", "resident_lock"])
def test_relay_link_waits_for_the_nested_facades_admission(facade, monkeypatch, scope_kind):
    """嵌套转交在内层输入可读后才公开链接，原输入始终可查。

    :design: decision_44cf587c1721
    """
    from . import family_test_support
    from management_portal.society_service import invariant_locks, resident_protocol, family_recovery

    outer = facade["resident_id"]
    original, forwarded = "msg_00000000000000d1", "msg_00000000000000d2"
    _register_test_member(facade, "Inner facade")
    family_test_support.set_role(MEMBER, family_id="fbi_inner0001", actor="CASE-A")
    monkeypatch.setattr(family_recovery, "handle", lambda *a, **kw: None)
    monkeypatch.setattr(family_facade, "is_relay_target", lambda *a: True)
    backend.deliver_resident_message_content(outer, "CASE-A", "Nested request", message_id=original)
    repository = resident_conversation_stream.get_repository()

    def state():
        return resident_protocol.get_resident_input_state(outer, "CASE-A", original)

    scope_id = facade["lineage_id"] if scope_kind == "lineage_scope" else MEMBER
    with getattr(invariant_locks, scope_kind)(scope_id):
        backend.deliver_resident_message_content(
            MEMBER, "CASE-A", "Nested request", message_id=forwarded,
            business_ref=family_facade.relay_mark(outer, original),
        )
        # Family quota also completes the relay on receipt. Its inner facade
        # has not yet run accept_for_router under this outer operation scope.
        family_facade.complete_relay((outer, original), MEMBER, forwarded)
        assert repository.message(MEMBER, forwarded) is None
        assert family_facade.relay_links(facade) == {}
        assert state()["state"] == "queued"

    assert family_facade.relay_links(facade)[original]["message_id"] == forwarded
    assert state()["state"] == "queued"
    repository.set_input_state(MEMBER, forwarded, "completed")
    assert state()["state"] == "completed"
    assert state()["message_id"] == original
    with pytest.raises(KeyError):
        resident_protocol.get_resident_input_state(outer, "CASE-A", "msg_00000000000000d3")


def test_only_the_sender_or_owner_can_relay(facade, monkeypatch):
    """只有原发送者、门面或 owner 能转交门面消息

    其他主体使用转交标注向成员投递时被拒绝。

    :module: security.identity-access
    :design: decision_44cf587c1721
    """
    resident_id = facade["resident_id"]
    backend.deliver_resident_message_content(resident_id, "CASE-A", "只有我能转交。", message_id="msg_00000000000000f3")
    monkeypatch.setattr(resident_directory_domain, "_can_manage", lambda record, actor: False)

    with pytest.raises(PermissionError):
        family_facade.validate_relay(family_facade.relay_mark(resident_id, "msg_00000000000000f3"), "intruder")


def test_ordinary_business_refs_are_not_relays(facade):
    """普通 business_ref 不被当作转交

    格式不是门面消息引用的 business_ref 不触发转交逻辑。

    :design: decision_44cf587c1721
    """
    assert family_facade.validate_relay("paper_123456789abc", "CASE-A") is None
    assert family_facade.validate_relay("resident_cccccccccccc/message/msg_00000000000000f9", "CASE-A") is None


def test_relay_mark_lets_the_sender_reach_a_family_member(input_delivery, monkeypatch, permissions):
    """转交标注让原发送者可以投递给 Family 成员

    门面委托授权：能向门面发消息的主体，经 Router 转交时可以向成员投递。

    :module: security.identity-access
    :design: decision_44cf587c1721
    """
    from tests.cases.isa_test_support import run
    member_id = input_delivery["resident_id"]
    facade_id = "resident_cccccccccccc"
    mark = family_facade.relay_mark(facade_id, "msg_00000000000000c1")
    permissions.place(Fact("resident", member_id))
    permissions.grant("CASE-A", member_id, "@contributor")
    # The default template lets everyone message a Resident; take it away from the outsider.
    permissions.deny("outsider", member_id, "@message")
    monkeypatch.setattr(family_facade, "validate_relay",
                        lambda business_ref, actor: (facade_id, "msg_00000000000000c1") if business_ref == mark else None)
    monkeypatch.setattr(family_facade, "relay_delegates",
                        lambda source, resident_id: source is not None and resident_id == member_id)

    for business_ref in ("", mark):
        refused = run("DeliverMessage", {"resident_id": member_id, "business_ref": business_ref,
            "message": {"content_parts": [{"type": "text", "text": "未获成员投递权限"}]}}, actor="outsider")
        assert refused.error["code"] == "instruction.forbidden"
    # A validated internal Family relay carries the facade's delegation. A caller
    # cannot obtain that delegation merely by supplying the same mark to ISA.
    result = backend.deliver_resident_message_content(member_id, "outsider", "经门面转交。", business_ref=mark,
                                                      message_id="msg_00000000000000c2")

    assert result["message_id"] == "msg_00000000000000c2"


def test_a_relayed_copy_does_not_make_the_member_one_of_the_senders_conversations(input_delivery, monkeypatch):
    """门面转交给成员的副本不让成员进入发送者的会话列表

    转交副本保留原发送者，但发送者的会话列表只显示门面；发送者直接给成员发消息后，成员照常出现在列表里，分页列表与定向查询一致。

    The sender talks to the facade; the copy the Router hands a member keeps the sender but stays out of its list.

    :module: lineage.hatg
    :importance: P1
    :design: decision_44cf587c1721
    """

    member_id = input_delivery["resident_id"]
    facade_id = "resident_cccccccccccc"
    mark = family_facade.relay_mark(facade_id, "msg_00000000000000c4")
    monkeypatch.setattr(family_facade, "validate_relay",
                        lambda business_ref, actor: (facade_id, "msg_00000000000000c4") if business_ref == mark else None)
    monkeypatch.setattr(family_facade, "relay_delegates",
                        lambda source, resident_id: source is not None and resident_id == member_id)
    from management_portal.society_service import resident_conversation_projection_domain

    # Only which Residents are listed matters here, not how each record is read.
    monkeypatch.setattr(resident_directory_domain, "_engaged_conversation_records",
                        lambda resident_ids, actor: [{"resident_id": item} for item in resident_ids])
    monkeypatch.setattr(resident_conversation_projection_domain, "_conversation_family_summaries",
                        lambda resident_ids, actor: {})
    listed = lambda: [  # noqa: E731
        item["resident_id"]
        for item in resident_directory_domain.list_resident_conversation_activity("CASE-A", page_size=20)["residents"]
    ]

    backend.deliver_resident_message_content(member_id, "CASE-A", "经门面转交。", business_ref=mark,
                                             message_id="msg_00000000000000c5")
    repository = resident_conversation_stream.get_repository()
    assert repository.message(member_id, "msg_00000000000000c5")["author_id"] == "CASE-A"  # the sender is kept
    assert member_id not in listed()
    assert resident_directory_domain.list_resident_conversation_activity("CASE-A", [member_id]) == {"residents": []}

    # Talking to the member directly makes it one of the sender's conversations.
    backend.deliver_resident_message_content(member_id, "CASE-A", "直接问你。", message_id="msg_00000000000000c6")
    assert member_id in listed()
    assert [item["resident_id"] for item in resident_directory_domain.list_resident_conversation_activity(
        "CASE-A", [member_id])["residents"]] == [member_id]


def test_a_relayed_request_is_answered_on_the_facade_and_is_not_the_members(facade):
    """经门面的请求由成员答完后，只在门面上显示未读

    发给门面的请求转交成员后，成员答完时发送者的门面条目出现未读，打开门面 Chat 读到的是同一条答复；成员收到的转交副本不算发送者对成员的请求，成员条目不出现未读；直接发给成员的请求照常在成员上显示未读。

    CASE-A 10-05: what CASE-A asked the facade is unread on the facade when its member answers, not on the member.

    :module: lineage.hatg
    :importance: P1
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import resident_conversation_projection_domain as projection
    from management_portal.society_service import resident_lineage
    from management_portal.society_service.state_store import get_lineage_data_store

    facade_id = facade["resident_id"]
    _register_test_member(facade, '成员')
    from management_portal import conversation_read_state
    conversation_read_state.delete_actor("CASE-A")
    repository = resident_conversation_stream.get_repository()

    def say(resident_id, message_id, at, text, **fields):
        repository.append_message(resident_id, {
            "message_id": message_id, "content_parts": [{"type": "text", "text": text}], "created_at": at, **fields,
        })

    def answer(request_id, reply_id, at):
        say(MEMBER, reply_id, at, "完成", role="agent", reply_to_message_id=request_id)
        repository.set_input_state(MEMBER, request_id, "completed", updated_at=at)

    def unread(resident_id):
        return projection._conversation_family_summaries([facade_id, MEMBER], "CASE-A")[resident_id]["unread_signature"]

    backend.deliver_resident_message_content(facade_id, "CASE-A", "请评审 Operation 资源。", message_id="msg_00000000000000f7")
    say(MEMBER, "msg_00000000000000e7", "2026-10-05T07:00:00Z", "请评审 Operation 资源。", role="user", author_id="CASE-A",
        business_ref=family_facade.relay_mark(facade_id, "msg_00000000000000f7"))
    family_facade.complete_relay((facade_id, "msg_00000000000000f7"), MEMBER, "msg_00000000000000e7")
    assert unread(facade_id)["completion_id"] == ""  # relayed, not answered yet

    answer("msg_00000000000000e7", "msg_00000000000000a7", "2026-10-05T07:00:30Z")
    assert unread(facade_id)["completion_id"] == "msg_00000000000000a7"
    assert unread(facade_id)["input_message_id"] == "msg_00000000000000f7"
    assert unread(MEMBER)["completion_id"] == ""
    flags = conversation_read_state.load("CASE-A")
    assert conversation_read_state.unread({"key": "resident:" + facade_id}, flags)
    assert not conversation_read_state.unread({"key": "resident:" + MEMBER}, flags)

    # What CASE-A asks the member directly is the member's own.
    say(MEMBER, "msg_00000000000000e8", "2026-10-05T07:01:00Z", "直接问你。", role="user", author_id="CASE-A")
    answer("msg_00000000000000e8", "msg_00000000000000a8", "2026-10-05T07:01:30Z")
    assert unread(MEMBER)["completion_id"] == "msg_00000000000000a8"
    assert unread(facade_id)["completion_id"] == "msg_00000000000000a7"


def test_a_facade_chat_says_what_the_facade_is_until_a_message_goes_through_it():
    """门面的 Chat 说明它是哪个 Family 的门面，而不是提示缺少 session 历史

    门面从不执行：空对话时说明这是哪个 Family 的门面、发来的消息如何处理、成员有谁；回复者名称是 Family 名称；标题行第一项标明“Family 门面（N 位成员）”；不提供运行设置、重启 Session 与 slash 指令。

    A facade never runs: no session history to miss, and its replies are the Family's.

    :module: lineage.hatg
    :importance: P1
    :design: decision_44cf587c1721
    """

    from management_portal import resident_chat_projection

    contract = resident_chat_projection.project_session({
        "resident_id": "resident_aaaaaaaaaaaa",
        "resident": {"resident_id": "resident_aaaaaaaaaaaa", "title": "Family 实现", "backend": "codex"},
        "execution_account": {"account_id": "codex_one"},
        "provider_session": {"provider": "codex", "id": "facade-legacy-session"},
        "backend_capabilities": {"backend": "codex", "session_restart": True, "runtime_settings": ["model"]},
        "allows": {"RestartSession": True, "DeliverMessage": True},
        "family_facade": {"family_id": "fbi_family0001", "title": "Family 实现", "members": [
            {"resident_id": MEMBER, "title": "family 设计"}, {"resident_id": "resident_cccccccccccc", "title": "分支"},
        ]},
    }, "CASE-A")["chat_workspace"]

    assert contract["rendering"]["assistant_name"] == "Family 实现"
    assert contract["rendering"]["empty_text"].startswith("这是 Family「Family 实现」的门面。")
    assert "成员：family 设计、分支。" in contract["rendering"]["empty_text"]
    assert "session" not in contract["rendering"]["empty_text"]
    assert {"runtime_settings", "session_restart"}.isdisjoint(contract["actions"])
    assert {"runtime_settings", "session_restart"}.isdisjoint(contract["capabilities"])
    assert contract["composer"]["slash_commands"] == []
    assert contract["actions"]["send"]["instruction"] == "DeliverMessage"
    # CASE-A 10-05: the header marks a facade's Chat.
    assert contract["header"]["subtitle_parts"][0] == {"text": "Family 门面（2 位成员）", "identity": "family"}
    assert [part["identity"] for part in contract["header"]["subtitle_parts"]] == ["family", "resident"]
    assert contract["header"]["subtitle_parts"][1]["copy_value"] == "resident_aaaaaaaaaaaa"
    assert "codex_one" not in contract["subtitle"]
    assert "facade-legacy-session" not in contract["subtitle"]


def test_relay_delegation_requires_family_membership(facade, monkeypatch):
    """转交委托只对本 Family 成员有效

    目标不是该门面所属 Family 的成员时，转交授权不成立。

    :module: security.identity-access
    :design: decision_44cf587c1721
    """
    source = (facade["resident_id"], "msg_00000000000000c3")
    monkeypatch.setattr(family_facade, "family_member_ids", lambda record: {MEMBER})

    assert family_facade.relay_delegates(source, MEMBER)
    assert not family_facade.relay_delegates(source, "resident_dddddddddddd")
    assert not family_facade.relay_delegates(None, MEMBER)


def _relayed(facade, monkeypatch):
    resident_id = facade["resident_id"]
    backend.deliver_resident_message_content(resident_id, "CASE-A", "登录页 500 了", message_id="msg_00000000000000f4")
    mark = family_facade.relay_mark(resident_id, "msg_00000000000000f4")
    family_facade.complete_relay(family_facade.validate_relay(mark, "CASE-A"), MEMBER, "msg_00000000000000e4")
    repository = resident_conversation_stream.get_repository()
    monkeypatch.setattr(type(repository), "message_business_ref",
                        lambda self, rid, mid: mark if (rid, mid) in {(MEMBER, "msg_00000000000000e4"),
                                                                      (MEMBER, "msg_forged0000000000")} else "")
    return resident_id


def test_member_terminal_events_are_also_reported_for_the_facade(facade, monkeypatch):
    """成员处理转交消息的结束事件同时报告给门面

    观察门面的 work_finished、work_failed、work_cancelled 在成员结束转交工作时产生。

    :module: lineage.service
    :design: decision_44cf587c1721
    """
    from management_portal.society_service import lineage_program_terminal_reconciliation as terminal

    facade_id = _relayed(facade, monkeypatch)

    assert family_facade.relay_source_of(MEMBER, "msg_00000000000000e4") == (facade_id, "msg_00000000000000f4")
    # A message that merely carries the mark, without the recorded relay, is not the facade's work.
    assert family_facade.relay_source_of(MEMBER, "msg_forged0000000000") is None
    assert terminal._terminal_subjects(MEMBER, "msg_00000000000000e4") == {
        MEMBER: ("msg_00000000000000e4", {}),
        facade_id: ("msg_00000000000000f4", {
            "member_resident_id": MEMBER, "member_message_id": "msg_00000000000000e4",
        }),
    }
    assert terminal._terminal_subjects(MEMBER, "msg_other00000000000") == {MEMBER: ("msg_other00000000000", {})}


def test_stopping_a_facade_message_targets_the_member_work(facade, monkeypatch):
    """停止门面消息会停止对应成员的工作

    对门面消息执行 CancelInput 时，作用的是承接该消息的成员消息；Router 还没转交的消息没有成员消息，从门面自己撤回。

    :module: resident.session
    :design: decision_44cf587c1721
    """
    from management_portal.society_service.isa import execution

    facade_id = _relayed(facade, monkeypatch)

    assert execution._relayed_message(facade_id, "msg_00000000000000f4") == (True, (MEMBER, "msg_00000000000000e4"))
    assert execution._relayed_message(MEMBER, "msg_00000000000000e4") == (False, None)
    assert execution._relayed_message(facade_id, "msg_00000000000000f9") == (True, None)


def test_a_request_stopped_while_routed_is_withdrawn_and_reaches_no_member(facade, monkeypatch, permissions):
    """路由中的门面请求可以停止，撤回后不会交给任何成员

    Router 还在选择成员时门面的停止可用；发送者或门面 owner 用 CancelInput 撤回门面上排队的请求（门面没有执行队列），之后 RelayFamilyMessage 回报 withdrawn、不投递；其他人无权撤回。

    CASE-A 10-09: the facade can be stopped while the Router chooses; the Router's late choice relays nothing.

    :module: resident.session
    """

    from management_portal.society_service import family_quota, resident_protocol
    from tests.cases.isa_test_support import run

    monkeypatch.setattr(family_facade, "family_members", lambda _: [MEMBER, "resident_ffffffffffff"])
    facade_id = facade["resident_id"]
    permissions.users("CASE-A", "intruder")
    permissions.place(Fact("resident", facade_id))
    permissions.grant("CASE-A", facade_id, "@admin")
    backend.deliver_resident_message_content(facade_id, "CASE-A", "先别做了。", message_id="msg_00000000000000f7")

    snapshot = resident_protocol.get_resident_conversation_snapshot(facade_id, "CASE-A")
    assert snapshot["state"]["work_phase"] == family_facade.ROUTING_PHASE
    assert snapshot["controls"]["can_stop"] is True
    run("CancelInput", {"resident_id": facade_id, "message_id": "msg_00000000000000f7"}, actor="CASE-A").raise_for_error()

    repository = resident_conversation_stream.get_repository()
    assert repository.input_state(facade_id, "msg_00000000000000f7")["state"] == "cancelled"
    assert family_quota.relay_message(facade_id, "msg_00000000000000f7", MEMBER, "CASE-A") == {
        "status": "withdrawn", "member_id": MEMBER,
    }
    assert family_facade.relay_links(_find_resident(facade_id)) == {}
    snapshot = resident_protocol.get_resident_conversation_snapshot(facade_id, "CASE-A")
    assert snapshot["controls"]["can_stop"] is False
    withdrawn = next(item for item in snapshot["messages"] if item["message_id"] == "msg_00000000000000f7")
    assert withdrawn["withdrawn"] is True and withdrawn["content_parts"] == []
    # History pages do not supply live input_states; withdrawal still shows.
    history = family_facade.project_messages(facade, repository.read_messages(facade_id))
    assert history[0]["withdrawn"] is True and history[0]["content_parts"] == []
    assert repository.message(facade_id, "msg_00000000000000f7")["content_parts"][0]["text"] == "先别做了。"
    # Cancellation authority comes from the permission system, including on a facade.
    backend.deliver_resident_message_content(facade_id, "CASE-A", "这条是我的。", message_id="msg_00000000000000f8")
    refused = run("CancelInput", {"resident_id": facade_id, "message_id": "msg_00000000000000f8"}, actor="intruder")
    assert refused.status == "failed"
    assert refused.error["code"] == "instruction.forbidden"
    assert repository.input_state(facade_id, "msg_00000000000000f8")["state"] == "queued"


@pytest.mark.parametrize("started", [False, True], ids=["withdrawn-in-queue", "stopped-after-delivery"])
def test_cancelled_family_request_hides_only_undelivered_content(facade, monkeypatch, started):
    """Family 排队撤回显示占位，执行后停止保留原文

    门面根据成员持久投递记录区分撤回与停止；撤回隐藏正文、图片和引用，实时刷新和历史分页一致，清空成员 Chat 不改变判断。

    A withdrawn relay is an annotation; stopping work retains its input, even after Chat clears.

    :module: resident.session
    """
    _register_test_member(facade, "执行成员")
    facade_id = _relayed(facade, monkeypatch)
    repository = resident_conversation_stream.get_repository()
    original_id, copy_id = "msg_00000000000000f4", "msg_00000000000000e4"
    member_input = {"message_id": copy_id, "role": "user", "author_id": "CASE-A",
                    "created_at": "2026-10-15T08:40:13Z",
                    "content_parts": [{"type": "text", "text": "登录页 500 了"}]}
    repository.accept_user_message(MEMBER, member_input)
    repository.set_input_state(MEMBER, copy_id, "queued", updated_at="2026-10-15T08:40:13Z")
    if started:
        repository.commit_user_message(MEMBER, member_input, activated_at="2026-10-15T08:40:20Z")
        repository.set_input_state(MEMBER, copy_id, "running", updated_at="2026-10-15T08:40:20Z")
    repository.set_input_state(MEMBER, copy_id, "cancelled", updated_at="2026-10-15T08:40:46Z")

    assert family_facade.projection_targets({
        "event_type": resident_conversation_stream.INPUT_STATE_CHANGED, "resident_id": MEMBER,
        "payload": {"input_state": {"message_id": copy_id, "state": "cancelled"}},
    }) == [facade_id]

    def check_projection():
        original = repository.message(facade_id, original_id)
        original["content_parts"].append({"type": "image", "upload_id": "upload_fixture"})
        original["quote"] = {"content": "引用的正文"}
        original["context_refs"] = {"attachments": [{"name": "fixture.txt"}]}
        for states in ({original_id: "completed"}, None):
            projected, = family_facade.project_messages(facade, [original], input_states=states)
            assert projected["relay"]["state"] == "cancelled"
            assert bool(projected.get("withdrawn")) is not started
            if started:
                assert projected["content_parts"] == original["content_parts"]
            else:
                assert projected["content_parts"] == []
                assert "quote" not in projected and "context_refs" not in projected
        # Projection must not destroy the request stored on the facade.
        assert len(original["content_parts"]) == 2
        assert repository.message(facade_id, original_id)["content_parts"][0]["text"] == "登录页 500 了"

    check_projection()
    repository.agent_data.clear_chat(MEMBER, actor_ref="CASE-A")
    check_projection()


def test_a_facade_refreshes_when_its_request_moves_up_in_the_member_queue(facade, monkeypatch):
    """成员队列移动时门面刷新请求的排队位置

    成员队列变化（resident.queue.changed）时，队列中带门面转交标注的消息所属的门面收到投影刷新通知，请求下显示的“前面还有 N 条”随之更新；队列里没有门面转交的消息时不通知（CASE-A 10-06）。

    The place a relayed request shows (前面还有 N 条) follows the member's queue (CASE-A 10-06).

    :importance: P1
    :design: decision_44cf587c1721
    """

    facade_id = _relayed(facade, monkeypatch)
    queue = resident_conversation_stream.QUEUE_CHANGED

    def moved(*message_ids):
        return family_facade.projection_targets({"event_type": queue, "resident_id": MEMBER, "payload": {
            "queue": {"resident_id": MEMBER, "items": [{"message_id": item} for item in message_ids]},
        }})

    assert moved("msg_00000000000000d1", "msg_00000000000000e4") == [facade_id]
    assert moved("msg_00000000000000d1") == []
    assert family_facade.projection_targets({
        "event_type": resident_conversation_stream.MESSAGE_APPENDED, "resident_id": MEMBER,
        "payload": {"message": {"role": "agent", "reply_to_message_id": "msg_00000000000000e4"}},
    }) == [facade_id]


def test_facade_work_starts_when_a_member_runs_its_relayed_message(facade, monkeypatch):
    """成员开始处理转交消息时门面产生 work_started

    门面自己不执行；成员运行转交消息时，观察门面的 work_started 产生并带成员 ID。

    :module: lineage.service
    :design: decision_44cf587c1721
    """
    from management_portal.society_service import lineage_event_reconciliation as reconciliation

    facade_id = _relayed(facade, monkeypatch)
    repository = resident_conversation_stream.get_repository()
    original = type(repository).input_states_by_id
    states = {"state": "queued"}

    def input_states_by_id(self, requests):
        found = original(self, requests)
        found.update({(rid, mid): {"message_id": mid, "state": states["state"],
                                  "updated_at": "2026-10-02T08:00:00Z"}
                      for rid, mid in requests if rid == MEMBER})
        return found

    monkeypatch.setattr(type(repository), "input_states_by_id", input_states_by_id)
    assert family_facade.relay_activity(facade_id) == {"active": False}
    states["state"] = "running"
    assert family_facade.relay_activity(facade_id) == {
        "active": True, "message_id": "msg_00000000000000f4", "started_at": "2026-10-02T08:00:00Z",
        "status": "running", "member_resident_id": MEMBER,
    }
    assert family_facade.relay_activity(MEMBER) is None

    snapshot = reconciliation._with_facade_activity(
        {"residents": {facade_id: {"active": False, "queue_count": 0}}},
        [{"event_type": "resident.work_started", "subject_resident_id": facade_id}],
    )
    assert snapshot["residents"][facade_id]["active"] is True
    assert snapshot["residents"][facade_id]["queue_count"] == 0


def test_an_inner_facade_relays_a_message_that_an_outer_facade_relayed_to_it(facade):
    """内层门面能转交外层门面转来的消息

    嵌套 Family 中内层门面消息带外层转交标注；转交后关闭门面消息时保留该标注，不再因元数据变化而失败。

    Nested Family: the inner facade's message carries the outer relay mark.

    Closing it used to rewrite the stored message without that business_ref,
    which the store refuses as changed metadata, so the inner Router's
    delivery to its member failed after the member had already received it.

    :design: decision_44cf587c1721
    """

    resident_id = facade["resident_id"]
    outer_mark = "resident_dddddddddddd/message/msg_00000000000000a1"
    backend.deliver_resident_message_content(resident_id, "CASE-A", "前端登录页的按钮配色怎么定？",
                                             message_id="msg_00000000000000f9", business_ref=outer_mark)
    repository = resident_conversation_stream.get_repository()
    assert repository.message_business_ref(resident_id, "msg_00000000000000f9") == outer_mark

    source = family_facade.validate_relay(family_facade.relay_mark(resident_id, "msg_00000000000000f9"), "CASE-A")
    family_facade.complete_relay(source, MEMBER, "msg_00000000000000e9")

    assert repository.input_state(resident_id, "msg_00000000000000f9")["state"] == "completed"
    assert repository.message(resident_id, "msg_00000000000000f9")["activated_at"]
    assert repository.message_business_ref(resident_id, "msg_00000000000000f9") == outer_mark


def test_a_nested_family_follows_the_relay_chain_to_the_member_doing_the_work(monkeypatch):
    """嵌套 Family 沿转交链找到实际处理的成员

    外层门面显示最终成员、经过的内层门面和成员回复；成员结束时内外层门面都产生结束事件。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service import lineage_program_terminal_reconciliation as terminal
    from management_portal.society_service import resident_workspace_domain

    outer, inner = "resident_dddddddddddd", "resident_eeeeeeeeeeee"
    links = {outer: {"msg_00000000000000a1": {"resident_id": inner, "message_id": "msg_00000000000000b1"}},
             inner: {"msg_00000000000000b1": {"resident_id": MEMBER, "message_id": "msg_00000000000000c1"}}}
    refs = {(MEMBER, "msg_00000000000000c1"): f"{inner}/message/msg_00000000000000b1",
            (inner, "msg_00000000000000b1"): f"{outer}/message/msg_00000000000000a1"}
    titles = {outer: "外层", inner: "内层 Family", MEMBER: "前端"}

    class Repository:
        def message_business_ref(self, resident_id, message_id):
            return refs.get((resident_id, message_id), "")

        def reply_rounds(self, requests, *, limit=20):
            assert requests == [(MEMBER, "msg_00000000000000c1")]
            return {requests[0]: {
                "input_state": {"state": "completed", "reply_message_ids": ["m_reply"]},
                "replies": [{"message_id": "m_reply", "content_parts": [{"type": "text", "text": "用品牌主色"}]}],
            }}

    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: Repository())
    monkeypatch.setattr(family_facade.resident_mailbox, "_groups_of", lambda *_: {})
    monkeypatch.setattr(resident_workspace_domain, "_find_resident",
                        lambda resident_id: {"resident_id": resident_id, "title": titles.get(resident_id, "")})
    monkeypatch.setattr(family_facade, "is_facade", lambda record: (record or {}).get("resident_id") in links)
    monkeypatch.setattr(family_facade, "relay_links", lambda record: links.get(record["resident_id"], {}))
    monkeypatch.setattr(family_facade, "facade_configuration", lambda record: {"family_id": "fbi_outer"})

    assert family_facade.relay_sources_of(MEMBER, "msg_00000000000000c1") == [(inner, "msg_00000000000000b1"), (outer, "msg_00000000000000a1")]
    assert family_facade.relay_holder(inner, "msg_00000000000000b1") == ([inner], (MEMBER, "msg_00000000000000c1"))
    assert family_facade.relay_holder(MEMBER, "msg_00000000000000c1") == ([], (MEMBER, "msg_00000000000000c1"))
    subjects = terminal._terminal_subjects(MEMBER, "msg_00000000000000c1")
    assert subjects[inner][0] == "msg_00000000000000b1" and subjects[outer][0] == "msg_00000000000000a1"
    assert subjects[outer][1] == {"member_resident_id": MEMBER, "member_message_id": "msg_00000000000000c1"}
    request, reply = family_facade.project_messages({"resident_id": outer}, [
        {"message_id": "msg_00000000000000a1", "role": "user", "created_at": "2026-10-03T10:00:00Z"},
    ])
    relay = request["relay"]
    assert (relay["resident_id"], relay["title"], relay["state"]) == (MEMBER, "前端", "completed")
    assert relay["via"] == [{"resident_id": inner, "title": "内层 Family"}]
    assert reply["author_id"] == outer and reply["content_parts"][0]["text"] == "用品牌主色"
    assert reply["family_reply"]["title"] == "前端"
    assert reply["family_reply"]["via"] == [{"resident_id": inner, "title": "内层 Family"}]
    monkeypatch.setattr(Repository, "reply_rounds", lambda self, requests, **kwargs: {
        key: {"input_state": {"state": "cancelled"}, "replies": [], "withdrawn": True} for key in requests
    })
    withdrawn, = family_facade.project_messages({"resident_id": outer}, [
        {"message_id": "msg_00000000000000a1", "role": "user", "content_parts": [{"type": "text", "text": "已撤回"}]},
    ])
    assert withdrawn["withdrawn"] is True and withdrawn["content_parts"] == []
    assert withdrawn["relay"]["via"] == [{"resident_id": inner, "title": "内层 Family"}]


def test_facade_batches_only_the_page_and_reuses_nested_member_lookups(monkeypatch):
    """Family 当前页批量读取与嵌套成员复用

    当前页的转交请求统一读取；页面外历史不触发逐轮查询，同一嵌套门面与成员仅加载一次。

    :module: platform.core
    :design: decision_44cf587c1721
    :design-point:
    """
    from collections import Counter
    from types import SimpleNamespace
    from management_portal.society_service import resident_workspace_domain

    outer, inner = "resident_dddddddddddd", "resident_eeeeeeeeeeee"
    links = {
        outer: {f"outer_{i}": {"resident_id": inner, "message_id": f"inner_{i}"} for i in range(100)},
        inner: {f"inner_{i}": {"resident_id": MEMBER, "message_id": f"input_{i}"} for i in range(100)},
    }
    records, relay_reads, rounds = Counter(), Counter(), []

    def record(resident_id):
        records[resident_id] += 1
        return {"resident_id": resident_id, "title": resident_id}

    def relays(record):
        resident_id = record["resident_id"]
        relay_reads[resident_id] += 1
        return links.get(resident_id, {})

    def replies(requests, *, limit):
        rounds.append(requests)
        return {key: {"input_state": {"state": "completed"}, "replies": []} for key in requests}

    monkeypatch.setattr(resident_workspace_domain, "_find_resident", record)
    monkeypatch.setattr(family_facade, "is_facade", lambda record: record.get("resident_id") in links)
    monkeypatch.setattr(family_facade, "relay_links", relays)
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(reply_rounds=replies))
    monkeypatch.setattr(family_facade.resident_mailbox, "_groups_of", lambda *_: {})
    messages = [{"message_id": f"outer_{i}", "role": "user"} for i in range(90, 100)]
    shown = family_facade.project_messages({"resident_id": outer}, messages)
    assert rounds == [[(MEMBER, f"input_{i}") for i in range(90, 100)]]
    assert records == {inner: 1, MEMBER: 1}
    assert relay_reads == {outer: 1, inner: 1}
    assert [item["relay"]["message_id"] for item in shown] == [f"input_{i}" for i in range(90, 100)]
    assert all(item["relay"]["via"] == [{"resident_id": inner, "title": inner}] for item in shown)


def test_facade_outcomes_read_each_resident_on_the_relay_path_once(monkeypatch):
    """门面请求结果只读取一次转发路径上的每个 Resident

    计算门面多条请求的成员结果时，嵌套门面与成员的记录在一次读取中各只读一次，并取最新完成的请求作为门面结果。

    :module: platform.core
    :aspects: performance
    """
    from collections import Counter
    from types import SimpleNamespace
    from management_portal.society_service import resident_workspace_domain

    outer, inner = "resident_dddddddddddd", "resident_eeeeeeeeeeee"
    links = {
        outer: {f"outer_{i}": {"resident_id": inner, "message_id": f"inner_{i}"} for i in range(20)},
        inner: {f"inner_{i}": {"resident_id": MEMBER, "message_id": f"input_{i}"} for i in range(20)},
    }
    records, asked = Counter(), []

    def record(resident_id):
        records[resident_id] += 1
        return {"resident_id": resident_id}

    def latest(requests):
        asked.append(requests)
        return {MEMBER: {"message_id": "input_19", "state": "completed", "updated_at": "2026-10-16T00:00:00Z"}}

    monkeypatch.setattr(resident_workspace_domain, "_find_resident", record)
    monkeypatch.setattr(family_facade, "is_facade", lambda record: record.get("resident_id") in links)
    monkeypatch.setattr(family_facade, "relay_links", lambda record: links.get(record["resident_id"], {}))
    monkeypatch.setattr(family_facade, "get_lineage_data_store", lambda: SimpleNamespace(
        family_relays=SimpleNamespace(by_keys=lambda keys: {outer: {"links": links[outer]}})))
    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: SimpleNamespace(
        latest_notifiable_input_states_for_messages=latest))
    outcomes = family_facade.relayed_outcomes({outer: [f"outer_{i}" for i in range(20)]})
    assert outcomes[outer]["message_id"] == "outer_19"
    assert asked == [{MEMBER: [f"input_{i}" for i in range(20)]}]
    assert records == {inner: 1, MEMBER: 1}


def test_the_facade_conversation_reads_like_a_resident_answering_its_requests(monkeypatch):
    """门面对话按请求分组投影成员的中间输出、回复和每条请求的进度

    每条请求后跟着成员对它写出的所有消息（中间输出和最终回复），署名门面；每条请求带自己的进度：Router 选择中、在成员队列里前面还有几条、成员运行中的活动。实时活动属于各条请求，门面自身不再显示活动；门面的工作状态仍是成员为它做的工作。

    Requests keep their sender; each is followed by its member replies, shown as the facade's replies.

    Each request shows its own progress: the Router choosing a member, its
    place in the member's queue, or the member's live work with the outputs
    written so far.

    :design: decision_44cf587c1721
    """

    from management_portal.society_service import resident_workspace_domain

    facade_id, first, second = "resident_ffffffffffff", "resident_111111111111", "resident_222222222222"
    links = {
        "msg_0000000000000f01": {"resident_id": first, "message_id": "msg_0000000000000e01", "relayed_at": "2026-10-03T10:00:01Z"},
        "msg_0000000000000f02": {"resident_id": second, "message_id": "msg_0000000000000e02", "relayed_at": "2026-10-03T10:00:02Z"},
        "msg_0000000000000f04": {"resident_id": second, "message_id": "msg_0000000000000e04", "relayed_at": "2026-10-03T10:00:16Z"},
    }

    def output(resident_id, message_id, request_id, at, text):
        return {"message_id": message_id, "role": "agent", "created_at": at, "reply_to_message_id": request_id,
                "origin": {"sender": {"kind": "resident", "id": resident_id}}, "author_id": resident_id,
                "content_parts": [{"type": "text", "text": text}]}

    replies = {
        (first, "msg_0000000000000a00"): output(first, "msg_0000000000000a00", "msg_0000000000000e01", "2026-10-03T10:00:10Z", "先读了权限表"),
        (first, "msg_0000000000000a01"): output(first, "msg_0000000000000a01", "msg_0000000000000e01", "2026-10-03T10:00:20.5Z", "第一项的回答"),
        (second, "msg_0000000000000a02"): output(second, "msg_0000000000000a02", "msg_0000000000000e02", "2026-10-03T10:00:21Z", "正在改页面"),
    }
    input_states = {
        (first, "msg_0000000000000e01"): ("completed", ["msg_0000000000000a01"]),
        (second, "msg_0000000000000e02"): ("running", []),
        (second, "msg_0000000000000e04"): ("queued", []),
    }
    reads = []

    class Repository:
        def message_business_ref(self, resident_id, message_id):
            return ""

        def input_state(self, resident_id, message_id):
            state, ids = input_states[(resident_id, message_id)]
            return {"state": state, "reply_message_ids": ids}

        def input_states_by_id(self, requests):
            return {key: self.input_state(*key) for key in requests}

        def reply_rounds(self, requests, *, limit=20):
            reads.append(list(requests))
            return {key: {"input_state": self.input_state(*key), "replies": [
                item for (owner, _), item in replies.items()
                if owner == key[0] and item["reply_to_message_id"] == key[1]
            ][-limit:]} for key in requests}

        def message(self, resident_id, message_id):
            return replies.get((resident_id, message_id))

        def current_state(self, resident_id):
            active = next((mid for (rid, mid), (state, _) in input_states.items() if rid == resident_id and state == "running"), "")
            return {"work_state": "running" if active else "idle", "active_message_id": active, "queued_count": 0, "intake_state": "open"}

        def activities(self, resident_id):
            return [{"activity_id": "act_1", "resident_id": resident_id, "message_id": "msg_0000000000000e02", "kind": "command",
                     "status": "running", "title": "读取文件", "content": "", "started_at": "", "updated_at": ""},
                    {"activity_id": "act_0", "resident_id": resident_id, "message_id": "msg_0000000000000e99", "kind": "command",
                     "status": "completed", "title": "旧活动", "content": "", "started_at": "", "updated_at": ""}]

        def current_projection(self, resident_id):
            # Another sender's message waits before the relayed one.
            queued = [mid for (rid, mid), (state, _) in input_states.items() if rid == resident_id and state == "queued"]
            return {"state": self.current_state(resident_id), "activities": self.activities(resident_id),
                    "queue": {"resident_id": resident_id, "items": [{"message_id": "msg_0000000000000e03"}]
                              + [{"message_id": mid} for mid in queued]}}

    monkeypatch.setattr(resident_conversation_stream, "get_repository", lambda: Repository())
    monkeypatch.setattr(family_facade.resident_mailbox, "_groups_of", lambda *_: {})
    monkeypatch.setattr(resident_workspace_domain, "_find_resident", lambda resident_id: {
        "resident_id": resident_id, "title": {first: "权限内核", second: "admin 页面"}.get(resident_id, "门面"),
    })
    monkeypatch.setattr(family_facade, "is_facade", lambda record: (record or {}).get("resident_id") == facade_id)
    monkeypatch.setattr(family_facade, "relay_links", lambda record: links if record.get("resident_id") == facade_id else {})
    monkeypatch.setattr(family_facade, "facade_configuration", lambda record: {"family_id": "fbi_family0001"})
    monkeypatch.setattr(family_facade, "family_members", lambda _: [first, second])
    # This fixture isolates reply grouping; real role health is covered through
    # the conversation API in test_resident_quota_gate.
    monkeypatch.setattr(family_facade, "chat_presentation", lambda _: {})

    def request(message_id, at, text):
        return {"message_id": message_id, "role": "user", "created_at": at, "author_id": "CASE-A",
                "origin": {"sender": {"kind": "user", "id": "CASE-A"}}, "content_parts": [{"type": "text", "text": text}]}

    snapshot = family_facade.project_snapshot({"resident_id": facade_id}, {
        "messages": [
            request("msg_0000000000000f01", "2026-10-03T10:00:00Z", "第一项"),
            request("msg_0000000000000f02", "2026-10-03T10:00:00Z", "第二项"),
            request("msg_0000000000000f03", "2026-10-03T10:00:15Z", "第三项"),
            request("msg_0000000000000f04", "2026-10-03T10:00:15Z", "第四项"),
        ],
        "input_states": [{"message_id": "msg_0000000000000f03", "state": "queued"}],
        "state": {"work_state": "idle", "active_message_id": "", "queued_count": 1, "intake_state": "open"},
        "activities": [],
    })
    messages = snapshot["messages"]
    assert [item["message_id"] for item in messages] == [
        "msg_0000000000000f01", "msg_0000000000000a00", "msg_0000000000000a01",
        "msg_0000000000000f02", "msg_0000000000000a02", "msg_0000000000000f03", "msg_0000000000000f04",
    ]
    assert messages[0]["origin"]["sender"] == {"kind": "user", "id": "CASE-A"}
    # Intermediate outputs show under their request, before the final reply, as the facade's replies.
    intermediate, answer = messages[1], messages[2]
    assert intermediate["content_parts"][0]["text"] == "先读了权限表"
    assert intermediate["family_reply"]["request_message_id"] == "msg_0000000000000f01"
    assert answer["author_id"] == facade_id and answer["origin"]["sender"] == {"kind": "resident", "id": facade_id}
    assert answer["reply_to_message_id"] == "msg_0000000000000f01"
    assert answer["family_reply"] == {
        "resident_id": first, "title": "权限内核", "message_id": "msg_0000000000000a01", "via": [],
        "request_message_id": "msg_0000000000000f01",
    }
    assert replies[(first, "msg_0000000000000a01")]["author_id"] == first
    # The selected rounds are read together, including queued/running ones.
    assert reads == [[(first, "msg_0000000000000e01"), (second, "msg_0000000000000e02"),
                      (second, "msg_0000000000000e04")]]
    # Each request carries its own progress.
    assert messages[0]["relay"]["state"] == "completed" and "activities" not in messages[0]["relay"]
    running = messages[3]["relay"]
    assert (running["state"], running["work_state"], running["title"]) == ("running", "running", "admin 页面")
    assert [(item["title"], item["resident_id"]) for item in running["activities"]] == [("读取文件", second)]
    assert messages[4]["content_parts"][0]["text"] == "正在改页面"
    assert messages[5]["relay"] == {"state": family_facade.ROUTING_STATE}
    # The fourth waits behind the member's running turn and another sender's message.
    assert (messages[6]["relay"]["state"], messages[6]["relay"]["ahead"]) == ("queued", 2)
    # The facade is working while its member runs a relayed request; the live
    # activity is the request's own, so the facade shows none of its own.
    assert snapshot["state"]["work_state"] == "running"
    assert snapshot["state"]["active_message_id"] == "msg_0000000000000f02"
    assert snapshot["state"]["family_member"] == {"resident_id": second, "title": "admin 页面"}
    assert snapshot["state"]["queued_count"] == 1
    assert snapshot["activities"] == []
    # A member message shown in the facade is found by its ID, intermediate outputs included.
    shown = family_facade.projected_reply({"resident_id": facade_id}, "msg_0000000000000a02")
    assert (shown["author_id"], shown["family_reply"]["request_message_id"]) == (facade_id, "msg_0000000000000f02")
    assert family_facade.projected_reply({"resident_id": facade_id}, "msg_0000000000000e04") is None
    # Once the member is idle and only waiting relays remain, the facade waits for the latest.
    input_states[(second, "msg_0000000000000e02")] = ("queued", [])
    waiting = family_facade.project_snapshot({"resident_id": facade_id}, {
        "messages": [], "input_states": [], "state": {"work_state": "idle"}, "activities": [],
    })
    assert (waiting["state"]["work_state"], waiting["state"]["work_phase"]) == ("queued", family_facade.WAITING_PHASE)
    assert waiting["state"]["active_message_id"] == "msg_0000000000000f04"


def test_facade_viewers_hear_when_a_member_works_on_a_relayed_message(monkeypatch):
    """成员处理转交消息的变化通知门面的查看者

    对话事件中继在成员事件涉及转交消息时，额外发布门面的 projection 变化；查找失败不影响原事件发布。

    :design: decision_44cf587c1721
    """
    from management_portal.society_service.resident_conversation_event_relay import ResidentConversationEventRelay

    published = []

    class Publisher:
        def publish(self, subject, payload):
            published.append((subject, payload))

    event = {"schema": resident_conversation_stream.RESIDENT_CONVERSATION_SCHEMA,
             "event_type": resident_conversation_stream.INPUT_STATE_CHANGED, "resident_id": MEMBER, "cursor": 7,
             "occurred_at": "2026-10-03T10:00:00Z", "payload": {"input_state": {"message_id": "msg_0000000000000e01"}}}
    monkeypatch.setattr(family_facade, "projection_targets", lambda item: ["resident_ffffffffffff"] if item is event else [])
    relay = ResidentConversationEventRelay(object(), Publisher())
    relay._publish(event)

    assert [subject for subject, _ in published] == [
        f"portal.resident.changed.{MEMBER}.conversation",
        "portal.resident.changed.resident_ffffffffffff.projection",
    ]
    assert published[1][1] == {"kind": "facade_projection", "member_resident_id": MEMBER, "cursor": 7}

    def broken(_event):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(family_facade, "projection_targets", broken)
    published.clear()
    relay._publish(event)
    assert len(published) == 1


def test_a_message_the_family_sent_is_a_round_of_the_member_that_wrote_it(facade):
    """Family 主动发给发送者的消息算作写它的成员的一轮

    ListRecentRounds 给出 sender_id 时，门面对话中 recipients 含这位发送者的 agent 消息也列为一轮：kind 为 sent，answered_by 是写这条消息的成员（来源的 initiated_by），request 为空，state 为 sent，reply 是消息开头；发给别人的消息不列出；请求轮次的 kind 为 request。用户回答成员经门面提出的问题时，Router 因此交回这位成员。

    A user's answer to what a member asked through the facade goes back to that member.

    :module: lineage.hatg
    :importance: P1
    :design: decision_2fe6fd5367fb, decision_44cf587c1721
    """

    from management_common.principal import message_origin
    from management_portal.execution.agent_data_store import utc_now
    from management_portal.society_service import resident_lineage, resident_protocol
    from management_portal.society_service.state_store import get_lineage_data_store

    facade_id = facade["resident_id"]
    _register_test_member(facade, '登录成员')
    repository = resident_conversation_stream.get_repository()

    def sent(message_id, user, text):
        now = utc_now()
        repository.append_message(facade_id, {
            "message_id": message_id, "role": "agent", "author_id": facade_id, "created_at": now,
            "origin": message_origin(facade_id, initiated_by=MEMBER), "recipients": [{"kind": "user", "id": user}],
            "content_parts": [{"type": "text", "text": text}],
        }, activated_at=now)

    backend.deliver_resident_message_content(facade_id, "CASE-A", "登录页 500 了", message_id="msg_00000000000000f1")
    sent("msg_00000000000000d1", "CASE-A", "修复要重启服务，现在可以吗？")
    sent("msg_00000000000000d2", "sample_admin", "注册页的问题已记下。")
    backend.deliver_resident_message_content(facade_id, "CASE-A", "可以，重启吧", message_id="msg_00000000000000f2")

    rounds = resident_protocol.recent_rounds(
        facade_id, "CASE-A", sender_id="CASE-A", before_message_id="msg_00000000000000f2",
    )["rounds"]

    # What went to someone else is not one of this sender's rounds.
    assert [(item["kind"], item["message_id"]) for item in rounds] == [
        ("request", "msg_00000000000000f1"), ("sent", "msg_00000000000000d1"),
    ]
    question = rounds[1]
    assert question["answered_by"] == {"resident_id": MEMBER, "title": "登录成员"}
    assert (question["request"], question["state"], question["reply"]) == ("", "sent", "修复要重启服务，现在可以吗？")
    assert question["sender"] == {"kind": "resident", "id": facade_id}


def test_recent_rounds_name_the_member_that_answered_each_request(facade):
    """最近几轮往来写明每轮由谁答复

    ListRecentRounds 按发送者列出门面对话中此前的轮次，从旧到新；每轮写明请求开头、转交的成员、成员的结果和回复开头，超过 400 字的文本截短并以“…”结尾；尚未转交的请求没有答复者；其他发送者的请求不列出；普通 Resident 的答复者是它自己。

    The Family Router reads a sender's last rounds to keep a continued topic with the member that answered it.

    :module: lineage.hatg
    :importance: P1
    :design: decision_2fe6fd5367fb, decision_44cf587c1721
    """

    from management_portal.society_service import resident_lineage, resident_protocol
    from management_portal.society_service.state_store import get_lineage_data_store

    facade_id = facade["resident_id"]
    _register_test_member(facade, '登录成员')
    repository = resident_conversation_stream.get_repository()
    long_answer = "已修复：会话表缺索引。" + "细节" * 300

    def ask(facade_message_id, text, member_message_id="", answer=""):
        backend.deliver_resident_message_content(facade_id, "CASE-A", text, message_id=facade_message_id)
        if not member_message_id:
            return
        repository.append_message(MEMBER, {
            "message_id": member_message_id, "role": "user", "author_id": "CASE-A", "created_at": "2026-10-05T07:00:00Z",
            "content_parts": [{"type": "text", "text": text}],
            "business_ref": family_facade.relay_mark(facade_id, facade_message_id),
        })
        family_facade.complete_relay((facade_id, facade_message_id), MEMBER, member_message_id)
        if answer:
            reply_id = "msg_a" + member_message_id[5:]
            repository.append_message(MEMBER, {
                "message_id": reply_id, "role": "agent", "reply_to_message_id": member_message_id,
                "created_at": "2026-10-05T07:00:30Z", "content_parts": [{"type": "text", "text": answer}],
            })
            repository.set_input_state(MEMBER, member_message_id, "completed", updated_at="2026-10-05T07:00:30Z")

    ask("msg_00000000000000f1", "登录页 500 了", "msg_00000000000000e1", long_answer)
    ask("msg_00000000000000f2", "上一轮的修复上线了吗？", "msg_00000000000000e2")
    ask("msg_00000000000000f3", "顺便看下注册页")  # the message being routed now

    rounds = resident_protocol.recent_rounds(
        facade_id, "CASE-A", sender_id="CASE-A", before_message_id="msg_00000000000000f3", limit=3,
    )["rounds"]

    assert [item["message_id"] for item in rounds] == ["msg_00000000000000f1", "msg_00000000000000f2"]
    first, second = rounds
    assert first["sender"] == {"kind": "user", "id": "CASE-A"}
    assert first["request"] == "登录页 500 了"
    assert first["answered_by"] == {"resident_id": MEMBER, "title": "登录成员"}
    assert first["state"] == "completed"
    # Texts are cut short: the Router needs the topic, not the whole answer.
    assert first["reply"] == long_answer[:resident_protocol.ROUND_TEXT_CHARS] + "…"
    assert second["answered_by"]["resident_id"] == MEMBER and second["reply"] == ""
    newest = resident_protocol.recent_rounds(facade_id, "CASE-A", sender_id="CASE-A", limit=1)["rounds"]
    assert [(item["message_id"], item["answered_by"]) for item in newest] == [("msg_00000000000000f3", {})]
    assert resident_protocol.recent_rounds(facade_id, "CASE-A", sender_id="resident_eeeeeeeeeeee")["rounds"] == []
    # On any other Resident the Resident itself answered.
    own = resident_protocol.recent_rounds(MEMBER, "CASE-A", sender_id="CASE-A")["rounds"]
    assert [(item["message_id"], item["answered_by"]["resident_id"]) for item in own] == [
        ("msg_00000000000000e1", MEMBER), ("msg_00000000000000e2", MEMBER),
    ]
    assert own[0]["reply"].startswith("已修复") and own[0]["state"] == "completed"


def test_recent_rounds_on_a_facade_add_the_senders_direct_conversations_with_members(facade, monkeypatch):
    """门面上的 ListRecentRounds 带出发送者在门面之外直接和成员的最近对话

    Family 门面按发送者读取最近几轮时，另给出这位发送者直接与各成员的最新往来（不含门面转交的副本），供 Router 判断“我们的讨论”指哪个成员；非门面或不指定发送者时不给出。

    CASE-A 10-12: "按我们的讨论" meant the sender's own Chat with a member, which the facade rounds did not show.

    :module: lineage.hatg
    :importance: P1
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import resident_protocol

    facade_id = facade["resident_id"]
    _register_test_member(facade, '登录成员')
    monkeypatch.setattr(family_facade, "family_members", lambda _: [MEMBER])
    repository = resident_conversation_stream.get_repository()

    def direct():
        return resident_protocol.recent_rounds(facade_id, "CASE-A", sender_id="CASE-A")["direct"]

    # A copy the facade relayed belongs to the facade's rounds.
    repository.append_message(MEMBER, {
        "message_id": "msg_00000000000000e1", "role": "user", "author_id": "CASE-A", "created_at": "2026-10-12T11:00:00Z",
        "content_parts": [{"type": "text", "text": "转交来的请求"}],
        "business_ref": family_facade.relay_mark(facade_id, "msg_00000000000000f1"),
    })
    assert direct() == []
    repository.append_message(MEMBER, {
        "message_id": "msg_00000000000000e2", "role": "user", "author_id": "CASE-A", "created_at": "2026-10-12T12:06:00Z",
        "content_parts": [{"type": "text", "text": "这两个 ISA 分到 C 集合"}],
    })
    [latest] = direct()
    assert {key: latest[key] for key in ("kind", "resident_id", "title", "message_id", "text")} == {
        "kind": "request", "resident_id": MEMBER, "title": "登录成员", "message_id": "msg_00000000000000e2",
        "text": "这两个 ISA 分到 C 集合",
    }
    assert latest["created_at"]
    # Only a facade's rounds for one sender carry them.
    assert "direct" not in resident_protocol.recent_rounds(facade_id, "CASE-A")
    assert "direct" not in resident_protocol.recent_rounds(MEMBER, "CASE-A", sender_id="CASE-A")


def test_a_member_turn_shows_its_outputs_so_far_under_the_facade_request(facade):
    """成员运行中写出的中间输出随写随显示在门面请求下

    成员处理转交的请求时，回复该请求的每条 Agent 消息都投影到门面请求之后；结束后只读到本轮最后一条回复为止，之后写的不算本轮；门面里显示的中间输出也能按 ID 查到，其他请求的输出不会混进来（CASE-A 10-06）。

    CASE-A 10-06: each facade request shows the member's intermediate outputs as they are written.

    :aspects: functional, behavior
    :design: decision_44cf587c1721
    """

    from management_portal.society_service import resident_lineage
    from management_portal.society_service.state_store import get_lineage_data_store

    facade_id = facade["resident_id"]
    _register_test_member(facade, '登录成员')
    repository = resident_conversation_stream.get_repository()
    backend.deliver_resident_message_content(facade_id, "CASE-A", "登录页 500 了", message_id="msg_00000000000000f1")
    repository.append_message(MEMBER, {
        "message_id": "msg_00000000000000e1", "role": "user", "author_id": "CASE-A", "created_at": "2026-10-06T07:00:00Z",
        "content_parts": [{"type": "text", "text": "登录页 500 了"}],
        "business_ref": family_facade.relay_mark(facade_id, "msg_00000000000000f1"),
    })
    family_facade.complete_relay((facade_id, "msg_00000000000000f1"), MEMBER, "msg_00000000000000e1")

    def output(message_id, text, reply_to="msg_00000000000000e1"):
        repository.append_message(MEMBER, {
            "message_id": message_id, "role": "agent", "reply_to_message_id": reply_to,
            "created_at": "2026-10-06T07:00:30Z", "content_parts": [{"type": "text", "text": text}],
        })

    def shown():
        request = {"message_id": "msg_00000000000000f1", "role": "user", "content_parts": []}
        messages = family_facade.project_messages(facade, [request], input_states={})
        return messages[0]["relay"], [item["content_parts"][0]["text"] for item in messages[1:]]

    repository.set_input_state(MEMBER, "msg_00000000000000e1", "running", updated_at="2026-10-06T07:00:01Z")
    output("msg_00000000000000a1", "先查日志")
    output("msg_00000000000000b1", "别的请求的输出", reply_to="msg_00000000000000e9")
    relay, texts = shown()
    assert relay["state"] == "running" and texts == ["先查日志"]

    output("msg_00000000000000a2", "已修复：会话表缺索引")
    repository.set_input_state(MEMBER, "msg_00000000000000e1", "completed",
                               updated_at="2026-10-06T07:01:00Z")
    relay, texts = shown()
    assert relay["state"] == "completed" and texts == ["先查日志", "已修复：会话表缺索引"]
    # The final reply is the trace's last message answering the request.
    assert repository.input_state(MEMBER, "msg_00000000000000e1")["reply_message_ids"] == ["msg_00000000000000a2"]
    assert [item["message_id"] for item in repository.replies(MEMBER, "msg_00000000000000e1", limit=1)] == [
        "msg_00000000000000a2",
    ]
    assert repository.replies(MEMBER, "msg_00000000000000ff") == []
    # An intermediate output shown in the facade is found by its ID, as the facade shows it.
    found = family_facade.projected_reply(facade, "msg_00000000000000a1")
    assert (found["author_id"], found["family_reply"]["request_message_id"]) == (facade_id, "msg_00000000000000f1")
    assert family_facade.projected_reply(facade, "msg_00000000000000b1") is None
    # Clearing the member's Chat does not remove the Family's completed answer.
    repository.agent_data.clear_chat(MEMBER, actor_ref="CASE-A")
    assert repository.message(MEMBER, "msg_00000000000000a2") is None
    relay, texts = shown()
    assert relay["state"] == "completed" and texts == ["已修复：会话表缺索引"]
    rounds = repository.reply_rounds([(MEMBER, "msg_00000000000000e1"), (MEMBER, "msg_missing")])
    assert rounds[(MEMBER, "msg_missing")] == {"input_state": {}, "replies": []}


def test_withdrawing_or_stopping_a_relayed_request_reaches_the_member_doing_the_work(monkeypatch):
    """撤回或停止门面请求作用于实际干活的成员消息

    对已转交的门面消息执行 CancelInput：成员消息还在等待时从成员队列撤回，正在执行时停止成员的工作；嵌套 Family 沿转交链找到实际干活的成员；成员照常按 owner 或发送者核对调用者。回执不带成员的状态、队列和游标，免得覆盖门面的投影；还没转交的请求从门面自己的队列撤回。

    The sender stops or withdraws one facade request; a nested Family's relays are followed.

    :module: resident.session
    :design: decision_44cf587c1721, decision_2a3ff353165c
    """

    from types import SimpleNamespace

    from management_portal.society_service.isa import execution
    from tests.cases.test_isa_g2_audit_fixes import invocation

    outer, inner = "resident_dddddddddddd", "resident_eeeeeeeeeeee"
    links = {outer: {"msg_00000000000000a1": {"resident_id": inner, "message_id": "msg_00000000000000b1"}},
             inner: {"msg_00000000000000b1": {"resident_id": MEMBER, "message_id": "msg_00000000000000c1"}}}
    calls, states = [], {}

    def cancel(lineage, payload, actor):
        calls.append(("cancel", payload, actor))
        if states.get(payload["message_id"]) == "running":
            return {"input_state": {"state": "running"}, "cancellation_requested": True}
        states[payload["message_id"]] = "cancelled"
        return {"cancelled": True}

    monkeypatch.setattr("management_portal.society_service.resident_workspace_domain._find_resident",
                        lambda resident_id: {"resident_id": resident_id})
    monkeypatch.setattr(family_facade, "is_facade", lambda record: (record or {}).get("resident_id") in links)
    monkeypatch.setattr(family_facade, "relay_links", lambda record: links.get(record["resident_id"], {}))
    monkeypatch.setattr(execution, "lineage_of_resident", lambda _: "lineage_000000000001")
    monkeypatch.setattr(execution, "domain", lambda: SimpleNamespace(
        cancel_lineage_resident_execution=cancel,
    ))
    monkeypatch.setattr(execution, "protocol", lambda: SimpleNamespace(
        _stored_resident_input_state=lambda resident_id, message_id: {"message_id": message_id, "state": states.get(message_id, "queued")},
    ))

    withdrawn = execution.cancel_input(invocation("CancelInput", resident_id=outer, message_id="msg_00000000000000a1"))
    states["msg_00000000000000c1"] = "running"
    stopped = execution.cancel_input(invocation("CancelInput", resident_id=outer, message_id="msg_00000000000000a1"))

    member = {"resident_id": MEMBER, "message_id": "msg_00000000000000c1"}
    assert calls == [("cancel", member, "CASE-A"), ("cancel", member, "CASE-A")]
    assert (withdrawn["facade_message_id"], withdrawn["member_resident_id"]) == ("msg_00000000000000a1", MEMBER)
    # Receipts name the actual state owner and never overwrite the facade.
    assert withdrawn["resident_id"] == MEMBER
    assert withdrawn["message_id"] == "msg_00000000000000c1"
    assert withdrawn["input_state"] == {"message_id": "msg_00000000000000c1", "state": "cancelled"}
    assert stopped["member_message_id"] == "msg_00000000000000c1"
    assert stopped["cancellation_requested"] is True
    assert stopped["input_state"]["state"] == "running"
    # A request the Router has not relayed is withdrawn from the facade itself.
    execution.cancel_input(invocation("CancelInput", resident_id=outer, message_id="msg_00000000000000a9"))
    assert calls[-1] == ("cancel", {"resident_id": outer, "message_id": "msg_00000000000000a9"}, "CASE-A")


@pytest.mark.parametrize("members,state", [([], "routing"), ([MEMBER], "routing"),
                                           ([MEMBER, "resident_ffffffffffff"], "routing")])
def test_waiting_request_shows_member_availability(facade, monkeypatch, members, state):
    """门面待处理请求显示成员可用状态

    :module: platform.core"""
    monkeypatch.setattr(family_facade, "family_members", lambda _: members)
    message = {"message_id": "msg_waiting", "role": "user"}
    projected = family_facade.project_messages(facade, [message], input_states={"msg_waiting": "queued"})
    assert projected[0]["relay"]["state"] == state
    assert "resident_id" not in projected[0]["relay"]
