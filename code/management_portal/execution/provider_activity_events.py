"""The activity form of a Provider event.

``BackendContext`` hands every Provider event to the canonical stream as it
came and emits a compact copy for the activity stream: only the methods and
fields the activity view shows, long text cut to size, sensitive values
redacted (``activity_redaction``).
"""

from __future__ import annotations

from typing import Any, Mapping

from management_common.provider_subagent_runs import PROGRESS_METHOD as SUBAGENT_PROGRESS_METHOD

from .activity_redaction import redact_sensitive_mapping


PROVIDER_ACTIVITY_TEXT_LIMIT = 16_000
PROVIDER_ACTIVITY_METHODS = frozenset({
    SUBAGENT_PROGRESS_METHOD,
    "error",
    "guardianWarning",
    "item/autoApprovalReview/started",
    "item/autoApprovalReview/completed",
    "item/started",
    "item/completed",
    "turn/plan/updated",
    "item/plan/delta",
    "turn/started",
    "turn/completed",
    "thread/attached",
})


def _activity_text(value: object, limit: int = PROVIDER_ACTIVITY_TEXT_LIMIT) -> str:
    text = str(value or "")
    encoded = text.encode("utf-8", errors="replace")
    return text if len(encoded) <= limit else encoded[:limit].decode("utf-8", errors="ignore")


def _activity_item_text(item: dict[str, Any]) -> str:
    direct = str(item.get("text") or "")
    if direct:
        return _activity_text(direct)
    for key in ("content", "summary"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return _activity_text(value)
        if not isinstance(value, list):
            continue
        chunks = [
            str(part.get("text") or part.get("output_text") or "")
            for part in value
            if isinstance(part, dict)
        ]
        text = "\n\n".join(chunk for chunk in chunks if chunk)
        if text:
            return _activity_text(text)
    return ""


def _activity_arguments(value: object) -> dict[str, Any]:
    arguments = value if isinstance(value, dict) else {}
    result: dict[str, Any] = {}
    for key, item in list(arguments.items())[:32]:
        if str(key).casefold() in {"content", "prompt", "system_prompt"}:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)] = _activity_text(item, 2_000)
        elif isinstance(item, list):
            result[str(key)] = [
                _activity_text(entry, 500)
                for entry in item[:10]
                if isinstance(entry, (str, int, float, bool))
            ]
    return result


def _compact_activity_item(value: object) -> dict[str, Any]:
    item = value if isinstance(value, dict) else {}
    if item.get("type") == "turnArtifacts":
        return {key: item[key] for key in ("id", "type", "artifacts") if key in item}
    if item.get("type") == "subagentRun":
        return {key: item[key] for key in ("id", "type", "run") if key in item}
    if item.get("type") in {"userInputRequest", "userInputResponse"}:
        return {key: item[key] for key in ("id", "type", "user_input") if key in item}
    compact = {
        key: item[key]
        for key in (
            "id",
            "type",
            "status",
            "phase",
            "exitCode",
            "exit_code",
            "durationMs",
            "percent",
        )
        if item.get(key) not in (None, "")
    }
    for key in (
        "command",
        "cwd",
        "name",
        "server",
        "tool",
        "description",
        "path",
        "query",
        "revisedPrompt",
    ):
        if item.get(key) not in (None, ""):
            compact[key] = _activity_text(item[key], 4_000)
    text = _activity_item_text(item)
    if text:
        compact["text"] = text
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    if progress:
        compact["progress"] = {
            key: (
                _activity_text(progress[key], 2_000)
                if key == "text"
                else progress[key]
            )
            for key in ("text", "percent")
            if progress.get(key) not in (None, "")
        }
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    if display:
        compact["display"] = {
            key: _activity_text(display[key], 2_000)
            for key in ("command", "path", "url", "skill_name")
            if display.get(key) not in (None, "")
        }
    arguments = _activity_arguments(item.get("arguments"))
    if arguments:
        compact["arguments"] = arguments
    changes = item.get("changes") if isinstance(item.get("changes"), list) else []
    if changes:
        compact["changes"] = [
            {
                key: _activity_text(change[key], 2_000)
                for key in ("kind", "path")
                if change.get(key) not in (None, "")
            }
            for change in changes[:5]
            if isinstance(change, dict)
        ]
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    if queries:
        compact["action"] = {
            "queries": [_activity_text(query, 2_000) for query in queries[:5]]
        }
    return compact


def _compact_provider_activity_event_unredacted(
    value: object,
) -> dict[str, Any] | None:
    event = value if isinstance(value, dict) else {}
    method = str(event.get("method") or "")
    if method:
        if method not in PROVIDER_ACTIVITY_METHODS:
            return None
        params = event.get("params") if isinstance(event.get("params"), dict) else {}
        compact_params: dict[str, Any] = {
            key: params[key]
            for key in (
                "threadId",
                "turnId",
                "startedAtMs",
                "completedAtMs",
            )
            if params.get(key) not in (None, "")
        }
        if (method.startswith("item/") or method == SUBAGENT_PROGRESS_METHOD) and isinstance(params.get("item"), dict):
            item = params["item"]
            item_type = str(item.get("type") or "")
            if item_type == "userMessage":
                return None
            if method == "item/started" and item_type in {"agentMessage", "reasoning"}:
                return None
            compact_params["item"] = _compact_activity_item(item)
        elif method == "item/plan/delta":
            compact_params.update(itemId=params.get("itemId"), delta=_activity_text(params.get("delta")))
        elif method == "turn/plan/updated":
            compact_params["explanation"] = _activity_text(
                params.get("explanation"),
                4_000,
            )
            compact_params["plan"] = [
                {
                    "step": _activity_text(item.get("step"), 2_000),
                    "status": _activity_text(item.get("status"), 100),
                }
                for item in (params.get("plan") or [])[:100]
                if isinstance(item, dict) and str(item.get("step") or "")
            ]
        elif method in {"error", "guardianWarning"}:
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            compact_params.update({
                "willRetry": bool(params.get("willRetry")),
                "message": _activity_text(
                    params.get("message") or params.get("warning"),
                    4_000,
                ),
                "error": {
                    "message": _activity_text(error.get("message"), 4_000),
                    "additionalDetails": _activity_text(
                        error.get("additionalDetails"),
                        4_000,
                    ),
                },
            })
        elif method.startswith("turn/"):
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            compact_params["turn"] = {
                key: _activity_text(turn[key], 2_000)
                for key in ("id", "status")
                if turn.get(key) not in (None, "")
            }
            if turn.get("error"):
                error = turn["error"]
                compact_params["turn"]["error"] = (
                    {"message": _activity_text(error.get("message"), 2_000)}
                    if isinstance(error, Mapping) else _activity_text(error, 2_000)
                )
            if isinstance(params.get("usage"), dict):
                compact_params["usage"] = dict(params["usage"])
        return {"method": method, "params": compact_params}

    event_type = str(event.get("type") or "")
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    payload_type = str(payload.get("type") or "")
    if event_type == "response_item":
        if payload_type not in {
            "function_call",
            "custom_tool_call",
            "function_call_output",
        }:
            return None
        compact_payload = {
            key: _activity_text(payload[key], 2_000)
            for key in ("type", "name", "call_id")
            if payload.get(key) not in (None, "")
        }
        arguments = _activity_arguments(payload.get("arguments"))
        if arguments:
            compact_payload["arguments"] = arguments
        if payload.get("output") not in (None, ""):
            compact_payload["output"] = _activity_text(payload["output"], 2_000)
        if payload.get("is_error") is True:
            # Claude's tool results say whether they failed; Codex's carry it in the output text.
            compact_payload["is_error"] = True
        return {"type": event_type, "payload": compact_payload}
    return None


def _compact_provider_activity_event(value: object) -> dict[str, Any] | None:
    compact = _compact_provider_activity_event_unredacted(value)
    if compact is None:
        return None
    return redact_sensitive_mapping(compact)
