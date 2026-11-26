"""Pending-question repair must only attribute inputs to authenticated Users."""

from types import SimpleNamespace

from management_common.principal import mailbox_origin, message_origin
from management_portal.society_service import user_pending_questions as questions


def test_repair_keeps_valid_users_when_other_input_authors_are_unusable(monkeypatch):
    inputs = {
        "historical_system": {"role": "user", "author_id": "system"},
        "masked_service": {
            "role": "user", "author_id": "forged",
            "origin": message_origin("portal", initiated_by="operator"),
        },
        "mismatch": {
            "role": "user", "author_id": "other",
            "origin": message_origin("writer"),
        },
        "invalid": {"role": "user", "author_id": "@reserved"},
        "resident": {"role": "user", "author_id": "resident_123456789abc"},
        "plain_mailbox": {"role": "user", "author_id": "mailbox"},
        "malformed_mailbox": {
            "role": "user", "author_id": "mailbox",
            "origin": {**mailbox_origin("organizer"), "sender": 1},
        },
        "system_merge": {"role": "user", "author_id": "mailbox", "origin": mailbox_origin("system")},
        "bad_origin": {"role": "user", "author_id": "unverified", "origin": "corrupt"},
        "system_role": {"role": "system", "author_id": "writer"},
        "merged": {"role": "user", "author_id": "mailbox", "origin": mailbox_origin("organizer")},
        "direct": {"role": "user", "author_id": "writer", "origin": message_origin("writer")},
        "legacy": {"role": "user", "author_id": "legacy-user"},
    }
    resident = "resident_123456789abc"
    monkeypatch.setattr(questions, "asked_kind", lambda *_: "question_set")
    monkeypatch.setattr(questions, "_answered_inputs", lambda *_: list(inputs))
    monkeypatch.setattr(questions, "_open_place", lambda resident_id, *_: resident_id)
    repository = SimpleNamespace(message=lambda _, input_id: inputs[input_id])

    rows = questions.rows_for(resident, {"message_id": "msg_reply"}, repository)

    assert {row[0] for row in rows} == {"organizer", "writer", "legacy-user"}
