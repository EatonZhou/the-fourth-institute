from __future__ import annotations
from management_portal.society_service import resident_conversation_stream

import json
from copy import deepcopy
from unittest import mock

import pytest

from management_common.async_questions import decode_question_content, normalize_async_questions, question_answer_id
from management_portal.execution import event_projection
from management_portal.society_service import resident_directory_domain
from management_portal.society_service import resident_execution_domain
from management_portal.society_service import resident_intake_domain
from management_portal.society_service import resident_lifecycle_domain
from management_portal.society_service import resident_record_domain
from management_portal.society_service import resident_registration_domain
from management_portal.society_service import resident_session_backend_domain
from management_portal.society_service import resident_execution_work, resident_protocol
from management_portal.society_service.resident_questions import prepare_question_answer
from tests.cases.test_resident_execution_protocol import resident_record
from management_portal.execution import slash_commands


def question_event(text="", method="item/completed"):
    return {
        "type": "provider_event", "execution_id": "execution-question", "sequence": 1,
        "details": {"method": method, "params": {"turnId": "turn-question", "item": {
            "id": "item-question", "type": "agentMessage", "phase": "commentary",
            "text": text, "questions": [{"title": "选择测试范围？", "options": ["定向", "全部"]}],
        }}},
    }


def persist_question(record):
    resident_id = record["resident_id"]
    resident_execution_work.write(record, {
         "execution_id": "execution-question",
        "status": "running", "actor": "CASE-A", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    outputs = event_projection.execution_outputs_from_events([question_event()])
    return resident_execution_domain._persist_execution_display_messages(
        resident_id,  "execution-question", outputs,
    )[0]


def reply(resident_id, source, answers=None):
    return {
        "message_id": question_answer_id(resident_id, source["message_id"]),
        "role": "user", "content_parts": [
            {"type": "text", "text": "定向"},
            {"type": "async_question_answer", "source_message_id": source["message_id"],
             "answers": answers if answers is not None else [{"question_index": 0, "answer": "定向"}]},
        ],
    }


CARD_OUTPUT = '检查继续。\n```portal-question\n{"title":"选择测试范围？","options":["定向","全部"]}\n```\n后续进展。'


@pytest.mark.parametrize("phase", ["commentary", "final_answer"])
def test_normal_provider_output_is_stored_as_typed_message(resident_record, phase):
    """普通输出与最终回复中的 portal-question 块存为问题卡片，重放不重复保存"""
    resident_id = resident_record["resident_id"]
    persist_question(resident_record)
    event = question_event(CARD_OUTPUT)
    item = event["details"]["params"]["item"]
    item.update(id="ordinary-question-output", phase=phase)
    item.pop("questions")
    outputs = event_projection.execution_outputs_from_events([event])
    if phase == "commentary":
        source = resident_execution_domain._persist_execution_display_messages(resident_id,  "execution-question", outputs)[0]
        replay = resident_execution_domain._persist_execution_display_messages(resident_id,  "execution-question", outputs)[0]
    else:
        message = resident_intake_domain.build_message("agent", CARD_OUTPUT)
        source = resident_execution_domain._persist_execution_final_message(resident_id,  "execution-question", message)
        replay = resident_execution_domain._persist_execution_final_message(resident_id,  "execution-question", message)
    assert replay == source
    assert [part["type"] for part in source["content_parts"]] == ["text", "async_questions", "text"]
    assert source["content_parts"][1]["answer_message_id"] == question_answer_id(resident_id, source["message_id"])
    repository = resident_conversation_stream.get_repository()
    assert repository.message(resident_id, source["message_id"])["content_parts"] == source["content_parts"]
    from management_portal.execution.agent_data_store import chat_segment_id

    segment = repository.agent_data.segment(chat_segment_id(resident_id, source["message_id"]))
    assert "content_parts" not in segment["metadata"]["message"]
    assert repository.agent_data.trace_length(resident_id) == 1
    assert prepare_question_answer(resident_id, reply(resident_id, source), repository)[1]["role"] == "user"


@pytest.mark.parametrize("text", [
    "", "普通提问？", '```portal-question\n{"title":"未完成"}',
    '```portal-question\nnot json\n```', '```portal-question\n[]\n```',
    '```portal-question\n{"title":"","options":[]}\n```',
    '```portal-question\n{"title":"错误选项","options":[null]}\n```',
    '```portal-question\n{"title":"额外字段","action":"execute"}\n```',
    '````text\n' + CARD_OUTPUT + '\n````', '\n'.join('    ' + line for line in CARD_OUTPUT.splitlines()),
])
def test_invalid_or_example_card_content_remains_text(text):
    """格式无效或作为示例引用的 portal-question 内容保持为普通文本"""
    parts = [{"type": "text", "text": text}]
    assert decode_question_content(parts, "resident_aaaaaaaaaaaa", "msg_test") == parts


def test_one_message_asks_one_question_set_and_user_text_is_not_decoded(resident_record):
    """一条消息中的多个问题块合成一个问题集，用户消息里的问题块不解析"""
    text = CARD_OUTPUT + "\n" + CARD_OUTPUT.replace(
        '{"title":"选择测试范围？","options":["定向","全部"]}',
        '[{"title":"是否更新规范？","options":["更新"]},{"title":"补充说明？"}]',
    )
    resident_id = resident_record["resident_id"]
    parts = decode_question_content([{"type": "text", "text": text}], resident_id, "msg_multiple")
    assert [part["type"] for part in parts] == ["text", "async_questions", "text", "text"]
    question_set = parts[1]
    assert question_set["answer_message_id"] == question_answer_id(resident_id, "msg_multiple")
    assert [(question["question_index"], question["title"]) for question in question_set["questions"]] == [
        (0, "选择测试范围？"), (1, "是否更新规范？"), (2, "补充说明？"),
    ]
    assert decode_question_content(parts, resident_id, "msg_multiple") == parts
    user = resident_lifecycle_domain.append_conversation_message(resident_id, resident_intake_domain.build_message("user", text, "CASE-A"))
    assert user["content_parts"] == [{"type": "text", "text": text}]


def test_snapshot_only_requests_bounded_message_page(resident_record):
    """对话快照只读取限定条数的消息页，不读取全部历史"""
    resident_id = resident_record["resident_id"]
    persist_question(resident_record)
    repository = resident_conversation_stream.get_repository()
    repository.append_message(resident_id, resident_intake_domain.build_message("agent", "最近消息"))
    with (
        mock.patch.object(repository.agent_data, "read_chat_messages", side_effect=AssertionError("full history")),
        mock.patch.object(repository.agent_data, "read_chat_message_page", wraps=repository.agent_data.read_chat_message_page) as read_page,
    ):
        snapshot = repository.snapshot(resident_id, history_limit=1)
    read_page.assert_called_once_with(resident_id, limit=1)
    assert len(snapshot["messages"]) == 1
    assert "question_messages" not in snapshot


@pytest.mark.parametrize("work_state", ["idle", "running"])
def test_unanswered_card_preserves_runtime_and_does_not_block_final_output(resident_record, work_state):
    """未回答的问题卡片不改变运行状态与队列，也不阻止保存最终回复"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    resident_execution_work.write(resident_record, {
         "execution_id": "execution-question",
        "status": "running", "actor": "CASE-A", "queue": [],
     "submitted_at": "2026-10-14T00:00:00Z"})
    active_message_id = ""
    if work_state == "running":
        user = repository.append_message(resident_id, resident_intake_domain.build_message("user", "继续检查", "CASE-A"))
        active_message_id = user["message_id"]
        repository.set_input_state(resident_id, active_message_id, "running")
    repository.set_state(resident_id, {
        "work_state": work_state, "active_message_id": active_message_id,
        "queued_count": 0, "intake_state": "open",
    })
    before = repository.snapshot(resident_id)
    outputs = event_projection.execution_outputs_from_events([question_event()])
    source = resident_execution_domain._persist_execution_display_messages(
        resident_id,  "execution-question", outputs,
    )[0]
    after = repository.snapshot(resident_id)
    for field in ("state", "input_states", "queue", "runtime_settings"):
        assert after[field] == before[field]
    answer_id = question_answer_id(resident_id, source["message_id"])
    assert repository.message(resident_id, answer_id) is None
    final = resident_execution_domain._persist_execution_final_message(
        resident_id,  "execution-question", resident_intake_domain.build_message("agent", "可独立完成的检查已经结束。"),
    )
    assert final is not None
    assert repository.message(resident_id, source["message_id"])["content_parts"] == source["content_parts"]
    assert repository.message(resident_id, answer_id) is None


def test_question_set_stays_open_until_it_is_answered(resident_record):
    """问题集在用户和其他 Resident 继续发消息后仍保持可答，直到被回答"""
    # 10-12 sample_admin: a set stays answerable until answered, also after he
    # sends other messages; Residents' reports and relayed messages alike.
    from management_common.principal import message_origin

    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    source = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT, created_at="2001-01-01T00:00:00Z"),
    )
    repository.append_message(resident_id, resident_intake_domain.build_message("agent", "后续任务已经结束。"))
    repository.append_message(resident_id, resident_intake_domain.build_message("user", "先做别的", "CASE-A"))
    report, facade = "resident_0afd9a0731f7", "resident_111111111111"
    repository.append_message(resident_id, {
        **resident_intake_domain.build_message("user", "T8 已修复并发布。", report),
        "origin": message_origin(report, initiated_by=report),
    })
    repository.append_message(resident_id, {
        **resident_intake_domain.build_message("user", "再做一件事", facade),
        "origin": message_origin(facade, initiated_by="CASE-A"),
    })
    with mock.patch.object(repository, "queues", return_value={resident_id: {"items": [
            {"message_id": "msg_waiting", "author_id": "CASE-A"}]}}):
        assert repository.question_set_state(resident_id, source["message_id"]) == "open"
    content, answer = prepare_question_answer(resident_id, reply(resident_id, source), repository)
    assert "选择测试范围？" in content
    assert answer["message_id"] == question_answer_id(resident_id, source["message_id"])
    repository.append_message(resident_id, resident_intake_domain.build_message(
        "user", content, "CASE-A", message_id=answer["message_id"], content_parts=answer["content_parts"],
    ))
    assert repository.question_set_state(resident_id, source["message_id"]) == "answered"


def test_a_later_question_set_leaves_earlier_sets_answerable(resident_record):
    """后来的问题集出现后，较早的问题集仍可回答"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    first = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT),
    )
    second = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT),
    )
    assert repository.question_set_state(resident_id, first["message_id"]) == "open"
    assert repository.question_set_state(resident_id, second["message_id"]) == "open"
    content, answer = prepare_question_answer(resident_id, reply(resident_id, first), repository)
    repository.append_message(resident_id, resident_intake_domain.build_message(
        "user", content, "CASE-A", message_id=answer["message_id"], content_parts=answer["content_parts"],
    ))
    assert repository.question_set_state(resident_id, first["message_id"]) == "answered"
    assert repository.question_set_state(resident_id, second["message_id"]) == "open"


def test_a_person_closes_a_set_unanswered_without_telling_the_resident(resident_record):
    """用户可以不回答直接关闭问题集，Resident 收不到任何消息"""
    # 10-12 sample_admin: sets answered by plain text stay open; 不再回答 closes them.
    from management_portal.society_service import resident_protocol

    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    source = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT),
    )
    answer_id = question_answer_id(resident_id, source["message_id"])
    with mock.patch.object(resident_protocol.resident_access, "can_manage_resident", return_value=True):
        with pytest.raises(ValueError, match="action=cancel"):
            resident_protocol.respond_resident_user_input(resident_id, "CASE-A", {
                "message_id": source["message_id"], "action": "answer", "answers": {"0": "定向"},
            })
        closed = resident_protocol.respond_resident_user_input(resident_id, "CASE-A", {
            "message_id": source["message_id"], "action": "cancel",
        })
        assert closed == {"status": "cancelled"}
        # Closing again is the same close.
        assert resident_protocol.respond_resident_user_input(resident_id, "CASE-A", {
            "message_id": source["message_id"], "action": "cancel",
        }) == {"status": "cancelled"}
    assert repository.question_set_state(resident_id, source["message_id"]) == "closed"
    slot = repository.input_state(resident_id, answer_id)
    assert (slot["state"], slot["error"]["code"]) == ("cancelled", "question_set_closed")
    # Nothing reaches the Resident: no answer message, no queued input.
    assert repository.message(resident_id, answer_id) is None
    assert repository.queues([resident_id])[resident_id]["items"] == []
    with pytest.raises(ValueError, match="已关闭"):
        prepare_question_answer(resident_id, reply(resident_id, source), repository)
    snapshot = repository.snapshot(resident_id)
    assert any(item["message_id"] == answer_id and item["error"]["code"] == "question_set_closed"
               for item in snapshot["input_states"])


def test_an_answered_set_cannot_be_closed(resident_record):
    """已回答的问题集不能再关闭"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    source = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT),
    )
    content, answer = prepare_question_answer(resident_id, reply(resident_id, source), repository)
    repository.append_message(resident_id, resident_intake_domain.build_message(
        "user", content, "CASE-A", message_id=answer["message_id"], content_parts=answer["content_parts"],
    ))
    with pytest.raises(ValueError, match="已回答"):
        repository.close_question_set(resident_id, source["message_id"], actor="CASE-A")


def test_a_set_takes_one_answer_for_all_of_its_questions(resident_record):
    """问题集要求一次回答全部问题，回答按问题顺序整理成一条消息"""
    resident_id = resident_record["resident_id"]
    repository = resident_conversation_stream.get_repository()
    source = resident_lifecycle_domain.append_conversation_message(
        resident_id, resident_intake_domain.build_message("agent", CARD_OUTPUT.replace(
            '{"title":"选择测试范围？","options":["定向","全部"]}',
            '[{"title":"选择测试范围？","options":["定向","全部"]},{"title":"是否更新规范？"}]',
        )),
    )
    with pytest.raises(ValueError, match="全部问题"):
        prepare_question_answer(resident_id, reply(resident_id, source), repository)
    content, answer = prepare_question_answer(resident_id, reply(resident_id, source, [
        {"question_index": 1, "answer": "更新"}, {"question_index": 0, "answer": "全部"},
    ]), repository)
    assert content == "关于你提出的问题，我的回答是：\n1. 「选择测试范围？」：全部\n2. 「是否更新规范？」：更新"
    assert answer["content_parts"][1]["answers"] == [
        {"question_index": 0, "answer": "全部"}, {"question_index": 1, "answer": "更新"},
    ]


def test_completed_async_questions_survive_provider_and_activity_replay(subtests, isolated_portal_case):
    """已完成的问题卡片经 Provider 事件或运行活动重放后内容与 ID 不变"""
    for text in ["", "我继续检查代码，请你选择范围。"]:
        with subtests.test(text=text), isolated_portal_case() as case_path:
            event = question_event(text)
            outputs = event_projection.execution_outputs_from_events([event])
            assert outputs[0]["questions"][0]["title"] == "选择测试范围？"
            details = event_projection.runtime_details_from_objects([(1, event)], include_updates=True)
            replay = {"sequence": 1, "type": "runtime_activity", "activity": details[0]}
            recovered = event_projection.execution_outputs_from_events([replay])
            assert recovered[0]["questions"] == outputs[0]["questions"]
            assert recovered[0]["output_id"] == outputs[0]["output_id"]


def test_incomplete_question_does_not_publish_a_card_contracts(subtests, isolated_portal_case):
    """未完成的问题条目不生成卡片，问题规范化保留原始问题序号

    :module: resident.fork-handoff
    :importance: P1
    """
    with subtests.test(contract='test_incomplete_question_does_not_publish_a_card'), isolated_portal_case() as case_path:
        assert event_projection.execution_outputs_from_events([question_event(method='item/started')]) == []
    with subtests.test(contract='test_normalization_preserves_original_question_indices'), isolated_portal_case() as case_path:
        questions = normalize_async_questions([{}, {'title': 'Question', 'options': [None, 'Yes']}])
        assert questions == [{'question_index': 1, 'title': 'Question', 'options': ['Yes']}]


def test_questions_survive_durable_execution_activity(tmp_path):
    """问题卡片从持久化的执行活动文件中恢复"""
    path = tmp_path / "activity.jsonl"
    event_projection.append_execution_activity(path, [question_event()])
    recovered = event_projection.execution_outputs_from_events([
        json.loads(line) for line in path.read_text().splitlines()
    ])
    assert recovered[0]["questions"][0]["title"] == "选择测试范围？"


def test_final_question_persists_without_duplicating_final_answer_text(resident_record):
    """最终回复中的问题只保存为问题卡片，不重复保存回复正文"""
    persist_question(resident_record)
    event = question_event("最终内容由原有结果链路保存")
    event["details"]["params"]["item"].update(id="item-final-question", phase="final_answer")
    outputs = event_projection.execution_outputs_from_events([event])
    messages = resident_execution_domain._persist_execution_display_messages(
        resident_record["resident_id"],  "execution-question", outputs,
    )
    assert len(messages) == 1
    assert [part["type"] for part in messages[0]["content_parts"]] == ["async_questions"]


def test_questions_follow_history_page_and_answers_are_idempotent(resident_record):
    """问题卡片随历史分页加载，重复提交相同回答幂等，清空 Chat 后不能再答"""
    resident_id = resident_record["resident_id"]
    source = persist_question(resident_record)
    repository = resident_conversation_stream.get_repository()
    for index in range(3):
        repository.append_message(resident_id, resident_intake_domain.build_message("agent", f"继续工作 {index}"))
    with mock.patch.object(repository.agent_data, "read_chat_messages", side_effect=AssertionError("full history")):
        snapshot = repository.snapshot(resident_id, history_limit=1)
    assert source["message_id"] not in [message["message_id"] for message in snapshot["messages"]]
    assert "question_messages" not in snapshot
    older = repository.messages_page(resident_id, snapshot["next_page_token"])
    assert source["message_id"] in [message["message_id"] for message in older["messages"]]
    content, answer = prepare_question_answer(resident_id, reply(resident_id, source), repository)
    assert "选择测试范围？" in content
    answer = resident_intake_domain.build_message("user", content, "CASE-A", message_id=answer["message_id"], content_parts=answer["content_parts"])
    repository.append_message(resident_id, answer)
    first_cursor = repository.current_projection(resident_id)["cursor"]
    repository.append_message(resident_id, answer)
    assert repository.current_projection(resident_id)["cursor"] == first_cursor
    conflicting = deepcopy(answer)
    conflicting["content_parts"][0]["text"] = "不同回答"
    with pytest.raises(ValueError):
        repository.append_message(resident_id, conflicting)
    repository.set_input_state(resident_id, answer["message_id"], "completed")
    repository.append_message(resident_id, resident_intake_domain.build_message("agent", "后续进展"))
    restored = repository.snapshot(resident_id, history_limit=1)
    assert "question_messages" not in restored
    assert not any(item["message_id"] == answer["message_id"] for item in restored["input_states"])
    boundary = repository.messages_page(resident_id, restored["next_page_token"], limit=3)["next_page_token"]
    older = repository.messages_page(resident_id, boundary)
    assert source["message_id"] in [message["message_id"] for message in older["messages"]]
    assert answer["message_id"] not in [message["message_id"] for message in older["messages"]]
    assert any(item["message_id"] == answer["message_id"] and item["state"] == "completed" for item in older["input_states"])
    repository.agent_data.clear_chat(resident_id, actor_ref="CASE-A")
    assert source["message_id"] not in [message["message_id"] for message in repository.snapshot(resident_id)["messages"]]
    with pytest.raises(ValueError):
        prepare_question_answer(resident_id, reply(resident_id, source), repository)


@pytest.mark.parametrize("change", ["cross-resident", "index", "identity", "empty", "bool-index", "duplicate", "none"])
def test_question_reply_rejects_wrong_identity_or_source(resident_record, change):
    """回答的 Resident、消息 ID、问题序号或答案内容不符时被拒绝"""
    resident_id = resident_record["resident_id"]
    source = persist_question(resident_record)
    answer = reply(resident_id, source)
    entry = answer["content_parts"][1]["answers"][0]
    if change == "cross-resident":
        resident_id = "resident_bbbbbbbbbbbb"
    elif change == "index":
        entry["question_index"] = 9
    elif change == "identity":
        answer["message_id"] = "msg_wrong"
    elif change == "empty":
        entry["answer"] = " "
    elif change == "duplicate":
        answer["content_parts"][1]["answers"].append(dict(entry))
    elif change == "none":
        answer["content_parts"][1]["answers"] = []
    else:
        entry["question_index"] = False
    with pytest.raises(ValueError):
        prepare_question_answer(resident_id, answer, resident_conversation_stream.get_repository())


def test_question_answer_accepts_non_owner_and_preserves_author(resident_record, monkeypatch):
    """非管理者也可以回答问题集，回答保留实际作者"""
    source = persist_question(resident_record)
    resident_id = resident_record["resident_id"]
    monkeypatch.setattr(resident_directory_domain, "_can_manage_resident", lambda *_args, **_kwargs: False)
    answer = reply(resident_id, source)
    resident_intake_domain.persist_resident_input(
        resident_id, "non-owner", "定向", message_id=answer["message_id"],
        protocol_input=answer,
    )
    repository = resident_conversation_stream.get_repository()
    assert repository.message(resident_id, answer["message_id"])["author_id"] == "non-owner"
    assert repository.question_set_state(resident_id, source["message_id"]) == "answered"


def test_compact_race_preserves_one_canonical_answer_in_real_resident_queue(resident_record):
    """压缩中无法并入当前轮的问题回答进入队列，重复投递只排队一次"""
    from tests.cases.resident_delivery_test_support import published_delivery
    source = persist_question(resident_record)
    resident_id = resident_record["resident_id"]
    answer = reply(resident_id, source)
    with (
        mock.patch.object(resident_execution_work, "execution_snapshot", return_value={
            "execution_id": "execution-question", "status": "running",
        }),
        mock.patch.object(resident_session_backend_domain, "handle_resident_native_command", return_value={}),
        mock.patch.object(slash_commands, "handle_slash_command", return_value={"handled": False}),
        mock.patch.object(resident_record_domain, "resolve_context_refs", return_value={}),
        mock.patch.object(resident_registration_domain, "build_resident_prompt", return_value="prompt"),
        mock.patch.object(resident_intake_domain, "_submit_active_turn_input", side_effect=RuntimeError(
            "ActiveTurnNotSteerable { turn_kind: Compact }",
        )),
    ):
        first = published_delivery(resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": answer}, "CASE-A"))
        replay = published_delivery(resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": answer}, "CASE-A"))
    assert first["input_state"]["state"] == replay["input_state"]["state"] == "queued"
    queue = resident_execution_work.read(resident_record)["queue"]
    assert len(queue) == 1
    assert queue[0]["input_message"]["content_parts"][1]["type"] == "async_question_answer"
    assert "选择测试范围？" in queue[0]["input_message"]["content_parts"][0]["text"]
    snapshot = resident_conversation_stream.get_repository().snapshot(resident_id)
    assert any(
        state["message_id"] == answer["message_id"] and state["state"] == "queued"
        for state in snapshot["input_states"]
    )


def test_question_answer_queues_like_a_normal_message(monkeypatch, isolated_portal_case):
    """问题回答像普通消息一样排队，不插入正在运行的轮次

    An answer from a card is a new user message (decision_1e2eb869bc10): while
    the Resident works it waits in the queue like any other message; it is not
    pushed into the running turn or ahead of the queue."""
    with isolated_portal_case():
        resident_id = "resident_aaaaaaaaaaaa"
        answer = reply(resident_id, {"message_id": "msg_question"})
        queued = {"message_id": answer["message_id"], "input_state": {"state": "queued"}}
        enqueue = mock.Mock(return_value=deepcopy(queued))
        inject = mock.Mock()
        monkeypatch.setattr(resident_execution_domain, "deliver_resident_message_content", enqueue)
        monkeypatch.setattr(resident_intake_domain, "inject_resident_queue_input_into_active_turn", inject)
        monkeypatch.setattr(resident_protocol, "_with_conversation_controls", lambda result, *_args: result)
        result = resident_protocol.deliver_resident_input({"resident_id": resident_id, "message": answer}, "CASE-A")
        enqueue.assert_called_once()
        assert enqueue.call_args.kwargs.get("activation_policy", "enqueue_and_trigger") == "enqueue_and_trigger"
        inject.assert_not_called()
        assert result["input_state"]["state"] == "queued"
