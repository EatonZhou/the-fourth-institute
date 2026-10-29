"""Activity summaries across Resident Conversations and their signatures."""
from __future__ import annotations

import hashlib
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

from management_common.database_time import iso_time, instant

from management_portal.execution.agent_data_store import (
    AgentDataStore,
    canonical_json,
    finish_conversation_summary,
)
from management_portal.execution.chat_position_index import RELAYED_COPY_PATTERN

from .resident_conversation_records import (
    business_ref_excluded,
    instant_or_none,
    instants,
    newest_first,
    required_resident_id,
    with_outcomes,
)
from .state_store import LineageDataStore


class ConversationActivity:
    """What one actor did across Resident Conversations: activity times, summaries and change signatures."""

    def __init__(self, state_store: LineageDataStore, agent_data: AgentDataStore) -> None:
        self.state_store = state_store
        self.agent_data = agent_data

    # Aggregates count the Chat (the trace) and the Mailbox: a message that
    # waits there was sent to the Resident as much as one it read.

    def _waiting(
        self, resident_ids: Sequence[str], *, author: str = "", excluded_business_ref: str = "",
    ) -> list[dict[str, Any]]:
        return [
            entry for entry in self.state_store.mailbox_entries(resident_ids, author=author)
            if isinstance(entry["message"].get("content_parts"), list)
            and str(entry["message"].get("role") or "user") == "user"
            and not business_ref_excluded(entry["message"], excluded_business_ref)
        ]

    def user_message_counts(self, resident_ids: Sequence[object]) -> dict[str, int]:
        requested = [required_resident_id(item) for item in resident_ids if str(item or "").strip()]
        counts = self.agent_data.chat_user_counts(requested)
        for entry in self._waiting(requested):
            counts[entry["resident_id"]] = counts.get(entry["resident_id"], 0) + 1
        return counts

    def _outcome_activity(self, actor: str) -> dict[str, datetime]:
        """When the latest outcome of the actor's request happened, by Resident."""
        from management_portal import conversation_receipt_store

        with self.state_store.database.read() as connection:
            times = conversation_receipt_store.outcome_times(connection, actor, "resident:")
        return {key.removeprefix("resident:"): instant(at) for key, at in times.items()}

    def dismissed_activity(self, actor: str) -> dict[str, datetime]:
        """Through when the actor's "清除全部" hides each Resident conversation."""
        from management_portal import conversation_receipt_store

        with self.state_store.database.read() as connection:
            times = conversation_receipt_store.dismissed_times(connection, actor, "resident:")
        return {key.removeprefix("resident:"): instant(at) for key, at in times.items()}

    def _actor_activity(self, actor: str, resident_ids: Sequence[str] | None) -> list[tuple[str, str]]:
        """Each Resident's latest activity with the actor, newest first (ties by Resident id).

        Relayed copies are not their sender's activity (AgentDataStore.chat_actor_activity).
        """

        activity = instants(self.agent_data.chat_actor_activity(actor, resident_ids))
        scope = None if resident_ids is None else set(resident_ids)
        for entry in self.state_store.mailbox_entries_by_author(actor):
            if scope is not None and entry["resident_id"] not in scope:
                continue
            if not isinstance(entry["message"].get("content_parts"), list) or business_ref_excluded(
                entry["message"], RELAYED_COPY_PATTERN,
            ):
                continue
            at = instant_or_none(entry["message"].get("created_at") or entry["accepted_at"])
            if at and (entry["resident_id"] not in activity or at > activity[entry["resident_id"]]):
                activity[entry["resident_id"]] = at
        return [(resident_id, iso_time(at)) for resident_id, at in newest_first(
            with_outcomes(activity, self._outcome_activity(actor)),
        )]

    def actor_activity(
        self,
        actor: object,
        resident_ids: Sequence[object] | None = None,
    ) -> dict[str, str]:
        requested = (
            None if resident_ids is None else [
                required_resident_id(item) for item in resident_ids
                if str(item or "").strip()
            ]
        )
        actor_id = str(actor or "").strip()
        if not actor_id or requested == []:
            return {}
        return dict(self._actor_activity(actor_id, requested))

    def actor_activity_page(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> dict[str, Any]:
        return next(self.iter_actor_activity_pages(actor, limit=limit, page_token=page_token))

    def iter_actor_activity_pages(
        self, actor: object, *, limit: int = 50, page_token: str = "",
    ) -> Iterator[dict[str, Any]]:
        """Read ordered candidate batches with a data-source keyset boundary."""
        size = int(limit)
        if not 1 <= size <= 100:
            raise ValueError("Conversation page size must be between 1 and 100")
        after = self.agent_data.chat_activity_page_boundary(page_token)
        actor_id = str(actor or "").strip()
        mailbox = instants(self.state_store.mailbox_actor_activity(
            actor_id, excluded_business_ref=RELAYED_COPY_PATTERN,
        )) if actor_id else {}
        outcomes = self._outcome_activity(actor_id) if actor_id else {}
        # Residents in Mailbox are merged once using their true latest time.
        # Excluding them from SQL prevents an older trace entry reappearing on
        # the next page after their newer Mailbox entry has been consumed.
        # Outcomes raise the others' activity in SQL itself.
        engaged = dict(mailbox)
        if mailbox:
            for resident_id, at in instants(self.agent_data.chat_actor_activity(
                actor_id, list(mailbox),
            )).items():
                engaged[resident_id] = max(engaged[resident_id], at)
        engaged = with_outcomes(engaged, outcomes)
        known = {resident_id: at for resident_id, at in outcomes.items() if resident_id not in mailbox}
        if actor_id:
            # Once for all the pages of this listing.
            self.agent_data.bring_chat_current()
        boundary = None if after is None else (instant(after[0]), after[1])
        while True:
            rows = self.agent_data.chat_actor_activity_page(
                actor_id, after=after, limit=size + 1,
                exclude_agent_ids=tuple(mailbox), known_times=known,
            ) if actor_id else []
            merged = [*((resident_id, instant(at)) for resident_id, at in rows), *(
                (resident_id, at) for resident_id, at in engaged.items()
                if boundary is None or at < boundary[0] or (at == boundary[0] and resident_id > boundary[1])
            )]
            selected = newest_first(dict(merged))[:size]
            next_token = (
                self.agent_data.chat_activity_page_token(selected[-1][0], iso_time(selected[-1][1]))
                if len(merged) > size else ""
            )
            yield {
                "activity": {resident_id: iso_time(at) for resident_id, at in selected},
                "next_page_token": next_token,
            }
            if not next_token:
                return
            after = (iso_time(selected[-1][1]), selected[-1][0])
            boundary = (selected[-1][1], selected[-1][0])

    def activity_page_token(self, resident_id: str, latest_at: str) -> str:
        return self.agent_data.chat_activity_page_token(resident_id, latest_at)

    def conversation_summaries(
        self,
        resident_ids: Sequence[object],
        actor: object,
        *,
        excluded_business_ref: str = "",
    ) -> dict[str, dict[str, Any]]:
        requested = [
            required_resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        actor_id = str(actor or "").strip()
        summaries = self.agent_data.chat_conversation_summaries(
            requested,
            actor_id,
            excluded_business_ref=excluded_business_ref,
            finish=False,
        )
        clean_actor = actor_id.lstrip("@＠").strip().casefold()
        for entry in self._waiting(requested, excluded_business_ref=excluded_business_ref):
            summary = summaries.get(entry["resident_id"])
            if summary is None:
                continue
            at = str(entry["message"].get("created_at") or entry["accepted_at"])
            summary["user_message_count"] += 1
            if at > summary["last_user_message_at"]:
                summary["last_user_message_at"] = at
                summary["last_user_actor"] = entry["author_id"]
            if clean_actor and entry["author_id"].lstrip("@＠").strip().casefold() == clean_actor:
                summary["actor_requests"].append((at, entry["message_id"]))
        for summary in summaries.values():
            finish_conversation_summary(summary)
        return summaries

    def signature(self, resident_id: object) -> str:
        return self.signatures([resident_id]).get(required_resident_id(resident_id), "")

    def signatures(self, resident_ids: Sequence[object]) -> dict[str, str]:
        """A value that changes whenever a Resident's Chat or Mailbox changes."""

        requested = [
            required_resident_id(item)
            for item in resident_ids
            if str(item or "").strip()
        ]
        signatures = self.agent_data.chat_signatures(requested)
        waiting: dict[str, list[list[str]]] = {}
        for entry in self.state_store.mailbox_entries(requested):
            waiting.setdefault(entry["resident_id"], []).append(
                [entry["message_id"], entry["place"], entry["updated_at"]],
            )
        for resident_id, entries in waiting.items():
            signatures[resident_id] = hashlib.sha256(
                canonical_json([signatures.get(resident_id, ""), sorted(entries)]).encode("utf-8")
            ).hexdigest()
        return signatures
