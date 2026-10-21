""":design: decision_b1ab45c4b3ec"""
from dataclasses import replace

import pytest

from management_portal.execution.agent_data_store import AgentDataStore, stable_segment_id
from management_portal.execution.agent_data_projection import AgentDataProjector
from management_portal.execution.message_projection import wrap_message, unwrap_message
from tests.cases.test_agent_data_projection import _turn_request, _runtime_session, _invocation, _deliver_captured_message


def message(identity, body):
    return {"message_id": identity, "role": "user", "author_id": "sample_admin",
            "created_at": "2026-10-08T01:00:00Z", "content_parts": [{"type": "text", "text": body}]}


def stored_final_message_id(store, execution_id):
    with store.database.read() as connection:
        return connection.execute(
            "SELECT final_message_id FROM executions WHERE execution_id=%s", (execution_id,),
        ).fetchone()["final_message_id"]


@pytest.mark.parametrize("raw_usage", [
    {"last": {"inputTokens": 1234, "outputTokens": 56, "reasoningOutputTokens": 12},
     "total": {"inputTokens": 999999}, "context": {"inputTokens": 999}},
    {"input_tokens": 1234, "output_tokens": 56, "reasoning_output_tokens": 12},
    {"input_tokens": 1234, "output_tokens": 56, "reasoning_output_tokens": 12,
     "api_id": "kimi_primary", "model": "configured-model", "private_provider_payload": "not a Chat field"},
])
def test_turn_footer_projects_execution_usage_only_on_final_answer(raw_usage):
    """执行用量仅显示在最终回复且可由分支继承

    :design-point:"""
    from tests.cases.live_trace_support import _turn
    from tests.cases.test_live_trace_ingress import _emit
    from management_portal.society_service.resident_conversation_stream import _message_from_storage
    store, projector, request, _ = _turn("codex")
    _emit(projector, request, {"id": "progress", "type": "agentMessage", "phase": "commentary", "text": "working"})
    final_segment_id = projector.complete(execution_id=request.execution_id, backend_session_id="thread-native",
                                          backend_route_id="one", turn_id="turn-footer", response="finished", usage=raw_usage)
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, request.agent_id,
                              stable_segment_id("provider-model", request.execution_id, "progress", "commentary"))
    _deliver_captured_message(store, request.agent_id, final_segment_id)
    messages = store.read_chat_messages(request.agent_id)
    agent_messages = [m for m in messages if m["role"] == "agent"]
    assert not agent_messages[0].get("usage")
    final = agent_messages[-1]
    assert _message_from_storage(final)["usage"] == {
        "input_tokens": 1234, "output_tokens": 56, "reasoning_output_tokens": 12, "total_tokens": 1290,
        **{key: raw_usage[key] for key in ("api_id", "model") if key in raw_usage},
    }
    assert "usage" not in store.segment(final["segment_id"])["metadata"]["message"]
    # A later read and a fork get the footer from the same execution record.
    store.fork_agent(source_agent_id=request.agent_id, target_agent_id="resident_usage_fork", actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(request.agent_id)))
    assert store.read_chat_messages("resident_usage_fork")[-1]["usage"] == final["usage"]


def test_turn_footer_shows_what_the_scheduler_counted_for_all_provider_turns():
    """本轮 token 统计显示全部 Provider 轮次的计入用量

    一次执行跨多个 Codex Provider 轮次时，最终回复和停止或失败通知的 token 统计取执行记录的 accounted；Harness 输入不显示；Worker 终态事实重放仍被接受。

    Codex reports only its last Provider turn; an Execution that ran several shows them all.

    :module: execution.service
    :importance: P1
    :design: decision_f1331d57be89
    :design-point:
    """
    from tests.cases.live_trace_support import _turn
    store, projector, request, _ = _turn("codex")
    last_provider_turn = {"last": {"inputTokens": 1234, "outputTokens": 56}, "context": {"inputTokens": 999}}
    final_segment_id = projector.complete(execution_id=request.execution_id, backend_session_id="thread-native",
                                          backend_route_id="one", turn_id="turn-footer", response="finished", usage=last_provider_turn)
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, request.agent_id, final_segment_id)
    counted = {"input_tokens": 90_000_000, "cached_input_tokens": 88_000_000, "output_tokens": 260_000,
               "reasoning_output_tokens": 77_000, "total_tokens": 90_260_000}
    assert store.record_accounted_usage(request.execution_id, counted)
    assert not store.record_accounted_usage(request.execution_id, counted)
    assert store.read_chat_messages(request.agent_id)[-1]["usage"] == counted
    # The Worker's terminal fact stays what the Provider reported; replaying it is still accepted.
    store.finish_execution(request.execution_id, status="completed", usage=last_provider_turn,
                           finished_at=store.execution(request.execution_id)["finished_at"], provider_turn_id="turn-footer")


def test_final_message_pointer_keeps_partial_fork_correct():
    """最终消息指针使部分分支正确显示用量

    没有记下最终消息的终态 Execution（指针为 NULL）不在读取时补算，它的消息不显示用量。

    :design-point:"""
    from tests.cases.live_trace_support import _turn
    from tests.cases.test_live_trace_ingress import _emit

    store, projector, request, _ = _turn("codex")
    _emit(projector, request, {"id": "progress", "type": "agentMessage", "phase": "commentary", "text": "working"})
    source_ids = store.sequence_segment_ids(store.trace_sequence_id(request.agent_id))
    store.fork_agent(source_agent_id=request.agent_id, target_agent_id="resident_partial_usage_fork",
                     actor_ref="sample_admin", segment_ids=source_ids)
    final_segment_id = projector.complete(
        execution_id=request.execution_id, backend_session_id="thread-native",
        backend_route_id="one", turn_id="turn-footer", response="finished",
        usage={"input_tokens": 12, "output_tokens": 3},
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    # Deliver the captured messages in their production order.
    _deliver_captured_message(store, request.agent_id,
                              stable_segment_id("provider-model", request.execution_id, "progress", "commentary"))
    _deliver_captured_message(store, request.agent_id, final_segment_id)

    parent = store.read_chat_messages(request.agent_id)
    final = parent[-1]
    assert stored_final_message_id(store, request.execution_id) == final["message_id"]
    assert "usage" in final
    assert all("usage" not in item for item in store.read_chat_messages("resident_partial_usage_fork"))

    # Reads use the recorded pointer only.
    store.database.write(lambda connection: connection.execute(
        "UPDATE executions SET final_message_id=NULL WHERE execution_id=%s", (request.execution_id,),
    ))
    assert all("usage" not in item for item in store.read_chat_messages(request.agent_id))
    assert all("usage" not in item for item in store.read_chat_messages("resident_partial_usage_fork"))


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_an_execution_ended_before_it_started_records_that_it_has_no_reply(status):
    """开始前就结束的 Execution 也记下它没有最终回复（指针为空串，不是 NULL）"""
    store = AgentDataStore()
    store.ensure_agent("resident_ended_unstarted", actor_ref="sample_admin")
    for execution_id, queued in (("execution_unstarted_preparing", False), ("execution_unstarted_queued", True)):
        store.reserve_execution(execution_id=execution_id, agent_id="resident_ended_unstarted",
                                actor_ref="sample_admin", provider="codex")
        if queued:
            store.queue_execution(execution_id)
        assert store.finish_execution(execution_id, status=status) is True
        assert store.execution(execution_id)["started_at"] in (None, "")
        assert stored_final_message_id(store, execution_id) == ""


def test_late_terminal_notice_updates_final_message_pointer():
    """迟到终态通知更新最终消息指针并显示计入用量

    :design-point:"""
    from tests.cases.live_trace_support import _turn
    from tests.cases.test_live_trace_ingress import _emit

    store, projector, request, _ = _turn("codex")
    _emit(projector, request, {"id": "progress", "type": "agentMessage", "phase": "commentary", "text": "working"})
    store.finish_execution(request.execution_id, status="failed")
    notice = store.display_chat_message(request.agent_id, {
        "message_id": "msg_late_terminal_notice", "role": "system", "author_id": "portal",
        "created_at": "2026-10-12T00:00:00Z", "execution_id": request.execution_id,
        "content_parts": [{"type": "text", "text": "Execution failed"}],
    })
    assert stored_final_message_id(store, request.execution_id) == notice["message_id"]
    assert store.record_accounted_usage(request.execution_id, {"input_tokens": 17, "output_tokens": 2})
    shown = store.read_chat_messages(request.agent_id)
    assert shown[-1]["message_id"] == notice["message_id"]
    assert shown[-1]["usage"]["input_tokens"] == 17
    assert all("usage" not in item for item in shown[:-1])


def test_late_collected_reply_replaces_terminal_pointer():
    """迟到收集的回复替换终态指针并承接用量

    :design-point:"""
    from tests.cases.live_trace_support import _turn

    store, projector, request, _ = _turn("codex")
    final_segment_id = projector.complete(
        execution_id=request.execution_id, backend_session_id="thread-native",
        backend_route_id="one", turn_id="turn-footer", response="first",
        usage={"input_tokens": 9, "output_tokens": 1},
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, request.agent_id, final_segment_id)
    earlier = stored_final_message_id(store, request.execution_id)
    late_id = "msg_late_collected_reply"
    segment_id = store.put_segment(
        segment_type="model", content="later", created_by_agent_id=request.agent_id,
        execution_id=request.execution_id,
        metadata={"role": "agent", "kind": "commentary", "message": {
            "message_id": late_id, "role": "agent", "execution_id": request.execution_id,
            "parts": [{"type": "text", "text": "later"}],
        }},
    )
    _deliver_captured_message(store, request.agent_id, segment_id)
    assert stored_final_message_id(store, request.execution_id) == late_id != earlier
    assert "usage" not in store.read_chat_messages(request.agent_id)[-2]
    assert store.read_chat_messages(request.agent_id)[-1]["usage"]["input_tokens"] == 9



@pytest.mark.parametrize("body", ["  original\n\n", "<user-message>\nx\n</user-message>", ""])
def test_envelope_is_lossless(body):
    """消息封装往返保留空白与原始正文

    :module: spec.design-governance
    :design-point:
    """
    assert unwrap_message(wrap_message(body)) == body
    assert unwrap_message("<user-message>\nbroken") == "<user-message>\nbroken"


def test_reset_private_archive_keeps_content_and_excludes_new_input():
    """清除上下文后私有归档保留旧内容并排除新输入

    :design-point:"""
    store = AgentDataStore()
    agent = "resident_reset_archive"
    old = store.display_chat_message(agent, message("msg_before_clear", "retained history"))
    operation = store.clear_chat(agent, actor_ref="sample_admin")
    new = store.display_chat_message(agent, message("msg_after_clear", "new context"))
    assert [item["message_id"] for item in store.read_chat_messages(agent)] == ["msg_after_clear"]
    assert unwrap_message(store.segment(old["segment_id"])["content"]) == "retained history"
    # Private history is the retained content before the clear operation. It
    # needs neither another message copy nor a persisted Chat visibility flag.
    sequence = store.trace_sequence_id(agent)
    with store.database.read() as connection:
        archived = [row[0] for row in connection.execute(
            "SELECT s.segment_id FROM sequence_segments s JOIN operation_positions p "
            "ON p.sequence_id=s.sequence_id WHERE p.operation_id=%s AND s.ordinal<p.ordinal "
            "ORDER BY s.ordinal", (operation,)).fetchall()]
    assert archived == [old["segment_id"]]
    assert new["segment_id"] not in archived
    assert store.sequence_segment_ids(sequence) == [old["segment_id"], new["segment_id"]]


@pytest.mark.parametrize("input_identity", [
    {"runtime_options": {"client_user_message_id": "msg_input"}},
    {"metadata": {"user_message_id": "msg_input"}},
])
def test_current_input_is_the_only_trace_member_and_chat_uses_its_body(isolated_portal_case, input_identity):
    """当前输入在 trace 中仅占一项且 Chat 使用最新正文

    :module: spec.design-governance
    :design-point:
    """
    store = AgentDataStore()
    original = store.display_chat_message("resident_current", message("msg_input", "original"))
    source = store.segment(original["segment_id"])
    assert source["content"] == wrap_message("original")
    assert "content_parts" not in source["metadata"]["message"]
    store.fork_agent(source_agent_id="resident_current", target_agent_id="resident_frozen", actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id("resident_current")))
    request = replace(_turn_request("resident_current"), prompt=wrap_message("original"),
                      **input_identity)
    session = _runtime_session(request, "thread-current")
    projector = AgentDataProjector(store)
    projector.begin(request, session)
    projector.observe_invocation(request.execution_id, replace(_invocation(request, session), user_prompt=wrap_message("changed")))
    trace = store.sequence_segment_ids(store.trace_sequence_id("resident_current"))
    assert len(trace) == 1
    assert store.read_chat_messages("resident_current")[0]["content_parts"] == [{"type": "text", "text": "changed"}]
    assert store.read_chat_messages("resident_frozen")[0]["content_parts"] == [{"type": "text", "text": "original"}]
    assert store.segment(original["segment_id"])["content"] == wrap_message("original")
    final_segment_id = projector.complete(execution_id=request.execution_id, backend_session_id=session.backend_session_id,
                                          backend_route_id=session.backend_route_id, turn_id="turn-current", response="answer")
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    _deliver_captured_message(store, request.agent_id, final_segment_id)
    replies = store.chat_replies("resident_current", "msg_input")
    assert len(replies) == 1
    assert replies[0]["content_parts"] == [{"type": "text", "text": "answer"}]
    assert store.chat_final_replies_many({"resident_current": ["msg_input"]})["resident_current"] == {
        "msg_input": (replies[0]["message_id"], replies[0]["message_id"])}
    assert store.trace_length("resident_current") == 2


def test_many_to_one_replaces_only_named_occurrences(isolated_portal_case):
    """多项合并仅替换指定消息的出现位置

    :module: spec.design-governance
    :design-point:
    """
    store = AgentDataStore()
    messages = [store.display_chat_message("resident_merge", message(f"msg_{n}", "same")) for n in range(3)]
    store.transform_agent_segments("resident_merge", [m["segment_id"] for m in messages[:2]],
        segment_id="segment_merged", content="summary without envelope", actor_ref="agent_harness",
        operation_id="merge", operation_type="harness.merge")
    shown = store.read_chat_messages("resident_merge")
    assert [m["content_parts"][0]["text"] for m in shown] == ["summary without envelope", "same"]
    assert store.chat_message("resident_merge", "msg_0")["content_parts"][0]["text"] == "summary without envelope"


def test_clear_and_fork_cutoffs_count_current_content_after_merging(isolated_portal_case):
    """消息合并后清除与分支截点按当前内容计数

    :module: spec.design-governance
    :design-point:
    """
    store = AgentDataStore()
    agent = "resident_gap"
    original = [store.display_chat_message(agent, message(f"msg_gap_{n}", str(n))) for n in range(3)]
    store.transform_agent_segments(agent, [m["segment_id"] for m in original[:2]],
        segment_id="segment_gap_merge", content=wrap_message("merged"), actor_ref="agent_harness",
        operation_id="gap_merge", operation_type="harness.merge")
    cutoff = store.trace_length(agent)
    store.display_chat_message(agent, message("msg_gap_new", "new"))
    store.record_context_reset(agent, reset_at="2026-10-08T02:00:00Z", actor_ref="sample_admin", trace_position=cutoff)
    assert [m["message_id"] for m in store.read_chat_messages(agent)] == ["msg_gap_new"]
    store.fork_agent(source_agent_id=agent, target_agent_id="resident_gap_fork", actor_ref="sample_admin", segment_ids=store.sequence_segment_ids(store.trace_sequence_id(agent))[:cutoff])
    assert store.trace_length("resident_gap_fork") == cutoff
    assert store.sequence_segment_ids(store.trace_sequence_id("resident_gap_fork")) == ["segment_gap_merge", original[2]["segment_id"]]


def test_input_history_is_what_the_caller_typed_in_the_whole_trace(isolated_portal_case):
    """输入历史是调用者本人在整条 trace 中发过的输入

    按发送者读出调用者本人作为用户发过的输入，从新到旧分页：包括清除上下文之前和 Fork 继承的输入，不含其他成员的输入、Resident 投递、Mailbox 合并和系统消息；发送者不区分大小写；分页标记非法时报错。

    Up recalls the caller's own inputs, newest first, across context resets and Forks (sample_admin 10-16).

    :importance: P1
    :design-point:
    """
    from management_common.principal import mailbox_origin, message_origin
    from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository

    store = AgentDataStore()
    agent = "resident_history"
    assert store.read_input_history(agent, "sample_admin", limit=5) == {"messages": [], "ordinals": [], "next_before_ordinal": None}
    ids = lambda page: [m["message_id"] for m in page["messages"]]
    store.display_chat_message(agent, message("msg_first", "first"))
    # Another member's input, a Resident's delivery, a Mailbox group and a service notice.
    store.display_chat_message(agent, {**message("msg_other", "other"), "author_id": "CASE-A"})
    store.display_chat_message(agent, {**message("msg_delivery", "task"), "author_id": "resident_aaaaaaaaaaaa",
                                       "origin": message_origin("resident_aaaaaaaaaaaa", initiated_by="sample_admin")})
    store.display_chat_message(agent, {**message("msg_group", "grouped"), "author_id": "mailbox", "origin": mailbox_origin("sample_admin")})
    store.display_chat_message(agent, {**message("msg_notice", "notice"), "role": "system", "author_id": "portal"})
    store.display_chat_message(agent, {**message("msg_second", "second"), "author_id": "Sample_Admin", "origin": message_origin("Sample_Admin")})
    store.record_context_reset(agent, reset_at="2026-10-08T02:00:00Z", actor_ref="sample_admin", trace_position=store.trace_length(agent))
    store.display_chat_message(agent, message("msg_third", "third"))
    store.display_chat_message(agent, {**message("msg_reply", "reply"), "role": "agent", "author_id": agent})
    assert [m["message_id"] for m in store.read_chat_messages(agent)] == ["msg_third", "msg_reply"]

    newest = store.read_input_history(agent, "sample_admin", limit=2)
    assert ids(newest) == ["msg_third", "msg_second"]
    assert newest["ordinals"][0] > newest["ordinals"][1]
    older = store.read_input_history(agent, "SAMPLE_ADMIN", before_ordinal=newest["next_before_ordinal"], limit=2)
    assert (ids(older), older["next_before_ordinal"]) == (["msg_first"], None)
    assert ids(store.read_input_history(agent, "CASE-A", limit=5)) == ["msg_other"]
    assert store.read_input_history(agent, "resident_aaaaaaaaaaaa", limit=5)["messages"] == []

    # A Fork's history holds the inputs it inherited.
    store.fork_agent(source_agent_id=agent, target_agent_id="resident_history_fork", actor_ref="sample_admin",
                     segment_ids=store.sequence_segment_ids(store.trace_sequence_id(agent)))
    assert ids(store.read_input_history("resident_history_fork", "sample_admin", limit=5)) == ["msg_third", "msg_second", "msg_first"]

    repository = ResidentConversationRepository(None, store)
    first = repository.input_history_page(agent, "sample_admin", "", limit=2)
    assert ids(first) == ["msg_third", "msg_second"]
    assert first["messages"][0]["content_parts"] == [{"type": "text", "text": "third"}]
    rest = repository.input_history_page(agent, "sample_admin", first["next_page_token"], limit=2)
    assert (ids(rest), rest["next_page_token"]) == (["msg_first"], "")
    with pytest.raises(ValueError):
        repository.input_history_page(agent, "sample_admin", "not-a-position")


def test_input_history_keeps_an_edit_and_the_text_before_a_retraction(isolated_portal_case):
    """输入历史取编辑后的版本和撤回前的内容

    Chatroom 中编辑过的消息在输入历史里是编辑后的正文；撤回的消息返回撤回前的正文且不带撤回标记，撤回后仍能翻出来重发。

    An edited message is recalled as edited; a retracted one as it was, to send again (sample_admin 10-16).

    :design-point:
    """
    store = AgentDataStore()
    room = "chatroom_aaaaaaaaaaaa"
    edited = store.display_chat_message(room, message("msg_edited", "draft"))
    retracted = store.display_chat_message(room, message("msg_retracted", "oops"))
    store.update_chat_message(room, "msg_edited", {**edited, "content_parts": [{"type": "text", "text": "final"}],
                                                   "edited_at": "2026-10-08T01:05:00Z"})
    store.update_chat_message(room, "msg_retracted", {**retracted, "content_parts": [], "retracted_at": "2026-10-08T01:06:00Z"})
    page = store.read_input_history(room, "sample_admin", limit=5)
    assert [(m["message_id"], m["content_parts"]) for m in page["messages"]] == [
        ("msg_retracted", [{"type": "text", "text": "oops"}]),
        ("msg_edited", [{"type": "text", "text": "final"}]),
    ]
    assert "retracted_at" not in page["messages"][0]


def test_activity_text_enters_trace_once(isolated_portal_case):
    """重复展示活动文本时仅向 trace 写入一次

    :design-point:"""
    store = AgentDataStore()
    output = {**message("msg_activity", "working"), "role": "agent", "execution_id": "execution_activity"}
    store.display_chat_message("resident_activity", output)
    store.display_chat_message("resident_activity", output)
    assert store.trace_length("resident_activity") == 1
    assert store.read_chat_messages("resident_activity")[0]["content_parts"][0]["text"] == "working"


def test_multiple_text_parts_and_attachments_roundtrip_without_text_copies():
    """多段文本与附件无需复制正文即可无损往返

    :design-point:"""
    from management_portal.execution.message_projection import message_facts, project_message
    original = message("msg_parts", "")
    original["content_parts"] = [{"type": "text", "text": "a\n"}, {"type": "image", "image_id": "img"}, {"type": "text", "text": "  b"}]
    facts = message_facts(original)
    assert all("text" not in p for p in facts["parts"])
    assert project_message("segment_parts", facts, wrap_message("a\n\n  b"))["content_parts"] == original["content_parts"]


def test_completed_capture_replaces_the_earlier_ui_notice():
    """已完成捕获替换先前界面通知并保留附件

    :design-point:"""
    from tests.cases.live_trace_support import _turn
    from tests.cases.test_live_trace_ingress import _emit
    from management_portal.execution.message_projection import model_message_id
    store, projector, request, _ = _turn('codex')
    mid = model_message_id(request.execution_id, 'live-item', 'commentary')
    store.display_chat_message(request.agent_id, {**message(mid, 'completed text'),
        'role': 'agent', 'author_id': request.agent_id, 'execution_id': request.execution_id,
        'provider_response': {'attachments': [{'name': 'report.txt'}]}})
    _emit(projector, request, {'id': 'live-item', 'type': 'agentMessage', 'phase': 'commentary', 'text': 'completed text'})
    shown = [m for m in store.read_chat_messages(request.agent_id) if m['message_id'] == mid]
    assert len(shown) == 1
    assert shown[0]['provider_response'] == {'attachments': [{'name': 'report.txt'}]}
    assert store.segment(shown[0]['segment_id'])['content'] == 'completed text'
    with store.database.read() as connection:
        assert connection.execute("SELECT COUNT(*) FROM operation_positions p JOIN agent_operations o USING(operation_id) WHERE o.kind='notice'").fetchone()[0] == 0


def test_only_the_selected_visible_final_enters_trace():
    """仅选定展示的最终回复进入 trace

    :design-point:"""
    from tests.cases.live_trace_support import _turn
    from tests.cases.test_live_trace_ingress import _emit
    store, projector, request, _ = _turn('codex')
    for item, text in [('answer', 'the answer'), ('ack', 'acknowledged')]:
        _emit(projector, request, {'id': item, 'type': 'agentMessage', 'phase': 'final_answer', 'text': text})
    first_segment_id = stable_segment_id('provider-model', request.execution_id, 'answer', 'final_answer')
    second_segment_id = stable_segment_id('provider-model', request.execution_id, 'ack', 'final_answer')
    assert store.segment(second_segment_id)['content'] == 'acknowledged'
    _deliver_captured_message(store, request.agent_id, first_segment_id)
    events = [s for s in store.read_sequence(store.trace_sequence_id(request.agent_id))['segments'] if s['segment_type'] == 'model']
    assert [s['content'] for s in events] == ['the answer']
    messages = [m for m in store.read_chat_messages(request.agent_id) if m['role'] == 'agent']
    assert [m['content_parts'][0]['text'] for m in messages] == ['the answer']
    assert all(not {'show_in_chat', 'hidden_from_chat', 'send_to_ai', 'native_id'} & s['metadata'].keys() for s in events)


@pytest.mark.parametrize("card_type", ["native_turn", "resource_changes", "subagent_run"])
def test_ui_card_remains_visible_without_superseding_the_actual_reply(card_type):
    """界面卡片保持可见且不替代实际回复

    :design-point:"""
    store = AgentDataStore()
    agent = "resident_reply"
    store.display_chat_message(agent, message("msg_request", "work"))
    store.display_chat_message(agent, {**message("msg_answer", "PORTAL_TAU_RESULT: completed"),
        "role": "agent", "reply_to_message_id": "msg_request"})
    part = {"type": card_type, "segment_id": "segment_card"}
    store.put_operation_record(segment_id="segment_card", created_by_agent_id=agent,
        segment_type="model" if card_type == "native_turn" else "artifact", content=part)
    store.display_chat_message(agent, {**message("msg_card", ""), "role": "agent",
        "reply_to_message_id": "msg_request", "content_parts": [part]})
    assert store.chat_final_replies_many({agent: ["msg_request"]})[agent] == {"msg_request": ("msg_answer", "msg_answer")}
    assert [m["message_id"] for m in store.chat_replies(agent, "msg_request")] == ["msg_answer"]
    assert {m["message_id"] for m in store.chat_message_headers([agent])} == {"msg_request", "msg_answer"}
    assert store.read_chat_messages(agent)[-1]["content_parts"] == [part]
    assert store.trace_length(agent) == 2


def test_reply_lookup_does_not_scan_other_agents_notices(monkeypatch):
    """回复查询限制在本会话且不扫描其他 agent 通知

    Family reads each relayed round; unrelated notices must not multiply its work.

    :design-point:
    """
    import json
    from management_common.postgres_database import PostgresConnection

    store = AgentDataStore()
    agent = "resident_bounded_replies"
    store.display_chat_message(agent, message("msg_request", "work"))
    expected = [f"msg_reply_{i}" for i in range(10)]
    for identity in expected:
        segment = store.put_segment(
            segment_type="model", content=identity, created_by_agent_id=agent,
            metadata={"kind": "commentary", "message": {
                "message_id": identity, "role": "agent", "reply_to_message_id": "msg_request",
                "parts": [{"type": "text"}],
            }},
        )
        store.append_agent_segments(agent, [segment], actor_ref="sample_admin")
    # Preserve operational replies as well as canonical model content.
    store.display_chat_message(agent, {**message("msg_notice_reply", "visible reply"),
        "role": "agent", "reply_to_message_id": "msg_request"})
    expected.append("msg_notice_reply")
    # Forks may answer the same inherited input. A reply-ID index alone cannot
    # replace constraining the lookup to this conversation's occurrences.
    unrelated = json.dumps({"segment_type": "model", "content": "x" * 2048, "metadata": {
        "message": {"message_id": "msg_unrelated", "reply_to_message_id": "msg_request",
                    "role": "agent", "parts": [{"type": "text"}]},
    }})
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO agent_operations(operation_id,kind,agent_id,created_at,data_json) "
            "SELECT 'unrelated_notice_' || n, 'notice', 'resident_unrelated', '2026-10-12', %s "
            "FROM generate_series(1, 800) n", (unrelated,),
        )
        connection.execute("ANALYZE agent_operations")
        connection.execute("ANALYZE segments")
        connection.execute("ANALYZE sequence_segments")

    plans = []
    execute = PostgresConnection.execute

    def inspect(self, sql, parameters=None):
        if "position.ordinal > requested.after_ordinal" in sql:
            plans.append(execute(self, "EXPLAIN (ANALYZE, FORMAT JSON) " + sql, parameters).fetchone()[0][0]["Plan"])
        return execute(self, sql, parameters)

    monkeypatch.setattr(PostgresConnection, "execute", inspect)
    assert [item["message_id"] for item in store.chat_replies(agent, "msg_request")] == expected
    assert len(plans) == 1

    def inspected_notices(node):
        own = 0
        if node.get("Relation Name") == "agent_operations":
            own = (node["Actual Rows"] + node.get("Rows Removed by Filter", 0)) * node["Actual Loops"]
        return own + sum(inspected_notices(child) for child in node.get("Plans", []))

    # Measure database work rather than wall time or a particular index/plan shape.
    assert inspected_notices(plans[0]) <= 2 * len(expected)


def test_reply_batches_keep_round_limits_and_do_not_add_queries_per_round(monkeypatch):
    """批量回复读取保持轮次边界与固定查询次数

    10 轮与 100 轮的数据库往返次数一致，独立回复上限、顺序、缺失输入与 Fork 隔离保持正确。

    :design-point:
    """
    from management_common.postgres_database import PostgresConnection

    store = AgentDataStore()
    agent, fork = "resident_batch_source", "resident_batch_fork"
    windows = {}
    for i in range(100):
        request = f"msg_request_{i}"
        store.display_chat_message(agent, message(request, "work"))
        for j in range(3):
            store.display_chat_message(agent, {
                **message(f"msg_reply_{i}_{j}", f"reply {i}/{j}"),
                "role": "agent", "reply_to_message_id": request,
            })
        windows[request] = f"msg_reply_{i}_1"
    store.fork_agent(source_agent_id=agent, target_agent_id=fork, actor_ref="sample_admin",
                     segment_ids=store.sequence_segment_ids(store.trace_sequence_id(agent)))
    store.display_chat_message(fork, {**message("msg_fork_reply", "fork only"),
                                     "role": "agent", "reply_to_message_id": "msg_request_0"})
    queries = []
    execute = PostgresConnection.execute

    def count(self, sql, parameters=None):
        queries.append(sql)
        return execute(self, sql, parameters)

    monkeypatch.setattr(PostgresConnection, "execute", count)
    small = dict(list(windows.items())[:10])
    store.chat_replies_many({agent: small}, limit=1)
    small_count = len(queries)
    queries.clear()
    result = store.chat_replies_many({agent: windows}, limit=1)
    # Within a batch, more rounds add returned rows, not database round trips.
    assert len(queries) == small_count
    assert small_count < 15
    assert {mid: [item["message_id"] for item in replies]
            for mid, replies in result[agent].items()} == {
        f"msg_request_{i}": [f"msg_reply_{i}_1"] for i in range(100)
    }
    mixed = store.chat_replies_many({agent: {"msg_request_0": "", "msg_missing": ""},
                                    fork: {"msg_request_0": ""}, "resident_absent": {"msg_missing": ""}}, limit=2)
    assert [m["message_id"] for m in mixed[agent]["msg_request_0"]] == ["msg_reply_0_1", "msg_reply_0_2"]
    assert [m["message_id"] for m in mixed[fork]["msg_request_0"]] == ["msg_reply_0_2", "msg_fork_reply"]
    assert mixed[agent]["msg_missing"] == mixed["resident_absent"]["msg_missing"] == []
    assert store.chat_replies_many({}) == {}


def test_a_captured_final_that_asks_questions_reaches_chat_as_its_question_card():
    """带提问卡片的最终回复按已捕获的 Segment 送进 Chat

    已捕获的最终回复正文含 portal-question 代码围栏，送达时围栏已转成提问卡片；两边去掉提问卡片后的正文相同，回复使用已捕获的 Segment 进入 Chat，显示正文和提问卡片，不报 Captured message does not match。

    :module: execution.persistence
    :importance: P1
    :design: decision_1e2eb869bc10
    :design-point:
    """
    # resident_595ccac5952e 10-13: the delivery turns a portal-question fence
    # into a question part, while the captured Segment keeps the fence; the two
    # still hold the same answer.
    from management_common.async_questions import QUESTION_PART, decode_question_content
    from management_portal.execution.message_projection import project_message
    from tests.cases.live_trace_support import _turn

    store, projector, request, _ = _turn("codex")
    answer = "正式名字是 AI员工。\n\n"
    fence = '```portal-question\n{"title":"改成哪个名字？","options":["AI员工","AlignMe"]}\n```'
    final_segment_id = projector.complete(
        execution_id=request.execution_id, backend_session_id="thread-native",
        backend_route_id="one", turn_id="turn-question", response=answer + fence,
    )
    store.finish_execution(request.execution_id, status="completed", usage=store.execution(request.execution_id)["usage"])
    segment = store.segment(final_segment_id)
    message = project_message(final_segment_id, segment["metadata"]["message"], segment["content"])
    # What _append_conversation_message hands over for an Agent reply.
    message["content_parts"] = decode_question_content(
        message["content_parts"], request.agent_id, message["message_id"],
    )
    store.project_chat_message(request.agent_id, message)

    final = store.read_chat_messages(request.agent_id)[-1]
    assert final["segment_id"] == final_segment_id
    assert [part["type"] for part in final["content_parts"]] == ["text", QUESTION_PART]
    assert final["content_parts"][0]["text"] == answer
    assert final["content_parts"][1]["questions"][0]["title"] == "改成哪个名字？"
