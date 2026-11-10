"""Optional Chat annotations cannot break conversations or caller transactions.

:module: resident.conversation
:importance: P1
:design: decision_b1ab45c4b3ec
"""
from __future__ import annotations

import pytest
from psycopg import OperationalError

from management_common.postgres_database import PostgresConnection
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service.resident_conversation_records import (
    immutable_message_fields,
    message_for_storage,
    message_from_storage,
    protocol_message,
)


AT = "2026-11-10T00:00:00Z"


def _conversation(store, suffix):
    agent_id = f"resident_{suffix}"
    execution_id = f"execution_{suffix}"
    store.ensure_agent(agent_id)
    store.begin_execution(execution_id=execution_id, agent_id=agent_id,
                          actor_ref="sample_admin", provider="codex", started_at=AT)
    for message_id, role, content in ((f"input_{suffix}", "user", "question"),
                                      (f"answer_{suffix}", "agent", f"answer {suffix}")):
        segment_id = store.put_segment(
            segment_type="input" if role == "user" else "model", content=content,
            created_by_agent_id=agent_id, execution_id=execution_id if role == "agent" else "",
            metadata={"kind": "user_message" if role == "user" else "final_answer", "message": {
                "message_id": message_id, "role": role, "created_at": AT,
                "author_id": "sample_admin" if role == "user" else agent_id,
                **({"execution_id": execution_id, "reply_to_message_id": f"input_{suffix}"} if role == "agent" else {}),
            }},
        )
        store.append_agent_segments(agent_id, [segment_id], actor_ref="sample_admin")
    session = store.ensure_provider_session(provider="codex", backend_route_id="one",
        backend_session_id=f"session_{suffix}", agent_id=agent_id, actor_ref="sample_admin")
    store.bind_execution_provider_session(execution_id, provider_session_ref=session["provider_session_ref"])
    store.finish_execution(execution_id, status="completed", usage={
        "input_tokens": 3, "output_tokens": 2, "model": "recorded-model",
    })
    return agent_id


def _break_annotation_query(monkeypatch):
    original = PostgresConnection.execute

    def broken(connection, statement, parameters=None, **kwargs):
        if statement.startswith("SELECT e.execution_id,e.usage_json,e.final_message_id"):
            # A real PostgreSQL error, including its aborted-transaction effect.
            statement = "SELECT e.request_json FROM executions e WHERE e.execution_id=ANY(%s)"
        return original(connection, statement, parameters, **kwargs)

    monkeypatch.setattr(PostgresConnection, "execute", broken)


@pytest.mark.parametrize("transaction_kind", ["snapshot", "transaction"])
def test_annotation_sql_failure_preserves_chats_and_caller_transaction(monkeypatch, caplog, transaction_kind):
    """附加查询缺列时正文、批量对话、分页与调用者的事务继续有效"""
    store = AgentDataStore()
    agents = [_conversation(store, suffix) for suffix in ("first", "second")]
    with monkeypatch.context() as faults:
        _break_annotation_query(faults)
        with getattr(store.database, transaction_kind)() as connection:
            if transaction_kind == "transaction":
                connection.execute("INSERT INTO agent_store_meta(key,value) VALUES('annotation_test','kept')")
            messages = store.read_chat_messages_many(agents)
            for agent_id in agents:
                question, answer = messages[agent_id]
                assert question["content_parts"] == [{"type": "text", "text": "question"}]
                assert "execution_annotations_status" not in question
                assert answer["content_parts"] == [{"type": "text", "text": f"answer {agent_id.removeprefix('resident_')}"}]
                assert answer["execution_annotations_status"] == "unavailable"
                assert "usage" not in answer
                assert "generation_source" not in answer
                assert answer["reply_to_message_id"].startswith("input_")
                assert answer["conversation_position"]["resident_id"] == agent_id
            page = store.read_chat_message_page(agents[0], limit=1)
            assert page["messages"][0]["message_id"] == "answer_first"
            assert page["next_before_ordinal"] is not None
            assert connection.execute("SELECT 1 AS usable").fetchone()["usable"] == 1
            if transaction_kind == "transaction":
                assert connection.execute("SELECT value FROM agent_store_meta WHERE key='annotation_test'").fetchone()["value"] == "kept"
    assert "Chat execution annotations unavailable" in caplog.text
    assert "UndefinedColumn" in caplog.text
    with store.database.read() as connection:
        if transaction_kind == "transaction":
            assert connection.execute("SELECT value FROM agent_store_meta WHERE key='annotation_test'").fetchone()["value"] == "kept"
        columns = connection.execute("SELECT * FROM executions LIMIT 0").description
        assert "request_json" not in {column.name for column in columns}
    # Recovery reads the current facts; no error is stored or cached as a message.
    recovered = store.read_chat_messages(agents[0])[-1]
    assert "execution_annotations_status" not in recovered
    assert recovered["usage"]["input_tokens"] == 3
    assert recovered["generation_source"] == {
        "execution_id": "execution_first", "provider": "codex", "account_id": "one",
        "model": "recorded-model", "model_evidence": "provider_reported",
    }


def test_bad_execution_annotations_leave_other_messages_and_protocol_readable(caplog):
    """单条执行的坏用量仅降级该执行，状态可呈现且不进入消息事实和身份"""
    store = AgentDataStore()
    agents = [_conversation(store, suffix) for suffix in ("bad", "good")]
    store.database.write(lambda connection: connection.execute(
        "UPDATE executions SET usage_json='[]' WHERE execution_id='execution_bad'"))
    messages = store.read_chat_messages_many(agents)
    bad = messages[agents[0]][-1]
    good = messages[agents[1]][-1]
    assert bad["execution_annotations_status"] == "unavailable"
    assert bad["content_parts"] == [{"type": "text", "text": "answer bad"}]
    assert "execution_annotations_status" not in good
    assert good["usage"]["output_tokens"] == 2
    assert good["generation_source"]["model"] == "recorded-model"
    public = message_from_storage(bad)
    assert public["execution_annotations_status"] == "unavailable"
    assert protocol_message(public)["execution_annotations_status"] == "unavailable"
    assert "execution_annotations_status" not in message_for_storage(public)
    healthy = {key: value for key, value in public.items() if key != "execution_annotations_status"}
    assert immutable_message_fields(public) == immutable_message_fields(healthy)
    assert "Chat execution annotations invalid for execution_bad" in caplog.text


def test_required_message_content_failure_is_not_hidden(monkeypatch):
    """正文读取失败继续报错，不伪装成空会话或成功快照"""
    from management_portal.execution import agent_data_chat

    store = AgentDataStore()
    agent_id = _conversation(store, "required")

    def unavailable(*args):
        raise OperationalError("message content unavailable")

    monkeypatch.setattr(agent_data_chat, "message_contents", unavailable)
    with pytest.raises(OperationalError, match="message content unavailable"):
        store.read_chat_messages(agent_id)


def test_annotation_failure_preserves_complete_resident_snapshot(monkeypatch):
    """附加查询失败时聊天快照仍带正文、状态、队列及历史位置"""
    from management_portal.society_service.resident_conversation_stream import ResidentConversationRepository
    from management_portal.society_service.state_store import LineageDataStore
    from tests.cases.database_test_support import register_owned_workspace

    store = AgentDataStore()
    agent_id = _conversation(store, "111111111111")
    repository = ResidentConversationRepository(LineageDataStore(), store)
    repository.state_store.database.write(lambda connection: connection.execute(
        "INSERT INTO residents(resident_id,title,owned_workspace_id) VALUES (%s,%s,%s)",
        (agent_id, "Chat fixture", register_owned_workspace(connection, agent_id)),
    ))
    _break_annotation_query(monkeypatch)
    snapshot = repository.snapshot(agent_id, history_limit=1)
    assert snapshot["resident_id"] == agent_id
    assert snapshot["messages"][0]["content_parts"] == [{"type": "text", "text": "answer 111111111111"}]
    assert snapshot["messages"][0]["execution_annotations_status"] == "unavailable"
    assert snapshot["next_page_token"]
    assert isinstance(snapshot["state"], dict)
    assert isinstance(snapshot["queue"], dict)
