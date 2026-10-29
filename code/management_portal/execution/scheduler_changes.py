"""How scheduler changes reach waiters: execution events, change signals and PostgreSQL notifications."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from management_common.site_compatibility import compatibility_value

from .contracts import execution_timestamp
from .scheduler_rows import compact_json, json_object


TRANSIENT_EVENTS_PER_EXECUTION = 256
SHARED_SCHEDULER_POLL_SECONDS = 5.0
SCHEDULER_CHANGE_CHANNEL = compatibility_value("execution_scheduler_change_channel", "tfi_execution_scheduler_changed")
# pg_trigger.tgtype of a FOR EACH ROW AFTER INSERT OR UPDATE OR DELETE trigger.
SCHEDULER_TRIGGER_TYPE = 1 | 4 | 8 | 16


def install_change_triggers(connection: Any) -> None:
    """Make every scheduler change that a waiter acts on NOTIFY ``SCHEDULER_CHANGE_CHANNEL``."""
    connection.executescript(
        f"""
                CREATE OR REPLACE FUNCTION notify_scheduler_change()
                RETURNS trigger LANGUAGE plpgsql AS $$
                DECLARE
                    categories TEXT := '';
                BEGIN
                    IF TG_TABLE_NAME = 'executions' THEN
                        categories := 'execution';
                        IF TG_OP <> 'UPDATE' OR NOT (
                            NEW.status IS NOT DISTINCT FROM OLD.status
                            AND NEW.cancel_requested IS NOT DISTINCT FROM OLD.cancel_requested
                            AND NEW.available_at IS NOT DISTINCT FROM OLD.available_at
                            AND NEW.priority IS NOT DISTINCT FROM OLD.priority
                            AND NEW.capability IS NOT DISTINCT FROM OLD.capability
                            AND NEW.request_json IS NOT DISTINCT FROM OLD.request_json
                        ) THEN
                            categories := categories || ',claim';
                        END IF;
                        IF TG_OP <> 'UPDATE' OR NOT (
                            NEW.status IS NOT DISTINCT FROM OLD.status
                            AND NEW.cancel_requested IS NOT DISTINCT FROM OLD.cancel_requested
                            AND NEW.owner_worker_id IS NOT DISTINCT FROM OLD.owner_worker_id
                            AND NEW.claim_token IS NOT DISTINCT FROM OLD.claim_token
                        ) THEN
                            categories := categories || ',control';
                        END IF;
                    ELSIF TG_TABLE_NAME = 'execution_workers' THEN
                        IF TG_OP <> 'UPDATE' OR NEW.state IS DISTINCT FROM OLD.state THEN
                            categories := 'claim,control';
                        END IF;
                    ELSIF TG_TABLE_NAME = 'execution_input_controls' THEN
                        categories := 'control';
                    ELSE
                        categories := 'claim';
                    END IF;
                    IF categories <> '' THEN
                        PERFORM pg_notify('{SCHEDULER_CHANGE_CHANNEL}', current_schema() || ':' || categories);
                    END IF;
                    RETURN NULL;
                END;
                $$;
                """
    )
    for table in (
        "executions",
        "execution_workers",
        "capacity_dispatch_state",
        "execution_input_controls",
        "execution_store_meta",
    ):
        trigger = f"{table}_scheduler_change"
        # Startup used to drop and recreate each trigger.  Dropping
        # takes an ACCESS EXCLUSIVE lock, which waits for every
        # reader, including a running pg_dump backup, and queues
        # every later reader behind it; the service then failed on
        # its lock timeout and restarted in a loop until the backup
        # ended.  A trigger that already matches is left alone, and
        # CREATE OR REPLACE needs only a lock that readers share.
        if connection.execute(
            """
                    SELECT 1 FROM pg_trigger
                    WHERE tgrelid = to_regclass(%s) AND tgname = %s
                      AND NOT tgisinternal AND tgenabled = 'O'
                      AND tgfoid = to_regproc('notify_scheduler_change')
                      AND tgtype = %s AND tgqual IS NULL
                      AND cardinality(tgattr::int2[]) = 0
                    """,
            (table, trigger, SCHEDULER_TRIGGER_TYPE),
        ).fetchone() is None:
            connection.execute(
                f"CREATE OR REPLACE TRIGGER {trigger} "
                f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION notify_scheduler_change()"
            )


class _SchedulerTransaction:
    """A scheduler connection whose events reach this process only once committed.

    Readers and waiters do not hold the scheduler lock while a transaction
    runs, so an event shown before its commit could appear next to the old row.
    """

    def __init__(self, connection: Any, publish: Callable[[list[dict[str, Any]]], None]) -> None:
        self._connection = connection
        self._publish = publish
        self.pending_events: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def commit(self) -> None:
        self._connection.commit()
        events, self.pending_events = self.pending_events, []
        self._publish(events)

    def rollback(self) -> None:
        self._connection.rollback()
        self.pending_events = []


class SchedulerChanges:
    """This process's execution events and the wake-ups of its waiters."""

    def __init__(self, database: Any) -> None:
        self.database = database
        # Guards only this process's memory (event cursor, transient events,
        # wake-ups). Transactions never hold it: row updates keep each
        # execution consistent, and capacity is admitted without a lock
        # because a briefly exceeded limit is acceptable.
        self.lock = threading.RLock()
        self._condition = threading.Condition(self.lock)
        self._claim_changed = threading.Condition(self.lock)
        self._worker_control_changed = threading.Condition(self.lock)
        self._change_generations = {"execution": 0, "claim": 0, "control": 0}
        self._listener_guard = threading.Lock()
        self._listener_ready = threading.Event()
        self._listener_thread: threading.Thread | None = None
        self._listener_waiters = 0
        self._listener_idle_since = 0.0
        # Event cursors survive service replacement.  Epoch microseconds stay
        # within JSON's exact integer range while advancing across processes.
        self._event_sequence = time.time_ns() // 1_000
        self._transient_event_items: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=TRANSIENT_EVENTS_PER_EXECUTION)
        )

    @contextmanager
    def connect(self) -> Iterator[Any]:
        with self.database.connect() as connection:
            transaction = _SchedulerTransaction(connection, self.publish_events)
            yield transaction
        # connect() committed what was still open.
        self.publish_events(transaction.pending_events)

    def signal(self, kind: str) -> threading.Condition:
        return {
            "execution": self._condition,
            "claim": self._claim_changed,
            "control": self._worker_control_changed,
        }[kind]

    def notify(self, *kinds: str) -> None:
        with self.lock:
            for kind in kinds:
                self._change_generations[kind] += 1
                self.signal(kind).notify_all()

    def change_generation(self, kind: str) -> int:
        with self.lock:
            return self._change_generations[kind]

    def wait_for_change_signal(self, kind: str, seen: int, timeout: float) -> None:
        """Sleep until a change after ``seen``; the caller read the database unlocked."""
        condition = self.signal(kind)
        with condition:
            if self._change_generations[kind] == seen:
                condition.wait(timeout)

    def publish_events(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        with self.lock:
            for event in events:
                self._transient_event_items[event["execution_id"]].append(event)
        self.notify("execution")

    def transient_events(self, execution_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return list(self._transient_event_items.get(execution_id, ()))

    @contextmanager
    def change_notifications(self) -> Iterator[None]:
        with self._listener_guard:
            self._listener_waiters += 1
            self._listener_idle_since = 0.0
            thread = self._listener_thread
            if thread is None or not thread.is_alive():
                self._listener_ready = threading.Event()
                thread = threading.Thread(
                    target=self._listen_for_changes,
                    name="execution-scheduler-listener",
                    daemon=True,
                )
                self._listener_thread = thread
                thread.start()
            ready = self._listener_ready
        ready.wait(timeout=1.0)
        try:
            yield
        finally:
            with self._listener_guard:
                self._listener_waiters -= 1
                if self._listener_waiters == 0:
                    self._listener_idle_since = time.monotonic()

    def _listen_for_changes(self) -> None:
        try:
            import psycopg
            from psycopg import sql

            with psycopg.connect(
                self.database.dsn,
                autocommit=True,
                application_name="tfi:execution-listener",
            ) as connection:
                connection.execute(
                    sql.SQL("LISTEN {}").format(
                        sql.Identifier(SCHEDULER_CHANGE_CHANNEL)
                    )
                )
                self._listener_ready.set()
                while True:
                    for notification in connection.notifies(
                        timeout=3.0,
                        stop_after=1,
                    ):
                        schema, separator, payload = str(notification.payload or "").partition(":")
                        if not separator or schema != self.database.schema:
                            continue
                        categories = {
                            item.strip()
                            for item in payload.split(",")
                            if item.strip()
                        }
                        self.notify(*(
                            kind for kind in ("execution", "claim", "control")
                            if kind in categories
                        ))
                    with self._listener_guard:
                        if (
                            self._listener_waiters == 0
                            and self._listener_idle_since
                            and time.monotonic() - self._listener_idle_since >= 1.0
                        ):
                            return
        except Exception:
            logging.getLogger(__name__).exception(
                "Execution Scheduler PostgreSQL notification listener failed"
            )
            self._listener_ready.set()
        finally:
            with self._listener_guard:
                if self._listener_thread is threading.current_thread():
                    self._listener_thread = None

    def event(
        self,
        connection: Any,
        execution_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            self._event_sequence = max(
                self._event_sequence + 1,
                time.time_ns() // 1_000,
            )
            sequence = self._event_sequence
        event = {
            "execution_id": execution_id,
            "sequence": sequence,
            "type": str(event_type or ""),
            "created_at": execution_timestamp(),
            "details": dict(details or {}),
        }
        from .execution_lifecycle import scheduler_transition
        scheduler_transition(connection, execution_id, event_type, event["created_at"])
        if event_type == "started":
            connection.execute(
                "INSERT INTO execution_store_meta(key,value) VALUES (%s,%s) ON CONFLICT(key) DO NOTHING",
                (f"started:{execution_id}", compact_json(event)),
            )
        pending = getattr(connection, "pending_events", None)
        if pending is None:
            self.publish_events([event])
        else:
            pending.append(event)
        return dict(event)

    def events(self, connection: Any, execution_id: str, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        cursor = max(0, int(after_sequence))
        items = {int(item["sequence"]): dict(item) for item in self.transient_events(execution_id)}
        row = connection.execute("SELECT value FROM execution_store_meta WHERE key=%s", (f"started:{execution_id}",)).fetchone()
        if row:
            event = json_object(row["value"])
            items[int(event["sequence"])] = event
        return [items[key] for key in sorted(items) if key > cursor]

    def forget_transient_events(self, execution_ids: Iterable[str]) -> None:
        """Drop the in-memory events of executions that no longer exist."""
        with self.lock:
            for execution_id in execution_ids:
                self._transient_event_items.pop(execution_id, None)
