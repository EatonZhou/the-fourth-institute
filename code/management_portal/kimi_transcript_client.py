"""Kimi Code 2.5 transcript and interaction protocol."""
from __future__ import annotations

import hashlib
import threading
import json
import queue
import time
from fnmatch import fnmatchcase
from urllib.parse import quote

from management_common.provider_user_input import REQUEST_ITEM, RESULT_ITEM, validate_response
from management_common.provider_subagent_runs import PROGRESS_METHOD, SubagentRuns

from .kimi_code_client import (KimiCodeClient, KimiCodeDetached, KimiCodeObservationLost,
                               KimiCodeTurnFailed, KimiCodeTurnResult, KimiCodeUnavailable,
                               SNAPSHOT_RECOVERY_SECONDS)
from .kimi_live_events import KimiLiveStream, UnsupportedKimiProtocol


class TranscriptStream(KimiLiveStream):
    """Subscribe for changes. The server's transcript is the authoritative projection."""

    def _subscribe(self, transport):
        for method, ident, payload in (
            ("client_hello", "portal-hello", {"client_id": "tfi-portal"}),
            ("subscribe_v2", "portal-subscribe", {
                "session_id": self.session_id, "transcript": {"*": "delta"}}),
        ):
            transport.send_text(json.dumps({"type": method, "id": ident, "payload": payload}))

    def _accept(self, frame, payload):
        if frame.get("code") or self.session_id not in payload.get("accepted", []):
            raise UnsupportedKimiProtocol("Kimi rejected the transcript subscription")

    def _enqueue(self, frame):
        if frame.get("type") in {"transcript.ops", "transcript.reset", "interaction"}:
            # Coalesce token deltas. After a reconnect the reset wakes the same reader.
            if self.frames.empty():
                self.frames.put(True)

    def wait(self, timeout):
        try:
            self.frames.get(timeout=timeout)
            return True
        except queue.Empty:
            return False


class TurnItems:
    def __init__(self, session, prompt, emit):
        self.session = session
        self.prompt = prompt
        self.emit = emit
        self.runs = SubagentRuns()
        self.text = {}
        self.delivered = set()

    def event(self, method, details, agent, parent):
        event = {"method": method, "params": {"threadId": self.session, "turnId": self.prompt, **details}}
        if parent:
            if method == "item/completed":
                self.runs.observe(agent, details["item"])
            event = {"method": "subagent/event", "params": {
                "traceKey": f"{self.session}:{agent}", "agentId": agent,
                "parentToolUseId": parent, "event": event}}
        self.emit(event)

    def follow_child(self, agent, parent_frame, page, main_turn):
        call = parent_frame["toolCallId"]
        finished = (call, agent, "reported")
        if finished in self.delivered:
            return []
        args = parent_frame.get("input") or {}
        if (call, agent) not in self.delivered:
            self.runs.start(agent, tool_use_id=call, trace_ref=f"{self.session}:{agent}",
                            agent_type=args.get("subagent_type") or "coder",
                            prompt=args.get("prompt") or "", description=args.get("description") or "")
            self.delivered.add((call, agent))
        turns = [t for t in page["items"] if t["kind"] == "turn" and
                 (t.get("startedAt") or "") >= (main_turn.get("startedAt") or "")]
        for turn in turns:
            self.render(turn, agent, call)
        card = self.runs.live(agent, turn_id=self.prompt)
        if card:
            self.event(PROGRESS_METHOD, {"item": card}, "main", None)
        if parent_frame["state"] in {"done", "error"} and turns and all(
                t["state"] in {"completed", "failed", "cancelled"} for t in turns):
            card = self.runs.finish(agent, turn_id=self.prompt,
                                    status="failed" if parent_frame["state"] == "error" else "completed",
                                    result=parent_frame.get("output"))
            if card:
                self.event("item/completed", {"item": card}, "main", None)
            self.delivered.add(finished)
        return turns

    def render(self, turn, agent="main", parent=None):
        """Publish complete frames with stable identities across attachment and replay."""
        answer = ""
        children = {}
        steps = turn.get("steps", [])
        texts = [f["frameId"] for s in steps if s["state"] == "completed" for f in s["frames"]
                 if f["kind"] == "text" and f.get("role") == "assistant" and f.get("text")]
        for step in steps:
            if step.get("usage") and agent == "main" and ("usage", step["stepId"]) not in self.delivered:
                sample = step["usage"]
                tokens = sum(sample.get(k, 0) for k in ("inputOther", "inputCacheRead", "inputCacheCreation"))
                self.event("thread/tokenUsage/updated", {"tokenUsage": {"context": {"inputTokens": tokens}}},
                           agent, parent)
                self.delivered.add(("usage", step["stepId"]))
            frames = step.get("frames", [])
            for index, frame in enumerate(frames):
                kind = frame["kind"]
                key = f"{self.session}:{agent}:{frame['frameId']}"
                if kind == "tool":
                    call = frame["toolCallId"]
                    for ref in frame.get("agentRefs", []):
                        children[ref["agentId"]] = frame
                    status = frame["state"]
                    method = "item/started" if status == "running" else "item/completed"
                    item = {"id": call, "type": "toolCall", "name": frame.get("name"),
                            "input": frame.get("input"), "output": frame.get("output"),
                            "status": {"running": "running", "error": "failed", "done": "completed"}[status],
                            "error": status == "error"}
                elif kind == "thinking" or (kind == "text" and frame.get("role") == "assistant"):
                    text = frame["text"]
                    previous = self.text.get(key, "")
                    if text != previous:
                        if text.startswith(previous):
                            method = "item/reasoning/textDelta" if kind == "thinking" else "item/agentMessage/delta"
                            self.event(method, {"itemId": key, "delta": text[len(previous):]}, agent, parent)
                        self.text[key] = text
                    last = step is steps[-1] and index == len(frames)-1
                    final = turn["state"] == "completed" and texts and frame["frameId"] == texts[-1]
                    if final:
                        answer = text
                    if step["state"] in {"failed", "interrupted"} or (turn["state"] == "running" and
                            (last or (texts and frame["frameId"] == texts[-1]))):
                        continue
                    method = "item/completed"
                    item = ({"id": key, "type": "reasoning", "content": [text]} if kind == "thinking" else
                            {"id": key, "type": "agentMessage", "text": text,
                             "phase": "final_answer" if final else "commentary"})
                else:
                    continue
                token = (key, method)
                if token not in self.delivered:
                    self.event(method, {"item": item}, agent, parent)
                    self.delivered.add(token)
        return answer, children


class KimiTranscriptClient(KimiCodeClient):
    def __init__(self, *args, native_questions=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.native_questions = native_questions
        self._question_lock = threading.RLock()
        self._questions = {}

    def _open_event_stream(self, session_id):
        return TranscriptStream(self.base_url, session_id)

    def run_turn(self, *, system_prompt_append="", **kwargs):
        # The 2.5 session creation endpoint ignores the agent_config system prompt.
        if system_prompt_append:
            kwargs["prepend_system_prompt"] = True
        return super().run_turn(system_prompt_append=system_prompt_append, **kwargs)

    def resume_turn(self, *, session_id, prompt_id, event_callback=None, tools=None, detach_requested=None):
        if not session_id or not prompt_id:
            raise ValueError("session_id and prompt_id are required")
        stream = self._open_event_stream(session_id)
        try:
            return self._follow_prompt(session_id=session_id, prompt_id=prompt_id, tools=tools or [],
                                       started_at=time.monotonic(), emit=event_callback or (lambda event: None),
                                       detach_requested=detach_requested, live_stream=stream, resumed=True)
        finally:
            stream.close()

    def _sequence_before_prompt(self, session_id):
        # v2 attributes steps and usage directly to the accepted prompt.
        return 0

    def get_status(self, session_id):
        status = self._request("GET", f"sessions/{quote(session_id, safe='')}")
        return {**status, "max_context_tokens": (status.get("usage") or {}).get("context_limit")}

    def transcript(self, session, agent="main", before=None):
        return self._request("GET", f"sessions/{quote(session, safe='')}/transcript",
                             query={"agent_id": agent, "before_turn": before, "page_size": 30})

    def _interaction(self, session, ident, response):
        return self._request("POST", f"sessions/{quote(session, safe='')}/interactions/{quote(ident, safe='')}", response)

    def resolve_approval(self, session_id, approval_id, *, decision, feedback=""):
        return self._interaction(session_id, approval_id, {"kind": "approval", "decision": decision, "feedback": feedback})

    def dismiss_question(self, session_id, question_id):
        return self._interaction(session_id, question_id, {"kind": "question", "dismissed": True})

    def stop_subagent(self, *args, **kwargs):
        return {"stopped": False, "reason": "unsupported_by_provider"}

    def _prompt_turn(self, session, prompt, page, search_history):
        seen = set()
        while True:
            turns = [item for item in page["items"] if item["kind"] == "turn"]
            turn = next((t for t in turns if t.get("triggerPromptId") == prompt), None)
            if turn or not search_history or not turns or not page["has_more"]:
                return page, turn
            cursor = turns[0]["turnId"]
            if cursor in seen:
                raise KimiCodeObservationLost(f"Kimi transcript repeated page {cursor}")
            seen.add(cursor)
            page = self.transcript(session, before=cursor)

    @staticmethod
    def _question_event(emit, item_type, data):
        key = data["request_id"]
        item = {"type": item_type, "id": f"user-input:{key}:{item_type}", "user_input": data}
        emit({"method": "item/started" if item_type == REQUEST_ITEM else "item/completed",
              "params": {"threadId": data["thread_id"], "turnId": data["turn_id"], "item": item}})

    def _open_question(self, session, prompt, req, emit):
        ident = req["interactionId"]
        key = hashlib.sha256(json.dumps([session, prompt, ident]).encode()).hexdigest()
        questions = [{**q, "header": q.get("header") or q["question"]} for q in req["request"]["questions"]]
        data = {"request_id": key, "thread_id": session, "turn_id": prompt, "item_id": ident,
                "is_blocking": True, "questions": questions, "status": "pending"}
        with self._question_lock:
            self._questions[key] = (data, emit)
            self._question_event(emit, REQUEST_ITEM, data)

    def answer_question(self, session, prompt, item):
        with self._question_lock:
            key = item["request_id"]
            entry = self._questions.get(key)
            if not entry or (entry[0]["thread_id"], entry[0]["turn_id"]) != (session, prompt):
                raise ValueError("Kimi question is no longer pending in this turn")
            data, emit = entry
            reply = validate_response(item, data["questions"])
            cancel = reply["action"] == "cancel"
            body = {"kind": "question"}
            if cancel:
                body["dismissed"] = True
            else:
                answers = {}
                for q in data["questions"]:
                    selected = reply["answers"][q["id"]]["answers"]
                    options = {o["label"]: o["id"] for o in q["options"]}
                    ids = [options[v] for v in selected if v in options]
                    other = [v for v in selected if v not in options]
                    if (len(other) > 1 or (other and not q.get("allow_other", True))
                            or (not q.get("multi_select") and len(selected) != 1)):
                        raise ValueError("Invalid Kimi question selection")
                    if other and ids:
                        answer = {"kind": "multi_with_other", "option_ids": ids, "other_text": other[0]}
                    elif other:
                        answer = {"kind": "other", "text": other[0]}
                    elif q.get("multi_select"):
                        answer = {"kind": "multi", "option_ids": ids}
                    else:
                        answer = {"kind": "single", "option_id": ids[0]}
                    answers[q["id"]] = answer
                body.update(answers=answers, method="click")
            self._interaction(session, data["item_id"], body)
            del self._questions[key]
            self._question_event(emit, RESULT_ITEM, {**data, "status": "cancelled" if cancel else "answered",
                                                     "answers": reply["answers"]})

    def _resolve_interactions(self, session_id, projection, owner, tools, resolved, *, prompt_id=None, emit=None):
        calls = {f["toolCallId"] for s in owner["steps"] for f in s["frames"] if f["kind"] == "tool"}
        for req in projection.get("interactions", []):
            ident = req["interactionId"]
            if req["state"] != "pending" or ident in resolved or owner["state"] != "running":
                continue
            if req.get("toolCallId") and req["toolCallId"] not in calls:
                continue
            if req["interactionKind"] == "question":
                if self.native_questions:
                    self._open_question(session_id, prompt_id, req, emit)
                else:
                    self.dismiss_question(session_id, ident)
            else:
                request = req.get("request") or {}
                name = request.get("toolName") or request.get("tool_name") or ""
                allowed = bool(name) and any(fnmatchcase(name, pattern) for pattern in tools)
                self.resolve_approval(session_id, ident, decision="approved" if allowed else "rejected")
            resolved.add(ident)

    def _follow_prompt(self, *, session_id, prompt_id, **options):
        try:
            return self._read_prompt(session_id=session_id, prompt_id=prompt_id, **options)
        finally:
            with self._question_lock:
                for key, (data, _) in list(self._questions.items()):
                    if (data["thread_id"], data["turn_id"]) == (session_id, prompt_id):
                        del self._questions[key]

    def _read_prompt(self, *, session_id, prompt_id, tools, started_at, emit, detach_requested,
                       live_stream, resumed=False, **kwargs):
        items = TurnItems(session_id, prompt_id, emit)
        resolved = set()
        last_contact = time.monotonic()
        refresh = True
        missing_since = time.monotonic()
        while True:
            if detach_requested and detach_requested():
                raise KimiCodeDetached(f"Worker handover: Kimi prompt {prompt_id} keeps running")
            if not refresh and not live_stream.wait(self.poll_seconds):
                if time.monotonic() - last_contact < 5:
                    continue
            refresh = False
            try:
                page = self.transcript(session_id)
            except KimiCodeUnavailable:
                if time.monotonic() - last_contact >= SNAPSHOT_RECOVERY_SECONDS:
                    raise KimiCodeObservationLost(f"Kimi lost contact with accepted prompt {prompt_id}") from None
                time.sleep(self.poll_seconds)
                refresh = True
                continue
            last_contact = time.monotonic()
            page, turn = self._prompt_turn(session_id, prompt_id, page, resumed)
            if not turn:
                if (resumed or time.monotonic() - missing_since >= SNAPSHOT_RECOVERY_SECONDS) and not self.get_status(session_id).get("busy"):
                    raise KimiCodeObservationLost(f"Transcript has no turn for accepted prompt {prompt_id}")
                continue
            answer, children = items.render(turn)
            pending = [(page, turn)]
            for agent, parent_frame in children.items():
                child_page = self.transcript(session_id, agent)
                for child in items.follow_child(agent, parent_frame, child_page, turn):
                    pending.append((child_page, child))
            for projection, owner in pending:
                self._resolve_interactions(session_id, projection, owner, tools, resolved, prompt_id=prompt_id, emit=emit)
            state = turn["state"]
            if state in {"failed", "cancelled"}:
                raise KimiCodeTurnFailed(turn.get("error") or f"Kimi turn {state}")
            if state == "completed":
                if not answer:
                    raise KimiCodeTurnFailed("Completed Kimi transcript has no final assistant text")
                samples = [s["usage"] for s in turn["steps"] if s.get("usage")]
                usage = {key: sum(s.get(source, 0) for s in samples) for key, source in (
                    ("input_tokens", "inputOther"), ("output_tokens", "output"),
                    ("cached_input_tokens", "inputCacheRead"), ("cache_creation_tokens", "inputCacheCreation"))}
                usage["input_tokens"] += usage["cached_input_tokens"] + usage["cache_creation_tokens"]
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                emit({"method": "turn/completed", "params": {"threadId": session_id,
                      "turn": {"id": prompt_id, "status": state}, "usage": usage}})
                return KimiCodeTurnResult(session_id, prompt_id, answer, usage,
                                          int((time.monotonic()-started_at)*1000))
