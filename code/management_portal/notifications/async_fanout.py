from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable

from .bus import Notification, NotificationHub

LOGGER = logging.getLogger(__name__)

DEFAULT_PENDING_NOTIFICATIONS = 4096
DEFAULT_SUBSCRIBER_QUEUE_SIZE = 64

_CLOSED = object()
_SNAPSHOT_REQUIRED = object()


@dataclass(frozen=True)
class AsyncNotificationBatch:
    notifications: tuple[Notification, ...] = ()
    snapshot_required: bool = False
    closed: bool = False


class AsyncNotificationSubscription:
    def __init__(
        self,
        fanout: "AsyncNotificationFanout",
        subscription_id: int,
        predicate: Callable[[Notification], bool],
        queue_size: int,
    ) -> None:
        self._fanout = fanout
        self._subscription_id = subscription_id
        self._predicate = predicate
        self._queue: asyncio.Queue[Notification | object] = asyncio.Queue(
            maxsize=max(1, int(queue_size)),
        )
        self._closed = False
        self._snapshot_pending = False

    async def wait(self, timeout: float) -> AsyncNotificationBatch | None:
        try:
            first = await asyncio.wait_for(
                self._queue.get(),
                timeout=max(0.0, float(timeout)),
            )
        except asyncio.TimeoutError:
            return None
        if first is _CLOSED:
            return AsyncNotificationBatch(closed=True)
        if first is _SNAPSHOT_REQUIRED:
            self._snapshot_pending = False
            return AsyncNotificationBatch(snapshot_required=True)

        notifications = [first]
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item is _CLOSED:
                self._closed = True
                break
            if item is _SNAPSHOT_REQUIRED:
                self._snapshot_pending = False
                return AsyncNotificationBatch(snapshot_required=True)
            notifications.append(item)
        return AsyncNotificationBatch(notifications=tuple(notifications))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._fanout._unsubscribe(self._subscription_id)

    def _offer(self, notification: Notification) -> None:
        if self._closed or self._snapshot_pending:
            return
        try:
            if not self._predicate(notification):
                return
        except Exception:
            LOGGER.warning("Notification filter failed; this subscriber misses the notification", exc_info=True)
            return
        try:
            self._queue.put_nowait(notification)
        except asyncio.QueueFull:
            self._require_snapshot()

    def _require_snapshot(self) -> None:
        if self._closed or self._snapshot_pending:
            return
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._snapshot_pending = True
        self._queue.put_nowait(_SNAPSHOT_REQUIRED)

    def _close_from_fanout(self) -> None:
        if self._closed:
            return
        self._closed = True
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self._queue.put_nowait(_CLOSED)


class AsyncNotificationFanout:
    """Bridge a thread-safe NotificationHub into bounded asyncio queues."""

    def __init__(
        self,
        hub: NotificationHub,
        *,
        pending_limit: int = DEFAULT_PENDING_NOTIFICATIONS,
        subscriber_queue_size: int = DEFAULT_SUBSCRIBER_QUEUE_SIZE,
    ) -> None:
        self._hub = hub
        self._pending_limit = max(1, int(pending_limit))
        self._subscriber_queue_size = max(1, int(subscriber_queue_size))
        self._pending: deque[Notification] = deque()
        self._pending_overflow = False
        self._drain_scheduled = False
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._unsubscribe_hub: Callable[[], None] | None = None
        self._subscribers: dict[int, AsyncNotificationSubscription] = {}
        self._subscriber_sequence = 0
        self._closed = True

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        with self._lock:
            if not self._closed:
                raise RuntimeError("async notification fanout is already started")
            self._loop = loop
            self._closed = False
            self._pending.clear()
            self._pending_overflow = False
            self._drain_scheduled = False
        self._unsubscribe_hub = self._hub.subscribe(self._accept_from_hub)

    def subscribe(
        self,
        predicate: Callable[[Notification], bool] | None = None,
    ) -> AsyncNotificationSubscription:
        if self._closed or self._loop is None:
            raise RuntimeError("async notification fanout is not running")
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("async notification subscription uses another event loop")
        self._subscriber_sequence += 1
        subscription = AsyncNotificationSubscription(
            self,
            self._subscriber_sequence,
            predicate or (lambda _notification: True),
            self._subscriber_queue_size,
        )
        self._subscribers[self._subscriber_sequence] = subscription
        return subscription

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            unsubscribe_hub = self._unsubscribe_hub
            self._unsubscribe_hub = None
            self._pending.clear()
            self._pending_overflow = False
            self._drain_scheduled = False
        if unsubscribe_hub is not None:
            unsubscribe_hub()
        subscribers = tuple(self._subscribers.values())
        self._subscribers.clear()
        for subscription in subscribers:
            subscription._close_from_fanout()

    def _unsubscribe(self, subscription_id: int) -> None:
        self._subscribers.pop(subscription_id, None)

    def _accept_from_hub(self, notification: Notification) -> None:
        with self._lock:
            if self._closed or self._loop is None:
                return
            if len(self._pending) >= self._pending_limit:
                self._pending.clear()
                self._pending_overflow = True
            elif not self._pending_overflow:
                self._pending.append(notification)
            if self._drain_scheduled:
                return
            self._drain_scheduled = True
            loop = self._loop
        try:
            loop.call_soon_threadsafe(self._drain_pending)
        except RuntimeError:
            with self._lock:
                self._drain_scheduled = False

    def _drain_pending(self) -> None:
        with self._lock:
            if self._closed:
                self._drain_scheduled = False
                return
            pending = tuple(self._pending)
            overflow = self._pending_overflow
            self._pending.clear()
            self._pending_overflow = False
            self._drain_scheduled = False
        subscribers = tuple(self._subscribers.values())
        if overflow:
            for subscription in subscribers:
                subscription._require_snapshot()
            return
        for notification in pending:
            for subscription in subscribers:
                subscription._offer(notification)
