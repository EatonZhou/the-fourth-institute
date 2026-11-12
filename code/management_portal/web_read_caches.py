"""Process-local read caches of Portal Web and the change stream payload that expires them.

Agent status reads, scheduled and triggered work, active agents and the Inbox are
coalesced here; ``_portal_change_payload`` tells a subscriber what changed and drops
the cached copies the change made stale.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
import threading
import time
import weakref
from concurrent.futures import Future
from contextvars import copy_context

from management_portal import agent_status_backend, scheduled_triggered_monitor
from management_portal.http_errors import failure_frames
from management_portal.society_service import get_society_service

LOGGER = logging.getLogger(__name__)

_ACTIVE_AGENTS_CACHE_TTL_SECONDS = 5.0


_NO_COLD_READ_VALUE = object()


class _CoalescingReadCache:
    """Small process-local cache for identical, immutable read projections."""

    def __init__(self, ttl_seconds: float, *, max_entries: int = 128,
                 stale_retention_seconds: float = 300.0) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self.stale_retention_seconds = max(0.0, float(stale_retention_seconds))
        self._lock = threading.Lock()
        self._values: OrderedDict[object, tuple[float, object]] = OrderedDict()
        self._retained_until: dict[object, float] = {}
        self._inflight: dict[object, Future] = {}
        self._generation = 0
        self._timer: threading.Timer | None = None
        self._timer_token: object | None = None

    def _drop(self, key: object) -> None:
        self._values.pop(key, None)
        self._retained_until.pop(key, None)

    def _purge(self, now: float) -> None:
        for key in [key for key, deadline in self._retained_until.items() if deadline <= now]:
            self._drop(key)

    @staticmethod
    def _expire_idle(owner, token) -> None:
        cache = owner()
        if cache is None:
            return
        with cache._lock:
            if cache._timer_token is not token:
                return
            cache._timer = cache._timer_token = None
            cache._purge(time.monotonic())
            cache._schedule()

    def _schedule(self) -> None:
        if not self._values:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = self._timer_token = None
            return
        if self._timer is None:
            deadline = min(self._retained_until.values())
            token = object()
            timer = threading.Timer(max(.001, deadline - time.monotonic()),
                                    self._expire_idle, args=(weakref.ref(self), token))
            timer.name = 'portal-read-cache-expiry'
            timer.daemon = True
            self._timer, self._timer_token = timer, token
            try:
                timer.start()
            except RuntimeError:
                # A cache expiry worker is optional. Without it retain no
                # completed snapshots; the current reader still gets its result.
                self._timer = self._timer_token = None
                self._values.clear()
                self._retained_until.clear()
                LOGGER.warning('Portal read cache expiry thread unavailable; completed snapshots not retained')

    def _store(self, key: object, value: object) -> None:
        now = time.monotonic()
        self._purge(now)
        self._values[key] = (now + self.ttl_seconds, value)
        self._values.move_to_end(key)
        self._retained_until[key] = now + (max(self.ttl_seconds, self.stale_retention_seconds)
                                          if self.stale_retention_seconds else self.ttl_seconds)
        while len(self._values) > self.max_entries:
            self._drop(next(iter(self._values)))
        self._schedule()

    def diagnostics(self) -> dict:
        from management_common.memory_observation import json_footprint
        now = time.monotonic()
        with self._lock:
            values = list(self._values.values())
            metadata = {'entries': len(values), 'inflight': len(self._inflight),
                        'max_entries': self.max_entries, 'fresh_ttl_seconds': self.ttl_seconds,
                        'stale_idle_seconds': self.stale_retention_seconds,
                        'stale': sum(deadline <= now for deadline, _ in values),
                        'retention_overdue': sum(deadline <= now for deadline in self._retained_until.values()),
                        'expiry_timer': self._timer is not None}
        return {**metadata, **json_footprint([value for _, value in values])}

    def _refresh(
        self,
        key: object,
        builder,
        refresh: Future,
        generation: int,
        *,
        stale: object,
        has_stale: bool,
        stale_error_transform=None,
    ):
        try:
            result = builder()
        except BaseException as exc:
            failed = stale_error_transform(stale, exc) if has_stale and stale_error_transform else stale
            with self._lock:
                if self._inflight.get(key) is refresh:
                    self._inflight.pop(key, None)
                    if has_stale and stale_error_transform and generation == self._generation:
                        self._store(key, failed)
            if has_stale:
                # Readers keep the previous (or cold) value; the failure stays visible here.
                LOGGER.warning("Portal read cache rebuild failed: %s; frames=%s; serving the previous value",
                               type(exc).__name__, failure_frames(exc))
                refresh.set_result(failed)
                return failed
            refresh.set_exception(exc)
            raise
        with self._lock:
            if self._inflight.get(key) is refresh:
                self._inflight.pop(key, None)
                if generation == self._generation:
                    self._store(key, result)
        refresh.set_result(result)
        return result

    def get(
        self,
        key: object,
        builder,
        *,
        timeout: float = 90.0,
        cold_value: object = _NO_COLD_READ_VALUE,
        stale_transform=None,
        allow_stale: bool = True,
        stale_error_transform=None,
    ):
        now = time.monotonic()
        owns_refresh = False
        missing = object()
        stale: object = missing
        with self._lock:
            self._purge(now)
            cached = self._values.get(key)
            if cached:
                self._values.move_to_end(key)
                if self.stale_retention_seconds:
                    self._retained_until[key] = now + max(self.ttl_seconds, self.stale_retention_seconds)
            self._schedule()
            if cached and now < cached[0]:
                return cached[1]
            if cached and allow_stale:
                stale = cached[1]
            refresh = self._inflight.get(key)
            if refresh is None:
                refresh = Future()
                self._inflight[key] = refresh
                generation = self._generation
                owns_refresh = True
        if not owns_refresh:
            if stale is not missing:
                return stale_transform(stale) if stale_transform else stale
            if cold_value is not _NO_COLD_READ_VALUE:
                return cold_value
            return refresh.result(timeout=timeout)

        if stale is not missing or cold_value is not _NO_COLD_READ_VALUE:
            has_snapshot = stale is not missing
            previous = stale if has_snapshot else cold_value
            try:
                threading.Thread(
                    target=self._refresh,
                    args=(key, builder, refresh, generation),
                    kwargs={"stale": previous, "has_stale": True,
                            "stale_error_transform": stale_error_transform if has_snapshot else None},
                    name="portal-read-cache-refresh" if has_snapshot else "portal-read-cache-cold-refresh",
                    daemon=True,
                ).start()
            except RuntimeError:
                # No worker owns this future. Release joiners and let the next
                # read retry admission without running slow IO in this caller.
                with self._lock:
                    if self._inflight.get(key) is refresh:
                        self._inflight.pop(key, None)
                refresh.set_result(previous)
                LOGGER.warning("Portal read cache refresh thread unavailable; refresh admission released")
            return stale_transform(previous) if has_snapshot and stale_transform else previous
        return self._refresh(
            key,
            builder,
            refresh,
            generation,
            stale=missing,
            has_stale=False,
        )

    def invalidate(self, *, preserve_stale: bool = False) -> None:
        with self._lock:
            self._generation += 1
            if preserve_stale and self.stale_retention_seconds:
                for key, (_expires_at, value) in self._values.items():
                    self._values[key] = (0.0, value)
            else:
                self._values.clear()
                self._retained_until.clear()
            self._schedule()

    def expire(self, key: object) -> None:
        """Refresh one viewer; a pre-change rebuild cannot replace their next read."""
        with self._lock:
            if key in self._values:
                self._values[key] = (0.0, self._values[key][1])
                self._inflight.pop(key, None)

    def discard(self, matches) -> None:
        """Forget matching entries outright; their next read is built from committed state."""
        with self._lock:
            for store in (self._values, self._inflight):
                for key in [key for key in store if matches(key)]:
                    del store[key]
                    self._retained_until.pop(key, None)
            self._schedule()


_SCHEDULED_TRIGGERED_READ_CACHE = _CoalescingReadCache(30.0)
_AGENT_CONVERSATION_READ_CACHE = _CoalescingReadCache(2.0, stale_retention_seconds=0)
_ACTIVE_AGENTS_CACHE = _CoalescingReadCache(_ACTIVE_AGENTS_CACHE_TTL_SECONDS)


def memory_diagnostics() -> dict:
    return {name: cache.diagnostics() for name, cache in (
        ('inbox', _AGENT_CONVERSATION_READ_CACHE), ('scheduled', _SCHEDULED_TRIGGERED_READ_CACHE),
        ('active_agents', _ACTIVE_AGENTS_CACHE))}


def _cached_agent_status_summary(actor: str) -> dict:
    # The backend keeps the stored summary and rebuilds it in milliseconds. A
    # stale-while-revalidate cache here would answer every poll with the
    # summary the previous poll caused, long enough for quota readings to expire.
    return agent_status_backend.get_agent_status_summary(str(actor or ""))


def _cached_scheduled_triggered_status(actor: str, *, fresh: bool = False) -> dict:
    context = copy_context()
    def build() -> dict:
        try:
            return scheduled_triggered_monitor.get_scheduled_triggered_status(str(actor or ""))
        except Exception as exc:
            return {"status": "error", "loaded": False, "items": [], "error": str(exc)}

    if fresh:
        # An explicit post-mutation read must neither return a cached plan nor
        # join a rebuild that began before the instruction committed.
        _SCHEDULED_TRIGGERED_READ_CACHE.discard(lambda key: key == str(actor or ""))
        return _SCHEDULED_TRIGGERED_READ_CACHE.get(
            str(actor or ""), lambda: context.run(build), allow_stale=False,
        )
    return _SCHEDULED_TRIGGERED_READ_CACHE.get(
        str(actor or ""),
        lambda: context.run(build),
        cold_value={"status": "loading", "loaded": False, "items": []},
        stale_transform=lambda value: {**value, "refreshing": True},
    )


def _invalidate_agent_conversation_cache() -> None:
    # Conversation mutations already carry an authoritative receipt to the
    # initiating browser.  Keep the last committed index available while one
    # background rebuild incorporates the mutation for other tabs.
    _AGENT_CONVERSATION_READ_CACHE.invalidate(preserve_stale=True)


def _forget_cached_inbox(actor: str) -> None:
    # The viewer re-reads the Inbox right after changing a read state; a copy
    # cached before the change would bring the unread mark back. Other
    # viewers' copies do not contain this viewer's read state.
    _AGENT_CONVERSATION_READ_CACHE.discard(lambda key: key[0] == actor)


def _invalidate_active_agents_cache(actor: str) -> None:
    """Discard an actor snapshot and detach any older refresh in flight."""

    _ACTIVE_AGENTS_CACHE.discard(lambda key: key == str(actor or ""))


def _cached_active_agents(actor: str) -> dict:
    """Collapse refresh bursts without holding a lock during projection IO."""
    return _ACTIVE_AGENTS_CACHE.get(
        str(actor or ''),
        lambda: get_society_service().projection.collect_active_agents(actor=str(actor or '')),
        timeout=45.0,
        stale_transform=lambda value: {**value, 'refreshing': True},
        stale_error_transform=lambda value, error: {**value, 'refreshing': False, 'error': str(error)},
    )


def _notification_target(subject: str) -> tuple[str, str]:
    parts = str(subject or "").split(".")
    if len(parts) < 4 or parts[0] != "portal":
        return "", ""
    domain = parts[1]
    if domain in {"execution", "resident", "lineage"}:
        return domain, parts[3]
    if domain == "projection" and len(parts) >= 5:
        return domain, parts[4]
    if domain == "resource" and len(parts) >= 5:
        return domain, parts[4]
    return domain, ""


def _portal_change_payload(
    actor: str,
    notifications: list,
    domains: set[str],
) -> dict:
    from management_portal.portal_changes import PortalChanges

    changes = PortalChanges(
        actor, domains, on_active_agents_snapshot=lambda: _invalidate_active_agents_cache(actor),
    )
    for notification in notifications:
        changes.add(notification, *_notification_target(str(notification.subject or "")))
    changes.authorize()
    if changes.inbox_changed:
        _forget_cached_inbox(actor)
    return changes.payload()
