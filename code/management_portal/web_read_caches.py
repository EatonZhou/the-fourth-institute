"""Process-local read caches of Portal Web and the change stream payload that expires them.

Agent status reads, scheduled and triggered work, active agents and the Inbox are
coalesced here; ``_portal_change_payload`` tells a subscriber what changed and drops
the cached copies the change made stale.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future
from contextvars import copy_context

from management_portal import agent_status_backend, scheduled_triggered_monitor
from management_portal.http_errors import failure_frames
from management_portal.society_service import get_society_service

LOGGER = logging.getLogger(__name__)

_ACTIVE_AGENTS_CACHE_TTL_SECONDS = 5.0
_ACTIVE_AGENTS_CACHE_LOCK = threading.Lock()
_ACTIVE_AGENTS_CACHE: dict[str, tuple[float, dict]] = {}
_ACTIVE_AGENTS_INFLIGHT: dict[str, Future[dict]] = {}


_NO_COLD_READ_VALUE = object()


class _CoalescingReadCache:
    """Small process-local cache for identical, immutable read projections."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._values: dict[object, tuple[float, object]] = {}
        self._inflight: dict[object, Future] = {}
        self._generation = 0

    def _refresh(
        self,
        key: object,
        builder,
        refresh: Future,
        generation: int,
        *,
        stale: object,
        has_stale: bool,
    ):
        try:
            result = builder()
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(key) is refresh:
                    self._inflight.pop(key, None)
            if has_stale:
                # Readers keep the previous (or cold) value; the failure stays visible here.
                LOGGER.warning("Portal read cache rebuild failed: %s; frames=%s; serving the previous value",
                               type(exc).__name__, failure_frames(exc))
                refresh.set_result(stale)
                return stale
            refresh.set_exception(exc)
            raise
        with self._lock:
            if self._inflight.get(key) is refresh:
                self._inflight.pop(key, None)
                if generation == self._generation:
                    self._values[key] = (
                        time.monotonic() + self.ttl_seconds,
                        result,
                    )
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
    ):
        now = time.monotonic()
        owns_refresh = False
        missing = object()
        stale: object = missing
        with self._lock:
            cached = self._values.get(key)
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

        if stale is not missing:
            threading.Thread(
                target=self._refresh,
                args=(key, builder, refresh, generation),
                kwargs={"stale": stale, "has_stale": True},
                name="portal-read-cache-refresh",
                daemon=True,
            ).start()
            return stale_transform(stale) if stale_transform else stale
        if cold_value is not _NO_COLD_READ_VALUE:
            threading.Thread(
                target=self._refresh,
                args=(key, builder, refresh, generation),
                kwargs={"stale": cold_value, "has_stale": True},
                name="portal-read-cache-cold-refresh",
                daemon=True,
            ).start()
            return cold_value
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
            if preserve_stale:
                self._values = {
                    key: (0.0, value)
                    for key, (_expires_at, value) in self._values.items()
                }
            else:
                self._values.clear()

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


_SCHEDULED_TRIGGERED_READ_CACHE = _CoalescingReadCache(30.0)
_AGENT_CONVERSATION_READ_CACHE = _CoalescingReadCache(2.0)


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

    cache_key = str(actor or "")
    with _ACTIVE_AGENTS_CACHE_LOCK:
        _ACTIVE_AGENTS_CACHE.pop(cache_key, None)
        _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)


def _cached_active_agents(actor: str) -> dict:
    """Collapse refresh bursts without holding a lock during projection IO."""

    cache_key = str(actor or "")
    now = time.monotonic()
    owns_refresh = False
    missing = object()
    stale: object = missing
    with _ACTIVE_AGENTS_CACHE_LOCK:
        cached = _ACTIVE_AGENTS_CACHE.get(cache_key)
        if cached and now - cached[0] < _ACTIVE_AGENTS_CACHE_TTL_SECONDS:
            return cached[1]
        if cached:
            stale = cached[1]
        refresh = _ACTIVE_AGENTS_INFLIGHT.get(cache_key)
        if refresh is None:
            refresh = Future()
            _ACTIVE_AGENTS_INFLIGHT[cache_key] = refresh
            owns_refresh = True
    if not owns_refresh:
        if stale is not missing:
            return {**stale, "refreshing": True}
        return refresh.result(timeout=45.0)

    def build() -> dict:
        try:
            result = get_society_service().projection.collect_active_agents(
                actor=cache_key,
            )
        except Exception as exc:
            failed = {**stale, "refreshing": False, "error": str(exc)} if stale is not missing else None
            with _ACTIVE_AGENTS_CACHE_LOCK:
                if _ACTIVE_AGENTS_INFLIGHT.get(cache_key) is refresh:
                    if failed is not None:
                        _ACTIVE_AGENTS_CACHE[cache_key] = (time.monotonic(), failed)
                    _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)
            if stale is not missing:
                refresh.set_result(failed)
                return failed
            refresh.set_exception(exc)
            raise
        with _ACTIVE_AGENTS_CACHE_LOCK:
            if _ACTIVE_AGENTS_INFLIGHT.get(cache_key) is refresh:
                _ACTIVE_AGENTS_CACHE[cache_key] = (time.monotonic(), result)
                _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)
        refresh.set_result(result)
        return result

    if stale is not missing:
        threading.Thread(
            target=build,
            name="active-agents-cache-refresh",
            daemon=True,
        ).start()
        return {**stale, "refreshing": True}
    return build()


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
