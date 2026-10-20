from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .kimi_live_events import KimiLiveItems, KimiLiveStream
from .native_agent_transport import NativeAgentDetached, NativeAgentUnavailable


DEFAULT_BASE_URL = "http://127.0.0.1:58561/api/v1"
DEFAULT_POLL_SECONDS = 0.75
from .kimi_code_server import DEFAULT_MODEL, DEFAULT_HOME


def credential_paths() -> tuple[Path, ...]:
    return (Path(os.getenv("PORTAL_KIMI_CODE_HOME", str(DEFAULT_HOME))).expanduser(),)


class KimiCodeError(RuntimeError):
    pass


class KimiCodeUnavailable(KimiCodeError, NativeAgentUnavailable):
    pass


class KimiCodeDetached(KimiCodeError, NativeAgentDetached):
    """A retiring Worker stopped following a prompt; the prompt keeps running."""


class KimiCodeTurnFailed(KimiCodeError):
    pass


@dataclass
class KimiCodeTurnResult:
    session_id: str
    turn_id: str
    response: str
    usage: dict[str, Any]
    duration_ms: int


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# Prompts steered into a running prompt (``KimiCodeClient.steer``), by the
# running prompt's id: their user messages continue that prompt's turn.
_STEERED_LOCK = threading.Lock()
_STEERED: dict[str, set[str]] = {}


def _steered_into(prompt_id: str) -> set[str]:
    with _STEERED_LOCK:
        return set(_STEERED.get(prompt_id) or ())


def _record_steered(prompt_id: str, steered_prompt_id: str) -> None:
    with _STEERED_LOCK:
        _STEERED.setdefault(prompt_id, set()).add(steered_prompt_id)
        # Bounded: entries of long-finished turns are dropped first.
        while len(_STEERED) > 256:
            _STEERED.pop(next(iter(_STEERED)))


def _usage_delta(before: object, after: object) -> dict[str, Any]:
    previous = before if isinstance(before, dict) else {}
    current = after if isinstance(after, dict) else {}
    aliases = {
        "input_tokens": "input_tokens",
        "cached_input_tokens": "cache_read_tokens",
        "cache_creation_tokens": "cache_creation_tokens",
        "output_tokens": "output_tokens",
    }
    result: dict[str, Any] = {}
    for target, source in aliases.items():
        try:
            result[target] = max(0, int(current.get(source) or 0) - int(previous.get(source) or 0))
        except (TypeError, ValueError):
            result[target] = 0
    result["total_tokens"] = (
        result["input_tokens"]
        + result["cache_creation_tokens"]
        + result["output_tokens"]
    )
    try:
        result["total_cost_usd"] = max(
            0.0,
            float(current.get("total_cost_usd") or 0) - float(previous.get("total_cost_usd") or 0),
        )
    except (TypeError, ValueError):
        result["total_cost_usd"] = 0.0
    return result


class KimiCodeClient:
    unavailable_type = KimiCodeUnavailable

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        events_root: str | Path | None = None,
    ) -> None:
        self.base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
        self.poll_seconds = max(0.2, float(poll_seconds or DEFAULT_POLL_SECONDS))
        home = Path(os.getenv("PORTAL_KIMI_CODE_HOME", str(DEFAULT_HOME))).expanduser()
        self.events_root = Path(events_root).expanduser() if events_root is not None else home / "server" / "events"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        query: dict[str, Any] | None = None,
        timeout: float = 10.0,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            url += "?" + urlencode(clean_query)
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, method=method.upper(), headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            raise KimiCodeError(f"Kimi Code HTTP {exc.code}: {detail[:500]}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise KimiCodeUnavailable(f"Kimi Code backend unavailable: {exc}") from exc
        try:
            envelope = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise KimiCodeError("Kimi Code returned an invalid JSON response") from exc
        if not isinstance(envelope, dict):
            raise KimiCodeError("Kimi Code returned an invalid response envelope")
        if int(envelope.get("code") or 0) != 0:
            code = envelope.get("code")
            message = str(envelope.get("msg") or "Kimi Code request failed")
            raise KimiCodeError(f"Kimi Code error {code}: {message}")
        return envelope.get("data")


    def create_session(
        self,
        *,
        cwd: str,
        title: str,
        system_prompt: str = "",
        tools: list[str] | None = None,
        permission_mode: str = "yolo",
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        agent_config: dict[str, Any] = {
            "model": str(model or DEFAULT_MODEL),
            "permission_mode": permission_mode,
        }
        if system_prompt:
            agent_config["system_prompt"] = system_prompt
        if tools is not None:
            agent_config["tools"] = list(tools)
        data = self._request(
            "POST",
            "sessions",
            {
                "title": title or "Portal Resident",
                "metadata": {"cwd": cwd},
                "agent_config": agent_config,
            },
        )
        return data if isinstance(data, dict) else {}

    def update_session_profile(
        self,
        session_id: str,
        *,
        tools: list[str],
        permission_mode: str = "yolo",
        model: str = DEFAULT_MODEL,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}/profile",
            {
                "agent_config": {
                    "model": str(model or DEFAULT_MODEL),
                    "tools": list(tools),
                    "permission_mode": permission_mode,
                }
            },
        )
        return data if isinstance(data, dict) else {}

    def session_tools(self, session_id: str) -> list[dict[str, Any]]:
        """The tools registered for a live session: ``name``, ``source``, ``active``."""

        data = self._request("GET", "tools", query={"session_id": session_id}, timeout=5.0)
        tools = data.get("tools") if isinstance(data, dict) else None
        return [tool for tool in tools if isinstance(tool, dict)] if isinstance(tools, list) else []


    def get_snapshot(self, session_id: str) -> dict[str, Any]:
        data = self._request("GET", f"sessions/{quote(session_id, safe='')}/snapshot", timeout=10.0)
        return data if isinstance(data, dict) else {}

    def get_status(self, session_id: str) -> dict[str, Any]:
        data = self._request("GET", f"sessions/{quote(session_id, safe='')}/status", timeout=5.0)
        return data if isinstance(data, dict) else {}

    def resolve_approval(
        self,
        session_id: str,
        approval_id: str,
        *,
        decision: str,
        feedback: str = "",
    ) -> dict[str, Any]:
        payload = {"decision": decision}
        if feedback:
            payload["feedback"] = feedback
        data = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}/approvals/{quote(approval_id, safe='')}",
            payload,
        )
        return data if isinstance(data, dict) else {}

    def dismiss_question(self, session_id: str, question_id: str) -> dict[str, Any]:
        data = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}/questions/{quote(question_id, safe='')}:dismiss",
            {},
        )
        return data if isinstance(data, dict) else {}

    def list_messages(self, session_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        before_id = ""
        seen_cursors: set[str] = set()
        while True:
            data = self._request(
                "GET",
                f"sessions/{quote(session_id, safe='')}/messages",
                query={"page_size": 100, "before_id": before_id or None},
                timeout=10.0,
            )
            page = data if isinstance(data, dict) else {}
            batch = [item for item in page.get("items") or [] if isinstance(item, dict)]
            items.extend(batch)
            if not page.get("has_more") or not batch:
                break
            cursor = str(batch[-1].get("id") or "")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
            before_id = cursor
        items.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
        return items

    def fork_session(self, session_id: str = "", *, source_session_id: str = "", title: str = "",
                     metadata: dict[str, Any] | None = None, **options) -> dict[str, Any]:
        session_id = source_session_id or session_id
        if not session_id:
            raise ValueError("The session has no Provider history to fork yet")
        payload: dict[str, Any] = {}
        if title:
            payload["title"] = title
        if metadata:
            payload["metadata"] = dict(metadata)
        data = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}:fork",
            payload,
        )
        return {**data, "session_id": str(data.get("id") or "")} if isinstance(data, dict) else {}

    def compact_session(
        self, session_id: str, instruction: str = "", *,
        cancelled: Callable[[], bool] | None = None,
        detach_requested: Callable[[], bool] | None = None,
        **options,
    ) -> dict[str, Any]:
        from .execution.native_session_compaction import kimi_wire_path, read_kimi_latest_compaction
        path = kimi_wire_path(credential_paths()[0], session_id)
        before = read_kimi_latest_compaction(path) if path else None

        def check_control() -> None:
            if cancelled is not None and cancelled():
                self.abort_session(session_id)
                raise InterruptedError("Kimi compaction cancelled")
            if detach_requested is not None and detach_requested():
                raise KimiCodeDetached("Worker handover: Kimi compaction keeps running")

        check_control()
        payload = {"instruction": instruction} if instruction else {}
        response = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}:compact",
            payload,
        )
        result = response if isinstance(response, dict) else {}
        idle_samples = 0
        while idle_samples < 2:
            check_control()
            try:
                status = self.get_status(session_id)
            except KimiCodeUnavailable:
                time.sleep(self.poll_seconds)
                continue
            if status.get("busy"):
                idle_samples = 0
            else:
                idle_samples += 1
            if idle_samples < 2:
                time.sleep(self.poll_seconds)
        path = kimi_wire_path(credential_paths()[0], session_id)
        after = read_kimi_latest_compaction(path) if path else None
        compacted = after is not None and (before is None or before.ref != after.ref)
        return {"session_id": session_id, "compacted": compacted,
                "reason": "" if compacted else "nothing_to_compact"}

    def steer(self, session_key: str = "", session_id: str = "", *, prompt_id: str = "", turn_id: str = "", text: str, control_id: str = "") -> dict[str, Any]:
        """Give the running prompt more input; Kimi Code takes it at the turn's next step.

        Kimi Code queues a prompt submitted during a turn, and ``:steer``
        moves a queued prompt into the running one.  A prompt left queued
        would run as a turn of its own after this one, outside Portal, so one
        that could not be steered is aborted.
        """

        session_id = session_id or session_key
        prompt_id = prompt_id or turn_id
        session = quote(session_id, safe="")
        prompts = self._request("GET", f"sessions/{session}/prompts")
        active = (prompts or {}).get("active") if isinstance(prompts, dict) else None
        if not isinstance(active, dict) or str(active.get("prompt_id") or "") != prompt_id:
            return {"steered": False, "reason": "no_active_turn"}
        key = re.sub(r"[^0-9a-zA-Z]", "", control_id)[-40:] or hashlib.sha256(text.encode()).hexdigest()[:24]
        steered_id = f"portal-steer-{prompt_id[-24:]}-{key}"
        if steered_id in _steered_into(prompt_id):
            return {"steered": True, "duplicate": True}
        submitted = self._request("POST", f"sessions/{session}/prompts", {
            "content": [{"type": "text", "text": text}],
            "prompt_id": steered_id,
        })
        submitted = submitted if isinstance(submitted, dict) else {}
        user_message_id = str(submitted.get("user_message_id") or "")
        if str(submitted.get("status") or "") != "queued":
            # The running prompt ended in between and this one started a turn
            # of its own: stop it; Portal runs the input as its next turn.
            self._abort_prompt(session, steered_id)
            return {"steered": False, "reason": "turn_closing"}
        try:
            self._request("POST", f"sessions/{session}/prompts/{quote(steered_id, safe='')}:steer", {})
        except KimiCodeError:
            self._abort_prompt(session, steered_id)
            return {"steered": False, "reason": "turn_closing"}
        _record_steered(prompt_id, steered_id)
        return {"steered": True, "prompt_id": steered_id}

    def _abort_prompt(self, session: str, prompt_id: str) -> None:
        try:
            self._request("POST", f"sessions/{session}/prompts/{quote(prompt_id, safe='')}:abort", {})
        except KimiCodeError:
            pass

    def stop_subagent(self, session_key: str = "", session_id: str = "", *, tool_use_id: str) -> dict[str, Any]:
        """Stop one subagent run the session's model started (its Agent call id); the turn goes on.

        Kimi Code runs each subagent as a task of the main agent, named by the
        call that started it; cancelling the task ends the run as cancelled.
        """

        if not tool_use_id:
            return {"stopped": False, "reason": "unknown_subagent"}
        session = quote(session_id or session_key, safe="")
        data = self._request("GET", f"sessions/{session}/tasks", timeout=5.0)
        items = data.get("items") if isinstance(data, dict) else None
        runs = [task for task in items or [] if isinstance(task, dict) and task.get("kind") == "subagent"
                and str(task.get("parent_tool_call_id") or "") == tool_use_id]
        if not runs:
            return {"stopped": False, "reason": "unknown_subagent"}
        running = [task for task in runs if task.get("status") == "running"]
        if not running:
            return {"stopped": False, "reason": "subagent_finished"}
        task = quote(str(running[-1].get("id") or ""), safe="")
        try:
            result = self._request("POST", f"sessions/{session}/tasks/{task}:cancel", {})
        except KimiCodeUnavailable:
            raise
        except KimiCodeError:
            # It ended in between (Kimi Code: the task already finished).
            return {"stopped": False, "reason": "subagent_finished"}
        if isinstance(result, dict) and result.get("cancelled") is False:
            return {"stopped": False, "reason": "subagent_finished"}
        return {"stopped": True}

    def abort_session(self, session_id: str) -> dict[str, Any]:
        data = self._request("POST", f"sessions/{quote(session_id, safe='')}:abort", {})
        return data if isinstance(data, dict) else {}

    def _journal_turn_result(self, session_id: str, after_seq: int) -> tuple[dict[str, Any], str]:
        if not re.fullmatch(r"session_[A-Za-z0-9._-]+", session_id):
            return {}, ""
        path = self.events_root / f"{session_id}.jsonl"
        usage_totals = {
            "input_other": 0,
            "cache_read": 0,
            "cache_creation": 0,
            "output": 0,
        }
        error_message = ""
        try:
            stream = path.open("r", encoding="utf-8")
        except OSError:
            return {}, ""
        with stream:
            for raw_line in stream:
                try:
                    row = json.loads(raw_line)
                except (TypeError, ValueError):
                    continue
                envelope = row.get("envelope") if isinstance(row, dict) else None
                if not isinstance(envelope, dict):
                    continue
                try:
                    seq = int(envelope.get("seq") or 0)
                except (TypeError, ValueError):
                    seq = 0
                if seq <= after_seq:
                    continue
                payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
                event_type = str(payload.get("type") or envelope.get("type") or "")
                if event_type == "turn.step.completed":
                    native_usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
                    usage_totals["input_other"] += _safe_nonnegative_int(native_usage.get("inputOther"))
                    usage_totals["cache_read"] += _safe_nonnegative_int(native_usage.get("inputCacheRead"))
                    usage_totals["cache_creation"] += _safe_nonnegative_int(native_usage.get("inputCacheCreation"))
                    usage_totals["output"] += _safe_nonnegative_int(native_usage.get("output"))
                elif event_type in {"error", "turn.step.interrupted"}:
                    error_message = str(payload.get("message") or error_message)
                elif event_type == "turn.ended" and isinstance(payload.get("error"), dict):
                    error_message = str(payload["error"].get("message") or error_message)
        total = sum(usage_totals.values())
        if total <= 0:
            return {}, error_message
        return {
            "input_tokens": usage_totals["input_other"],
            "cached_input_tokens": usage_totals["cache_read"] + usage_totals["cache_creation"],
            "cache_creation_tokens": usage_totals["cache_creation"],
            "output_tokens": usage_totals["output"],
            "total_tokens": total,
        }, error_message

    def _open_event_stream(self, session_id: str) -> KimiLiveStream:
        try:
            return KimiLiveStream(self.base_url, session_id)
        except (OSError, ConnectionError) as exc:
            raise KimiCodeUnavailable(str(exc)) from exc

    def close_session(self, session_key, session_id):
        if session_id:
            self.abort_session(session_id)
        return {"closed": True}

    def interrupt(self, session_key, session_id, *, turn_id=""):
        if not session_id:
            return {"interrupted": False}
        self.abort_session(session_id)
        return {"interrupted": True}

    def run_turn(
        self, *, session_id: str, prompt: str, tools=None, native_tools=None,
        continue_prompt: str = "", turn_id: str = "", detach_requested=None,
        cancelled=None, event_callback=None, system_prompt_append="", prepend_system_prompt=False,
        permission_mode="yolo", model="", disabled_tools=None, **options,
    ) -> KimiCodeTurnResult:
        allowed = list(native_tools if native_tools is not None else tools or [])
        if continue_prompt:
            if cancelled is not None and cancelled():
                raise InterruptedError("Kimi turn cancelled before resume")
            try:
                return self.resume_turn(session_id=session_id, prompt_id=turn_id, tools=allowed,
                                        event_callback=event_callback, detach_requested=detach_requested)
            except KimiCodeTurnFailed as exc:
                if "without a final response" not in str(exc) or (cancelled is not None and cancelled()):
                    raise
                prompt = continue_prompt
                if event_callback:
                    event_callback({"method": "turn/recovered", "params": {
                        "threadId": session_id, "turnId": turn_id, "prompt": prompt, "input_delivery": "continued"}})
        elif prepend_system_prompt and system_prompt_append:
            prompt = f"{system_prompt_append}\n\n{prompt}"
        with_stream = self._open_event_stream(session_id)
        try:
            return self._run_turn(session_id=session_id, live_stream=with_stream, prompt=prompt,
                                  tools=allowed, permission_mode=permission_mode or "yolo", model=model or DEFAULT_MODEL,
                                  event_callback=event_callback, disabled_tools=disabled_tools,
                                  detach_requested=detach_requested)
        finally:
            with_stream.close()

    def _run_turn(
        self,
        *,
        session_id: str,
        live_stream: KimiLiveStream,
        prompt: str,
        tools: list[str],
        permission_mode: str = "yolo",
        model: str = DEFAULT_MODEL,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        disabled_tools: list[str] | None = None,
        detach_requested: Callable[[], bool] | None = None,
    ) -> KimiCodeTurnResult:
        """Run one turn.  ``disabled_tools`` replaces the tools switched off for the session.

        The Kimi Code server runs the prompt; this call follows it.  When
        ``detach_requested`` turns true (a Worker handover) it stops following
        with ``KimiCodeDetached``, and the prompt runs on for ``resume_turn``.
        """

        started_at = time.monotonic()
        profile = self.update_session_profile(
            session_id,
            tools=tools,
            permission_mode=permission_mode,
            model=model,
        )
        before_usage = profile.get("usage") if isinstance(profile.get("usage"), dict) else {}
        before_snapshot = self.get_snapshot(session_id)
        try:
            before_seq = int(before_snapshot.get("as_of_seq") or 0)
        except (TypeError, ValueError):
            before_seq = 0
        submitted = self._request(
            "POST",
            f"sessions/{quote(session_id, safe='')}/prompts",
            {
                "content": [{"type": "text", "text": prompt}],
                "model": str(model or DEFAULT_MODEL),
                "permission_mode": permission_mode,
                **({} if disabled_tools is None else {"disabled_tools": list(disabled_tools)}),
            },
        )
        submission = submitted if isinstance(submitted, dict) else {}
        prompt_id = str(submission.get("prompt_id") or "")
        user_message_id = str(submission.get("user_message_id") or "")
        if not prompt_id:
            raise KimiCodeTurnFailed("Kimi Code prompt submission returned no prompt_id")

        def emit(event: dict[str, Any]) -> None:
            if event_callback:
                event_callback(event)

        emit({
            "timestamp": _now_iso(),
            "method": "turn/started",
            "params": {
                "thread": {"id": session_id},
                "turn": {"id": prompt_id},
                "userMessageId": user_message_id,
            },
        })
        return self._follow_prompt(
            session_id=session_id, prompt_id=prompt_id, user_message_id=user_message_id, tools=tools,
            before_usage=before_usage, before_seq=before_seq,
            model=model,
            started_at=started_at, emit=emit, detach_requested=detach_requested, live_stream=live_stream,
        )

    def _follow_prompt(
        self,
        *,
        session_id: str,
        prompt_id: str,
        user_message_id: str,
        tools: list[str],
        before_usage: dict[str, Any],
        before_seq: int,
        started_at: float,
        emit: Callable[[dict[str, Any]], None],
        detach_requested: Callable[[], bool] | None = None,
        resumed: bool = False,
        live_stream: KimiLiveStream,
        model: str = "",
    ) -> KimiCodeTurnResult:
        """Follow a submitted prompt to its end: its progress, approvals and answer.

        ``resumed``: the prompt was submitted by an earlier attempt, and it
        has ended once the session is not busy.
        """

        live = KimiLiveItems(session_id, emit, prompt_id=prompt_id, joining=resumed,
                             request_timing=None)
        from .kimi_code_server import model_catalog
        windows = model_catalog()["context_windows"]
        live.context_window = windows.get(model, 0)
        resolved_approvals: set[str] = set()
        dismissed_questions: set[str] = set()
        allowed_tools = {str(item) for item in tools if str(item)}
        observed_busy = False

        last_snapshot: dict[str, Any] = {}
        retry_delay = self.poll_seconds
        while True:
            if detach_requested is not None and detach_requested():
                raise KimiCodeDetached(f"Worker handover: prompt {prompt_id} keeps running")
            try:
                snapshot = self.get_snapshot(session_id)
                retry_delay = self.poll_seconds
            except KimiCodeUnavailable:
                live_stream.drain(live.observe)
                time.sleep(retry_delay)
                retry_delay = min(5.0, retry_delay * 1.5)
                continue
            last_snapshot = snapshot
            session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else {}
            agent_config = session.get("agent_config") if isinstance(session.get("agent_config"), dict) else {}
            observed_model = str(agent_config.get("model") or "")
            if observed_model in windows:
                live.context_window = windows[observed_model]
            live_stream.drain(live.observe)
            in_flight = snapshot.get("in_flight_turn") if isinstance(snapshot.get("in_flight_turn"), dict) else {}
            for approval in snapshot.get("pending_approvals") or []:
                if not isinstance(approval, dict):
                    continue
                approval_id = str(approval.get("approval_id") or "")
                if not approval_id or approval_id in resolved_approvals:
                    continue
                tool_name = str(approval.get("tool_name") or "")
                approved = any(fnmatchcase(tool_name, pattern) for pattern in allowed_tools)
                self.resolve_approval(
                    session_id,
                    approval_id,
                    decision="approved" if approved else "rejected",
                    feedback="" if approved else "Portal non-owner policy does not allow this tool.",
                )
                resolved_approvals.add(approval_id)
            for question in snapshot.get("pending_questions") or []:
                if not isinstance(question, dict):
                    continue
                question_id = str(question.get("question_id") or "")
                if not question_id or question_id in dismissed_questions:
                    continue
                self.dismiss_question(session_id, question_id)
                dismissed_questions.add(question_id)
            observed_busy = observed_busy or bool(session.get("busy")) or bool(in_flight)
            busy = bool(session.get("busy")) or bool(in_flight)
            snapshot_messages = snapshot.get("messages") if isinstance(snapshot.get("messages"), dict) else {}
            submitted_user_seen = any(
                str(item.get("id") or "") == user_message_id
                for item in snapshot_messages.get("items") or []
                if isinstance(item, dict)
            )
            reason = str(session.get("last_turn_reason") or "")
            if not busy and (observed_busy or resumed):
                break
            if not busy and submitted_user_seen and reason in {"completed", "cancelled", "failed"}:
                break
            if not busy and time.monotonic() - started_at >= max(1.0, self.poll_seconds * 2):
                if any(
                    str(item.get("prompt_id") or "") == prompt_id
                    for item in snapshot_messages.get("items") or []
                    if isinstance(item, dict)
                ):
                    break
            time.sleep(self.poll_seconds)

        # Drain events through the terminal boundary before finalizing immutable items.
        for _ in range(5):
            live_stream.drain(live.observe)
            if live.ended:
                break
            time.sleep(self.poll_seconds)
        live.finish()
        response = live.response
        if resumed:
            # A WebSocket subscription starts at attachment time. Recover the
            # accepted prompt's durable answer after it ends, as a new immutable
            # item; never replace blocks an earlier Worker already displayed.
            for message in reversed(self.list_messages(session_id)):
                if message.get("role") != "assistant" or message.get("prompt_id") != prompt_id:
                    continue
                text = "\n".join(
                    str(part.get("text") or "") for part in message.get("content") or []
                    if isinstance(part, dict) and part.get("type") == "text"
                ).strip()
                if not text:
                    continue
                if text != response:
                    identity = str(message.get("id") or hashlib.sha256(text.encode()).hexdigest())
                    emit({"timestamp": _now_iso(), "method": "item/completed", "params": {
                        "threadId": session_id, "turnId": prompt_id,
                        "item": {"id": f"kimi-recovered:{session_id}:{prompt_id}:{identity}",
                                 "type": "agentMessage", "phase": "final_answer", "text": text},
                    }})
                response = text
                break
        session = last_snapshot.get("session") if isinstance(last_snapshot.get("session"), dict) else {}
        cumulative_usage = session.get("usage") if isinstance(session.get("usage"), dict) else {}
        reason = str(session.get("last_turn_reason") or "")
        journal_usage, journal_error = self._journal_turn_result(session_id, before_seq)
        if resumed:
            # Where the prompt began in the journal is unknown here; the
            # Backend reads the turn's usage from the session (observe_turn_usage).
            journal_usage, journal_error, before_usage, cumulative_usage = {}, "", {}, {}
        if reason == "failed" and not response:
            detail = f": {journal_error}" if journal_error else ""
            raise KimiCodeTurnFailed(f"Kimi Code turn failed before returning a response{detail}")
        if not response:
            raise KimiCodeTurnFailed("Kimi Code turn completed without a final response from the live event stream")
        usage = journal_usage or _usage_delta(before_usage, cumulative_usage)
        emit({
            "timestamp": _now_iso(),
            "method": "turn/completed",
            "params": {
                "threadId": session_id,
                "turn": {"id": prompt_id, "status": reason or "completed"},
                "usage": usage,
            },
        })
        return KimiCodeTurnResult(
            session_id=session_id,
            turn_id=prompt_id,
            response=response,
            usage=usage,
            duration_ms=int((time.monotonic() - started_at) * 1000),
        )

    def resume_turn(
        self,
        *,
        session_id: str,
        prompt_id: str,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
        tools: list[str] | None = None,
        detach_requested: Callable[[], bool] | None = None,
    ) -> KimiCodeTurnResult:
        """Follow a prompt an earlier attempt submitted, like ``run_turn`` does.

        A prompt still running is followed to its end (its approvals answered
        by ``tools``). Its complete persisted answer is appended after the live
        items when the handover missed part or all of that answer.
        """

        safe_session_id = str(session_id or "").strip()
        safe_prompt_id = str(prompt_id or "").strip()
        if not safe_session_id or not safe_prompt_id:
            raise ValueError("session_id and prompt_id are required")
        user_message_id = next(
            (
                str(message.get("id") or "")
                for message in self.list_messages(safe_session_id)
                if (
                    isinstance(message, dict)
                    and str(message.get("role") or "") == "user"
                    and str(message.get("prompt_id") or "") == safe_prompt_id
                )
            ),
            "",
        )

        def emit(event: dict[str, Any]) -> None:
            if event_callback:
                event_callback(event)

        live_stream = self._open_event_stream(safe_session_id)
        try:
            return self._follow_prompt(
                session_id=safe_session_id, prompt_id=safe_prompt_id, user_message_id=user_message_id,
                tools=list(tools or []), before_usage={},
                before_seq=0, started_at=time.monotonic(), emit=emit, detach_requested=detach_requested,
                resumed=True, live_stream=live_stream,
            )
        finally:
            live_stream.close()


_CLIENT: KimiCodeClient | None = None
_CLIENT_LOCK = threading.Lock()


def shared_client() -> KimiCodeClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = KimiCodeClient(
                os.getenv("PORTAL_KIMI_CODE_BASE_URL", DEFAULT_BASE_URL),
                float(os.getenv("PORTAL_KIMI_CODE_POLL_SECONDS", str(DEFAULT_POLL_SECONDS))),
                Path(os.getenv("PORTAL_KIMI_CODE_HOME", str(DEFAULT_HOME))) / "server" / "events",
            )
        return _CLIENT
