"""The cards of the subagents a Codex Resident's model starts.

Codex runs a subagent in a thread of its own. The parent thread tells it
started and ended with ``subAgentActivity`` items; the app-server client
routes the child thread's own items as ``subagent/event``. From these
``CodexSubagentCards`` gives the same events the other Providers give: the
running card (``subagent/progress``) on each step and the finished card
(``subagentRun``) when the run ends.

A Codex subagent never blocks the model's call: the model goes on and waits
for it when it chooses, so every run is a background run. Only the main
thread's own subagents have cards; their subagents have their traces only.
"""
from __future__ import annotations

import shlex
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Mapping

from management_common.provider_subagent_runs import PROGRESS_METHOD, SubagentRuns

ENDS = {"completed": "completed", "interrupted": "stopped"}
# A child turn that ends so ends its run: an interrupt from outside the parent
# (the Chat's Stop) tells the parent nothing.
TURN_ENDS = {"interrupted": "stopped", "failed": "failed"}
SHELLS = {"bash", "sh", "zsh"}
# The threads this Worker follows; a run outlives its turn, not its Worker.
THREADS = 256
# A child thread's token use (``tokenUsage.total``) in the card's names.
USAGE = {"totalTokens": "total_tokens", "inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens",
         "outputTokens": "output_tokens", "reasoningOutputTokens": "reasoning_tokens"}


def _count(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _moment(milliseconds: object) -> str:
    """Codex's epoch milliseconds as an ISO 8601 moment, or ``""``."""

    if isinstance(milliseconds, bool) or not isinstance(milliseconds, (int, float)) or milliseconds <= 0:
        return ""
    try:
        moment = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return ""
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _command(value: object) -> str:
    """The command the subagent ran, without the shell that wraps it."""

    try:
        parts = list(value) if isinstance(value, list) else shlex.split(str(value or ""))
    except ValueError:
        return str(value or "")
    if len(parts) == 3 and parts[0].rsplit("/", 1)[-1] in SHELLS and parts[1] in {"-c", "-lc"}:
        return str(parts[2])
    return " ".join(str(part) for part in parts)


def step_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """A child thread item in the shape ``SubagentRuns.observe`` reads."""

    kind = str(item.get("type") or "")
    status = str(item.get("status") or "")
    call = {"type": "toolCall", "id": str(item.get("id") or ""), "status": status,
            "error": status in {"failed", "declined"}}
    if kind == "agentMessage":
        return {"type": "agentMessage", "text": str(item.get("text") or "")}
    if kind == "commandExecution":
        exit_code = item.get("exitCode")
        return {**call, "name": "Shell", "input": {"command": _command(item.get("command"))},
                "error": call["error"] or (isinstance(exit_code, int) and exit_code != 0)}
    if kind == "fileChange":
        paths = [str(change.get("path") or "") for change in item.get("changes") or [] if isinstance(change, Mapping)]
        return {**call, "name": "Edit", "input": {"path": ", ".join(path for path in paths if path)}}
    if kind == "mcpToolCall":
        return {**call, "name": ".".join(str(part) for part in (item.get("server"), item.get("tool")) if part),
                "input": item.get("arguments"), "error": call["error"] or bool(item.get("error"))}
    if kind == "dynamicToolCall":
        return {**call, "name": str(item.get("tool") or "tool"), "input": item.get("arguments"),
                "error": call["error"] or item.get("success") is False}
    if kind == "webSearch":
        return {**call, "name": "WebSearch", "input": {"query": str(item.get("query") or "")}}
    if kind == "imageView":
        return {**call, "name": "ViewImage", "input": {"path": str(item.get("path") or "")}}
    if kind == "collabAgentToolCall":
        return {**call, "name": str(item.get("tool") or "agent"), "input": {"prompt": str(item.get("prompt") or "")}}
    return None


class CodexSubagentCards:
    """Follows one Codex thread's subagents across its turns."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.turn_id = ""
        self.runs = SubagentRuns()
        # Children whose run ended: the late events of that turn start no new card.
        self.ended: set[str] = set()
        # A child thread's token use before its current run: ``tokenUsage.total``
        # counts the whole thread, a card counts its own run.
        self.usage_before: dict[str, dict[str, int]] = {}

    def _start(self, child: str) -> None:
        if not self.runs.knows(child):
            self.runs.start(child, tool_use_id=child, background=True)

    def _end(self, child: str) -> None:
        self.runs.forget(child)
        self.ended.add(child)
        self.usage_before.pop(child, None)

    def _finished(self, card: dict[str, Any] | None) -> list[dict[str, Any]]:
        return [] if card is None else [{"method": "item/completed", "params": {
            "threadId": self.thread_id, "turnId": self.turn_id, "item": card}}]

    def _progress(self, child: str) -> list[dict[str, Any]]:
        card = self.runs.live(child, turn_id=self.turn_id)
        return [] if card is None else [{"method": PROGRESS_METHOD, "params": {
            "threadId": self.thread_id, "turnId": self.turn_id, "item": card}}]

    def observe(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The card events this Provider event gives, to follow it in the same stream."""

        method = str(event.get("method") or "")
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        if method == "subagent/event":
            return self._child(params)
        turn = params.get("turn") if isinstance(params.get("turn"), Mapping) else {}
        self.turn_id = str(turn.get("id") or params.get("turnId") or self.turn_id)
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        if method != "item/completed" or item.get("type") != "subAgentActivity":
            return []
        child = str(item.get("agentThreadId") or "")
        kind = str(item.get("kind") or "")
        if not child:
            return []
        if kind in {"started", "interacted"}:
            # ``interacted``: the parent gave a finished child more work (``followup_task``), a new run.
            self.ended.discard(child)
            self._start(child)
            path = str(item.get("agentPath") or "")
            self.runs.describe(child, description=path.rstrip("/").rsplit("/", 1)[-1], model=str(item.get("model") or ""))
            return self._progress(child)
        if kind in ENDS and self.runs.knows(child):
            card = self.runs.finish(child, status=ENDS[kind], end_id=str(item.get("id") or ""), turn_id=self.turn_id)
            self._end(child)
            return self._finished(card)
        return []

    def _child(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        if params.get("ancestorTraceKeys"):
            return []
        child = str(params.get("traceKey") or "")
        inner = params.get("event") if isinstance(params.get("event"), Mapping) else {}
        method = str(inner.get("method") or "")
        body = inner.get("params") if isinstance(inner.get("params"), Mapping) else {}
        if not child:
            return []
        if method == "turn/started":
            # The parent gave a stopped child more work: a new run.
            self.ended.discard(child)
            return []
        if child in self.ended:
            return []
        if method == "turn/completed":
            turn = body.get("turn") if isinstance(body.get("turn"), Mapping) else {}
            status = TURN_ENDS.get(str(turn.get("status") or ""))
            if status is None or not self.runs.knows(child):
                return []
            card = self.runs.finish(child, status=status, end_id=str(turn.get("id") or ""), turn_id=self.turn_id)
            self._end(child)
            return self._finished(card)
        if method == "thread/started":
            thread = body.get("thread") if isinstance(body.get("thread"), Mapping) else {}
            self._start(child)
            # The thread the client joined previews its first input: the task the model gave.
            self.runs.describe(child, agent_type=str(thread.get("agentRole") or thread.get("agentNickname") or ""),
                               model=str(thread.get("model") or ""), prompt=str(thread.get("preview") or ""))
            return self._progress(child)
        if method == "thread/tokenUsage/updated":
            usage = body.get("tokenUsage") if isinstance(body.get("tokenUsage"), Mapping) else {}
            total = usage.get("total") if isinstance(usage.get("total"), Mapping) else {}
            last = usage.get("last") if isinstance(usage.get("last"), Mapping) else {}
            self._start(child)
            # The run's first request tells what the thread had used before it.
            before = self.usage_before.setdefault(child, {key: _count(total.get(key)) - _count(last.get(key)) for key in USAGE})
            self.runs.report_usage(child, {name: max(_count(total.get(key)) - before[key], 0) for key, name in USAGE.items()})
            return self._progress(child)
        if method not in {"item/started", "item/completed"}:
            return []
        step = step_item(body.get("item") if isinstance(body.get("item"), Mapping) else {})
        if step is None:
            return []
        moment = _moment(body.get("completedAtMs") if method == "item/completed" else body.get("startedAtMs"))
        if moment:
            step["timestamp"] = moment
        self._start(child)
        self.runs.observe(child, step)
        return self._progress(child)


_THREADS: OrderedDict[str, CodexSubagentCards] = OrderedDict()
_THREADS_LOCK = threading.Lock()


def for_thread(thread_id: str) -> CodexSubagentCards:
    with _THREADS_LOCK:
        cards = _THREADS.pop(thread_id, None) or CodexSubagentCards(thread_id)
        _THREADS[thread_id] = cards
        while len(_THREADS) > THREADS:
            _THREADS.popitem(last=False)
        return cards
