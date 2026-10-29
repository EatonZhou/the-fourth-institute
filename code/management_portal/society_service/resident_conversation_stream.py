"""The Resident Conversation repository: its components composed over one store."""
from __future__ import annotations

import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any

from management_portal.execution.agent_data_store import AgentDataStore, get_agent_data_store

from .resident_conversation_activity import ConversationActivity
from .resident_conversation_events import ConversationEvents
from .resident_conversation_history import ConversationHistory
from .resident_conversation_inputs import ConversationInputs
from .resident_conversation_messages import ConversationMessages
from .resident_conversation_records import DEFAULT_HISTORY_LIMIT
from .resident_conversation_state import ConversationProjection
from .state_store import LineageDataStore, get_lineage_data_store


_REPOSITORIES: dict[str, "ResidentConversationRepository"] = {}


_REPOSITORIES_LOCK = threading.Lock()


class ResidentConversationRepository:
    """Persistence authority for Resident messages, state and ordered events.

    Each responsibility is a component; the repository is their published
    interface and serves every method from the component that owns it.
    """

    def __init__(self, state_store: LineageDataStore, agent_data: AgentDataStore) -> None:
        self.state_store = state_store
        self.agent_data = agent_data
        self.events = ConversationEvents(state_store, agent_data, self)
        self.messages = ConversationMessages(state_store, agent_data, self.events)
        self.inputs = ConversationInputs(state_store, agent_data, self.events, self.messages)
        self.projection = ConversationProjection(state_store, agent_data, self.events, self.messages, self.inputs, self)
        self.history = ConversationHistory(state_store, agent_data, self.messages, self.inputs)
        self.activity = ConversationActivity(state_store, agent_data)

    # Event log

    def execution_settings_changed(
        self,
        resident_id: object,
        settings: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        return self.events.execution_settings_changed(resident_id, settings)

    def context_reset_boundary(self, resident_id: object) -> list[str]:
        return self.events.context_reset_boundary(resident_id)

    def archive_context(
        self, resident_id: object, *, archived_at: str = "",
        message_ids: Sequence[str] | None = None,
    ) -> list[str]:
        return self.events.archive_context(resident_id, archived_at=archived_at, message_ids=message_ids)

    def prune_event_deliveries(self, *, older_than: str, limit: int = 5000) -> int:
        return self.events.prune_event_deliveries(older_than=older_than, limit=limit)

    def events_after(
        self,
        resident_id: object,
        after_cursor: object,
        *,
        limit: int = 500,
    ) -> dict[str, Any]:
        return self.events.events_after(resident_id, after_cursor, limit=limit)

    def hydrate_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.events.hydrate_events(events)

    # Messages

    def commit_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        return self.messages.commit_user_message(resident_id, message, activated_at=activated_at)

    def accept_user_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.accept_user_message(resident_id, message)

    def validate_message_identity(
        self,
        resident_id: object,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.validate_message_identity(resident_id, message)

    def execution_content_parts(
        self,
        resident_id: object,
        execution_id: str,
        content_parts: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        return self.messages.execution_content_parts(resident_id, execution_id, content_parts)

    def append_message(
        self,
        resident_id: object,
        message: Mapping[str, Any],
        *,
        activated_at: str = "",
    ) -> dict[str, Any]:
        return self.messages.append_message(resident_id, message, activated_at=activated_at)

    def tool_status_messages(self, messages: list[dict[str, Any]], *, resident_id: str) -> list[dict[str, Any]]:
        return self.messages.tool_status_messages(messages, resident_id=resident_id)

    def message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        return self.messages.message(resident_id, message_id)

    def stored_message(self, resident_id: object, message_id: object) -> dict[str, Any] | None:
        return self.messages.stored_message(resident_id, message_id)

    def waiting(self, resident_id: object, message_id: object) -> bool:
        return self.messages.waiting(resident_id, message_id)

    def message_business_ref(self, resident_id: object, message_id: object) -> str:
        return self.messages.message_business_ref(resident_id, message_id)

    def public_message(
        self,
        resident_id: object,
        message_id: object,
    ) -> dict[str, Any]:
        return self.messages.public_message(resident_id, message_id)

    def artifact(
        self,
        resident_id: object,
        artifact_id: object,
    ) -> dict[str, Any]:
        return self.messages.artifact(resident_id, artifact_id)

    def subagent_run(
        self,
        resident_id: object,
        trace_ref: object,
        *,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        return self.messages.subagent_run(resident_id, trace_ref, offset=offset, limit=limit)

    def update_message(
        self,
        resident_id: object,
        message_id: object,
        *,
        message: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.messages.update_message(resident_id, message_id, message=message)

    # Inputs and question sets

    def question_set_state(self, resident_id: object, message_id: object) -> str:
        return self.inputs.question_set_state(resident_id, message_id)

    def close_question_set(self, resident_id: object, message_id: object, *, actor: str) -> str:
        return self.inputs.close_question_set(resident_id, message_id, actor=actor)

    def set_input_state(
        self,
        resident_id: object,
        message_id: object,
        state: str,
        *,
        error: Mapping[str, Any] | None = None,
        updated_at: str = "",
    ) -> dict[str, Any]:
        return self.inputs.set_input_state(resident_id, message_id, state, error=error, updated_at=updated_at)

    def publish_archived_input_state(self, resident_id: str, message_id: str) -> None:
        return self.inputs.publish_archived_input_state(resident_id, message_id)

    def reopen_input(self, resident_id: object, message_id: object) -> dict[str, Any]:
        return self.inputs.reopen_input(resident_id, message_id)

    def record_accepted_input(self, connection, resident_id: str, message_id: str) -> None:
        return self.inputs.record_accepted_input(connection, resident_id, message_id)

    def input_state(self, resident_id: object, message_id: object) -> dict[str, Any]:
        return self.inputs.input_state(resident_id, message_id)

    def input_states_by_id(
        self, requests: Sequence[tuple[str, str]],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self.inputs.input_states_by_id(requests)

    def reply_rounds(
        self, requests: Sequence[tuple[str, str]], *, limit: int = 20,
    ) -> dict[tuple[str, str], dict[str, Any]]:
        return self.inputs.reply_rounds(requests, limit=limit)

    def input_states(
        self,
        resident_id: object,
        *,
        states: Sequence[str] = (),
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.inputs.input_states(resident_id, states=states, limit=limit)

    def latest_notifiable_input_states_for_messages(
        self,
        message_ids_by_resident: Mapping[object, object],
    ) -> dict[str, dict[str, Any]]:
        return self.inputs.latest_notifiable_input_states_for_messages(message_ids_by_resident)

    # Current state and snapshots

    def set_state(self, resident_id: object, state: Mapping[str, Any]) -> dict[str, Any] | None:
        return self.projection.set_state(resident_id, state)

    def set_activities(self, resident_id: object, activities: object) -> dict[str, Any] | None:
        return self.projection.set_activities(resident_id, activities)

    def queues(self, resident_ids: Iterable[object]) -> dict[str, dict[str, Any]]:
        return self.projection.queues(resident_ids)

    def publish_queue(self, resident_id: object) -> dict[str, Any] | None:
        return self.projection.publish_queue(resident_id)

    def current_projection(self, resident_id: object) -> dict[str, Any]:
        return self.projection.current_projection(resident_id)

    def activities(self, resident_id: object) -> list[dict[str, Any]]:
        return self.projection.activities(resident_id)

    def published_state(self, resident_id):
        return self.projection.published_state(resident_id)

    def current_state(self, resident_id):
        return self.projection.current_state(resident_id)

    def current_projections(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        return self.projection.current_projections(resident_ids)

    def states(self, resident_ids):
        return self.projection.states(resident_ids)

    def state_snapshots(
        self,
        resident_ids: Iterable[object],
    ) -> dict[str, dict[str, Any]]:
        return self.projection.state_snapshots(resident_ids)

    def snapshot(
        self,
        resident_id: object,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        return self.projection.snapshot(resident_id, history_limit=history_limit)

    # History

    def read_messages(self, resident_id: object, *, limit: int = 0) -> list[dict[str, Any]]:
        return self.history.read_messages(resident_id, limit=limit)

    def replies(
        self,
        resident_id: object,
        message_id: object,
        *,
        until_message_id: object = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.history.replies(resident_id, message_id, until_message_id=until_message_id, limit=limit)

    def latest_message(
        self,
        resident_id: object,
        *,
        role: str = "",
    ) -> dict[str, Any] | None:
        return self.history.latest_message(resident_id, role=role)

    def public_messages(
        self,
        resident_id: object,
        *,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        return self.history.public_messages(resident_id, limit=limit)

    def messages_before_input(self, resident_id: str, message_id: str, *, limit: int) -> list[dict]:
        return self.history.messages_before_input(resident_id, message_id, limit=limit)

    def messages_through_turn(
        self,
        resident_id: object,
        message_ids: Sequence[object],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.history.messages_through_turn(resident_id, message_ids, limit=limit)

    def messages_page(
        self,
        resident_id: object,
        page_token: object,
        *,
        limit: int = DEFAULT_HISTORY_LIMIT,
    ) -> dict[str, Any]:
        return self.history.messages_page(resident_id, page_token, limit=limit)

    def input_history_page(
        self,
        resident_id: object,
        sender: object,
        page_token: object = "",
        *,
        limit: int = 50,
    ) -> dict[str, Any]:
        return self.history.input_history_page(resident_id, sender, page_token, limit=limit)

    def search_messages(
        self,
        resident_ids: Sequence[object],
        query: object,
        *,
        page_token: object = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return self.history.search_messages(resident_ids, query, page_token=page_token, limit=limit)

    # Activity across Conversations

    def user_message_counts(self, resident_ids: Sequence[object]) -> dict[str, int]:
        return self.activity.user_message_counts(resident_ids)

    def dismissed_activity(self, actor: str) -> dict[str, datetime]:
        return self.activity.dismissed_activity(actor)

    def actor_activity(
        self,
        actor: object,
        resident_ids: Sequence[object] | None = None,
    ) -> dict[str, str]:
        return self.activity.actor_activity(actor, resident_ids)

    def actor_activity_page(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> dict[str, Any]:
        return self.activity.actor_activity_page(actor, limit=limit, page_token=page_token)

    def iter_actor_activity_pages(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> Iterator[dict[str, Any]]:
        return self.activity.iter_actor_activity_pages(actor, limit=limit, page_token=page_token)

    def activity_page_token(self, resident_id: str, latest_at: str) -> str:
        return self.activity.activity_page_token(resident_id, latest_at)

    def conversation_summaries(
        self,
        resident_ids: Sequence[object],
        actor: object,
        *,
        excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        return self.activity.conversation_summaries(resident_ids, actor, excluded_business_ref=excluded_business_ref)

    def signature(self, resident_id: object) -> str:
        return self.activity.signature(resident_id)

    def signatures(self, resident_ids: Sequence[object]) -> dict[str, str]:
        return self.activity.signatures(resident_ids)


def get_repository() -> ResidentConversationRepository:
    state_store = get_lineage_data_store()
    agent_data = get_agent_data_store()
    key = f"{state_store.database_identity}\0{agent_data.database_identity}"
    with _REPOSITORIES_LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = ResidentConversationRepository(state_store, agent_data)
            _REPOSITORIES[key] = repository
        return repository


def reset_repositories_for_tests() -> None:
    with _REPOSITORIES_LOCK:
        _REPOSITORIES.clear()
