"""Kimi's live session event stream and its single canonical item conversion.

Snapshots remain control/UI data. Only completed blocks and explicit tool
results observed here become Provider items; missing deltas are never repaired
from a transcript or mistaken for complete text.
"""
from __future__ import annotations

import json
import queue
import socket
import ssl
import threading
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from management_common.provider_subagent_runs import PROGRESS_METHOD, SubagentRuns

from .provider_websocket import WebSocketTransport


class KimiLiveStream:
    def __init__(self, base_url: str, session_id: str) -> None:
        self.url = urlsplit(base_url)
        self.session_id = session_id
        self.frames: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.transport: WebSocketTransport | None = None
        self.error: BaseException | None = None
        self.cursor: dict[str, Any] = {}
        self.thread = threading.Thread(target=self._run, name="kimi-live-events", daemon=True)
        self.thread.start()
        if not self.ready.wait(15) or self.error:
            self.close()
            raise ConnectionError("Kimi live subscription failed") from self.error

    def _run(self) -> None:
        connected = False
        gap_reported = False
        while not self.stop.is_set():
            try:
                port = self.url.port or (443 if self.url.scheme == "https" else 80)
                sock = socket.create_connection((self.url.hostname or "localhost", port), timeout=10)
                if self.url.scheme == "https":
                    sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.url.hostname)
                try:
                    transport = WebSocketTransport(sock, host=self.url.netloc, path=self.url.path.rstrip("/") + "/ws")
                except BaseException:
                    sock.close()
                    raise
                self.transport = transport
                hello = {"subscriptions": [self.session_id], "client_id": "tfi-portal"}
                if self.cursor:
                    hello["cursors"] = {self.session_id: dict(self.cursor)}
                transport.send_text(json.dumps({"type": "client_hello", "id": "portal-subscribe", "payload": hello}))
                while not self.stop.is_set():
                    frame = json.loads(transport.recv_text())
                    if not isinstance(frame, dict):
                        continue
                    typ = frame.get("type")
                    if typ == "ping":
                        transport.send_text(json.dumps({"type": "pong", "id": frame.get("id")}))
                        continue
                    if typ == "ack" and frame.get("id") == "portal-subscribe":
                        payload = frame.get("payload") or {}
                        if self.session_id not in payload.get("accepted_subscriptions", []):
                            raise ConnectionError("Kimi session subscription was rejected")
                        self.ready.set()
                        connected = True
                        gap_reported = False
                        sock.settimeout(None)
                        continue
                    if typ == "resync_required":
                        self.frames.put({"type": "portal.stream_gap"})
                        self.cursor = {}
                        continue
                    if frame.get("session_id") != self.session_id:
                        continue
                    if not frame.get("volatile"):
                        seq = int(frame.get("seq") or 0)
                        epoch = str(frame.get("epoch") or "")
                        if epoch == self.cursor.get("epoch") and seq <= self.cursor.get("seq", -1):
                            continue
                        self.cursor = {"epoch": epoch, "seq": seq}
                    self.frames.put(frame)
            except (OSError, EOFError, ValueError) as error:
                if not connected:
                    self.error = error
                    self.ready.set()
                    return
                if not self.stop.is_set() and not gap_reported:
                    gap_reported = True
                    self.frames.put({"type": "portal.stream_gap"})
            finally:
                if self.transport:
                    self.transport.close()
                    self.transport = None
            self.stop.wait(0.5)

    def drain(self, observe: Callable[[dict[str, Any]], None]) -> None:
        while True:
            try:
                observe(self.frames.get_nowait())
            except queue.Empty:
                return

    def close(self) -> None:
        self.stop.set()
        if self.transport:
            self.transport.close()
        self.thread.join(timeout=2)


@dataclass
class _AgentBlocks:
    turn: str = ""
    step: int = 0
    boundary: int = 0
    ordinal: int = 0
    kind: str = ""
    text: str = ""
    pending: dict[str, Any] | None = None
    incomplete: bool = False
    parent_call: str = ""
    parent_agent: str = ""
    # A subagent starts its turn before Kimi says who spawned it; its items
    # wait for subagent.spawned so the child trace knows its Agent call.
    held: list[tuple[str, dict, str]] = field(default_factory=list)
    unparented: bool = False
    # A subagent's input of its latest turn and its token use in its run so far.
    prompt: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    timing: dict[str, str] = field(default_factory=dict)
    timing_ready: bool = False
    timing_items: list[tuple[str, dict, str]] = field(default_factory=list)


# Kimi Code's events that end a subagent's run, and the run's status
# (a cancelled run was stopped, as the other Providers' cards say).
SUBAGENT_ENDS = {"subagent.completed": "completed", "subagent.failed": "failed", "subagent.cancelled": "stopped"}


def _usage(value: object) -> dict[str, int]:
    """Kimi Code's token use of a step or run in the card's names."""

    usage = value if isinstance(value, dict) else {}

    def count(key: str) -> int:
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    read = count("inputCacheRead")
    tokens = count("inputOther") + read + count("inputCacheCreation")
    output = count("output")
    return {"input_tokens": tokens, "cached_input_tokens": read, "output_tokens": output, "total_tokens": tokens + output}


class KimiLiveItems:
    def __init__(self, session_id: str, emit: Callable[[dict[str, Any]], None], *, prompt_id: str = "", joining: bool = False, request_timing=None) -> None:
        self.session_id = session_id
        self.emit = emit
        self.request_timing = request_timing
        self.prompt_id = prompt_id
        self.context_window = 0
        self.agents: dict[str, _AgentBlocks] = {}
        self.epoch = ""
        self.response = ""
        self.ended = False
        self.gapped = joining
        # The subagents the main agent started: their cards for the Chat.
        self.runs = SubagentRuns()
        # The task of each of the main agent's calls that may start a
        # subagent (its ``prompt``), by call, and the subagents whose card has it.
        self.call_prompts: dict[str, str] = {}
        self.call_prompted: set[str] = set()

    def _key(self, agent: str) -> str:
        # Kimi numbers subagents per session (agent-0, agent-1, ...); a
        # Resident's later session reuses the numbers for other subagents.
        return f"{self.session_id}:{agent}"

    def _main_turn(self) -> str:
        main = self.agents.get("main")
        return self.prompt_id or (main.turn if main else "")

    def _progress(self, child: str, timestamp: str = "") -> None:
        # The running card of a subagent the main agent started, when it changed.
        card = self.runs.live(child, turn_id=self._main_turn())
        if card is not None:
            self.emit({"method": PROGRESS_METHOD, "params": {"threadId": self.session_id, "turnId": self._main_turn(), "item": card},
                       **({"timestamp": timestamp} if timestamp else {})})

    def _send(self, agent: str, state: _AgentBlocks, method: str, item: dict, timestamp: str = "") -> None:
        if self.request_timing and not state.timing_ready and (state.timing_items or item.get('type') in {'agentMessage', 'reasoning'}):
            state.timing_items.append((method, item, timestamp))
            return
        if agent != "main" and not state.parent_agent and not state.unparented:
            state.held.append((method, item, timestamp))
            return
        if agent != "main":
            self.runs.observe(agent, {**item, "timestamp": timestamp} if timestamp else item)
        event = {"method": method, "params": {"threadId": self.session_id, "turnId": (self.prompt_id if agent == "main" and self.prompt_id else state.turn), "item": item}}
        if item.get('type') in {'agentMessage', 'reasoning'}:
            item = dict(item)
            event['params']['request_timing'] = item.pop('_request_timing', state.timing)
            event['params']['item'] = item
        if timestamp:
            event["timestamp"] = timestamp
        if agent != "main":
            ancestors = []
            parent = state.parent_agent
            while parent != "main" and parent in self.agents and parent not in ancestors:
                ancestors.append(parent)
                parent = self.agents[parent].parent_agent
            ancestry = ({"ancestorTraceKeys": [self._key(key) for key in reversed(ancestors)]} if parent == "main"
                        else {"parentTraceKey": self._key(state.parent_agent)} if state.parent_agent else {})
            event = {"method": "subagent/event", "params": {
                "traceKey": self._key(agent), "agentId": agent, "parentToolUseId": state.parent_call or agent,
                **ancestry,
                "event": event}, **({"timestamp": timestamp} if timestamp else {})}
        self.emit(event)
        if agent != "main":
            self._progress(agent, timestamp)

    def _send_held(self, agent: str, state: _AgentBlocks) -> None:
        held, state.held = state.held, []
        for method, item, timestamp in held:
            self._send(agent, state, method, item, timestamp)

    def _release_timed(self, agent: str, state: _AgentBlocks, timing: dict) -> None:
        state.timing, state.timing_ready = timing, True
        if state.pending:
            state.pending['_request_timing'] = timing
        held, state.timing_items = state.timing_items, []
        for method, item, timestamp in held:
            self._send(agent, state, method, {**item, '_request_timing': timing} if item.get('type') in {'agentMessage', 'reasoning'} else item, timestamp)

    def finish(self) -> None:
        """Keep a subagent's items whose spawn the stream did not show (e.g. a joined turn)."""

        for agent, state in self.agents.items():
            if not state.timing_ready:
                self._release_timed(agent, state, {})
            if state.held:
                state.unparented = True
                self._send_held(agent, state)

    def _release(self, agent: str, state: _AgentBlocks, final: bool = False) -> None:
        pending, state.pending = state.pending, None
        if pending:
            self._send(agent, state, "item/completed", {**pending, "phase": "final_answer" if final else "commentary"})
            if agent == "main" and final:
                self.response = pending["text"]

    def _finish_block(self, agent: str, state: _AgentBlocks) -> None:
        text, kind = state.text, state.kind
        state.text, state.kind = "", ""
        if not text or state.incomplete:
            return
        identity = f"{self.epoch}:{agent}:{state.turn}:{state.step}:{state.boundary}:{state.ordinal}:{kind}"
        state.ordinal += 1
        if kind == "thinking":
            self._send(agent, state, "item/completed", {"id": identity, "type": "reasoning", "content": [text]})
        else:
            self._release(agent, state)
            state.pending = {"id": identity, "type": "agentMessage", "text": text}

    def observe(self, frame: dict[str, Any]) -> None:
        typ = str(frame.get("type") or "")
        if typ in {"portal.stream_gap", "resync_required"}:
            self.gapped = True
            for state in self.agents.values():
                state.text, state.kind, state.pending, state.incomplete = "", "", None, True
            self.emit({"method": "provider/traceGap", "params": {"threadId": self.session_id, "reason": "live_event_stream_interrupted"}})
            return
        payload = frame.get("payload") or {}
        agent = str(payload.get("agentId") or "main")
        if frame.get("session_id") != self.session_id:
            return
        self.epoch = str(frame.get("epoch") or self.epoch)
        state = self.agents.setdefault(agent, _AgentBlocks(incomplete=self.gapped))
        turn = str(payload.get("turnId") or state.turn)
        if typ in {"turn.started", "turn.step.started"}:
            self._finish_block(agent, state)
            if not state.timing_ready:
                self._release_timed(agent, state, {})
            self._release(agent, state)
            state.timing, state.timing_ready = {}, False
            state.turn = turn
            state.step = int(payload.get("step") or 0)
            # A retried step may keep its number but is a different occurrence.
            # Durable boundary sequence plus local ordinal preserves that identity.
            state.boundary = int(frame.get("seq") or state.boundary)
            state.incomplete = False
            if typ == "turn.started" and agent != "main" and payload.get("prompt"):
                state.prompt = str(payload["prompt"])
                if agent not in self.call_prompted:
                    self.runs.describe(agent, prompt=state.prompt)
                self._send(agent, state, "item/completed", {
                    "id": f"{self.epoch}:{agent}:{turn}:input:{state.boundary}",
                    "type": "providerInput", "role": "user", "content": payload["prompt"],
                }, str(frame.get("timestamp") or ""))
            return
        state.turn = turn
        if typ == "subagent.spawned":
            child = str(payload.get("subagentId") or "")
            if child:
                # Emitted by the spawning agent, also when it resumes the subagent.
                spawned = self.agents.setdefault(child, _AgentBlocks(incomplete=self.gapped))
                spawned.parent_call = str(payload.get("parentToolCallId") or child)
                spawned.parent_agent = str(payload.get("parentAgentId") or agent)
                if spawned.parent_agent == "main":
                    # The task the main agent's call gave, else the input the subagent's turn began with.
                    prompt = self.call_prompts.get(spawned.parent_call, "")
                    if prompt:
                        self.call_prompted.add(child)
                    else:
                        self.call_prompted.discard(child)
                    self.runs.start(child, tool_use_id=spawned.parent_call, agent_type=str(payload.get("subagentName") or ""),
                                    description=str(payload.get("description") or ""),
                                    background=bool(payload.get("runInBackground")), model=str(payload.get("model") or ""),
                                    prompt=prompt or spawned.prompt, trace_ref=self._key(child))
                    if spawned.usage:
                        self.runs.report_usage(child, spawned.usage)
                    self._progress(child, str(frame.get("timestamp") or ""))
                self._send_held(child, spawned)
            return
        if typ in SUBAGENT_ENDS:
            # The spawning agent hears its subagent's end with the result.
            child = str(payload.get("subagentId") or "")
            if isinstance(payload.get("usage"), dict):
                # The run's own total, when Kimi Code gives it.
                self.runs.report_usage(child, _usage(payload["usage"]))
            if child in self.agents:
                self.agents[child].usage = {}
            card = self.runs.finish(child, status=SUBAGENT_ENDS[typ], turn_id=self.prompt_id or turn,
                                    result=payload.get("resultSummary") or payload.get("error") or "")
            if card is not None:
                self.emit({"method": "item/completed", "params": {
                    "threadId": self.session_id, "turnId": self.prompt_id or turn, "item": card,
                }, **({"timestamp": str(frame["timestamp"])} if frame.get("timestamp") else {})})
            return
        if typ in {"assistant.delta", "thinking.delta"}:
            kind = "thinking" if typ == "thinking.delta" else "assistant"
            if state.kind and kind != state.kind:
                self._finish_block(agent, state)
            state.kind = kind
            state.text += str(payload.get("delta") or "")
            if agent == "main":
                self.emit({"method": "item/reasoning/textDelta" if kind == "thinking" else "item/agentMessage/delta",
                           "params": {"threadId": self.session_id, "turnId": self.prompt_id or turn, "delta": str(payload.get("delta") or "")}})
        elif typ in {"tool.call.started", "tool.result"}:
            self._finish_block(agent, state)
            self._release(agent, state)
            call = str(payload.get("toolCallId") or "")
            if not call:
                return
            item = {"id": call, "type": "toolCall"}
            if typ == "tool.call.started":
                item.update(name=payload.get("name"), input=payload.get("args"), status="running")
                args = payload.get("args")
                if agent == "main" and isinstance(args, dict) and isinstance(args.get("prompt"), str):
                    self.call_prompts[call] = args["prompt"]
            else:
                item.update(output=payload.get("output"), status="failed" if payload.get("isError") else "completed", error=bool(payload.get("isError")))
            self._send(agent, state, "item/started" if typ == "tool.call.started" else "item/completed", item, str(frame.get("timestamp") or ""))
        elif typ == "turn.step.completed":
            self._finish_block(agent, state)
            timing = self.request_timing(agent, state.turn, state.step, str(payload.get('stepId') or '')) if self.request_timing else {}
            self._release_timed(agent, state, timing)
            if agent == "main" and isinstance(payload.get("usage"), dict):
                step = _usage(payload["usage"])
                self.emit({"method": "thread/tokenUsage/updated", "params": {
                    "threadId": self.session_id, "turnId": self._main_turn(),
                    "tokenUsage": {"context": {"inputTokens": step["input_tokens"]},
                                   "modelContextWindow": self.context_window},
                }})
            if agent != "main" and isinstance(payload.get("usage"), dict):
                # A subagent's steps add up to its run's token use.
                step = _usage(payload["usage"])
                state.usage = {key: state.usage.get(key, 0) + value for key, value in step.items()}
                if self.runs.knows(agent):
                    self.runs.report_usage(agent, state.usage)
                    self._progress(agent, str(frame.get("timestamp") or ""))
        elif typ in {"turn.step.interrupted", "turn.step.retrying"}:
            self._release_timed(agent, state, {})
            state.text, state.kind, state.pending, state.incomplete = "", "", None, True
            state.timing, state.timing_ready = {}, False
        elif typ == "turn.ended":
            successful = not payload.get("error") and payload.get("reason", "completed") in {"completed", "stop", "end_turn"}
            if successful:
                self._finish_block(agent, state)
                if not state.timing_ready:
                    self._release_timed(agent, state, {})
                self._release(agent, state, final=True)
            else:
                state.text, state.kind, state.pending = "", "", None
            if agent == "main":
                self.ended = True
                self.finish()
