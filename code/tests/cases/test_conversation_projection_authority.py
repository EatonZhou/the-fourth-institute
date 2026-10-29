from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from management_common.json_columns import json_value
from management_common.postgres_database import PostgresConnection
from management_portal.execution.agent_data_store import AgentDataStore
from management_portal.society_service import resident_conversation_event_relay
from management_portal.society_service.resident_conversation_stream import (
    ResidentConversationRepository,
    )
from management_portal.society_service.resident_conversation_event_relay import (
    ResidentConversationEventRelay,
)
from management_portal.society_service.resident_storage import RESIDENT_RECORD_SCHEMA
from management_portal.society_service.state_store import (
    LINEAGE_DATA_SCHEMA,
    LineageDataStore,
)
from management_portal.society_service import resident_conversation_records
from tests.cases.database_test_support import register_owned_workspace


RESIDENT = "resident_aaaaaaaaaaaa"


def _create_resident(connection, resident_id: str = RESIDENT) -> None:
    # Seed only the identity needed by Conversation storage and the owned
    # Workspace every live Resident row names. Lifecycle is outside these
    # repository contracts.
    connection.execute(
        """
        INSERT INTO residents(
            resident_id, title, backend,
            created_at, updated_at, created_by, owned_workspace_id
        ) VALUES (%s, '', 'codex', NULL, NULL, '', %s)
        """,
        (resident_id, register_owned_workspace(connection, resident_id)),
    )


def _repository(tmp_path: Path) -> ResidentConversationRepository:
    state_store = LineageDataStore()
    state_store.database.write(_create_resident)
    agent_data = AgentDataStore()
    agent_data.ensure_agent(RESIDENT, actor_ref="sample_admin")
    return ResidentConversationRepository(
        state_store,
        agent_data,
    )


def _message(message_id: str, content: str, at: str, role: str = "user") -> dict[str, Any]:
    return {
        "message_id": message_id,
        "role": "agent" if role == "assistant" else role,
        "content_parts": [{"type": "text", "text": content}],
        **({"author_id": "sample_admin"} if role == "user" else {}),
        "created_at": at,
    }


def test_append_is_ordered_and_idempotent_contracts(tmp_path, subtests, isolated_portal_case):
    """消息追加按写入顺序幂等保存且输入激活后进入序列"""
    with subtests.test(contract='test_append_is_ordered_and_idempotent'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        first = _message("message_1", "first", "2026-08-23T00:05:00Z")
        second = _message("message_2", "second", "2026-08-23T00:00:00Z")

        repository.append_message(RESIDENT, first)
        repository.append_message(RESIDENT, first)
        repository.append_message(RESIDENT, second)

        assert [item["message_id"] for item in repository.read_messages(RESIDENT)] == [
            "message_1",
            "message_2",
        ]
        events = repository.events_after(RESIDENT, 0)["events"]
        assert [event["cursor"] for event in events] == [1, 2]
        assert events[0]["event_type"] == resident_conversation_records.MESSAGE_APPENDED
        assert events[0]["payload"]["message"]["message_id"] == "message_1"
    with subtests.test(contract='test_accepted_input_joins_sequence_only_when_activated'), isolated_portal_case() as case_path:
        repository = _repository(case_path)
        queued = _message("message_queued", "queued", "2026-08-23T00:00:00Z")

        accepted = repository.accept_user_message(RESIDENT, queued)
        assert accepted["message_id"] == queued["message_id"]
        assert repository.message(RESIDENT, queued["message_id"]) == accepted
        assert repository.read_messages(RESIDENT) == []
        with repository.state_store.database.read() as connection:
            assert connection.execute(
                'SELECT COUNT(*) FROM conversation_events WHERE resident_id = %s',
                (RESIDENT,),
            ).fetchone()[0] == 0
        repository.agent_data.fork_agent(source_agent_id=RESIDENT, target_agent_id="resident_bbbbbbbbbbbb", actor_ref="sample_admin", segment_ids=repository.agent_data.sequence_segment_ids(repository.agent_data.trace_sequence_id(RESIDENT)))
        assert repository.agent_data.chat_message(
            "resident_bbbbbbbbbbbb",
            queued["message_id"],
        ) is None

        reply = _message(
            "message_previous_reply",
            "previous reply",
            "2026-08-23T00:01:00Z",
            role="assistant",
        )
        repository.append_message(RESIDENT, reply)
        activated = repository.commit_user_message(
            RESIDENT,
            queued,
            activated_at="2026-08-23T00:02:00Z",
        )

        assert activated["message_id"] == accepted["message_id"]
        assert activated["content_parts"] == accepted["content_parts"]
        assert activated["author_id"] == accepted["author_id"]
        assert activated["created_at"] == accepted["created_at"]
        assert activated["activated_at"] == "2026-08-23T00:02:00Z"
        assert [item["message_id"] for item in repository.read_messages(RESIDENT)] == [
            reply["message_id"],
            queued["message_id"],
        ]
    with subtests.test(contract='test_message_identity_rejects_different_content'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            _message("message_1", "first", "2026-08-23T00:00:00Z"),
        )
        with pytest.raises(ValueError, match="different immutable content"):
            repository.append_message(
                RESIDENT,
                _message("message_1", "changed", "2026-08-23T00:00:00Z"),
            )
    with subtests.test(contract='test_message_requires_canonical_content_parts'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)

        with pytest.raises(ValueError, match="requires content_parts"):
            repository.append_message(
                RESIDENT,
                {
                    "message_id": "message_1",
                    "role": "user",
                    "author_id": "sample_admin",
                    "created_at": "2026-08-23T00:00:00Z",
                },
            )
    with subtests.test(contract='test_repository_persists_only_canonical_reply_identity'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            {
                **_message(
                    "message_reply",
                    "done",
                    "2026-08-23T00:00:00Z",
                    role="assistant",
                ),
                "reply_to_message_id": "message_input",
                "user_message_id": "legacy_input",
            },
        )

        stored = repository.agent_data.chat_message(RESIDENT, "message_reply")
        assert stored is not None
        assert stored["reply_to_message_id"] == "message_input"
        assert "user_message_id" not in stored
    with subtests.test(contract='test_snapshot_and_events_share_one_cursor'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            _message("message_1", "hello", "2026-08-23T00:00:00Z"),
        )
        repository.set_input_state(RESIDENT, "message_1", "running")
        repository.set_state(
            RESIDENT,
            {
                "work_state": "running",
                "active_message_id": "message_1",
                "queued_count": 0,
                "intake_state": "open",
            },
        )
        activity = {
            "activity_id": "activity:thinking",
            "resident_id": RESIDENT,
            "message_id": "message_active",
            "kind": "thinking",
            "status": "running",
            "title": "Thinking",
            "content": "",
            "started_at": "2026-08-28T00:00:00Z",
            "updated_at": "2026-08-28T00:00:00Z",
        }
        repository.set_activities(RESIDENT, [activity])

        snapshot = repository.snapshot(RESIDENT)
        events = repository.events_after(RESIDENT, 1)

        # Activity is current state only; it does not move the cursor.
        assert snapshot["cursor"] == 3
        assert snapshot["messages"][0]["message_id"] == "message_1"
        assert snapshot["input_states"][0]["state"] == "running"
        assert snapshot["state"]["work_state"] == "running"
        assert snapshot["activities"] == [activity]
        assert [event["cursor"] for event in events["events"]] == [2, 3]
    with subtests.test(contract='test_stale_lineage_schema_is_rejected_without_runtime_migration'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        def install_stale_schema(connection) -> None:
            connection.execute(
                "UPDATE lineage_meta SET value = %s WHERE key = 'schema'",
                ("tfi.lineage-data.v4",),
            )

        repository.state_store.database.write(install_stale_schema)
        try:
            with pytest.raises(RuntimeError, match="does not match its schema"):
                LineageDataStore()
        finally:
            repository.state_store.database.write(lambda connection: connection.execute(
                "UPDATE lineage_meta SET value = %s WHERE key = 'schema'", (LINEAGE_DATA_SCHEMA,),
            ))


def test_each_instance_relays_events_from_the_shared_durable_log(
    tmp_path: Path,
) -> None:
    """各实例从共享持久日志转发启动后的会话事件"""
    repository = _repository(tmp_path)
    repository.append_message(
        RESIDENT,
        _message("message_existing", "before relay", "2026-08-23T00:00:00Z"),
    )

    class Publisher:
        def __init__(self) -> None:
            self.items: list[tuple[str, dict[str, Any]]] = []

        def publish(self, subject: str, payload: dict[str, Any]) -> None:
            self.items.append((subject, payload))

    first_publisher = Publisher()
    second_publisher = Publisher()
    relays = [
        ResidentConversationEventRelay(
            repository.state_store,
            first_publisher,
            poll_seconds=0.01,
        ),
        ResidentConversationEventRelay(
            repository.state_store,
            second_publisher,
            poll_seconds=0.01,
        ),
    ]
    for relay in relays:
        relay.start()
    try:
        foreign_writer = ResidentConversationRepository(
            repository.state_store,
            repository.agent_data,
        )
        foreign_writer.append_message(
            RESIDENT,
            _message("message_new", "after relay", "2026-08-23T00:00:01Z"),
        )
        for _attempt in range(100):
            if first_publisher.items and second_publisher.items:
                break
            threading.Event().wait(0.01)

        for publisher in (first_publisher, second_publisher):
            assert len(publisher.items) == 1
            subject, payload = publisher.items[0]
            assert subject == f"portal.resident.changed.{RESIDENT}.conversation"
            assert payload["kind"] == "conversation_event"
            assert payload["event"]["cursor"] == 2
            assert payload["event"]["payload"]["message"]["message_id"] == "message_new"
    finally:
        for relay in relays:
            relay.stop()


def test_event_relay_revisits_a_transaction_that_commits_out_of_sequence(tmp_path):
    """事件转发器补读序号较早但提交较晚的事务"""
    repository = _repository(tmp_path)
    store = repository.state_store
    other = "resident_bbbbbbbbbbbb"
    store.database.write(lambda connection: _create_resident(connection, other))
    repository.set_state(RESIDENT, {"work_state": "idle"})
    repository.set_state(other, {"work_state": "idle"})
    relay = ResidentConversationEventRelay(store, None)
    relay._sequence = relay._latest_sequence()
    inserted, release = threading.Event(), threading.Event()
    errors = []
    def append(connection, resident):
        connection.execute(
            "INSERT INTO conversation_events(resident_id,cursor,event_type,subject_id,occurred_at,payload_json) VALUES (%s,2,'state.changed','','now','{}')",
            (resident,),
        )
    def late_commit():
        try:
            with store.database.transaction() as connection:
                append(connection, RESIDENT)
                inserted.set()
                assert release.wait(5)
        except BaseException as exc:
            errors.append(exc)
    writer = threading.Thread(target=late_commit)
    writer.start()
    try:
        assert inserted.wait(5), errors
        store.database.write(lambda connection: append(connection, other))
        first = relay._events_after(relay._sequence)
        assert [event['resident_id'] for _, event in first] == [other]
        for sequence, event in first:
            relay._remember(sequence, event)
        release.set()
        writer.join(5)
        assert not errors and not writer.is_alive()
        late = relay._events_after(relay._sequence)
        assert [event['resident_id'] for _, event in late] == [RESIDENT]
        for sequence, event in late:
            relay._remember(sequence, event)
        assert relay._events_after(relay._sequence) == []
    finally:
        release.set()
        writer.join(5)


def test_event_relay_idle_poll_does_not_read_event_content(tmp_path, monkeypatch):
    """事件转发器空闲轮询不读取事件正文"""
    repository = _repository(tmp_path)
    repository.append_message(
        RESIDENT,
        _message("message_existing", "existing content " * 4096, "2026-08-23T00:00:00Z"),
    )
    relay = ResidentConversationEventRelay(repository.state_store, None)
    relay._sequence = relay._latest_sequence()
    queries = []
    execute = PostgresConnection.execute

    def record_query(connection, statement, parameters=None):
        queries.append(statement.lower())
        return execute(connection, statement, parameters)

    monkeypatch.setattr(PostgresConnection, "execute", record_query)

    def assert_idle_poll():
        queries.clear()
        assert relay._events_after(relay._sequence) == []
        assert queries
        assert any("conversation_event_delivery" in query for query in queries)
        assert all("conversation_events" not in query for query in queries)
        assert all("payload_json" not in query and "join" not in query for query in queries)

    assert_idle_poll()
    repository.append_message(
        RESIDENT,
        _message("message_new", "new content", "2026-08-23T00:00:01Z"),
    )
    events = relay._events_after(relay._sequence)
    assert [event["payload"]["message"]["message_id"] for _, event in events] == ["message_new"]
    for sequence, event in events:
        relay._remember(sequence, event)
    assert_idle_poll()


def test_event_relay_bounds_seen_keys_while_draining_batches(tmp_path, monkeypatch):
    """事件分批排空时限制已见键缓存大小"""
    monkeypatch.setattr(resident_conversation_event_relay, "DELIVERY_LOOKBACK", 3)
    repository = _repository(tmp_path)
    relay = ResidentConversationEventRelay(repository.state_store, None, batch_size=2)
    relay._sequence = relay._latest_sequence()
    for index in range(8):
        repository.append_message(
            RESIDENT,
            _message(f"message_{index}", str(index), "2026-08-23T00:00:00Z"),
        )
    received = []
    for _ in range(5):
        events = relay._events_after(relay._sequence)
        assert len(events) <= 2
        for sequence, event in events:
            received.append(event["payload"]["message"]["message_id"])
            relay._remember(sequence, event)
    assert received == [f"message_{index}" for index in range(8)]
    assert len(relay._seen) == 3
    assert all(sequence > relay._sequence - 3 for sequence in relay._seen.values())


def test_each_instance_relays_live_activity_without_an_event_record(
    tmp_path: Path,
) -> None:
    """每个实例的转发器都实时推送活动且不留事件记录

    另一个写入方更新活动后，两个 Relay 都经 LISTEN 收到并发布 live 事件，Conversation cursor 不变。
    """
    repository = _repository(tmp_path)
    repository.append_message(
        RESIDENT,
        _message("message_existing", "before relay", "2026-08-23T00:00:00Z"),
    )

    class Publisher:
        def __init__(self) -> None:
            self.items: list[tuple[str, dict[str, Any]]] = []

        def publish(self, subject: str, payload: dict[str, Any]) -> None:
            self.items.append((subject, payload))

    publishers = [Publisher(), Publisher()]
    relays = [
        ResidentConversationEventRelay(repository.state_store, publisher, poll_seconds=0.01)
        for publisher in publishers
    ]
    for relay in relays:
        relay.start()
    try:
        for _attempt in range(200):
            if all(relay.health()["live_running"] for relay in relays):
                break
            threading.Event().wait(0.01)
        threading.Event().wait(0.2)  # LISTEN is in place once the thread waits.
        activity = {
            "activity_id": "activity:command", "resident_id": RESIDENT,
            "message_id": "message_existing", "kind": "command", "status": "running",
            "title": "Command", "content": "$ ls",
            "started_at": "2026-08-28T00:00:00Z", "updated_at": "2026-08-28T00:00:00Z",
        }
        ResidentConversationRepository(repository.state_store, repository.agent_data).set_activities(
            RESIDENT, [activity],
        )
        for _attempt in range(300):
            if all(publisher.items for publisher in publishers):
                break
            threading.Event().wait(0.01)
        for publisher in publishers:
            assert len(publisher.items) == 1
            subject, payload = publisher.items[0]
            assert subject == f"portal.resident.changed.{RESIDENT}.conversation"
            event = payload["event"]
            assert event["live"] is True and event["cursor"] == 1
            assert event["payload"]["activities"] == [activity]
        assert repository.events_after(RESIDENT, 0)["cursor"] == 1
    finally:
        for relay in relays:
            relay.stop()


def test_repository_rejects_non_protocol_work_states(tmp_path, subtests, isolated_portal_case):
    """会话仓库拒绝协议外的工作状态"""
    for work_state in ["failed", "closed", "dispatching"]:
        with subtests.test(work_state=work_state), isolated_portal_case() as case_path:
            tmp_path = case_path
            repository = _repository(tmp_path)

            with pytest.raises(ValueError, match="Unsupported Resident work_state"):
                repository.set_state(
                    RESIDENT,
                    {
                        "work_state": work_state,
                        "active_message_id": "message_1",
                        "queued_count": 0,
                        "intake_state": "open",
                    },
                )


def test_repository_accepts_stopping_as_a_work_state_contracts(tmp_path, subtests, isolated_portal_case):
    """会话仓库支持停止中状态并批量读取投影与回复"""
    with subtests.test(contract='test_repository_accepts_stopping_as_a_work_state'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)

        repository.set_state(
            RESIDENT,
            {
                "work_state": "stopping",
                "active_message_id": "message_1",
                "queued_count": 2,
                "intake_state": "closed",
            },
        )

        assert repository.current_projection(RESIDENT)["state"] == {
            "work_state": "stopping",
            "active_message_id": "message_1",
            "queued_count": 2,
            "intake_state": "closed",
        }
    with subtests.test(contract='test_repository_reads_complete_resident_projections_in_one_batch'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.set_state(
            RESIDENT,
            {
                "work_state": "queued",
                "active_message_id": "",
                "queued_count": 0,
                "intake_state": "open",
            },
        )

        projections = repository.current_projections([
            RESIDENT,
            RESIDENT,
            "resident_bbbbbbbbbbbb",
        ])

        assert list(projections) == [RESIDENT]
        assert projections[RESIDENT] == repository.current_projection(RESIDENT)
    with subtests.test(contract='test_terminal_input_can_gain_reply_identity'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            _message("message_1", "question", "2026-08-23T00:00:00Z"),
        )
        repository.append_message(
            RESIDENT,
            {
                **_message("message_2", "answer", "2026-08-23T00:00:01Z", "assistant"),
                "reply_to_message_id": "message_1",
            },
        )
        repository.set_input_state(RESIDENT, "message_1", "completed")
        state = repository.set_input_state(
            RESIDENT,
            "message_1",
            "completed",
        )

        assert state["reply_message_ids"] == ["message_2"]
        assert repository.public_message(RESIDENT, "message_2")["reply_to_message_id"] == "message_1"
    with subtests.test(contract='test_repeating_same_terminal_input_state_is_a_noop'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            _message("message_1", "question", "2026-08-23T00:00:00Z"),
        )
        first = repository.set_input_state(
            RESIDENT,
            "message_1",
            "completed",
        )
        cursor = repository.snapshot(RESIDENT)["cursor"]

        repeated = repository.set_input_state(
            RESIDENT,
            "message_1",
            "completed",
        )

        assert repeated == first
        assert repository.snapshot(RESIDENT)["cursor"] == cursor
    with subtests.test(contract='test_provider_response_is_exposed_as_artifact'), isolated_portal_case() as case_path:
        tmp_path = case_path
        repository = _repository(tmp_path)
        repository.append_message(
            RESIDENT,
            {
                **_message("message_2", "answer", "2026-08-23T00:00:01Z", "assistant"),
                "provider_response": {"value": 42},
            },
        )

        message = repository.public_message(RESIDENT, "message_2")
        reference = next(
            part for part in message["content_parts"]
            if part["type"] == "artifact_reference"
        )
        artifact = repository.artifact(RESIDENT, reference["artifact_id"])
        assert artifact["value"] == {"value": 42}


def test_snapshot_cannot_interleave_message_and_cursor_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快照重试消除消息与游标并发提交的不一致"""
    reader = _repository(tmp_path)
    writer = ResidentConversationRepository(reader.state_store, reader.agent_data)
    read_started = threading.Event()
    release_read = threading.Event()
    write_finished = threading.Event()
    original_read = reader.agent_data.read_chat_message_page
    pages: list[dict] = []

    def blocked_read(
        agent_id: str,
        *,
        before_ordinal: int | None = None,
        limit: int,
    ):
        page = original_read(
            agent_id,
            before_ordinal=before_ordinal,
            limit=limit,
        )
        pages.append(page)
        if len(pages) == 1:
            read_started.set()
            assert release_read.wait(2)
        return page

    monkeypatch.setattr(reader.agent_data, "read_chat_message_page", blocked_read)
    snapshots: list[dict] = []
    snapshot_thread = threading.Thread(
        target=lambda: snapshots.append(reader.snapshot(RESIDENT)),
    )
    snapshot_thread.start()
    assert read_started.wait(2)

    def append() -> None:
        writer.append_message(
            RESIDENT,
            _message("message_1", "hello", "2026-08-23T00:00:00Z"),
        )
        write_finished.set()

    writer_thread = threading.Thread(target=append)
    writer_thread.start()
    try:
        assert write_finished.wait(2)
    finally:
        release_read.set()
        snapshot_thread.join(2)
        writer_thread.join(2)

    assert not snapshot_thread.is_alive()
    assert not writer_thread.is_alive()
    assert len(pages) == 2
    assert pages[0]["messages"] == []
    assert [message["message_id"] for message in snapshots[0]["messages"]] == ["message_1"]
    assert snapshots[0]["cursor"] == 1
    assert writer.snapshot(RESIDENT)["cursor"] == 1


def test_terminal_input_ignores_late_conflicting_state(tmp_path, subtests, isolated_portal_case):
    """已完成输入忽略迟到的冲突状态且不推进游标"""
    for stale_state in ["queued", "running", "failed", "cancelled"]:
        with subtests.test(stale_state=stale_state), isolated_portal_case() as case_path:
            tmp_path = case_path
            repository = _repository(tmp_path)
            repository.append_message(
                RESIDENT,
                _message("message_1", "question", "2026-08-23T00:00:00Z"),
            )
            completed = repository.set_input_state(
                RESIDENT,
                "message_1",
                "completed",
            )
            cursor = repository.snapshot(RESIDENT)["cursor"]

            ignored = repository.set_input_state(
                RESIDENT,
                "message_1",
                stale_state,
                error={"code": "late", "message": "late terminal report"},
            )

            assert ignored == completed
            snapshot = repository.snapshot(RESIDENT)
            assert snapshot["input_states"] == [completed]
            assert snapshot["cursor"] == cursor


def test_large_notification_omits_only_inline_payload() -> None:
    """超大通知仅省略内联载荷并保留游标"""
    event = {
        "schema": "tfi.resident-messages-state.v1",
        "resident_id": RESIDENT,
        "cursor": 4,
        "event_type": resident_conversation_records.MESSAGE_APPENDED,
        "occurred_at": "2026-08-23T00:00:00Z",
        "payload": {"message": {"content_parts": [{"type": "text", "text": "x" * (resident_conversation_records.MAX_INLINE_NOTIFICATION_BYTES + 1)}]}},
    }
    notification = resident_conversation_records.conversation_event_notification(event)
    assert notification["payload_omitted"] is True
    assert notification["cursor"] == 4
    assert "payload" not in notification


@pytest.mark.parametrize("schema", [None, "tfi.resident-messages-state.v0"])
def test_notification_rejects_noncurrent_event_schema(schema: object) -> None:
    """会话通知拒绝非当前版本的事件格式"""
    event = {
        "resident_id": RESIDENT,
        "cursor": 4,
        "event_type": resident_conversation_records.MESSAGE_APPENDED,
        "occurred_at": "2026-08-23T00:00:00Z",
        "payload": {},
    }
    if schema is not None:
        event["schema"] = schema
    with pytest.raises(ValueError, match="unsupported Resident Conversation event schema"):
        resident_conversation_records.conversation_event_notification(event)


def test_snapshot_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """会话快照仅执行只读操作"""
    repository = _repository(tmp_path)
    monkeypatch.setattr(
        repository.state_store.database,
        "write",
        lambda _operation: pytest.fail("snapshot must not write"),
    )
    assert repository.snapshot(RESIDENT)["messages"] == []


def _stored_payloads(repository: ResidentConversationRepository, event_type: str) -> list[dict[str, Any]]:
    with repository.state_store.database.read() as connection:
        return [
            json_value(row["payload_json"])
            for row in connection.execute(
                "SELECT payload_json FROM conversation_events WHERE resident_id = %s AND event_type = %s ORDER BY cursor",
                (RESIDENT, event_type),
            ).fetchall()
        ]


def test_event_log_keeps_message_headers_and_readers_get_the_trace_body(isolated_portal_case) -> None:
    """事件日志仅存消息头且读取时补全 trace 当前正文"""
    with isolated_portal_case() as case_path:
        repository = _repository(case_path)
        repository.append_message(RESIDENT, _message("message_1", "question", "2026-08-23T00:00:00Z"))
        reply = {**_message("message_2", "draft answer", "2026-08-23T00:01:00Z", role="assistant"),
                 "reply_to_message_id": "message_1"}
        repository.append_message(RESIDENT, reply)

        # The log keeps who each message is and what it answers, not its body.
        assert _stored_payloads(repository, resident_conversation_records.MESSAGE_APPENDED) == [
            {"message": {"message_id": "message_1", "role": "user"}},
            {"message": {"message_id": "message_2", "role": "agent", "reply_to_message_id": "message_1"}},
        ]
        events = repository.events_after(RESIDENT, 0)["events"]
        assert [event["payload"]["message"] for event in events] == [
            repository.message(RESIDENT, "message_1"), repository.message(RESIDENT, "message_2"),
        ]

        # A reader gets the message as the trace holds it now.
        repository.update_message(RESIDENT, "message_2", message={
            **reply, "content_parts": [{"type": "text", "text": "final answer"}],
        })
        assert _stored_payloads(repository, resident_conversation_records.MESSAGE_UPDATED) == [
            {"message": {"message_id": "message_2", "role": "agent", "reply_to_message_id": "message_1"}},
        ]
        texts = [
            event["payload"]["message"]["content_parts"][0]["text"]
            for event in repository.events_after(RESIDENT, 1)["events"]
        ]
        assert texts == ["final answer", "final answer"]

        # A message the trace does not have leaves its event without payload.
        repository.state_store.database.write(lambda connection: repository.events.record_event(
            connection, RESIDENT, resident_conversation_records.MESSAGE_APPENDED, {"message": {"message_id": "message_gone", "role": "user"}},
            subject_id="message_gone",
        ))
        missing = repository.events_after(RESIDENT, 3)["events"]
        assert [(event["payload_omitted"], "payload" in event) for event in missing] == [(True, False)]


def test_input_replies_are_the_trace_messages_answering_it(isolated_portal_case) -> None:
    """输入回复标识取自 trace 中实际关联的终态回复"""
    with isolated_portal_case() as case_path:
        repository = _repository(case_path)

        def say(message_id: str, role: str, reply_to: str = "") -> None:
            repository.append_message(RESIDENT, {
                **_message(message_id, message_id, "2026-08-23T00:00:00Z", role=role),
                **({"reply_to_message_id": reply_to} if reply_to else {}),
            })

        say("asked", "user")
        say("progress", "assistant", "asked")
        repository.set_input_state(RESIDENT, "asked", "running")
        assert repository.input_state(RESIDENT, "asked")["reply_message_ids"] == []
        say("answer", "assistant", "asked")
        completed = repository.set_input_state(RESIDENT, "asked", "completed")
        # A completed input's final reply is the Agent's last answer.
        assert completed["reply_message_ids"] == ["answer"]

        say("command", "user")
        say("receipt", "system", "command")
        repository.set_input_state(RESIDENT, "command", "completed")
        say("broken", "user")
        say("partial", "assistant", "broken")
        say("failure_notice", "system", "broken")
        repository.set_input_state(RESIDENT, "broken", "failed", error={"code": "boom"})
        assert {
            item["message_id"]: item["reply_message_ids"] for item in repository.input_states(RESIDENT)
        } == {"asked": ["answer"], "command": ["receipt"], "broken": ["failure_notice"]}

        # The log keeps the state; readers get the replies from the trace.
        assert all("reply_message_ids" not in payload["input_state"]
                   for payload in _stored_payloads(repository, resident_conversation_records.INPUT_STATE_CHANGED))
        finished = {
            event["payload"]["input_state"]["message_id"]: event["payload"]["input_state"]["reply_message_ids"]
            for event in repository.events_after(RESIDENT, 0)["events"]
            if event["event_type"] == resident_conversation_records.INPUT_STATE_CHANGED
            and event["payload"]["input_state"]["state"] != "running"
        }
        assert finished == {"asked": ["answer"], "command": ["receipt"], "broken": ["failure_notice"]}
        assert repository.snapshot(RESIDENT)["input_states"] == repository.inputs._with_replies(
            RESIDENT, [{key: value for key, value in item.items() if key != "reply_message_ids"}
                       for item in sorted(repository.input_states(RESIDENT),
                                          key=lambda item: (item["updated_at"], item["message_id"]))],
        )
