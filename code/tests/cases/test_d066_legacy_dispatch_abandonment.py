"""Scoped administrative abandonment stores evidence without creating an input."""

from hashlib import sha256

import pytest

from management_common.json_columns import json_text
from management_common.principal import message_origin
from management_portal.society_service import (
    family_strategy_state, family_task_dispatch_storage, resident_conversation_stream,
    resident_input_queue_domain,
)
from management_portal.society_service.state_store import get_lineage_data_store
from tests.cases.test_family_familize import charters, lineage  # noqa: F401
from tests.cases.test_family_tasks import configured_family  # noqa: F401
from tests.cases.test_resident_folder_creation_contract import future_creation_store  # noqa: F401


TASK = "msg_" + "6" * 32
APPROVAL = "msg_" + "7" * 32
OTHER_TASK = "msg_" + "8" * 32
ARCHIVE = "family_dispatch_dispositions"
USER = "sample_admin"


def _fingerprint(value):
    return sha256(json_text(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _prepare(configured_family, request, monkeypatch, variant="exact"):
    graph, block = configured_family
    lineage_id = graph["lineage_id"]
    facade, target = (block["family"][role] for role in ("facade", "steward"))
    repository = resident_conversation_stream.get_repository()
    intent = {"request": {"facade": facade, "target": target, "trigger": "queue",
                          "business_ref": "duty/family-queue", "facts": {"lineage_id": lineage_id}},
              "initiated_by": facade, "text": "Old steward notice"}
    approval = resident_input_queue_domain.build_message(
        "user", f"Abandon only {TASK}", USER, message_id=APPROVAL,
    )
    # The authority sets a hash before any deliberately mismatched persistence.
    authority = {
        "lineage_id": lineage_id, "message_id": TASK, "intent_sha256": _fingerprint(intent),
        "facade_id": facade, "target_id": target, "initiated_by": facade,
        "authorization": {"resident_id": facade, "message_id": APPROVAL, "user_id": USER,
                          "content_sha256": _fingerprint(approval["content_parts"]),
                          "instruction_id": "inst_fixture_get_conversation"},
    }
    monkeypatch.setattr(family_strategy_state, "_AUTHORIZED_ABANDONMENT", authority, raising=False)
    if variant == "wrong-hash":
        intent["text"] = "Different intent"
    elif variant == "wrong-author":
        approval = resident_input_queue_domain.build_message(
            "user", f"Abandon only {TASK}", "another_user", message_id=APPROVAL,
        )
    elif variant == "wrong-body":
        approval["content_parts"] = [{"type": "text", "text": "Different approval"}]
    if variant != "missing-approval":
        author = approval["author_id"]
        approval["origin"] = message_origin(author, initiated_by=author)
        repository.commit_user_message(facade, approval)
    if variant == "partial-intake":
        repository.set_input_state(target, TASK, "queued")
    database = get_lineage_data_store().database

    def cleanup():
        with database.transaction() as connection:
            connection.execute("DROP TABLE IF EXISTS family_task_dispatches")
            connection.execute("DROP TABLE IF EXISTS family_dispatch_dispositions")

    request.addfinalizer(cleanup)
    with database.transaction() as connection:
        family_task_dispatch_storage.create_schema(connection)
        connection.execute("INSERT INTO family_task_dispatches VALUES(%s,%s,%s)",
                           (lineage_id, TASK, json_text(intent)))
        if variant == "other-intent":
            connection.execute("INSERT INTO family_task_dispatches VALUES(%s,%s,%s)",
                               (lineage_id, OTHER_TASK, json_text(intent)))
    return database, repository, lineage_id, target, intent, authority


def _preflight(database):
    with database.readonly_snapshot() as connection:
        blockers = family_strategy_state.check_legacy_dispatches(connection)
        assert connection.execute("SELECT to_regclass(%s) AS name", (ARCHIVE,)).fetchone()["name"] is None
        return blockers


def test_exact_user_decision_archives_frozen_intent_without_admitting_input(configured_family, request, monkeypatch):
    database, repository, lineage_id, target, intent, authority = _prepare(configured_family, request, monkeypatch)
    assert _preflight(database) == []
    family_strategy_state.retire_legacy_dispatches()
    family_strategy_state.retire_legacy_dispatches()
    with database.read() as connection:
        assert connection.execute("SELECT to_regclass('family_task_dispatches') AS name").fetchone()["name"] is None
        rows = connection.execute("SELECT * FROM family_dispatch_dispositions").fetchall()
        assert len(rows) == 1
        row = rows[0]
        assert (row["lineage_id"], row["message_id"], row["intent_json"]) == (lineage_id, TASK, intent)
        assert row["decision_json"]["authorization"] == authority["authorization"]
        assert row["decision_json"]["intent_sha256"] == authority["intent_sha256"]
        assert row["decision_json"]["disposition"] == "abandoned_by_user"
        assert row["decision_json"]["admission"] == "unknown"
        assert connection.execute("SELECT cells_json FROM lineage_shared_state WHERE lineage_id=%s",
                                  (lineage_id,)).fetchone() is None
    assert repository.stored_message(target, TASK) is None
    assert not repository.input_statuses_by_id([(target, TASK)])


@pytest.mark.parametrize(("variant", "blocked_id", "reason"), [
    ("wrong-hash", TASK, "unaccepted_frozen_intent"),
    ("wrong-author", TASK, "abandonment_authorization_unverified"),
    ("wrong-body", TASK, "abandonment_authorization_unverified"),
    ("missing-approval", TASK, "abandonment_authorization_unverified"),
    ("partial-intake", TASK, "incomplete_acceptance_receipt"),
    ("other-intent", OTHER_TASK, "unaccepted_frozen_intent"),
])
def test_abandonment_remains_bound_to_exact_evidence(configured_family, request, monkeypatch, variant, blocked_id, reason):
    database, _, lineage_id, _, intent, _ = _prepare(configured_family, request, monkeypatch, variant)
    assert _preflight(database) == [{"kind": "legacy_family_dispatch", "lineage_id": lineage_id,
                                     "message_id": blocked_id, "reason": reason}]
    with pytest.raises(ValueError, match=reason):
        family_strategy_state.retire_legacy_dispatches()
    assert len(_preflight(database)) == 1
    with database.read() as connection:
        assert connection.execute("SELECT intent_json FROM family_task_dispatches WHERE message_id=%s", (TASK,)
                                  ).fetchone()["intent_json"] == intent


def test_abandonment_archive_and_retirement_share_the_same_transaction(configured_family, request, monkeypatch):
    database, repository, _, target, intent, _ = _prepare(configured_family, request, monkeypatch)
    archive = family_strategy_state._archive_dispatch_dispositions

    def fail_after_archive(connection, lineage_id, dispositions):
        archive(connection, lineage_id, dispositions)
        assert connection.execute("SELECT intent_json FROM family_dispatch_dispositions").fetchone()["intent_json"] == intent
        raise ValueError("abort after archiving")

    monkeypatch.setattr(family_strategy_state, "_archive_dispatch_dispositions", fail_after_archive)
    with pytest.raises(ValueError, match="abort after archiving"):
        family_strategy_state.retire_legacy_dispatches()
    assert _preflight(database) == []
    with database.read() as connection:
        assert connection.execute("SELECT intent_json FROM family_task_dispatches WHERE message_id=%s", (TASK,)
                                  ).fetchone()["intent_json"] == intent
    assert repository.stored_message(target, TASK) is None
