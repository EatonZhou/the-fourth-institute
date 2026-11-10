"""A Telepus bridge store on its own schema of the isolated test database, and a fake Portal.

The bridge reads ``telegram_contacts`` from its own schema; Portal writes it
(``management_portal.telepus.contacts_store``), so the helper creates and fills
it the same way. Each test gets a fresh schema, dropped afterwards.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from management_common.portal_database import portal_database_url
from management_portal.telepus.router_store import RouterStore

ACTOR = "alice"
FACADE = "resident_fac0000000aa"
MEMBER = "resident_eeee0000000b"
EXACT = "resident_aaaaaaaaaaaa"


@pytest.fixture
def telepus_schema():
    schema = "telepus_case_" + uuid.uuid4().hex[:16]
    yield schema
    with psycopg.connect(portal_database_url(), autocommit=True) as connection:
        connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def bind_contact(store: RouterStore, telegram_user_id: str, portal_actor: str) -> None:
    with store.connect() as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS telegram_contacts (telegram_user_id TEXT PRIMARY KEY, "
                           "name TEXT NOT NULL, portal_actor TEXT NOT NULL)")
        connection.execute("INSERT INTO telegram_contacts(telegram_user_id, name, portal_actor) VALUES (?, ?, ?)",
                           (telegram_user_id, portal_actor, portal_actor))


class FakePortal:
    """The HATG ISA results the bridge reads, kept per Resident."""

    def __init__(self, actor=ACTOR, facade=FACADE) -> None:
        self.actor = actor
        self.assistant = {"resident_id": facade, "lineage_id": "lineage_000000000001", "title": "Ask Me Anything | My Lineage"}
        self.residents = {facade: {"title": "个人助手"}, EXACT: {"title": "Portal Maintainer"}}
        self.delivered: list[tuple[str, str, dict, dict]] = []
        self.states: dict[tuple[str, str], dict] = {}
        self.messages: dict[str, list[dict]] = {facade: [], EXACT: []}
        self.event_log: dict[str, list[dict]] = {facade: [], EXACT: []}
        self.cancelled: list[tuple[str, str]] = []
        self.closed: list[tuple[str, str]] = []
        self.mailboxes: dict[str, list[dict]] = {EXACT: []}
        self.active: dict[str, str] = {}

    def personal_assistant(self, actor):
        assert actor == self.actor
        return dict(self.assistant)

    def resident(self, actor, resident_id):
        if resident_id not in self.residents:
            raise KeyError(resident_id)
        return dict(self.residents[resident_id])

    def deliver(self, actor, resident_id, message, *, activation_policy="enqueue_and_trigger", temporary_credentials=None):
        assert actor == self.actor
        self.delivered.append((resident_id, activation_policy, message, {"credentials": temporary_credentials}))
        self.states[(resident_id, message["message_id"])] = {"state": "queued", "reply_message_ids": []}
        self.messages[resident_id].append({"message_id": message["message_id"], "role": "user", **message,
                                          "created_at": "2026-11-07T10:00:00Z"})
        return {"resident_id": resident_id, "message_id": message["message_id"], "accepted": True}

    def conversation(self, actor, resident_id, *, history_limit=0, input_message_id=""):
        messages = self.messages[resident_id]
        if input_message_id:
            messages = [item for item in messages if item["message_id"] == input_message_id
                        or item.get("reply_to_message_id") == input_message_id]
        return {"cursor": len(self.event_log[resident_id]), "messages": list(messages)}

    def events(self, actor, resident_id, after_cursor, limit=500):
        items = [{"cursor": index + 1, "event": event} for index, event in enumerate(self.event_log[resident_id])]
        return [item for item in items if item["cursor"] > after_cursor], len(items)

    def input_state(self, actor, resident_id, message_id):
        return self.states[(resident_id, message_id)]

    def cancel_input(self, actor, resident_id, message_id):
        self.cancelled.append((resident_id, message_id))
        return {"cancelled": True}

    def resident_state(self, actor, resident_id):
        return {"work_state": "running" if self.active.get(resident_id) else "idle",
                "active_message_id": self.active.get(resident_id, "")}

    def mailbox(self, actor, resident_id):
        return list(self.mailboxes.get(resident_id, []))

    def prioritize(self, actor, resident_id, message_id):
        return {}

    def inject(self, actor, resident_id, message_id):
        return {}

    def close_questions(self, actor, resident_id, source_message_id):
        self.closed.append((resident_id, source_message_id))
        return {"status": "cancelled"}

    def attachments(self, actor, resident_id, message):
        return []

    def read_attachment(self, actor, attachment):
        return {"filename": "a.txt", "content_type": "text/plain", "data": b"x", "size": 1}

    def answer(self, resident_id, input_id, reply_id, text, *, member="", parts=None):
        """Portal finishing an input: the reply appears in the Conversation and the input completes."""
        reply = {"message_id": reply_id, "role": "agent", "reply_to_message_id": input_id,
                 "content_parts": [{"type": "text", "text": text}, *(parts or [])]}
        if member:
            reply["family_reply"] = {"resident_id": MEMBER, "title": member, "request_message_id": input_id}
        self.messages[resident_id].append(reply)
        self.event_log[resident_id].append({"event_type": "conversation.message.appended", "payload": {"message": reply}})
        self.states[(resident_id, input_id)] = {"state": "completed", "reply_message_ids": [reply_id]}
        self.event_log[resident_id].append({"event_type": "resident.input.state.changed",
                                            "payload": {"input_state": {"message_id": input_id, "state": "completed"}}})
