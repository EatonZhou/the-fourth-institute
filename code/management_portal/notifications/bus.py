from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .config import nats_client_name, nats_enabled, nats_url
from .subjects import business_projection_subject


MAX_NOTIFICATION_BYTES = 900 * 1024
MAX_LOCAL_NOTIFICATIONS = 4096


@dataclass(frozen=True)
class Notification:
    sequence: int
    subject: str
    payload: dict[str, Any]
    received_at: float


class NotificationHub:
    """Bounded, process-local cursor used by authenticated SSE handlers."""

    def __init__(self, max_items: int = MAX_LOCAL_NOTIFICATIONS) -> None:
        self._items: deque[Notification] = deque(maxlen=max(32, int(max_items)))
        self._sequence = 0
        self._subscriber_sequence = 0
        self._subscribers: dict[int, Callable[[Notification], None]] = {}
        self._condition = threading.Condition()

    @property
    def sequence(self) -> int:
        with self._condition:
            return self._sequence

    def publish(self, subject: str, payload: dict[str, Any]) -> Notification:
        with self._condition:
            self._sequence += 1
            notification = Notification(
                sequence=self._sequence,
                subject=str(subject or ""),
                payload=dict(payload),
                received_at=time.time(),
            )
            self._items.append(notification)
            subscribers = list(self._subscribers.values())
            self._condition.notify_all()
        for subscriber in subscribers:
            try:
                subscriber(notification)
            except Exception:
                continue
        return notification

    def subscribe(
        self,
        callback: Callable[[Notification], None],
    ) -> Callable[[], None]:
        with self._condition:
            self._subscriber_sequence += 1
            subscription_id = self._subscriber_sequence
            self._subscribers[subscription_id] = callback

        def unsubscribe() -> None:
            with self._condition:
                self._subscribers.pop(subscription_id, None)

        return unsubscribe

    def cursor_has_gap(self, after_sequence: int) -> bool:
        cursor = max(0, int(after_sequence))
        with self._condition:
            return bool(
                self._items
                and cursor < self._items[0].sequence - 1
            )

    def wait(
        self,
        after_sequence: int,
        *,
        predicate: Callable[[Notification], bool] | None = None,
        timeout: float = 45.0,
    ) -> tuple[list[Notification], int]:
        cursor = max(0, int(after_sequence))
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._condition:
            while True:
                matches = [
                    item
                    for item in self._items
                    if item.sequence > cursor
                    and (predicate is None or predicate(item))
                ]
                if matches:
                    return matches, self._sequence
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return [], self._sequence
                self._condition.wait(remaining)


class NatsNotificationPublisher:
    """Best-effort NATS publisher with a bounded, non-blocking caller path."""

    def __init__(self, *, component: str = "publisher", queue_size: int = 2048) -> None:
        self.component = str(component or "publisher")
        self._queue: queue.Queue[tuple[str, bytes]] = queue.Queue(
            maxsize=max(32, int(queue_size))
        )
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._published = 0
        self._dropped = 0
        self._last_error = ""
        self._connected = False

    def start(self) -> None:
        if not nats_enabled():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"portal-nats-{self.component}",
                daemon=True,
            )
            self._thread.start()

    def close(self, timeout: float = 6.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._connected = False

    def publish(self, subject: str, payload: dict[str, Any] | None = None) -> bool:
        body = json.dumps(
            payload or {"kind": "changed"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(body) > MAX_NOTIFICATION_BYTES:
            raise ValueError("notification payload exceeds 900 KiB")
        self.start()
        if not nats_enabled():
            return False
        try:
            self._queue.put_nowait((str(subject or ""), body))
            return True
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": nats_enabled(),
                "url": nats_url(),
                "connected": self._connected,
                "queued": self._queue.qsize(),
                "published": self._published,
                "dropped": self._dropped,
                "last_error": self._last_error,
            }

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        client = None
        while not self._stop.is_set():
            if client is None or not client.is_connected:
                try:
                    from .nats_client import connect_notifications

                    client = await connect_notifications(
                        servers=[nats_url()],
                        name=nats_client_name(self.component),
                        connect_timeout=3,
                        allow_reconnect=True,
                        max_reconnect_attempts=-1,
                        reconnect_time_wait=1,
                    )
                    with self._lock:
                        self._connected = True
                        self._last_error = ""
                except Exception as exc:
                    with self._lock:
                        self._connected = False
                        self._last_error = f"{exc.__class__.__name__}: {exc}"[:500]
                    await asyncio.sleep(1)
                    continue
            try:
                subject, body = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.1)
                continue
            try:
                await client.publish(subject, body)
                await client.flush(timeout=3)
                with self._lock:
                    self._published += 1
                    self._last_error = ""
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = f"{exc.__class__.__name__}: {exc}"[:500]
                try:
                    self._queue.put_nowait((subject, body))
                except queue.Full:
                    with self._lock:
                        self._dropped += 1
                try:
                    await client.close()
                except Exception:
                    pass
                client = None
        if client is not None:
            try:
                await client.drain()
            except Exception:
                try:
                    await client.close()
                except Exception:
                    pass


class NatsNotificationListener:
    def __init__(self, hub: NotificationHub, *, component: str = "portal-gateway") -> None:
        self.hub = hub
        self.component = component
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connected = False
        self._received = 0
        self._last_error = ""

    def start(self) -> None:
        if not nats_enabled():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="portal-nats-listener",
                daemon=True,
            )
            self._thread.start()

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": nats_enabled(),
                "url": nats_url(),
                "connected": self._connected,
                "received": self._received,
                "last_error": self._last_error,
                "cursor": self.hub.sequence,
            }

    def close(self, timeout: float = 6.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None
            self._connected = False

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        while not self._stop.is_set():
            client = None
            try:
                from .nats_client import connect_notifications

                client = await connect_notifications(
                    servers=[nats_url()],
                    name=nats_client_name(self.component),
                    connect_timeout=3,
                    allow_reconnect=True,
                    max_reconnect_attempts=-1,
                    reconnect_time_wait=1,
                )

                async def receive(message: Any) -> None:
                    try:
                        payload = json.loads(bytes(message.data))
                        if not isinstance(payload, dict):
                            raise ValueError("notification payload must be an object")
                    except Exception:
                        payload = {"kind": "changed"}
                    self.hub.publish(str(message.subject or ""), payload)
                    with self._lock:
                        self._received += 1

                await client.subscribe("portal.>", cb=receive)
                with self._lock:
                    self._connected = True
                    self._last_error = ""
                self.hub.publish(
                    "portal.gateway.reconnected",
                    {"kind": "snapshot-required"},
                )
                while not self._stop.is_set() and client.is_connected:
                    await asyncio.sleep(0.25)
            except Exception as exc:
                with self._lock:
                    self._connected = False
                    self._last_error = f"{exc.__class__.__name__}: {exc}"[:500]
                await asyncio.sleep(1)
            finally:
                if client is not None:
                    try:
                        await client.close()
                    except Exception:
                        pass


_HUB = NotificationHub()
_PUBLISHER = NatsNotificationPublisher()
_LISTENER = NatsNotificationListener(_HUB)


def get_notification_hub() -> NotificationHub:
    return _HUB


def get_notification_publisher() -> NatsNotificationPublisher:
    return _PUBLISHER


def ensure_notification_listener() -> NatsNotificationListener:
    _LISTENER.start()
    return _LISTENER


def close_notifications() -> None:
    _LISTENER.close()
    _PUBLISHER.close()


def publish_change(subject: str, *, fields: list[str] | None = None) -> bool:
    payload: dict[str, Any] = {"kind": "changed"}
    if fields:
        payload["fields"] = [str(item) for item in fields if str(item)]
    return _PUBLISHER.publish(subject, payload)


def publish_local_projection_change(
    projection: str,
    target_id: str,
    *,
    actor: str = "",
    actors: list[str] | None = None,
    fields: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> Notification:
    """Notify browser projections after a Portal-local durable commit."""

    payload: dict[str, Any] = {"kind": "changed"}
    if actor:
        payload["actor"] = str(actor)
    clean_actors = sorted({
        str(item).strip()
        for item in (actors or [])
        if str(item).strip()
    })
    if clean_actors:
        payload["actors"] = clean_actors
    if fields:
        payload["fields"] = [str(item) for item in fields if str(item)]
    if details:
        payload["details"] = dict(details)
    return _HUB.publish(
        business_projection_subject(projection, target_id),
        payload,
    )


def publish_projection_change(projection: str, target_id: str, **kwargs) -> Notification:
    """Deliver a committed viewer change locally and to every Portal instance.

    NATS may echo the notification. Consumers coalesce invalidations and read
    authoritative state, so duplicate delivery is safe.
    """
    notification = publish_local_projection_change(projection, target_id, **kwargs)
    _PUBLISHER.publish(notification.subject, notification.payload)
    return notification
