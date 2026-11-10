"""A User's waiting questions across Conversations, checked against their holders' current state.

:module: resident.conversation
"""
from __future__ import annotations

import pytest

from management_common.async_questions import question_answer_id, question_message_parts
from management_common.principal import message_origin
from management_portal import permission_system
from management_portal.society_service import (
    family_facade, resident_access, resident_conversation_records, resident_conversation_stream,
    resident_input_queue_domain, resident_protocol, user_pending_questions,
)
from management_portal.society_service.resident_questions import prepare_question_answer
from tests.cases.test_family_facade import facade, MEMBER, _register_test_member  # noqa: F401
from tests.cases.resident_execution_test_support import input_delivery, resident_record  # noqa: F401
from tests.cases.test_family_question_projection import question_round  # noqa: F401


CARD = '检查继续。\n```portal-question\n{"title":"选择测试范围？","options":["定向","全部"]}\n```'


@pytest.fixture
def allowed(monkeypatch):
    """Everyone may answer everywhere unless a test narrows it."""
    monkeypatch.setattr(resident_access, "can_manage_resident", lambda record, actor, **kw: True)
    monkeypatch.setattr(resident_access, "resident_deliverable_to_actor", lambda record, actor: True)
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: True)


def _pending(actor):
    return user_pending_questions.list_user_pending_requests(actor)["requests"]


def _ask(repo, resident_id, input_author, *, text=CARD, origin_initiator=""):
    request = resident_input_queue_domain.build_message("user", "请检查", input_author)
    if origin_initiator:
        request["origin"] = message_origin(input_author, initiated_by=origin_initiator)
    repo.append_message(resident_id, request)
    asked = resident_input_queue_domain.build_message("agent", text, resident_id)
    asked["reply_to_message_id"] = request["message_id"]
    return repo.append_message(resident_id, asked)


def _answer(repo, resident_id, source):
    answer = {"message_id": question_answer_id(resident_id, source["message_id"]), "role": "user", "content_parts": [
        {"type": "text", "text": "定向"},
        {"type": "async_question_answer", "source_message_id": source["message_id"],
         "answers": [{"question_index": 0, "answer": "定向"}]}]}
    content, message = prepare_question_answer(resident_id, answer, repo)
    repo.append_message(resident_id, resident_input_queue_domain.build_message(
        "user", content, "CASE-A", message_id=message["message_id"], content_parts=message["content_parts"]))


def test_question_set_waits_for_the_input_author_until_answered(resident_record, allowed):
    """问题集只列给发起这一轮的 User，回答后不再列出，阅读不算回答"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    source = _ask(repo, resident_id, "CASE-A")
    expected = {
        "key": f"{resident_id}/message/{source['message_id']}",
        "source_resident_id": resident_id, "source_message_id": source["message_id"],
        "resident_id": resident_id, "message_id": source["message_id"], "kind": "question_set",
    }
    listed = _pending("CASE-A")
    assert [{key: item[key] for key in expected} for item in listed] == [expected]
    assert set(listed[0]) == {*expected, "created_at", "title"}
    from management_portal.society_service import resident_record_domain
    assert listed[0]["title"] == resident_record_domain.find_resident(resident_id)["title"]
    assert "选择测试范围" not in repr(listed)
    assert _pending("someone-else") == []
    assert _pending("case-a") == listed  # User IDs compare without case.
    assert _pending("CASE-A") == listed  # Reading answers nothing.
    _answer(repo, resident_id, source)
    assert _pending("CASE-A") == []


def test_old_questions_are_not_cut_off_by_later_messages(resident_record, allowed):
    """较早的问题不会因后续大量消息被截断遗漏"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    source = _ask(repo, resident_id, "CASE-A")
    for index in range(resident_protocol.REQUEST_SCAN_MESSAGES + 5):
        repo.append_message(resident_id, resident_input_queue_domain.build_message("agent", f"进展 {index}"))
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [source["message_id"]]


def test_resident_inputs_question_marks_and_managers_ask_no_one(resident_record, allowed):
    """Resident 发起的轮次、只带问号的文字都不产生待答问题，也不发给可管理者"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    other = "resident_0afd9a0731f7"
    _ask(repo, resident_id, other, origin_initiator=other)
    _ask(repo, resident_id, "CASE-A", text="要不要继续？请告诉我？")
    assert _pending("CASE-A") == []
    assert _pending(other) == []


def test_closing_removes_the_question_and_announces_it(resident_record, allowed):
    """关闭问题集后不再列出，并产生 Mailbox 可见的消息更新事件"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    source = _ask(repo, resident_id, "CASE-A")
    cursor = repo.events_after(resident_id, 0)["cursor"]
    resident_protocol.respond_resident_user_input(resident_id, "CASE-A", {
        "message_id": source["message_id"], "action": "cancel"})
    assert _pending("CASE-A") == []
    events = repo.events_after(resident_id, cursor)["events"]
    assert any(event["event_type"] == resident_conversation_records.MESSAGE_UPDATED
               and event["payload"]["message"]["message_id"] ==source["message_id"] for event in events)


def test_a_user_who_may_not_answer_there_is_not_shown_the_question(resident_record, monkeypatch, allowed):
    """当前没有回答资格的 User 看不到该问题，恢复资格后重新出现"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    _ask(repo, resident_id, "CASE-A")
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: False)
    assert _pending("CASE-A") == []
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: True)
    assert len(_pending("CASE-A")) == 1


@pytest.fixture
def marked_round(question_round):
    """The same round with the relay mark every current relay gives the member's input."""
    facade, repo, outer, _, _, service, save = question_round
    facade_id = facade["resident_id"]
    request = resident_input_queue_domain.build_message("user", "Check this", "CASE-A")
    repo.append_message(facade_id, request)
    repo.set_input_state(facade_id, request["message_id"], "queued")
    inner = {**resident_input_queue_domain.build_message("user", "Check this", "CASE-A"),
             "business_ref": family_facade.relay_mark(facade_id, request["message_id"])}
    repo.append_message(MEMBER, inner)
    repo.set_input_state(MEMBER, inner["message_id"], "running")
    family_facade.complete_relay((facade_id, request["message_id"]), MEMBER, inner["message_id"])
    save("userInputResponse", status="expired")  # The base round's unmarked request is over.
    marked = "4" * 32

    def save_marked(kind="userInputRequest", **extra):
        return save(kind, request_id=marked, item_id="item-marked", **extra)

    native = resident_input_queue_domain.build_message("agent", "", MEMBER, content_parts=[save_marked()])
    native["reply_to_message_id"] = inner["message_id"]
    repo.append_message(MEMBER, native)
    return facade, repo, request, inner, native, service, save_marked


def test_family_member_question_opens_at_the_facade_once(marked_round, allowed):
    """Family 成员的原生问题与问题集各列一次，在门面打开，key 保持成员原消息"""
    facade, repo, outer, inner, native, service, save = marked_round
    facade_id = facade["resident_id"]
    assert family_facade.relay_sources_of(MEMBER, inner["message_id"]) == [(facade_id, outer["message_id"])]
    asked = resident_input_queue_domain.build_message("agent", "", MEMBER)
    asked["reply_to_message_id"] = inner["message_id"]
    asked["content_parts"] = question_message_parts(
        "", [{"question_index": 0, "title": "Which scope?", "options": ["Small", "All"]}], MEMBER, asked["message_id"])
    repo.append_message(MEMBER, asked)
    listed = {item["source_message_id"]: item for item in _pending("CASE-A")}
    assert set(listed) == {native["message_id"], asked["message_id"]}
    assert listed[native["message_id"]]["kind"] == "question"
    assert listed[asked["message_id"]]["kind"] == "question_set"
    for message_id, item in listed.items():
        assert (item["source_resident_id"], item["resident_id"], item["message_id"]) == (MEMBER, facade_id, message_id)
        assert item["key"] == f"{MEMBER}/message/{message_id}"
        assert family_facade.relayed_reply(facade, message_id)["worker_id"] == MEMBER

    cursor = repo.events_after(MEMBER, 0)["cursor"]
    result = resident_protocol.respond_resident_user_input(facade_id, "CASE-A", {
        "message_id": native["message_id"], "answers": {"scope": {"answers": ["定向"]}}})
    assert result["status"] == "answered"
    save("userInputResponse", status="answered", answers={"scope": {"answers": ["定向"]}})
    events = repo.events_after(MEMBER, cursor)["events"]
    assert any(event["event_type"] == resident_conversation_records.MESSAGE_UPDATED
               and event["payload"]["message"]["message_id"] ==native["message_id"] for event in events)
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [asked["message_id"]]


def test_native_request_expires_with_its_turn(marked_round, allowed):
    """原生请求所在轮次结束后即失效，不再列出"""
    facade, repo, outer, inner, native, service, save = marked_round
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [native["message_id"]]
    save("userInputResponse", status="expired")
    assert _pending("CASE-A") == []


def test_cancelled_relay_drops_its_native_request(marked_round, allowed):
    """门面原请求取消后，成员的原生请求不再列出"""
    facade, repo, outer, inner, native, service, save = marked_round
    assert len(_pending("CASE-A")) == 1
    repo.set_input_state(MEMBER, inner["message_id"], "cancelled")
    repo.set_input_state(facade["resident_id"], outer["message_id"], "cancelled")
    assert _pending("CASE-A") == []


def test_reading_at_the_facade_needs_conversation_and_answer_rights(marked_round, monkeypatch, allowed):
    """门面处必须同时能读对话和回答，缺一不列"""
    _, _, _, _, native, _, _ = marked_round
    monkeypatch.setattr(permission_system, "allows",
                        lambda actor, instruction, arguments=None: instruction != "GetConversation")
    assert _pending("CASE-A") == []
    monkeypatch.setattr(permission_system, "allows",
                        lambda actor, instruction, arguments=None: instruction != "AnswerResidentRequest")
    assert _pending("CASE-A") == []
    monkeypatch.setattr(permission_system, "allows", lambda actor, instruction, arguments=None: True)
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [native["message_id"]]


def test_an_unindexed_message_is_repaired_before_the_read(resident_record, monkeypatch, allowed):
    """登记失败的消息留下修复项，下次读取先补齐；补不上则报错而不返回残缺列表"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    original = user_pending_questions.rows_for
    monkeypatch.setattr(user_pending_questions, "rows_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    source = _ask(repo, resident_id, "CASE-A")
    with pytest.raises(user_pending_questions.IndexNotReady) as refused:
        _pending("CASE-A")
    assert resident_id not in str(refused.value) and source["message_id"] not in str(refused.value)
    monkeypatch.setattr(user_pending_questions, "rows_for", original)
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [source["message_id"]]
    assert [item["source_message_id"] for item in _pending("CASE-A")] == [source["message_id"]]


def test_a_store_with_history_is_not_ready_until_backfilled(resident_record, monkeypatch, allowed):
    """已有历史的库在回填前报未就绪；回填幂等，只登记仍在等待的问题"""
    from management_portal.society_service.state_store import get_lineage_data_store

    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    waiting = _ask(repo, resident_id, "CASE-A")
    answered = _ask(repo, resident_id, "CASE-A")
    _answer(repo, resident_id, answered)
    store = get_lineage_data_store()
    store.database.write(lambda connection: (
        connection.execute("DELETE FROM user_pending_requests"),
        connection.execute("DELETE FROM lineage_meta WHERE key=%s", (user_pending_questions.READY_KEY,)),
    ))
    try:
        with pytest.raises(user_pending_questions.IndexNotReady) as refused:
            _pending("CASE-A")
        assert resident_id not in str(refused.value)  # Objects stay in the server log.
        # One position per batch and no time left: each step indexes one batch, then resumes.
        monkeypatch.setattr(user_pending_questions, "BACKFILL_BATCH_SIZE", 1)
        steps = 1
        while not user_pending_questions.backfill_step(time_budget_seconds=0):
            steps += 1
            assert steps < 200
        assert steps > 1
        with store.database.read() as connection:
            indexed = connection.execute("SELECT source_message_id FROM user_pending_requests").fetchall()
        assert [row["source_message_id"] for row in indexed] == [waiting["message_id"]]
        assert [item["source_message_id"] for item in _pending("CASE-A")] == [waiting["message_id"]]
        assert user_pending_questions.backfill_step() is True
        assert [item["source_message_id"] for item in _pending("CASE-A")] == [waiting["message_id"]]
    finally:
        store.database.write(user_pending_questions.mark_ready)


def test_named_recipient_users_are_asked(resident_record, allowed):
    """消息明确列出的收件 User 也会收到"""
    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    asked = resident_input_queue_domain.build_message("agent", CARD, resident_id)
    asked["recipients"] = [{"kind": "user", "id": "CASE-B"}]
    rows = user_pending_questions.rows_for(resident_id, asked, repo)
    assert [(row[0], row[3], row[5]) for row in rows] == [("case-b", resident_id, "question_set")]


def test_backfill_thread_runs_to_ready_once(resident_record, monkeypatch, allowed):
    """回填后台线程连续续批直到就绪，同一进程只起一个"""
    from management_portal.society_service.state_store import get_lineage_data_store

    resident_id = resident_record["resident_id"]
    repo = resident_conversation_stream.get_repository()
    waiting = _ask(repo, resident_id, "CASE-A")
    store = get_lineage_data_store()
    store.database.write(lambda connection: (
        connection.execute("DELETE FROM user_pending_requests"),
        connection.execute("DELETE FROM lineage_meta WHERE key=%s", (user_pending_questions.READY_KEY,)),
    ))
    try:
        monkeypatch.setattr(user_pending_questions, "BACKFILL_BATCH_SIZE", 1)
        assert user_pending_questions.start_backfill(work_seconds=0, rest_seconds=0) is False
        thread = user_pending_questions._BACKFILL_THREAD
        assert user_pending_questions.start_backfill(work_seconds=0, rest_seconds=0) is False
        assert user_pending_questions._BACKFILL_THREAD is thread
        thread.join(timeout=60)
        assert not thread.is_alive()
        assert user_pending_questions.start_backfill() is True
        assert [item["source_message_id"] for item in _pending("CASE-A")] == [waiting["message_id"]]
    finally:
        store.database.write(user_pending_questions.mark_ready)
