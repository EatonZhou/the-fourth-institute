"""The cards of the subagents a DSH Resident's model starts.

DSH runs a subagent (its ``subagent`` tool) in a session of its own, inside
the tool call: the call waits for the subagent's result.  The bridge gives
the child session's items as ``subagent/event`` under the call's ID.  From
these ``DshSubagentCards`` gives the events the other Providers give: the
running card (``subagent/progress``) on each step and the finished card
(``subagentRun``) when the call has its result.

DSH's subagents get no subagent tool of their own (one level of delegation),
so every card is a child of the Resident's own turn.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from management_common.provider_subagent_runs import PROGRESS_METHOD, STOPPING_METHOD, SubagentRuns

TOOL = "subagent"
# DSH's ``spawn`` subagent starts in a fresh context with the parent's tools.
AGENT_TYPE = "general-purpose"
# One child request's token use (``tokenUsage.last``) in the card's names.
USAGE = {"totalTokens": "total_tokens", "inputTokens": "input_tokens", "cachedInputTokens": "cached_input_tokens",
         "outputTokens": "output_tokens", "reasoningOutputTokens": "reasoning_tokens"}


def _count(value: object) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _arguments(value: object) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return {}
    return value


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(
        str(part.get("text") or "") for part in content or []
        if isinstance(part, Mapping) and part.get("type") == "text"
    )


def step_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    """A child session item in the shape ``SubagentRuns.observe`` reads."""

    kind = str(item.get("type") or "")
    if kind == "agentMessage":
        return {"type": "agentMessage", "text": str(item.get("text") or "")}
    if kind == "dynamicToolCall":
        status = str(item.get("status") or "running")
        return {"type": "toolCall", "id": str(item.get("id") or ""), "name": str(item.get("tool") or "tool"),
                "input": _arguments(item.get("arguments")), "status": status,
                "error": status == "failed" or item.get("success") is False}
    return None


class DshSubagentCards:
    """Follows the subagents of one DSH turn."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.turn_id = ""
        self.runs = SubagentRuns()
        # Each run's token use so far: the bridge gives every child request's own.
        self.usage: dict[str, dict[str, int]] = {}
        # The runs the bridge stopped: their calls end failed, their cards stopped.
        self.stopping: set[str] = set()
        # The calls that have their result: a stopped child's late steps are no new run.
        self.ended: set[str] = set()

    def _start(self, call: str, arguments: object = None) -> None:
        if not self.runs.knows(call):
            self.runs.start(call, tool_use_id=call, agent_type=AGENT_TYPE)
        args = _arguments(arguments)
        if isinstance(args, Mapping):
            self.runs.describe(call, description=str(args.get("description") or ""), prompt=str(args.get("prompt") or ""))

    def _event(self, method: str, item: dict[str, Any]) -> dict[str, Any]:
        return {"method": method, "params": {"threadId": self.session_id, "turnId": self.turn_id, "item": item}}

    def _progress(self, call: str) -> list[dict[str, Any]]:
        card = self.runs.live(call, turn_id=self.turn_id)
        return [] if card is None else [self._event(PROGRESS_METHOD, card)]

    def observe(self, event: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The card events this bridge event gives, to follow it in the same stream."""

        method = str(event.get("method") or "")
        params = event.get("params") if isinstance(event.get("params"), Mapping) else {}
        if method == "subagent/event":
            return self._child(params)
        if method == STOPPING_METHOD:
            self.stopping.add(str(params.get("toolUseId") or ""))
            return []
        turn = params.get("turn") if isinstance(params.get("turn"), Mapping) else {}
        self.turn_id = str(turn.get("id") or params.get("turnId") or self.turn_id)
        item = params.get("item") if isinstance(params.get("item"), Mapping) else {}
        if item.get("type") != "dynamicToolCall" or item.get("tool") != TOOL or not item.get("id"):
            return []
        call = str(item["id"])
        if method == "item/started":
            self._start(call, item.get("arguments"))
            return self._progress(call)
        if method != "item/completed":
            return []
        # A Worker that took the turn over may not have seen the call start.
        self._start(call, item.get("arguments"))
        failed = item.get("status") == "failed" or item.get("success") is False
        stopped = failed and call in self.stopping
        self.stopping.discard(call)
        self.ended.add(call)
        card = self.runs.finish(call, status="stopped" if stopped else "failed" if failed else "completed",
                                result=_text(item.get("output")), end_id=call, turn_id=self.turn_id)
        self.usage.pop(call, None)
        return [] if card is None else [self._event("item/completed", card)]

    def _child(self, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        call = str(params.get("parentToolUseId") or "")
        inner = params.get("event") if isinstance(params.get("event"), Mapping) else {}
        body = inner.get("params") if isinstance(inner.get("params"), Mapping) else {}
        if not call or params.get("ancestorTraceKeys") or call in self.ended:
            return []
        if inner.get("method") == "thread/tokenUsage/updated":
            usage = body.get("tokenUsage") if isinstance(body.get("tokenUsage"), Mapping) else {}
            last = usage.get("last") if isinstance(usage.get("last"), Mapping) else {}
            self._start(call)
            spent = self.usage.setdefault(call, {})
            for key, name in USAGE.items():
                spent[name] = spent.get(name, 0) + _count(last.get(key))
            self.runs.report_usage(call, spent)
            return self._progress(call)
        if inner.get("method") not in {"item/started", "item/completed"}:
            return []
        step = step_item(body.get("item") if isinstance(body.get("item"), Mapping) else {})
        if step is None:
            return []
        self._start(call)
        # The child session's own trace is keyed by its session, not by the call.
        self.runs.describe(call, trace_ref=str(params.get("traceKey") or ""))
        self.runs.observe(call, step, model=str(params.get("model") or ""))
        return self._progress(call)
