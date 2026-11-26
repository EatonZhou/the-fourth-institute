"""Retire old Family dispatch intents using Conversation-owned evidence."""

import pytest

from management_common.json_columns import json_text
from management_portal.society_service import (
    family_strategy_state, family_task_dispatch_storage, resident_conversation_stream,
    resident_input_queue_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_family_familize import charters, lineage  # noqa: F401
from tests.cases.test_family_tasks import configured_family  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


REQUEST = "msg_" + "1" * 32
TASK = "msg_" + "2" * 32
TEXT = "Frozen original task"
QUEUE_REF = "duty/family-queue"


@pytest.mark.parametrize(("trigger", "state", "envelope", "root_state", "reason"), [
    ("queue", "completed", "historical", None, None),
    ("queue", "queued", "historical", None, "invalid_frozen_intent"),
    ("queue", "completed", "wrong_ref", None, "invalid_frozen_intent"),
    ("queue", "completed", "wrong_intent_ref", None, "invalid_frozen_intent"),
    ("queue", "completed", "wrong_body", None, "invalid_frozen_intent"),
    ("queue", "completed", "wrong_author", None, "invalid_frozen_intent"),
    ("queue", "completed", "missing", None, "incomplete_acceptance_receipt"),
    ("queue", None, "missing", None, "unaccepted_frozen_intent"),
    ("route", "cancelled", "missing", "cancelled", None),
    ("route", "cancelled", "missing", "queued", "incomplete_acceptance_receipt"),
    ("review", "cancelled", "missing", None, None),
    ("review", "cancelled", "wrong_body", None, "invalid_frozen_intent"),
])
def test_legacy_retirement_requires_exact_terminal_evidence(
    configured_family, request, trigger, state, envelope, root_state, reason,
):
    graph, block = configured_family
    lineage_id = graph["lineage_id"]
    facade = block["family"]["facade"]
    target = block["family"]["router" if trigger == "route" else "steward"]
    repository = resident_conversation_stream.get_repository()
    if root_state:
        root = resident_input_queue_domain.build_message("user", "Original request", "sample_admin", message_id=REQUEST)
        repository.accept_user_message(facade, root)
        repository.set_input_state(facade, REQUEST, root_state)

    canonical_ref = f"family-route/{facade}/message/{REQUEST}" if root_state else f"duty/family-{trigger}"
    if envelope != "missing":
        author = block["family"]["router"] if envelope == "wrong_author" else facade
        stored_ref = "duty/another-task" if envelope == "wrong_ref" else ""
        if trigger == "review":
            stored_ref = canonical_ref
        body = "Another task" if envelope == "wrong_body" else TEXT
        message = resident_input_queue_domain.build_message("user", body, author, message_id=TASK)
        if stored_ref:
            message["business_ref"] = stored_ref
        repository.commit_user_message(target, message)
    if state:
        repository.set_input_state(target, TASK, state)
    intent = {
        "request": {"facade": facade, "target": target, "trigger": trigger,
                    "business_ref": QUEUE_REF + "/other" if envelope == "wrong_intent_ref" else canonical_ref,
                    "facts": {"message_id": REQUEST} if root_state else {}},
        "text": TEXT,
    }
    database = get_lineage_data_store().database
    def remove_fixture_table():
        with database.transaction() as connection:
            connection.execute("DROP TABLE IF EXISTS family_task_dispatches")

    with database.transaction() as connection:
        family_task_dispatch_storage.create_schema(connection)
        request.addfinalizer(remove_fixture_table)
        connection.execute("INSERT INTO family_task_dispatches VALUES(%s,%s,%s)", (lineage_id, TASK, json_text(intent)))

    before = repository.stored_message(target, TASK)
    assert (before is None) == (envelope == "missing")
    with database.readonly_snapshot() as connection:
        blockers = family_strategy_state.check_legacy_dispatches(connection)
        assert blockers == ([{"kind": "legacy_family_dispatch", "lineage_id": lineage_id,
                              "message_id": TASK, "reason": reason}] if reason else [])
        assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"

    if reason:
        with pytest.raises(ValueError, match=reason):
            family_strategy_state.retire_legacy_dispatches()
        with database.read() as connection:
            row = connection.execute("SELECT intent_json FROM family_task_dispatches WHERE message_id=%s", (TASK,)).fetchone()
            assert row["intent_json"] == intent
    else:
        family_strategy_state.retire_legacy_dispatches()
        with database.read() as connection:
            assert connection.execute("SELECT to_regclass('family_task_dispatches') AS name").fetchone()["name"] is None
            assert connection.execute("SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s",
                                      (lineage_id,)).fetchone() is None

    assert repository.stored_message(target, TASK) == before
    if state:
        assert repository.input_state(target, TASK)["state"] == state
