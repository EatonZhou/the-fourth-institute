from __future__ import annotations

from management_common.database_time import iso_time
from management_common.json_columns import json_value

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from management_portal.notifications import notification_subject

from . import family_facade, resident_conversation_stream
from .input_progress import event_input_ids
from .resident_conversation_stream import (
    LIVE_CHANNEL,
    RESIDENT_CONVERSATION_SCHEMA,
    conversation_event_notification,
    live_activity_event,
)
from .state_store import LineageDataStore


LOGGER = logging.getLogger(__name__)
# Remembered business_refs of Resident messages (they never change once stored).
BUSINESS_REF_CACHE_LIMIT = 4096
# Sequences are allocated before commit; revisit a bounded trailing range.
DELIVERY_LOOKBACK = 10_000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


class ResidentConversationEventRelay:
    """Project the durable Conversation event log onto one instance-local bus.

    Dev and Release intentionally use separate notification buses while their
    workers share the canonical Conversation database. Every SocietyService
    therefore tails that database independently. Conversation writers only
    commit durable events; this relay is the sole realtime publication owner.
    Activity is not in the event log: its writers notify ``LIVE_CHANNEL`` and
    the relay publishes the Resident's current activity list.
    """

    def __init__(
        self,
        state_store: LineageDataStore,
        publisher: object,
        *,
        poll_seconds: float = 0.1,
        batch_size: int = 500,
    ) -> None:
        self._state_store = state_store
        self._publisher = publisher
        self._poll_seconds = max(0.01, float(poll_seconds))
        self._batch_size = max(1, min(int(batch_size), 2000))
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._live_thread: threading.Thread | None = None
        self._live_published_count = 0
        self._sequence = 0
        self._published_count = 0
        self._last_error = ""
        self._started_at = ""
        self._business_refs: dict[tuple[str, str], str] = {}
        self._seen: dict[tuple[str, int], int] = {}
        self._seen_floor = 0

    def _latest_sequence(self) -> int:
        with self._state_store.database.read() as connection:
            rows = connection.execute(
                'SELECT sequence,resident_id,cursor FROM conversation_event_delivery ORDER BY sequence DESC LIMIT %s',
                (DELIVERY_LOOKBACK,),
            ).fetchall()
        self._seen = {(row['resident_id'], int(row['cursor'])): int(row['sequence']) for row in rows}
        self._seen_floor = 0
        return int(rows[0]['sequence']) if rows else 0

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            # Existing events are represented by the Snapshot sent when SSE
            # connects. Start at the committed tail and relay only new events.
            self._sequence = self._latest_sequence()
            self._last_error = ""
            self._started_at = _now_iso()
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="lineage-resident-conversation-event-relay",
                daemon=True,
            )
            self._thread.start()
            self._live_thread = threading.Thread(
                target=self._run_live,
                name="lineage-resident-conversation-live-relay",
                daemon=True,
            )
            self._live_thread.start()

    def stop(self, timeout: float = 6.0) -> None:
        self._stop.set()
        for thread in (self._thread, self._live_thread):
            if thread is not None:
                thread.join(timeout=max(0.0, float(timeout)))

    def _live_events(self, resident_ids: set[str]) -> list[dict[str, Any]]:
        with self._state_store.database.read() as connection:
            rows = connection.execute(
                'SELECT resident_id, cursor, activity_json FROM conversation_states WHERE resident_id = ANY(%s)',
                (sorted(resident_ids),),
            ).fetchall()
        return [
            live_activity_event(
                str(row["resident_id"]),
                int(row["cursor"] or 0),
                json_value(row["activity_json"] or "[]"),
            )
            for row in rows
        ]

    def _run_live(self) -> None:
        import psycopg
        from psycopg import sql

        database = self._state_store.database
        while not self._stop.is_set():
            try:
                with psycopg.connect(
                    database.dsn,
                    autocommit=True,
                    application_name="tfi:conversation-live-relay",
                ) as connection:
                    connection.execute(sql.SQL("LISTEN {}").format(sql.Identifier(LIVE_CHANNEL)))
                    while not self._stop.is_set():
                        changed: set[str] = set()
                        for wait in (1.0, 0.02):
                            # The second short wait folds a burst into one read per Resident.
                            for notification in connection.notifies(timeout=wait, stop_after=None if changed else 1):
                                schema, separator, resident_id = str(notification.payload or "").partition(":")
                                if separator and schema == database.schema and resident_id:
                                    changed.add(resident_id)
                            if not changed:
                                break
                        if not changed:
                            continue
                        for event in self._live_events(changed):
                            self._publish(event)
                            with self._lock:
                                self._live_published_count += 1
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{exc.__class__.__name__}: {exc}"[:1000]
                LOGGER.exception("Resident Conversation live relay failed")
                self._stop.wait(1.0)

    def _events_after(self, sequence: int) -> list[tuple[int, dict[str, Any]]]:
        floor = max(0, sequence - DELIVERY_LOOKBACK)
        if floor > self._seen_floor:
            # Evict once after a published batch advances the window, not once
            # per event or repeatedly while the relay is idle.
            self._seen = {key: value for key, value in self._seen.items() if value > floor}
            self._seen_floor = floor
        with self._state_store.database.read() as connection:
            deliveries = connection.execute(
                'SELECT sequence, resident_id, cursor FROM conversation_event_delivery WHERE sequence > %s ORDER BY sequence LIMIT %s',
                (floor, self._batch_size + DELIVERY_LOOKBACK),
            ).fetchall()
            unseen = [
                int(row['sequence']) for row in deliveries
                if (row['resident_id'], int(row['cursor'])) not in self._seen
            ][:self._batch_size]
            if not unseen:
                return []
            rows = connection.execute(
                'SELECT delivery.sequence, event.resident_id, event.cursor, event.event_type, event.occurred_at, event.payload_json FROM conversation_event_delivery AS delivery JOIN conversation_events AS event ON event.resident_id = delivery.resident_id AND event.cursor = delivery.cursor WHERE delivery.sequence = ANY(%s) ORDER BY delivery.sequence',
                (unseen,),
            ).fetchall()
        events = resident_conversation_stream.get_repository().hydrate_events([self._event(row) for row in rows])
        return [(int(row['sequence']), event) for row, event in zip(rows, events)]

    def _remember(self, sequence: int, event: dict[str, Any]) -> None:
        self._sequence = max(self._sequence, sequence)
        self._seen[(event['resident_id'], int(event['cursor']))] = sequence

    @staticmethod
    def _event(row: Any) -> dict[str, Any]:
        return {
            "schema": RESIDENT_CONVERSATION_SCHEMA,
            "event_type": str(row["event_type"]),
            "resident_id": str(row["resident_id"]),
            "cursor": int(row["cursor"]),
            "occurred_at": iso_time(row["occurred_at"]),
            "payload": json_value(row["payload_json"]),
        }

    def _publish(self, event: dict[str, Any]) -> None:
        resident_id = str(event["resident_id"])
        self._publisher.publish(
            notification_subject("resident", resident_id, scope="conversation"),
            {
                "kind": "conversation_event",
                "event": conversation_event_notification(event),
            },
        )
        self._publish_facade_projections(event)
        self._publish_chatroom_progress(event)

    def _publish_chatroom_progress(self, event: dict[str, Any]) -> None:
        # A Chatroom shows the progress of each Resident it called
        # (decision_a6bebf4ac283); its view reloads when such an input moves
        # in the queue or the Resident's live work on it changes.
        resident_id = str(event.get("resident_id") or "")
        try:
            inputs = dict.fromkeys((resident_id, message_id) for message_id in event_input_ids(event))
            for source_id, message_id in list(inputs):
                if family_facade.parse_relay_mark(self._business_ref(source_id, message_id)):
                    inputs.update(dict.fromkeys(family_facade.relay_sources_of(source_id, message_id)))
            for source_id, message_id in inputs:
                business_ref = self._business_ref(source_id, message_id)
                if business_ref.startswith("chatroom_"):
                    from management_portal import chatroom_backend

                    chatroom_backend.handle_resident_progress(
                        business_ref=business_ref, resident_id=source_id, message_id=message_id,
                    )
                    if event.get("event_type") == resident_conversation_stream.INPUT_STATE_CHANGED:
                        # Members carry a Family relay ref, not the Chatroom ref.
                        # Reconcile the original call even with no browser open;
                        # GetInputState decides whether routing or work finished.
                        chatroom_backend.handle_resident_terminal(
                            business_ref=business_ref, resident_id=source_id, message_id=message_id,
                        )
        except Exception:
            LOGGER.exception("Chatroom progress notification failed")

    def _business_ref(self, resident_id: str, message_id: str) -> str:
        # A stored message's business_ref never changes, so each is read once.
        key = (resident_id, message_id)
        with self._lock:
            if key in self._business_refs:
                return self._business_refs[key]
        repository = resident_conversation_stream.get_repository()
        business_ref = str(repository.message_business_ref(resident_id, message_id) or "")
        if business_ref or repository.message(resident_id, message_id) is not None:
            with self._lock:
                if len(self._business_refs) >= BUSINESS_REF_CACHE_LIMIT:
                    self._business_refs.pop(next(iter(self._business_refs)))
                self._business_refs[key] = business_ref
        return business_ref

    def _publish_facade_projections(self, event: dict[str, Any]) -> None:
        # A Family facade's Conversation shows its members' replies
        # (decision_44cf587c1721); its viewers reload when a relayed member
        # message changes.
        try:
            facades = family_facade.projection_targets(event)
        except Exception:
            LOGGER.exception("Family facade projection lookup failed")
            return
        for facade_id in facades:
            self._publisher.publish(
                notification_subject("resident", facade_id, scope="projection"),
                {
                    "kind": "facade_projection",
                    "member_resident_id": str(event["resident_id"]),
                    "cursor": int(event.get("cursor") or 0),
                },
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                events = self._events_after(self._sequence)
                if not events:
                    self._stop.wait(self._poll_seconds)
                    continue
                for sequence, event in events:
                    if self._stop.is_set():
                        break
                    self._publish(event)
                    with self._lock:
                        self._remember(sequence, event)
                        self._published_count += 1
                        self._last_error = ""
            except Exception as exc:
                with self._lock:
                    self._last_error = f"{exc.__class__.__name__}: {exc}"[:1000]
                LOGGER.exception("Resident Conversation event relay failed")
                self._stop.wait(max(0.5, self._poll_seconds))

    def health(self) -> dict[str, Any]:
        thread = self._thread
        with self._lock:
            return {
                "running": bool(thread is not None and thread.is_alive()),
                "sequence": self._sequence,
                "published_count": self._published_count,
                "live_running": bool(
                    self._live_thread is not None and self._live_thread.is_alive()
                ),
                "live_published_count": self._live_published_count,
                "last_error": self._last_error,
                "started_at": self._started_at,
            }
