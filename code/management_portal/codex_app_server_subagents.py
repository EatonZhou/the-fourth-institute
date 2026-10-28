"""The subagent threads a Codex turn starts.

A subagent runs in a thread of its own.  The router learns each thread's
parent from the notifications, delivers a descendant's notifications to the
turn of its root thread, joins running subagent threads so that their steps
reach this connection, and leaves them when they end.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

from .codex_app_server_protocol import CodexRequestTimeouts

if TYPE_CHECKING:
    from .codex_app_server_events import CodexActiveWork
    from .codex_app_server_rpc import CodexRpcConnection


MAX_REMEMBERED_SUBAGENT_THREADS = 1024


class CodexSubagentRouter:
    def __init__(
        self,
        rpc: CodexRpcConnection,
        work: CodexActiveWork,
        timeouts: CodexRequestTimeouts,
    ) -> None:
        self._rpc = rpc
        self._work = work
        self._state_lock = work.state_lock
        self.timeouts = timeouts
        self._child_threads: dict[str, tuple[str, str]] = {}
        self._unclaimed_child_events: deque[tuple[str, dict[str, Any]]] = deque(maxlen=256)
        # Subagent threads this client joined to follow their steps.
        self._followed_children: set[str] = set()
        # The turn each subagent thread last ran, to stop that subagent alone.
        self._child_turns: dict[str, str] = {}

    def forget_all(self) -> None:
        """The connection ended: the parent edges and followed threads went with it."""

        with self._state_lock:
            self._followed_children.clear()
            self._child_threads.clear()
            self._child_turns.clear()
            self._unclaimed_child_events.clear()

    def running_turn(self, thread_id: str, child: str) -> tuple[bool, str]:
        """Whether ``child`` is a subagent thread of ``thread_id``, and the turn it last ran."""

        with self._state_lock:
            known = bool(child) and self._child_threads.get(child, ("", ""))[0] == thread_id
            return known, self._child_turns.get(child, "")

    def _root_of(self, child: str) -> str:
        seen: set[str] = set()
        while child in self._child_threads and child not in seen:
            seen.add(child)
            child = self._child_threads[child][0]
        return child

    def route(self, method: str, params: dict[str, Any]) -> bool:
        """Deliver descendants to their active root, never to another thread's turn.

        A child can publish before spawn completion. Keep a bounded transport
        buffer until an explicit parent edge is known; body equality is irrelevant.
        """
        thread = params.get("thread") or {}
        thread_id = str(params.get("threadId") or thread.get("id") or "")
        item = params.get("item") or {}
        edges = _announced_edges(method, thread_id, thread, item)
        # Codex 0.162 tells the parent a subagent started or ended; nothing of
        # the child's own thread reaches a client that did not join it.
        activity_child = str(item.get("agentThreadId") or "") if item.get("type") == "subAgentActivity" else ""
        # A finished child the parent gives more work (``followup_task``) is ``interacted``.
        activity_starts = activity_child and item.get("kind") in {"started", "interacted"}
        if activity_starts:
            edges.append((activity_child, thread_id, str(item.get("id") or "")))
        follow: list[str] = []
        release: list[str] = []
        replay: list[tuple[str, dict[str, Any]]] = []
        with self._state_lock:
            roots = self._work.roots()
            for child, parent, call in edges:
                # A grandchild can be announced before its parent's edge.
                # Record the explicit relationship now; route only after the
                # entire chain reaches an active root below.
                if child and parent and child != parent:
                    self._child_threads[child] = (parent, call or child)
            if edges:
                replay = self._claim_buffered_events(roots)
            is_root = thread_id in roots
            root = self._root_of(thread_id)
            waiter = roots.get(root) if thread_id in self._child_threads else None
            if thread_id in self._child_threads:
                self._track_child_turn(method, thread_id, params)
            ancestors = self._ancestors(thread_id)
            if waiter is None and not is_root and roots and method in {"item/started", "item/completed"}:
                self._unclaimed_child_events.append((method, params))
            if activity_starts:
                if self._root_of(activity_child) in roots and activity_child not in self._followed_children:
                    self._followed_children.add(activity_child)
                    follow.append(activity_child)
            elif activity_child and item.get("kind") in {"completed", "interrupted"}:
                self._child_turns.pop(activity_child, None)
                if activity_child in self._followed_children:
                    self._followed_children.discard(activity_child)
                    release.append(activity_child)
            if len(self._child_threads) > MAX_REMEMBERED_SUBAGENT_THREADS:
                self._forget_ownerless_edges(roots)
        for replay_method, replay_params in replay:
            self.route(replay_method, replay_params)
        for child in follow + release:
            # A request waits for this reader thread's next message: never block it.
            threading.Thread(
                target=self.follow if child in follow else self.release,
                args=(child,), name="portal-codex-subagent-follow", daemon=True,
            ).start()
        if waiter is not None and waiter.event_callback:
            try:
                waiter.event_callback({"method": "subagent/event", "params": {
                    "traceKey": thread_id, "agentId": thread_id,
                    "ancestorTraceKeys": list(reversed(ancestors)),
                    "parentToolUseId": self._child_threads[thread_id][1],
                    "event": {"method": method, "params": params},
                }})
            except Exception as error:
                logging.getLogger(__name__).warning(
                    "Child event projection failed: thread=%s method=%s error_type=%s",
                    thread_id, method, type(error).__name__,
                )
            return True
        return not is_root and method == "thread/started"

    def _claim_buffered_events(self, roots: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
        """The buffered child events whose chain now reaches an active root."""

        claimed: list[tuple[str, dict[str, Any]]] = []
        pending = list(self._unclaimed_child_events)
        self._unclaimed_child_events.clear()
        for entry in pending:
            child = str(entry[1].get("threadId") or "")
            if child in self._child_threads and self._root_of(child) in roots:
                claimed.append(entry)
            else:
                self._unclaimed_child_events.append(entry)
        return claimed

    def _track_child_turn(self, method: str, thread_id: str, params: dict[str, Any]) -> None:
        child_turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
        child_turn_id = str(child_turn.get("id") or params.get("turnId") or "")
        # The turn a stop interrupts: an ended turn's late items do not bring it back.
        if method == "turn/completed":
            self._child_turns.pop(thread_id, None)
        elif child_turn_id and method in {"turn/started", "item/started"}:
            self._child_turns[thread_id] = child_turn_id

    def _ancestors(self, thread_id: str) -> list[str]:
        ancestors: list[str] = []
        parent = self._child_threads.get(thread_id, ("", ""))[0]
        while parent in self._child_threads and parent not in ancestors:
            ancestors.append(parent)
            parent = self._child_threads[parent][0]
        return ancestors

    def _forget_ownerless_edges(self, roots: dict[str, Any]) -> None:
        # Forget edges with no active owner. The source may announce them again on resume.
        self._child_threads = {child: edge for child, edge in self._child_threads.items()
                               if self._root_of(child) in roots}
        self._child_turns = {child: turn for child, turn in self._child_turns.items()
                             if child in self._child_threads}

    def follow(self, child: str) -> None:
        """Join a running subagent thread; its own notifications then reach this client."""

        try:
            result = self._rpc.request("thread/resume", {"threadId": child, "excludeTurns": True},
                                       timeout=self.timeouts.lifecycle)
        except Exception as error:
            with self._state_lock:
                self._followed_children.discard(child)
            logging.getLogger(__name__).warning(
                "Subagent thread could not be followed: thread=%s error_type=%s", child, type(error).__name__)
            return
        thread = result.get("thread") if isinstance(result.get("thread"), dict) else {}
        if thread:
            # Who the subagent is (its nickname and role) comes with the thread.
            self.route("thread/started", {"thread": {**thread, "id": child}})

    def release(self, child: str) -> None:
        try:
            self._rpc.request("thread/unsubscribe", {"threadId": child}, timeout=45.0)
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Subagent thread could not be released: thread=%s error_type=%s", child, type(error).__name__)


def _announced_edges(
    method: str, thread_id: str, thread: dict[str, Any], item: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """The (child, parent, spawning call) edges a notification announces."""

    edges: list[tuple[str, str, str]] = []
    source = thread.get("source") or {}
    sub = source.get("subAgent", {}) if isinstance(source, dict) else {}
    spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
    if method == "thread/started" and spawn.get("parent_thread_id"):
        edges.append((thread_id, str(spawn["parent_thread_id"]), ""))
    if item.get("type") == "collabAgentToolCall" and item.get("tool") == "spawnAgent":
        edges.extend((str(child), thread_id, str(item.get("id") or ""))
                     for child in item.get("receiverThreadIds") or [] if child)
    return edges
