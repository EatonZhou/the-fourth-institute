"""Token usage of one Portal turn, in the form every Provider reports to Portal.

Portal counts every token a turn sent to the model and every token it got back:

* ``input_tokens``: all input, including the cached prefix that every request
  of an agent loop sends again; ``cached_input_tokens``: the cache reads in it.
* ``output_tokens``: all output, including reasoning;
  ``reasoning_output_tokens``: the reasoning (thinking) in it.
* ``total_tokens``: input plus output.

An Execution reports ``{"last": <turn>, "context": <last request>,
"modelContextWindow": <window>}`` with camelCase keys, like the Codex App
Server.  The turn is the sum of all model requests; the last request shows how
full the context window is.

Codex already reports this.  Claude Code and Kimi Code report input without
its cache, and their result covers only the Provider's last query: when a
Worker restart interrupts a turn, the recovered turn asks again and the
earlier queries are missing.  Their adapters therefore sum the per-request
usage records in the Provider's own session file over the whole Execution.

The adapters also report each request to the usage ledger under its Provider
request identity, also when the turn fails or is cancelled, as Codex reports
its requests while it works, in the hour each request was made.  A recovered
attempt reports the same requests again; the ledger counts each identity once.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator, Mapping
from typing import Any

TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_CAMEL_KEYS = {
    "input_tokens": "inputTokens",
    "cached_input_tokens": "cachedInputTokens",
    "output_tokens": "outputTokens",
    "reasoning_output_tokens": "reasoningOutputTokens",
    "total_tokens": "totalTokens",
}


@dataclass(frozen=True)
class RequestUsage:
    """One model request: its Provider identity, usage and when it was made."""

    request_id: str
    usage: dict[str, int]
    occurred_at: str


@dataclass(frozen=True)
class ObservedTurnUsage:
    """Usage summed from a Provider session file."""

    turn: dict[str, int]
    context: dict[str, int] = field(default_factory=dict)
    model: str = ""
    requests: tuple[RequestUsage, ...] = ()
    # The context window of the session's model, when the Provider tells.
    context_window: int = 0


def _observed(requests: list[dict[str, Any]], *, prefix: str) -> ObservedTurnUsage | None:
    """Sum requests (``id``, ``usage``, ``at``, ``model``, ``context``) in order."""

    if not requests:
        return None
    turn: dict[str, int] = {}
    for request in requests:
        turn = _add(turn, request["usage"])
    in_context = [request for request in requests if request.get("context", True)]
    last = in_context[-1] if in_context else {}
    return ObservedTurnUsage(
        turn=turn,
        context=dict(last.get("usage") or {}),
        model=str(last.get("model") or ""),
        requests=tuple(
            RequestUsage(f"{prefix}:{request['id']}", dict(request["usage"]), _iso(request["at"]))
            for request in requests
            if request["usage"]
        ),
    )


def _count(value: Any) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _canonical(input_tokens: int, cached: int, output: int, reasoning: int) -> dict[str, int]:
    usage = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output,
    }
    return {key: value for key, value in usage.items() if value > 0}


def _add(left: Mapping[str, int], right: Mapping[str, int]) -> dict[str, int]:
    return {
        key: left.get(key, 0) + right.get(key, 0)
        for key in TOKEN_USAGE_KEYS
        if left.get(key, 0) + right.get(key, 0) > 0
    }


def usage_payload(
    turn: Mapping[str, int],
    context: Mapping[str, int] | None = None,
    model_context_window: int = 0,
) -> dict[str, Any]:
    """The Execution usage contract for one turn (empty when nothing was used)."""

    if not turn:
        return {}
    payload: dict[str, Any] = {
        "last": {_CAMEL_KEYS[key]: int(turn[key]) for key in TOKEN_USAGE_KEYS if turn.get(key)},
    }
    if context:
        payload["context"] = {
            _CAMEL_KEYS[key]: int(context[key]) for key in TOKEN_USAGE_KEYS if context.get(key)
        }
    if model_context_window > 0:
        payload["modelContextWindow"] = int(model_context_window)
    return payload


def _lines(path: str | Path) -> list[str]:
    try:
        return Path(path).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return []


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _from_epoch_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def epoch_ms(value: object) -> int:
    """Milliseconds since the epoch of an ISO timestamp, or 0."""

    moment = _timestamp(value)
    return int(moment.timestamp() * 1000) if moment else 0


# Claude ---------------------------------------------------------------------


def anthropic_request_usage(raw: object) -> dict[str, int]:
    """Canonical usage of one Anthropic Messages response (or a sum of them).

    Anthropic reports uncached input, cache writes and cache reads separately
    and the thinking tokens as a detail of the output.
    """

    data = raw if isinstance(raw, Mapping) else {}
    details = data.get("output_tokens_details")
    details = details if isinstance(details, Mapping) else {}
    cache_read = _count(data.get("cache_read_input_tokens"))
    input_tokens = (
        _count(data.get("input_tokens"))
        + _count(data.get("cache_creation_input_tokens"))
        + cache_read
    )
    return _canonical(
        input_tokens,
        cache_read,
        _count(data.get("output_tokens")),
        _count(details.get("thinking_tokens")) or _count(details.get("reasoning_tokens")),
    )


def read_claude_turn_usage(path: str | Path, *, since: str, until: str = "") -> ObservedTurnUsage | None:
    """Sum the model requests a Claude Code transcript recorded since ``since``.

    Claude Code writes every content block of a response as its own assistant
    entry carrying the response usage, so entries are combined by message id.
    Subagent requests (kept beside the transcript, ``<session>/subagents``)
    count toward the turn but not the context.
    """

    start = _timestamp(since)
    end = _timestamp(until)
    if start is None:
        return None
    requests: dict[str, dict[str, Any]] = {}
    for line in _claude_request_lines(Path(path), start):
        if '"assistant"' not in line or '"usage"' not in line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") if isinstance(entry, dict) else None
        if entry.get("type") != "assistant" or not isinstance(message, dict):
            continue
        moment = _timestamp(entry.get("timestamp"))
        usage = message.get("usage")
        key = str(message.get("id") or entry.get("requestId") or "")
        if (
            moment is None or moment < start or (end is not None and moment > end)
            or not isinstance(usage, dict) or not key
        ):
            continue
        current = anthropic_request_usage(usage)
        previous = requests.get(key)
        if previous is not None:
            # Later entries of one response carry the same or a larger count.
            current = {
                name: max(previous["usage"].get(name, 0), current.get(name, 0))
                for name in TOKEN_USAGE_KEYS
                if max(previous["usage"].get(name, 0), current.get(name, 0)) > 0
            }
        requests[key] = {
            "id": key,
            "usage": current,
            "at": previous["at"] if previous is not None else moment,
            "context": not entry.get("isSidechain"),
            "model": str(message.get("model") or ""),
        }
    return _observed(sorted(requests.values(), key=lambda request: request["at"]), prefix="claude")


def _claude_request_lines(path: Path, since: datetime) -> Iterator[str]:
    """The session transcript's lines, then those of its subagents that worked since ``since``."""

    yield from _lines(path)
    for record in sorted((path.with_suffix("") / "subagents").glob("agent-*.jsonl")):
        try:
            if datetime.fromtimestamp(record.stat().st_mtime, timezone.utc) < since:
                continue
        except OSError:
            continue
        yield from _lines(record)


def claude_context_window(model_usage: object, model: str = "") -> int:
    """The context window Claude Code reported for the turn's main model."""

    entries = {
        str(name): value
        for name, value in (model_usage.items() if isinstance(model_usage, Mapping) else ())
        if isinstance(value, Mapping) and _count(value.get("contextWindow"))
    }
    if not entries:
        return 0
    if model:
        for name, value in entries.items():
            if name == model or name.startswith(model):
                return _count(value.get("contextWindow"))

    def size(value: Mapping[str, Any]) -> int:
        return sum(
            _count(value.get(key))
            for key in ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
        )

    return _count(max(entries.values(), key=size).get("contextWindow"))


def claude_turn_usage(
    reported: object,
    observed: ObservedTurnUsage | None,
    model_usage: object = None,
) -> dict[str, Any]:
    """The Execution usage of a Claude turn.

    ``reported`` is the usage of Claude Code's last query.  The transcript sum
    replaces it unless the transcript lost requests the query reported.
    """

    last_query = anthropic_request_usage(reported)
    if observed is None or observed.turn.get("total_tokens", 0) < last_query.get("total_tokens", 0):
        turn, context, model = last_query, {}, ""
    else:
        turn, context, model = observed.turn, observed.context, observed.model
    return usage_payload(turn, context, claude_context_window(model_usage, model))


# Kimi -----------------------------------------------------------------------


def _kimi_request_usage(raw: Mapping[str, Any]) -> dict[str, int]:
    cache_read = _count(raw.get("inputCacheRead"))
    return _canonical(
        _count(raw.get("inputOther")) + cache_read + _count(raw.get("inputCacheCreation")),
        cache_read,
        _count(raw.get("output")),
        0,
    )


def read_kimi_turn_usage(path: str | Path, *, since_ms: int, until_ms: int = 0) -> ObservedTurnUsage | None:
    """Sum the model requests of the Kimi session since ``since_ms``.

    Kimi Code appends a ``usage.record`` to each agent's wire after each
    request.  ``path`` is the main agent's wire; the subagents' wires lie
    beside it, and their requests count for the turn but not its context.
    """

    main = Path(path)
    agents = main.parent.parent if main.parent.name == "main" else None
    wires = [main, *sorted(wire for wire in (agents.glob("*/wire.jsonl") if agents else ()) if wire != main)]
    requests: list[dict[str, Any]] = []
    for wire in wires:
        agent = "main" if wire == main else wire.parent.name
        for line in _lines(wire):
            if '"usage.record"' not in line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                not isinstance(record, dict)
                or record.get("type") != "usage.record"
                or str(record.get("agentId") or "main") != agent
                or not isinstance(record.get("usage"), dict)
                or _count(record.get("time")) < since_ms
                or (until_ms and _count(record.get("time")) > until_ms)
            ):
                continue
            requests.append({
                "id": hashlib.sha256(line.encode("utf-8")).hexdigest()[:32],
                "usage": _kimi_request_usage(record["usage"]),
                "at": _from_epoch_ms(_count(record.get("time"))),
                "context": agent == "main" and str(record.get("usageScope") or "turn") == "turn",
                "model": str(record.get("model") or ""),
            })
    return _observed(sorted(requests, key=lambda request: request["at"]), prefix="kimi")


def kimi_reported_usage(raw: object) -> dict[str, int]:
    """Canonical form of the usage the Kimi Code client returned.

    The client reports input without its cache.  Depending on its source,
    ``cached_input_tokens`` either includes the cache writes (then the total
    is the sum of all four counts) or holds only the cache reads.
    """

    data = raw if isinstance(raw, Mapping) else {}
    uncached = _count(data.get("input_tokens"))
    cached = _count(data.get("cached_input_tokens"))
    created = _count(data.get("cache_creation_tokens"))
    output = _count(data.get("output_tokens"))
    includes_writes = created and cached >= created and _count(data.get("total_tokens")) == (
        uncached + cached + output
    )
    cache_read = cached - created if includes_writes else cached
    return _canonical(uncached + cache_read + created, cache_read, output, 0)


def kimi_turn_usage(reported: object, observed: ObservedTurnUsage | None) -> dict[str, Any]:
    """The Execution usage of a Kimi turn."""

    last_query = kimi_reported_usage(reported)
    if observed is None or observed.turn.get("total_tokens", 0) < last_query.get("total_tokens", 0):
        return usage_payload(last_query)
    return usage_payload(observed.turn, observed.context, observed.context_window)


# DSH ------------------------------------------------------------------------


def read_dsh_turn_usage(
    path: str | Path, *, session_id: str, since_ms: int, until_ms: int = 0,
) -> ObservedTurnUsage | None:
    """Sum the model requests a DSH session log recorded since ``since_ms``.

    Each ``assistant/message`` carries the usage of the request that produced
    it, with input split into uncached, cache read and cache write parts.  A
    subagent the turn started (``subagent/catalog``) keeps a log of its own
    beside the session's; its requests count for the turn, outside the
    session's context.
    """

    requests = _dsh_requests(path, session_id, since_ms, until_ms)
    for child in _dsh_children(path, since_ms, until_ms):
        child_path = Path(path).parent.parent / child / Path(path).name
        requests.extend({**request, "context": False} for request in _dsh_requests(child_path, child, since_ms, until_ms))
    requests.sort(key=lambda request: request["at"])
    observed = _observed(requests, prefix="dsh")
    if observed is None:
        return None
    return replace(observed, context_window=read_dsh_context_window(path))


def _dsh_events(path: str | Path, kind: str, since_ms: int, until_ms: int) -> Iterator[dict[str, Any]]:
    """The events of one type a DSH session log recorded in the window."""

    for line in _lines(path):
        if f'"{kind}"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != kind or not isinstance(event.get("data"), dict):
            continue
        moment = _count(event.get("time"))
        if moment >= since_ms and not (until_ms and moment > until_ms):
            yield event


def _dsh_children(path: str | Path, since_ms: int, until_ms: int) -> list[str]:
    children: list[str] = []
    for event in _dsh_events(path, "subagent/catalog", since_ms, until_ms):
        child = str(event["data"].get("childId") or "")
        # A child's log is a sibling directory of the session's.
        if child and "/" not in child and child not in {".", ".."} and child not in children:
            children.append(child)
    return children


def _dsh_requests(path: str | Path, session_id: str, since_ms: int, until_ms: int) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for event in _dsh_events(path, "assistant/message", since_ms, until_ms):
        usage = event["data"].get("usage")
        if not isinstance(usage, dict) or not isinstance(event.get("seq"), int):
            continue
        cache_read = _count(usage.get("cacheReadTokens"))
        requests.append({
            "id": f"{session_id}:{event['seq']}",
            "usage": _canonical(
                _count(usage.get("inputTokens")) + cache_read + _count(usage.get("cacheWriteTokens")),
                cache_read,
                _count(usage.get("outputTokens")),
                _count(usage.get("reasoningTokens")),
            ),
            "at": _from_epoch_ms(_count(event.get("time"))),
        })
    return requests


def read_dsh_context_window(path: str | Path) -> int:
    """The context window of the model a DSH session last sent a request to.

    DSH logs a ``request/context`` event with the routed model's window.
    """

    window = 0
    for line in _lines(path):
        if '"request/context"' not in line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "request/context":
            continue
        data = event.get("data")
        window = _count((data if isinstance(data, dict) else event).get("contextWindow")) or window
    return window


def observed_turn_usage(reported: object, observed: ObservedTurnUsage | None) -> dict[str, Any]:
    """The Execution usage of a turn whose Provider already reports canonical usage."""

    data = reported if isinstance(reported, Mapping) else {}
    last_query = {key: _count(data.get(key)) for key in TOKEN_USAGE_KEYS if _count(data.get(key))}
    if observed is None or observed.turn.get("total_tokens", 0) < last_query.get("total_tokens", 0):
        return dict(data)
    return usage_payload(observed.turn, observed.context, observed.context_window)
