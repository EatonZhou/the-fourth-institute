"""Persistent input work provenance, distinct from message delivery status."""
from contextlib import contextmanager

import pytest

from management_portal.execution import execution_inputs
from management_portal.execution.agent_data_store import AgentDataStore, chat_segment_id
from management_portal.execution.appended_input_projection import materialize_delivered_input
from tests.cases.database_test_support import guard_queries
from tests.cases.test_appended_input_projection import delivered_input


AGENT = "resident_111111111111"
PRIMARY = "msg_primary"
UNKNOWN = {"state": "unknown", "kind": "unknown", "delivery": "unknown", "updated_at": ""}
CREATED = "2026-09-11T00:00:00Z"
STARTED = "2026-09-11T00:00:01Z"
FINISHED = "2026-09-11T00:00:04Z"


def reserve(store, execution="execution-primary", *, message=PRIMARY, agent=AGENT, created=CREATED):
    store.reserve_execution(execution_id=execution, agent_id=agent, actor_ref="sample_admin",
                            provider="codex", created_at=created)
    execution_inputs.attach(store, execution, [{"message_id": message, "role": "primary"}])
    return execution


def appended(delivered_input, *, attach_primary=True):
    store, agent, message, _, receipt = delivered_input
    if attach_primary:
        execution_inputs.attach(store, receipt["execution_id"], [{"message_id": PRIMARY, "role": "primary"}])
    materialize_delivered_input(store, receipt)
    return store, agent, message, receipt


@pytest.mark.parametrize("status", ["preparing", "queued", "running", "completed", "failed", "cancelled"])
def test_primary_reserved_work_survives_reopen_without_chat_or_resident_busy(tmp_path, status):
    store = AgentDataStore()
    execution = reserve(store)
    if status != "preparing":
        store.queue_execution(execution)
    if status not in {"preparing", "queued"}:
        store.begin_execution(execution_id=execution, agent_id=AGENT, actor_ref="sample_admin",
                              provider="codex", started_at=STARTED)
    if status in {"completed", "failed", "cancelled"}:
        store.finish_execution(execution, status=status, finished_at=FINISHED)
    expected = {"state": "queued" if status == "preparing" else status, "kind": "primary",
                "execution_id": execution, "primary_message_id": PRIMARY, "delivery": "pending",
                "updated_at": CREATED if status in {"preparing", "queued"} else
                STARTED if status == "running" else FINISHED}
    assert AgentDataStore().input_work_states(AGENT, [PRIMARY]) == {PRIMARY: expected}
    assert store.execution_input_work_states(AGENT, execution) == {PRIMARY: expected}


@pytest.mark.parametrize("status", ["running", "completed", "failed", "cancelled"])
def test_confirmed_append_tracks_its_execution_not_transport_completed(delivered_input, status):
    store, agent, message, receipt = appended(delivered_input)
    if status != "running":
        store.finish_execution(receipt["execution_id"], status=status, finished_at=FINISHED)
    work = AgentDataStore().input_work_states(agent, [message])[message]
    assert work == {"state": status, "kind": "appended", "delivery": "delivered",
                    "execution_id": receipt["execution_id"], "primary_message_id": PRIMARY,
                    "provider_turn_id": receipt["expected_turn_id"],
                    "updated_at": receipt["updated_at"] if status == "running" else FINISHED}
    assert store.execution_input_work_states(agent, receipt["execution_id"])[message] == work


def test_primary_retries_do_not_move_old_delivered_append_to_new_execution(delivered_input):
    store, agent, message, receipt = appended(delivered_input)
    old = receipt["execution_id"]
    store.finish_execution(old, status="failed", finished_at=FINISHED)
    new = reserve(store, "retry-execution", created="2026-11-05T00:00:00Z")
    work = store.input_work_states(agent, [PRIMARY, message])
    assert work[PRIMARY]["execution_id"] == new
    assert work[PRIMARY]["state"] == "queued"
    assert work[message]["execution_id"] == old
    assert work[message]["state"] == "failed"
    old_inputs = store.execution_input_work_states(agent, old)
    assert PRIMARY not in old_inputs
    assert old_inputs[message] == work[message]
    assert set(store.execution_input_work_states(agent, new)) == {PRIMARY}


def test_same_status_snapshot_includes_new_delivery_and_then_turn_invalidation(delivered_input):
    store, agent, message, _, receipt = delivered_input
    execution_inputs.attach(store, receipt["execution_id"], [{"message_id": PRIMARY, "role": "primary"}])
    assert set(store.execution_input_work_states(agent, receipt["execution_id"])) == {PRIMARY}
    materialize_delivered_input(store, receipt)
    first = store.execution_input_work_states(agent, receipt["execution_id"])
    assert first[PRIMARY]["state"] == "running"
    assert first[message]["state"] == "running"
    store.record_input_acceptance(receipt["execution_id"], agent_id=agent, provider_turn_id="next-turn")
    second = store.execution_input_work_states(agent, receipt["execution_id"])
    assert second[PRIMARY]["state"] == "running"
    assert second[message]["state"] == "unknown"
    assert second[message]["provider_turn_id"] == receipt["expected_turn_id"]
    assert second[message]["primary_message_id"] == PRIMARY
    assert second[message]["updated_at"] == ""


@pytest.mark.parametrize("terminal", [False, True])
def test_same_execution_new_provider_turn_cannot_lend_status_to_old_append(delivered_input, terminal):
    store, agent, message, receipt = appended(delivered_input)
    store.record_input_acceptance(receipt["execution_id"], agent_id=agent, provider_turn_id="replacement-turn")
    if terminal:
        store.finish_execution(receipt["execution_id"], status="completed", finished_at=FINISHED)
    work = store.input_work_states(agent, [message])[message]
    assert work == {"state": "unknown", "kind": "appended", "delivery": "delivered",
                    "execution_id": receipt["execution_id"], "primary_message_id": PRIMARY,
                    "provider_turn_id": receipt["expected_turn_id"], "updated_at": ""}
    assert store.execution_input_work_states(agent, receipt["execution_id"])[message] == work


def test_late_receipt_after_turn_changed_never_fabricates_work(delivered_input):
    store, agent, message, _, receipt = delivered_input
    execution_inputs.attach(store, receipt["execution_id"], [{"message_id": PRIMARY, "role": "primary"}])
    store.record_input_acceptance(receipt["execution_id"], agent_id=agent, provider_turn_id="new-turn")
    with pytest.raises(ValueError, match="does not match"):
        materialize_delivered_input(store, receipt)
    assert store.input_work_states(agent, [message]) == {message: UNKNOWN}


def test_confirmed_late_delivery_after_execution_ended_uses_frozen_terminal_fact(delivered_input):
    store, agent, message, _, receipt = delivered_input
    execution_inputs.attach(store, receipt["execution_id"], [{"message_id": PRIMARY, "role": "primary"}])
    store.finish_execution(receipt["execution_id"], status="failed", finished_at=FINISHED)
    materialize_delivered_input(store, receipt)
    work = store.input_work_states(agent, [message])[message]
    assert work["state"] == "failed"
    assert work["delivery"] == "delivered"
    assert work["updated_at"] == FINISHED
    assert store.execution_input_work_states(agent, receipt["execution_id"])[message] == work


def test_provider_acceptance_and_group_member_keep_exact_primary(delivered_input):
    store, agent, _, _, receipt = delivered_input
    execution_inputs.attach(store, receipt["execution_id"], [
        {"message_id": PRIMARY, "role": "primary"},
        {"message_id": "group-member", "role": "member"},
        {"message_id": "info-only", "role": "info"},
    ])
    work = store.input_work_states(agent, [PRIMARY, "group-member", "info-only"])
    assert work[PRIMARY]["delivery"] == "delivered"
    assert work["group-member"] == work[PRIMARY]
    assert work["info-only"] == UNKNOWN


@pytest.mark.parametrize("defect", ["missing_relation", "wrong_control", "wrong_source_agent", "wrong_execution_agent"])
def test_misattributed_or_partial_append_evidence_is_unknown(delivered_input, defect):
    store, agent, message, receipt = appended(delivered_input)
    def corrupt(connection):
        if defect == "missing_relation":
            connection.execute("DELETE FROM segment_relations WHERE operation_id=%s", (receipt["control_id"],))
        elif defect == "wrong_control":
            connection.execute("UPDATE segment_relations SET operation_id='another-control' WHERE operation_id=%s",
                               (receipt["control_id"],))
        elif defect == "wrong_source_agent":
            connection.execute("UPDATE segments SET created_by_agent_id='other-agent' WHERE segment_id=%s",
                               (chat_segment_id(agent, message),))
        else:
            connection.execute("UPDATE executions SET agent_id='other-agent' WHERE execution_id=%s",
                               (receipt["execution_id"],))
    store.database.write(corrupt)
    assert store.input_work_states(agent, [message]) == {message: UNKNOWN}


def test_historical_delivery_without_primary_reservation_does_not_guess_work(delivered_input):
    store, agent, message, receipt = appended(delivered_input, attach_primary=False)
    work = store.input_work_states(agent, [message])[message]
    assert work == {"state": "unknown", "kind": "appended", "delivery": "delivered",
                    "execution_id": receipt["execution_id"], "provider_turn_id": receipt["expected_turn_id"],
                    "updated_at": ""}


def test_json_metadata_text_storage_has_the_same_work_contract(delivered_input):
    store, agent, message, receipt = appended(delivered_input)
    def legacy_storage(connection):
        # Shadow only the persisted Segment facts, without current-schema JSONB
        # expression indexes preventing the isolated legacy TEXT fixture.
        connection.execute("CREATE TEMP TABLE legacy_input_segments ON COMMIT DROP AS TABLE segments")
        connection.execute("ALTER TABLE legacy_input_segments ALTER COLUMN metadata_json TYPE TEXT "
                           "USING metadata_json::text")
        connection.execute("CREATE TEMP VIEW segments AS SELECT * FROM legacy_input_segments")
        try:
            work = store.input_work_states(agent, [message])[message]
            assert work["kind"] == "appended"
            assert work["state"] == "running"
            assert work["provider_turn_id"] == receipt["expected_turn_id"]
            assert store.execution_input_work_states(agent, receipt["execution_id"])[message] == work
        finally:
            connection.execute("DROP VIEW pg_temp.segments")
    store.database.write(legacy_storage)


def test_ambiguous_primary_and_wrong_agent_do_not_leak_or_guess(delivered_input):
    store, agent, message, receipt = appended(delivered_input)
    execution_inputs.attach(store, receipt["execution_id"], [{"message_id": "another-primary", "role": "primary"}])
    assert store.input_work_states(agent, [PRIMARY]) == {PRIMARY: UNKNOWN}
    assert store.input_work_states(agent, [message])[message]["state"] == "unknown"
    assert store.input_work_states("other-agent", [PRIMARY, message]) == {PRIMARY: UNKNOWN, message: UNKNOWN}
    assert store.execution_input_work_states("other-agent", receipt["execution_id"]) == {}
    assert store.execution_input_work_states(agent, "missing-execution") == {}


def test_missing_message_and_unmaterialized_delivery_are_explicit_unknowns(delivered_input):
    store, agent, message, _, _ = delivered_input
    assert store.input_work_states(agent, [message, "missing", message]) == {message: UNKNOWN, "missing": UNKNOWN}
    assert store.input_work_states(agent, []) == {}


def test_page_query_is_batched_and_does_not_read_payload_or_other_owner(tmp_path, monkeypatch):
    store = AgentDataStore()
    execution = reserve(store)
    members = [f"member-{index}" for index in range(501)]
    execution_inputs.attach(store, execution, [{"message_id": message, "role": "member"} for message in members])
    statements = []
    original = store.database.read_transaction
    @contextmanager
    def guarded():
        with original() as connection:
            yield guard_queries(connection, forbidden=("payloads", "society.", "scheduler."), statements=statements)
    monkeypatch.setattr(store.database, "read_transaction", guarded)
    work = store.input_work_states(AGENT, members)
    assert len(work) == 501
    assert all(item["state"] == "queued" and item["primary_message_id"] == PRIMARY for item in work.values())
    assert len(statements) <= 8, statements
    statements.clear()
    work = store.execution_input_work_states(AGENT, execution)
    assert set(work) == {PRIMARY, *members}
    assert len(statements) <= 8, statements
