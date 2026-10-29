from __future__ import annotations

from typing import Any

from management_portal.execution import chat_position_index
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import family_facade
from tests.cases.test_agent_data_store import _chat


def _message(identity: str, role: str = "user", text: str = "text") -> dict:
    return {"message_id": identity, "role": role,
            "content_parts": [{"type": "text", "text": text}],
            "created_at": "2026-10-12T00:00:00Z"}


def _page(store: AgentDataStore, resident: str) -> dict:
    page = store.read_chat_message_page(resident, limit=2)
    return {"messages": [(message["message_id"], message["content_parts"][0]["text"]) for message in page["messages"]],
            "ordinals": page["ordinals"], "next_before_ordinal": page["next_before_ordinal"]}


def test_chat_pages_follow_appends_edits_and_clears():
    """Chat 分页随追加、编辑和清空变化，结果与旧的逐段读取一致"""
    store = AgentDataStore()
    resident = "resident_seek_index_test"
    store.display_chat_message(resident, _message("one"))
    store.display_chat_message(resident, _message("two"))
    assert _page(store, resident) == {
        "messages": [("one", "text"), ("two", "text")], "ordinals": [1, 2], "next_before_ordinal": None}
    store.display_chat_message(resident, _message("three", role="agent"))
    assert _page(store, resident) == {
        "messages": [("two", "text"), ("three", "text")], "ordinals": [2, 3], "next_before_ordinal": 2}
    store.update_chat_message(resident, "two", _message("two", text="revised"))
    assert _page(store, resident) == {
        "messages": [("two", "revised"), ("three", "text")], "ordinals": [2, 3], "next_before_ordinal": 2}
    store.clear_chat(resident, actor_ref="test")
    assert _page(store, resident) == {"messages": [], "ordinals": [], "next_before_ordinal": None}


RELAYED = "resident_0123456789ab/message/msg_0123456789abcdef"


def _conversations() -> tuple[AgentDataStore, list[str]]:
    """Two Chats: sends, relays, notices, replies to the actor, a clear."""
    store = AgentDataStore()
    first, second = "resident_summary_first", "resident_summary_second"
    store.display_chat_message(first, _chat("asked", "user", "hello", author_id="CASE-A", created_at="2026-10-13T01:00:00Z"))
    store.display_chat_message(first, _chat("other", "user", "hi", author_id="sample_admin", created_at="2026-10-13T02:00:00Z"))
    relayed = _chat("relayed", "user", "copy", author_id="CASE-A", created_at="2026-10-13T05:00:00Z")
    store.display_chat_message(first, {**relayed, "business_ref": RELAYED})
    reply = _chat("reply", "agent", "for you", created_at="2026-10-13T03:00:00Z")
    store.display_chat_message(first, {**reply, "recipients": [{"kind": "user", "id": "case-a"}]})
    store.display_chat_message(first, {
        "message_id": "turn", "role": "user", "author_id": "CASE-A", "created_at": "2026-10-13T04:00:00Z",
        "content_parts": [{"type": "native_turn", "status": "completed"}],
    })
    store.display_chat_message(second, _chat("cleared", "user", "old", author_id="CASE-A", created_at="2026-10-13T06:00:00Z"))
    store.clear_chat(second, actor_ref="CASE-A")
    store.display_chat_message(second, {
        "message_id": "changes", "role": "agent", "created_at": "2026-10-13T02:30:00Z",
        "recipients": [{"kind": "user", "id": "CASE-A"}], "content_parts": [{"type": "resource_changes", "items": []}],
    })
    store.display_chat_message(second, _chat("after", "user", "new", author_id="@CASE-A", created_at="2026-10-13T01:30:00Z"))
    return store, [first, second]


def _summaries(store: AgentDataStore, agents: list[str]) -> dict[str, Any]:
    relay = family_facade.RELAY_REF.pattern
    requested = [*agents, "resident_summary_missing"]
    store.chat_activity._conversation_summaries_cache.clear()
    return {
        "summaries": store.chat_conversation_summaries(requested, "@CASE-A", excluded_business_ref=relay),
        "unfiltered": store.chat_conversation_summaries(requested, "case-a", finish=False),
        "counts": store.chat_user_counts(requested),
    }


def _activity(store: AgentDataStore, agents: list[str]) -> dict[str, Any]:
    """The Mailbox's activity reads."""
    return {
        "activity": store.chat_actor_activity("CASE-A"),
        "targeted": store.chat_actor_activity("case-a", [*agents, "resident_summary_missing"]),
        "page": store.chat_actor_activity_page("CASE-A", limit=1),
        "next": store.chat_actor_activity_page("CASE-A", after=("2026-10-13T04:00:00Z", agents[0])),
        "other": store.chat_actor_activity_page("sample_admin"),
    }


EMPTY = {"actor_addressed_at": "", "actor_addressed_message_id": "", "last_user_actor": "",
         "last_user_message_at": "", "user_message_count": 0}


def test_conversation_summaries_count_after_the_clear_and_leave_relayed_copies_out():
    """会话摘要和计数从 Chat 位置投影读取，结果与旧的逐段读取一致

    用户消息数、最新用户消息与作者、调用者的请求和发给调用者的消息都只算最近一次清空之后的；
    排除规则去掉 Family 转发副本；读取确实来自投影行。
    """
    store, agents = _conversations()
    first, second = agents
    summaries = _summaries(store, agents)
    assert summaries["counts"] == {first: 4, second: 1, "resident_summary_missing": 0}
    assert {agent: {key: value for key, value in summary.items() if key != "conversation_signature"}
            for agent, summary in summaries["summaries"].items()} == {
        first: {"actor_addressed_at": "2026-10-13T03:00:00Z", "actor_addressed_message_id": "reply",
                "actor_last_user_message_at": "2026-10-13T04:00:00Z", "actor_request_ids": ["asked", "turn"],
                "last_user_actor": "CASE-A", "last_user_message_at": "2026-10-13T04:00:00Z", "user_message_count": 3},
        second: {"actor_addressed_at": "2026-10-13T02:30:00Z", "actor_addressed_message_id": "changes",
                 "actor_last_user_message_at": "2026-10-13T01:30:00Z", "actor_request_ids": ["after"],
                 "last_user_actor": "@CASE-A", "last_user_message_at": "2026-10-13T01:30:00Z", "user_message_count": 1},
        "resident_summary_missing": {**EMPTY, "actor_last_user_message_at": "", "actor_request_ids": []},
    }
    assert summaries["unfiltered"] == {
        first: {"actor_addressed_at": "2026-10-13T03:00:00Z", "actor_addressed_message_id": "reply",
                "actor_requests": [("2026-10-13T01:00:00Z", "asked"), ("2026-10-13T04:00:00Z", "turn"),
                                   ("2026-10-13T05:00:00Z", "relayed")],
                "last_user_actor": "CASE-A", "last_user_message_at": "2026-10-13T05:00:00Z", "user_message_count": 4},
        second: {"actor_addressed_at": "2026-10-13T02:30:00Z", "actor_addressed_message_id": "changes",
                 "actor_requests": [("2026-10-13T01:30:00Z", "after")],
                 "last_user_actor": "@CASE-A", "last_user_message_at": "2026-10-13T01:30:00Z", "user_message_count": 1},
        "resident_summary_missing": {**EMPTY, "actor_requests": []},
    }
    # The reads come from the projection rows, not the trace.
    store.database.write(lambda connection: connection.execute(
        "DELETE FROM chat_message_positions WHERE sequence_id=%s AND segment_type='input'",
        (store.trace_sequence_id(second),)))
    assert store.chat_user_counts([second]) == {second: 0}


def test_kept_activity_follows_relays_replies_clears_and_forks():
    """Mailbox 最近活动随写入保存，结果与旧的从会话轨迹计算一致

    每个会话、每个人的最近活动随消息写入保存在 chat_actor_activity；发送、发给此人的回复、
    转发副本（不算发送者的活动）、清空上下文和 fork 之后，读到的活动时间与旧读法一致，
    并且读取确实来自保存行。
    """
    store, agents = _conversations()
    first, second = agents
    assert _activity(store, agents) == {
        "activity": {first: "2026-10-13T04:00:00Z", second: "2026-10-13T02:30:00Z"},
        "targeted": {first: "2026-10-13T04:00:00Z", second: "2026-10-13T02:30:00Z"},
        "page": [(first, "2026-10-13T04:00:00Z")], "next": [(second, "2026-10-13T02:30:00Z")],
        "other": [(first, "2026-10-13T02:00:00Z")],
    }

    # A fork inherits the activity; later writes, a relayed copy and a clear change it.
    child = "resident_summary_child"
    store.fork_agent(source_agent_id=first, target_agent_id=child, actor_ref="CASE-A",
                     segment_ids=store.sequence_segment_ids(store.trace_sequence_id(first)))
    agents.append(child)
    store.display_chat_message(child, _chat("forked", "user", "more", author_id="CASE-A", created_at="2026-10-13T07:00:00Z"))
    store.display_chat_message(child, {**_chat("copy", "user", "relayed", author_id="sample_admin",
                                               created_at="2026-10-13T08:00:00Z"), "business_ref": RELAYED})
    assert _activity(store, agents) == {
        "activity": {child: "2026-10-13T07:00:00Z", first: "2026-10-13T04:00:00Z", second: "2026-10-13T02:30:00Z"},
        "targeted": {child: "2026-10-13T07:00:00Z", first: "2026-10-13T04:00:00Z", second: "2026-10-13T02:30:00Z"},
        "page": [(child, "2026-10-13T07:00:00Z")], "next": [(second, "2026-10-13T02:30:00Z")],
        "other": [(child, "2026-10-13T02:00:00Z"), (first, "2026-10-13T02:00:00Z")],
    }
    store.clear_chat(first, actor_ref="CASE-A")
    assert _activity(store, agents) == {
        "activity": {child: "2026-10-13T07:00:00Z", second: "2026-10-13T02:30:00Z"},
        "targeted": {child: "2026-10-13T07:00:00Z", second: "2026-10-13T02:30:00Z"},
        "page": [(child, "2026-10-13T07:00:00Z")], "next": [(second, "2026-10-13T02:30:00Z")],
        "other": [(child, "2026-10-13T02:00:00Z")],
    }

    # The reads come from the kept rows.
    trace = store.trace_sequence_id(child)
    store.database.write(lambda connection: connection.execute(
        "DELETE FROM chat_actor_activity WHERE sequence_id=%s", (trace,)))
    assert child not in _activity(store, agents)["activity"]
    with store.database.transaction() as connection:
        chat_position_index.refresh_activity(connection, trace)
    assert _activity(store, agents)["activity"][child] == "2026-10-13T07:00:00Z"


def test_a_trace_whose_mark_is_behind_is_rebuilt_before_it_is_read(monkeypatch):
    """绕过投影的写入让版本标记落后，下次读取前先从轨迹重建这条会话

    发布切换期间不维护投影的写入者追加消息时，投影行、最近活动和版本标记都没有更新；
    读取 Chat、摘要、最近活动和最终回复前先在轨迹行锁内重建这条会话，读到完整结果，
    标记恢复为最新。
    """
    store, agents = _conversations()
    first, second = agents
    trace = store.trace_sequence_id(second)
    with monkeypatch.context() as older:
        # What a writer that does not keep the projection does on an append.
        for name in ("upsert_segments", "upsert_notices", "refresh_sequence", "mark"):
            older.setattr(chat_position_index, name, lambda *arguments: None)
        store.display_chat_message(second, _chat("older", "user", "late", author_id="CASE-A",
                                                created_at="2026-10-13T09:00:00Z"))
        store.display_chat_message(second, _chat("older-reply", "agent", "done", reply_to_message_id="older"))
    with store.database.read() as connection:
        assert chat_position_index.lagging(connection) == [trace]
        assert chat_position_index.lagging(connection, [trace, store.trace_sequence_id(first)]) == [trace]
    assert [message["message_id"] for message in store.read_chat_messages(second)] == ["changes", "after", "older", "older-reply"]
    with store.database.read() as connection:
        assert chat_position_index.lagging(connection) == []

    def lose_the_projection(connection: Any) -> None:
        connection.execute("DELETE FROM chat_message_positions WHERE sequence_id=%s AND source_kind<>'sync'", (trace,))
        connection.execute("DELETE FROM chat_actor_activity WHERE sequence_id=%s", (trace,))
        connection.execute("UPDATE sequences SET updated_by_actor_ref='older-writer' WHERE sequence_id=%s", (trace,))

    for read in (
        lambda: store.chat_user_counts([second]) == {second: 2},
        lambda: _activity(store, agents)["activity"][second] == "2026-10-13T09:00:00Z",
        lambda: store.chat_final_replies_many({second: ["older"]}) == {second: {"older": ("older-reply", "older-reply")}},
        lambda: store.chat_conversation_summaries([second], "CASE-A")[second]["actor_request_ids"] == ["after", "older"],
    ):
        store.database.write(lose_the_projection)
        assert read()
    # A listing rebuilds the traces behind and leaves the current ones alone.
    current = store.trace_sequence_id(first)
    with store.database.read() as connection:
        before = connection.execute(
            "SELECT xmin::text AS version FROM chat_message_positions WHERE sequence_id=%s AND source_kind='sync'",
            (current,)).fetchone()["version"]
    store.database.write(lose_the_projection)
    store.bring_chat_current()
    with store.database.read() as connection:
        assert chat_position_index.current(connection, trace)
        assert connection.execute(
            "SELECT xmin::text AS version FROM chat_message_positions WHERE sequence_id=%s AND source_kind='sync'",
            (current,)).fetchone()["version"] == before
    assert store.chat_user_counts([second]) == {second: 2}


def _model_reply(store: AgentDataStore, agent: str, message_id: str, reply_to: str, kind: str,
                 content: str = "") -> None:
    segment_id = store.put_segment(
        segment_type="model", content=content or message_id, created_by_agent_id=agent,
        metadata={"role": "agent", "kind": kind, "message": {
            "message_id": message_id, "reply_to_message_id": reply_to,
            "created_at": "2026-10-13T00:00:00Z",
            "role": "agent", "parts": [{"type": "text", "text": message_id}],
        }},
    )
    store.append_agent_segments(agent, [segment_id], actor_ref="test-actor")


def test_final_replies_follow_the_read_policy_and_mailbox_groups():
    """从 Chat 位置投影读取的最终回复与旧的逐段读取一致

    最终回复优先取最后的 Agent 回复；重放的 final_answer 按读取策略隐藏；失败输入的最后回复是失败通知；
    工作卡片不算回复；Mailbox 组合的成员由组合消息的回复回答。读取确实来自投影行。
    """
    store = AgentDataStore()
    agent, other = "resident_replies_first", "resident_replies_second"
    for input_id, kinds in (("completed", ("commentary", "final_answer", "commentary")),
                            ("unphased", ("assistant_message", "assistant_message"))):
        store.display_chat_message(agent, _chat(input_id, "user", "do it", author_id="CASE-A"))
        for index, kind in enumerate(kinds):
            _model_reply(store, agent, f"{input_id}-{index}", input_id, kind)
    # A replayed final answer repeats an earlier message; the read policy hides it.
    store.display_chat_message(agent, _chat("replayed", "user", "again", author_id="CASE-A"))
    _model_reply(store, agent, "replayed-answer", "replayed", "final_answer")
    _model_reply(store, agent, "replayed-late", "replayed", "commentary")
    _model_reply(store, agent, "replayed-answer", "replayed", "final_answer", content="replayed again")
    assert [message["message_id"] for message in store.read_chat_messages(agent)].count("replayed-answer") == 1
    store.display_chat_message(agent, _chat("failed", "user", "try", author_id="CASE-A"))
    _model_reply(store, agent, "failed-0", "failed", "commentary")
    store.display_chat_message(agent, _chat("failure-notice", "system", "execution failed", reply_to_message_id="failed"))
    # A work card answers nothing; a Mailbox group's members are answered by the group.
    store.display_chat_message(agent, {
        "message_id": "card", "role": "agent", "reply_to_message_id": "completed", "created_at": "2026-10-13T00:00:00Z",
        "content_parts": [{"type": "native_turn", "status": "completed"}],
    })
    for member in ("member-one", "member-two"):
        store.display_chat_message(other, _chat(member, "user", member, author_id="CASE-A"))
    group = store.display_chat_message(other, _chat("group", "user", "both", author_id="CASE-A"))
    store.merge_message_group(other, ["member-one", "member-two"], group["segment_id"])
    _model_reply(store, other, "group-answer", "group", "final_answer")
    requests = {agent: ["completed", "unphased", "replayed", "failed", "missing"],
                other: ["member-one", "member-two", "group"], "resident_replies_missing": ["completed"]}
    assert store.chat_final_replies_many(requests) == {
        agent: {"completed": ("completed-2", "completed-2"), "unphased": ("unphased-1", "unphased-1"),
                "replayed": ("replayed-late", "replayed-late"), "failed": ("failed-0", "failure-notice")},
        other: {"member-one": ("group-answer", "group-answer"), "member-two": ("group-answer", "group-answer"),
                "group": ("group-answer", "group-answer")},
    }
    # The read comes from the projection rows, not the trace.
    store.database.write(lambda connection: connection.execute(
        "UPDATE chat_message_positions SET reply_candidate=false WHERE sequence_id=%s AND source_kind<>'sync'",
        (store.trace_sequence_id(other),)))
    assert other not in store.chat_final_replies_many(requests)
