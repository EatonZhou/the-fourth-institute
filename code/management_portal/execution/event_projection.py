from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from management_common.message_identity import derived_message_identity
from management_common.file_lock import lock_stream
from management_common.async_questions import normalize_async_questions
from management_common.provider_subagent_runs import PROGRESS_METHOD as SUBAGENT_PROGRESS_METHOD
from .config import execution_state_root
from .recovery_messages import RECOVERY_INPUT_EVENT, recovery_input_message


DEFAULT_RUNTIME_DETAIL_LIMIT = 14
MAX_ACTIVITY_TRACE_BYTES = 32 * 1024 * 1024
ACTIVITY_TRACE_TTL_SECONDS = 30 * 24 * 60 * 60
TOKEN_USAGE_KEYS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)
_ACTIVITY_PRUNE_LOCK = threading.Lock()
_ACTIVITY_PRUNE_SCANS: dict[Path, Any] = {}
_ACTIVITY_WRITE_LOCK = threading.Lock()
_ACTIVITY_WRITES: OrderedDict[Path, tuple[tuple[int, int, int, int], int, set[tuple[str, str]]]] = OrderedDict()


def _nonnegative_int(value: object, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for part in content:
        if isinstance(part, str):
            chunks.append(part)
            continue
        if not isinstance(part, dict):
            continue
        text = (
            part.get("text")
            or part.get("input_text")
            or part.get("output_text")
        )
        if text:
            chunks.append(str(text))
    return "\n\n".join(chunks).strip()


def _parse_json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stored_activity_identity(
    value: object,
) -> tuple[str, str] | None:
    """Return the durable identity of one projected activity record.

    Execution event sequences make ``activity.id`` stable across waiter
    recovery. ``created_at`` keeps separately emitted, idless-source fallback
    events distinct when their local line-based ids happen to collide.
    """

    record = value if isinstance(value, dict) else {}
    if str(record.get("type") or "") != "runtime_activity":
        return None
    activity = (
        record.get("activity")
        if isinstance(record.get("activity"), dict)
        else {}
    )
    activity_id = str(activity.get("id") or "").strip()
    if not activity_id:
        return None
    if (
        activity.get("kind") == "command"
        and activity.get("provider_item_id")
        and activity.get("turn_id")
        and activity.get("provider_event_type")
    ):
        # Recovery may assign a new local sequence and observation time to
        # the same native event. Keep independent calls/turns and distinct
        # progress or terminal payloads, not a body-text deduplication.
        native_event = [activity.get(key, "") for key in (
            "execution_id", "turn_id", "provider_item_id",
            "provider_event_type", "status", "content",
        )]
        digest = hashlib.sha256(
            json.dumps(native_event, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return f"native-command:{digest}", ""
    return activity_id, str(record.get("created_at") or "")


TURN_START_TITLES = {
    "initial": "开始执行",
    "context_compaction": "压缩后继续",
    "backend_service_update": "后端服务更新后恢复运行",
    "provider_recovery": "恢复运行",
    "continuation": "继续执行",
}


def _runtime_detail(
    *,
    line_number: int,
    created_at: str,
    kind: str,
    title: str,
    content: str = "",
    status: str = "",
    phase: str = "",
    turn_id: str = "",
    turn_start_reason: str = "",
    interruption_reason: str = "",
) -> dict[str, Any]:
    detail = {
        "id": f"runtime:{line_number}:{kind}",
        "event_line": line_number,
        "created_at": created_at,
        "kind": kind,
        "title": title,
        "content": str(content or "").strip(),
        "status": status,
    }
    if phase:
        detail["phase"] = phase
    if turn_id:
        detail["turn_id"] = turn_id
    if turn_start_reason:
        detail["turn_start_reason"] = turn_start_reason
    if interruption_reason:
        detail["interruption_reason"] = interruption_reason
    return detail


def _turn_terminal_detail(
    *, line_number: int, created_at: str, turn_id: str,
    status: str, usage: str = "", error: object = None,
    interruption_reason: str = "",
) -> dict[str, Any]:
    normalized = str(status or "").strip().casefold()
    if normalized == "canceled":
        normalized = "cancelled"
    outcomes = {
        "completed": ("completed", "Completed"),
        "interrupted": ("interrupted", "已中断"),
        "failed": ("error", "执行失败"),
        "cancelled": ("cancelled", "已取消"),
    }
    if normalized not in outcomes:
        normalized = "unknown"
    kind, title = outcomes.get(normalized, ("ended", "执行轮已结束"))
    if normalized == "interrupted" and interruption_reason == "worker_handover":
        title = "Worker 换代旧 turn 中断"
    message = str(error.get("message") or "") if isinstance(error, dict) else str(error or "")
    return _runtime_detail(
        line_number=line_number, created_at=created_at, kind=kind,
        title=title, status=normalized, turn_id=turn_id,
        interruption_reason=interruption_reason if normalized == "interrupted" else "",
        content="\n".join(value for value in (usage, message) if value),
    )


def _execution_output_from_runtime_detail(
    detail: dict[str, Any],
    *,
    execution_id: str = "",
) -> dict[str, Any] | None:
    if str(detail.get("kind") or "").strip().lower() == "harness_input":
        message = recovery_input_message(
            detail.get("harness_input") or {},
            execution_id=str(detail.get("execution_id") or execution_id or ""),
        )
        return {
            "output_id": message["message_id"],
            "role": "system",
            "created_at": message["created_at"],
            "text": detail["content"],
            "message": message,
            "execution_id": str(message.get("execution_id") or ""),
        } if message else None
    if str(detail.get("kind") or "").strip().lower() != "update":
        return None
    content = str(detail.get("content") or "").strip()
    status = str(detail.get("status") or "").strip().lower()
    questions = detail.get("questions") or []
    parts = detail.get("content_parts") or []
    if (not content and not questions and not parts) or status in {"running", "in_progress", "inprogress"}:
        return None
    detail_id = str(detail.get("id") or "").strip()
    output_id = derived_message_identity(
        "resident-execution-message",
        str(detail.get("execution_id") or execution_id or "unknown"),
        detail_id or str(_nonnegative_int(detail.get("event_line"), 0)),
    )
    phase = str(detail.get("message_phase") or "").strip()
    if not phase and status in {"commentary", "final_answer"}:
        phase = status
    provider_item = str(detail.get("provider_item_id") or "")
    if not provider_item and detail_id.startswith("runtime-update:"):
        provider_item = detail_id.removeprefix("runtime-update:")
    if provider_item:
        from .message_projection import model_message_id
        output_id = model_message_id(str(detail.get("execution_id") or execution_id), provider_item, phase)

    return {
        "output_id": output_id,
        "created_at": str(detail.get("created_at") or ""),
        "phase": phase or "commentary",
        "text": content,
        **({"questions": questions} if questions else {}),
        **({"content_parts": parts} if parts else {}),
        "execution_id": str(
            detail.get("execution_id")
            or execution_id
            or ""
        ),
    }


def execution_outputs_from_events(
    events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return completed agent text outputs carried by Execution events."""

    objects = [
        (_nonnegative_int(event.get("sequence"), index), event)
        for index, event in enumerate(events, 1)
        if isinstance(event, dict)
    ]
    if not objects:
        return []
    return [
        output
        for detail in runtime_details_from_objects(
            objects,
            include_updates=True,
            limit=0,
        )
        if (
            output := _execution_output_from_runtime_detail(
                detail,
                execution_id=str(detail.get("execution_id") or ""),
            )
        )
    ]


def _clean_token_usage(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {
        key: number
        for key in TOKEN_USAGE_KEYS
        if (number := _nonnegative_int(value.get(key), 0)) > 0
    }


def _app_server_token_usage(value: object) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    data = payload.get("last") if isinstance(payload.get("last"), dict) else {}
    aliases = {
        "input_tokens": "inputTokens",
        "cached_input_tokens": "cachedInputTokens",
        "output_tokens": "outputTokens",
        "reasoning_output_tokens": "reasoningOutputTokens",
        "total_tokens": "totalTokens",
    }
    return {
        target: number
        for target, source in aliases.items()
        if (number := _nonnegative_int(data.get(source), 0)) > 0
    }


def _token_usage_brief(usage: dict[str, Any]) -> str:
    data = _clean_token_usage(usage)
    parts: list[str] = []
    if data.get("total_tokens"):
        parts.append(f"total {data['total_tokens']}")
    if data.get("input_tokens"):
        parts.append(f"in {data['input_tokens']}")
    if data.get("output_tokens"):
        parts.append(f"out {data['output_tokens']}")
    if data.get("reasoning_output_tokens"):
        parts.append(f"reason {data['reasoning_output_tokens']}")
    return " · ".join(parts)


def _tool_call_content(arguments: object) -> str:
    if isinstance(arguments, str):
        return arguments.strip()
    return json.dumps(arguments, ensure_ascii=False) if arguments is not None else ""


def _command_output_status(output: object) -> str:
    text = str(output or "")
    status = "completed"
    code_match = re.search(r"Process exited with code (-?\d+)", text)
    if code_match:
        code = code_match.group(1)
        if code != "0":
            status = "failed"
    return status


def _runtime_item_progress(item: dict[str, Any]) -> str:
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    text = str(progress.get("text") or "").strip()
    percent = progress.get("percent")
    if percent not in (None, ""):
        try:
            percent_text = f"{float(percent):g}%"
        except (TypeError, ValueError):
            percent_text = str(percent)
        return " · ".join(filter(None, (text, percent_text)))
    return text


def _runtime_tool_item_content(item: dict[str, Any]) -> str:
    progress = _runtime_item_progress(item)
    if progress:
        return progress
    description = str(item.get("description") or "").strip()
    if description:
        return description
    display = item.get("display") if isinstance(item.get("display"), dict) else {}
    for key in ("command", "path", "url", "skill_name"):
        value = str(display.get(key) or "").strip()
        if value:
            return value
    arguments = (
        item.get("arguments")
        if isinstance(item.get("arguments"), dict)
        else {}
    )
    return _tool_call_content(arguments) if arguments else ""


def _runtime_item_text(item: dict[str, Any]) -> str:
    return str(
        item.get("text")
        or _content_text(item.get("content"))
        or _content_text(item.get("summary"))
        or ""
    ).strip()


def _runtime_file_change_content(item: dict[str, Any]) -> str:
    changes = item.get("changes") if isinstance(item.get("changes"), list) else []
    summaries: list[str] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        kind_value = change.get("kind")
        kind = (
            str(kind_value.get("type") or "")
            if isinstance(kind_value, dict)
            else str(kind_value or "")
        )
        path = str(change.get("path") or "").strip()
        summary = " ".join(part for part in (kind, path) if part)
        if summary:
            summaries.append(summary)
    return "\n".join(summaries)


def _runtime_web_search_content(item: dict[str, Any]) -> str:
    query = str(item.get("query") or "").strip()
    if query:
        return query
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    queries = action.get("queries") if isinstance(action.get("queries"), list) else []
    return "\n".join(
        str(value).strip() for value in queries if str(value).strip()
    )


def _runtime_plan_content(params: dict[str, Any]) -> str:
    parts = [str(params.get("explanation") or "").strip()]
    plan = params.get("plan") if isinstance(params.get("plan"), list) else []
    for item in plan:
        if not isinstance(item, dict):
            continue
        step = str(item.get("step") or "").strip()
        status = str(item.get("status") or "").strip()
        if step:
            parts.append(f"[{status or 'pending'}] {step}")
    return "\n".join(part for part in parts if part)


def _runtime_item_status(item: dict[str, Any], default: str) -> str:
    status = str(item.get("status") or "").strip()
    normalized = re.sub(r"[^a-z]", "", status.casefold())
    if normalized in {"inprogress", "queued", "running", "working"}:
        return "running"
    if normalized in {"cancelled", "canceled", "completed", "failed", "suspended"}:
        return normalized.replace("canceled", "cancelled")
    return status or default


def _tool_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
    name = str(
        payload.get("name")
        or "tool"
    ).strip() or "tool"
    namespace = str(payload.get("namespace") or "").strip()
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 3:
            return "mcp", parts[1], "__".join(parts[2:])
    return "tool", namespace, name


def runtime_details_from_objects(
    objects: Iterable[tuple[int, dict[str, Any]]],
    *,
    include_updates: bool = False,
    execution_id: str = "",
    since_line: int = 0,
    until_line: int = 0,
    since_iso: str = "",
    limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
) -> list[dict[str, Any]]:
    """Normalize known Provider/Execution events without presentation truncation.

    Unknown event types and methods are intentionally ignored. This function
    never invents a generic activity row for an unrecognized event.
    """

    since_dt = _parse_iso(since_iso)
    call_labels: dict[str, str] = {}
    details: list[dict[str, Any]] = []
    update_positions: dict[str, int] = {}
    stored_activity_positions: dict[tuple[str, str], int] = {}
    subagent_positions: dict[str, int] = {}
    stream_text: dict[str, str] = {}

    def scoped_update_id(item_id: str, event_execution_id: str) -> str:
        if not item_id:
            return ""
        return (
            f"{event_execution_id}:{item_id}"
            if event_execution_id
            else item_id
        )

    def append_stream_detail(
        detail: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        update_id = str(payload.get("update_id") or "").strip()
        if update_id:
            detail["id"] = f"runtime-update:{update_id}"
        if (
            update_id
            and payload.get("replace_previous")
            and update_id in update_positions
        ):
            details[update_positions[update_id]] = detail
            return
        details.append(detail)
        if update_id:
            update_positions[update_id] = len(details) - 1

    def append_subagent_detail(detail: dict[str, Any], update_id: str) -> None:
        # One activity per subagent run, at the end when it changes: it is the
        # newest activity, which a page showing the last details keeps.
        detail["id"] = f"runtime-update:{update_id}"
        if update_id in subagent_positions:
            details[subagent_positions[update_id]] = None
        details.append(detail)
        subagent_positions[update_id] = len(details) - 1

    def in_scope(line_number: int, obj: dict[str, Any]) -> bool:
        if since_line and line_number <= since_line:
            return False
        if until_line and line_number >= until_line:
            return False
        if since_dt:
            created = _parse_iso(
                str(obj.get("timestamp") or obj.get("created_at") or "")
            )
            if created and created <= since_dt:
                return False
        return True

    for line_number, original in objects:
        if not isinstance(original, dict) or not in_scope(line_number, original):
            continue
        event_execution_id = str(
            original.get("execution_id")
            or execution_id
            or ""
        ).strip()
        obj = original
        created_at = str(obj.get("timestamp") or obj.get("created_at") or "")
        obj_type = str(obj.get("type") or "")
        transcript_details = (
            obj.get("details")
            if isinstance(obj.get("details"), dict)
            else {}
        )
        if obj_type == "runtime_activity":
            activity = (
                obj.get("activity")
                if isinstance(obj.get("activity"), dict)
                else {}
            )
            kind = str(activity.get("kind") or "").strip()
            title = str(activity.get("title") or "").strip()
            if not kind or not title:
                continue
            detail = _runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind=kind,
                title=title,
                content=str(activity.get("content") or ""),
                status=str(activity.get("status") or ""),
                phase=str(activity.get("phase") or ""),
            )
            for key in (
                "execution_id",
                "message_phase",
                "turn_id",
                "turn_start_reason",
                "interruption_reason",
                "provider_item_id",
            ):
                if activity.get(key) not in (None, ""):
                    detail[key] = str(activity[key])
            if isinstance(activity.get("questions"), list):
                detail["questions"] = activity["questions"]
            if isinstance(activity.get("content_parts"), list):
                detail["content_parts"] = activity["content_parts"]
            stored_id = str(activity.get("id") or "").strip()
            if kind == "subagent" and stored_id.startswith("runtime-update:"):
                if isinstance(activity.get("subagent"), dict):
                    detail["subagent"] = activity["subagent"]
                append_subagent_detail(detail, stored_id.removeprefix("runtime-update:"))
            elif stored_id.startswith("runtime-update:"):
                append_stream_detail(detail, {
                    "update_id": stored_id.removeprefix("runtime-update:"),
                    "replace_previous": True,
                })
            else:
                if stored_id:
                    detail["id"] = stored_id
                stored_identity = _stored_activity_identity(original)
                if (
                    stored_identity is not None
                    and stored_identity in stored_activity_positions
                ):
                    details[
                        stored_activity_positions[stored_identity]
                    ] = detail
                    continue
                if stored_identity is not None:
                    stored_activity_positions[stored_identity] = len(details)
                details.append(detail)
            continue
        if obj_type == RECOVERY_INPUT_EVENT:
            # The input a Harness sent to recover the turn: a message of its own.
            delivery = str(transcript_details.get("input_delivery") or "")
            detail = _runtime_detail(
                line_number=line_number,
                created_at=str(transcript_details.get("created_at") or created_at),
                kind="harness_input",
                title="Harness 让 Provider 继续这一轮" if delivery == "continued" else "Harness 把这一轮的输入交给 Provider",
                content=str(transcript_details.get("prompt") or ""),
                status="completed",
                turn_id=str(transcript_details.get("turn_id") or ""),
            )
            detail["execution_id"] = event_execution_id
            detail["harness_input"] = dict(transcript_details)
            details.append(detail)
            continue
        if obj_type == "provider_event":
            obj = transcript_details
            obj_type = str(obj.get("type") or "")
        elif obj_type in {"runtime_started", "service_started"}:
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="started",
                title=(
                    "Runtime started"
                    if obj_type == "runtime_started"
                    else "Execution service started"
                ),
                content=str(transcript_details.get("service_type") or ""),
                status="running",
            ))
            continue
        elif obj_type == "service_completed":
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="completed",
                title="Completed",
                status="completed",
            ))
            continue
        elif obj_type == "agent_harness_completed":
            raw_features = transcript_details.get("features")
            feature_reports = (
                raw_features if isinstance(raw_features, list) else []
            )
            routine_skips = {
                "below_inline_media_trigger",
                "new_backend_session",
                "no_duplicate_read_outputs",
                "no_oversized_tool_outputs",
                "stable_artifact_scope_unavailable",
                "tool_output_offload_reclaim_zero",
                "turn_recovery_in_progress",
            }
            visible_reports = [
                report
                for report in feature_reports
                if isinstance(report, dict)
                and (
                    str(report.get("status") or "") in {
                        "applied",
                        "failed_open",
                    }
                    or not (
                        str(report.get("reason") or "") in routine_skips
                        # The Adapter lacks a capability the feature needs:
                        # true for every turn of this Backend, not news.
                        or str(report.get("reason") or "").startswith("requires_")
                    )
                )
            ]
            if not visible_reports:
                continue
            summaries: list[str] = []
            for report in visible_reports:
                feature_id = str(report.get("feature_id") or "feature")
                report_status = str(report.get("status") or "skipped")
                reason = str(report.get("reason") or "")
                metrics = (
                    report.get("metrics")
                    if isinstance(report.get("metrics"), dict)
                    else {}
                )
                if feature_id == "inline_media_history" and report_status == "applied":
                    removed_count = _nonnegative_int(
                        metrics.get("inline_image_count_removed"),
                        0,
                    )
                    removed_kib = round(
                        _nonnegative_int(
                            metrics.get("inline_media_bytes_removed"),
                            0,
                        ) / 1024
                    )
                    summaries.append(
                        f"历史图片减少 {removed_count} 张 / {removed_kib} KiB"
                    )
                elif (
                    feature_id == "tool_output_offloading"
                    and report_status == "applied"
                ):
                    offloaded_count = _nonnegative_int(
                        metrics.get("tool_output_count_offloaded"),
                        0,
                    )
                    retained_kib = round(
                        _nonnegative_int(
                            metrics.get("artifact_bytes_retained"),
                            0,
                        ) / 1024
                    )
                    reclaimed_tokens = _nonnegative_int(
                        metrics.get("estimated_tokens_reclaimed"),
                        0,
                    )
                    summaries.append(
                        "大型工具输出转存 "
                        f"{offloaded_count} 条 / {retained_kib} KiB / "
                        f"约 {reclaimed_tokens} tokens"
                    )
                elif (
                    feature_id == "hash_aware_read_dedup"
                    and report_status == "applied"
                ):
                    removed_count = _nonnegative_int(
                        metrics.get("duplicate_output_count_removed"),
                        0,
                    )
                    reclaimed_tokens = _nonnegative_int(
                        metrics.get("estimated_tokens_reclaimed"),
                        0,
                    )
                    summaries.append(
                        "重复只读结果去重 "
                        f"{removed_count} 条 / 约 {reclaimed_tokens} tokens"
                    )
                else:
                    summaries.append(
                        f"{feature_id}: {report_status}"
                        + (f" ({reason})" if reason else "")
                    )
            context_savings = (
                transcript_details.get("context_savings")
                if isinstance(
                    transcript_details.get("context_savings"),
                    dict,
                )
                else {}
            )
            net_avoided = _nonnegative_int(
                context_savings.get(
                    "net_visible_tokens_estimated_avoided"
                ),
                0,
            )
            if net_avoided:
                summaries.append(
                    f"本轮可见上下文估算净减少 {net_avoided} tokens"
                )
            provider_wall_ms = transcript_details.get("provider_wall_ms")
            if isinstance(provider_wall_ms, (int, float)):
                summaries.append(
                    f"Provider {float(provider_wall_ms) / 1000:.1f}s"
                )
            provider_usage = (
                transcript_details.get("provider_usage")
                if isinstance(transcript_details.get("provider_usage"), dict)
                else {}
            )
            usage = _app_server_token_usage(provider_usage)
            if usage.get("input_tokens"):
                summaries.append(
                    "input "
                    f"{usage['input_tokens']}"
                    + (
                        f"（cached {usage['cached_input_tokens']}）"
                        if usage.get("cached_input_tokens")
                        else ""
                    )
                )
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="optimization",
                title="Agent Harness",
                content=" · ".join(summaries),
                # This card reports Harness feature application, not the
                # provider turn outcome. Provider failures have their own
                # runtime activity and must not make successfully attached
                # Harness controls appear to have failed.
                status=(
                    "failed"
                    if any(
                        isinstance(report, dict)
                        and str(report.get("status") or "").casefold()
                        == "failed"
                        for report in feature_reports
                    )
                    else "completed"
                ),
            ))
            continue

        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        payload_type = str(payload.get("type") or "")
        method = str(obj.get("method") or "")
        params = obj.get("params") if isinstance(obj.get("params"), dict) else {}

        native_item = params.get("item") or {}
        if method == SUBAGENT_PROGRESS_METHOD:
            # A subagent the model waits on: its running card is live activity.
            run = native_item.get("run") if isinstance(native_item.get("run"), dict) else {}
            if str(run.get("tool_use_id") or ""):
                detail = _runtime_detail(
                    line_number=line_number, created_at=created_at, kind="subagent", title="子 agent",
                    content=str(run.get("description") or ""), status="running",
                )
                detail.update({"execution_id": event_execution_id, "subagent": run})
                append_subagent_detail(detail, scoped_update_id(f"subagent:{run['tool_use_id']}", event_execution_id))
            continue
        if native_item.get("type") in {"userInputRequest", "userInputResponse", "turnArtifacts", "subagentRun"}:
            from management_common.provider_user_input import content_part

            if native_item.get("type") == "turnArtifacts":
                from management_common.provider_turn_artifacts import content_part
            if native_item.get("type") == "subagentRun":
                from management_common.provider_subagent_runs import content_part

            part = content_part(native_item, event_execution_id)
            detail = _runtime_detail(
                line_number=line_number, created_at=created_at, kind="update",
                title="本轮记录" if part.get("type") == "native_turn" else "子 agent" if part.get("type") == "subagent_run" else (
                    ("等待你的审批" if part.get("tool_name") == "request_approval" else "等待你的回答")
                    if part.get("is_blocking") else "提问"
                ),
                content="", status="completed",
            )
            detail.update({"id": native_item["id"], "execution_id": event_execution_id,
                           "content_parts": [part], "turn_id": part["turn_id"]})
            details.append(detail)
            if part["type"] == "subagent_run":
                # The run's card is a message now; its running card ends.
                ended = _runtime_detail(
                    line_number=line_number, created_at=created_at, kind="subagent", title="子 agent",
                    content=str(part.get("description") or ""),
                    status="failed" if part.get("status") == "failed" else "completed",
                )
                ended["execution_id"] = event_execution_id
                append_subagent_detail(ended, scoped_update_id(f"subagent:{part['tool_use_id']}", event_execution_id))
            continue

        if method in {"item/agentMessage/delta", "item/plan/delta"}:
            if include_updates:
                item_id = str(params.get("itemId") or "").strip()
                delta = str(params.get("delta") or "")
                if item_id and delta:
                    update_id = scoped_update_id(item_id, event_execution_id)
                    content = stream_text.get(update_id, "") + delta
                    stream_text[update_id] = content
                    detail = _runtime_detail(
                        line_number=line_number,
                        created_at=created_at,
                        kind="update",
                        title="Update",
                        content=stream_text[update_id],
                        status="running",
                    )
                    detail["execution_id"] = event_execution_id
                    detail["turn_id"] = str(params.get("turnId") or "")
                    detail["provider_item_id"] = item_id
                    append_stream_detail(detail, {
                        "update_id": update_id,
                        "replace_previous": True,
                    })
            continue
        if method == "turn/plan/updated":
            turn_id = str(params.get("turnId") or "current")
            update_id = scoped_update_id(
                f"plan:{turn_id}",
                event_execution_id,
            )
            append_stream_detail(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="plan",
                title="Plan updated",
                content=_runtime_plan_content(params),
                status="running",
            ), {
                "update_id": update_id,
                "replace_previous": True,
            })
            continue
        if method == "error":
            error = params.get("error") if isinstance(params.get("error"), dict) else {}
            will_retry = bool(params.get("willRetry"))
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="error",
                title="Reconnecting" if will_retry else "Provider error",
                content=str(
                    error.get("message")
                    or error.get("additionalDetails")
                    or "Provider error"
                ),
                status="running" if will_retry else "failed",
                phase="reconnecting" if will_retry else "",
            ))
            continue
        if method == "guardianWarning":
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="warning",
                title="Safety warning",
                content=str(params.get("message") or params.get("warning") or ""),
                status="failed",
            ))
            continue
        if method in {
            "item/autoApprovalReview/started",
            "item/autoApprovalReview/completed",
        }:
            completed = method.endswith("/completed")
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="approval",
                title=(
                    "Approval review completed"
                    if completed
                    else "Approval review"
                ),
                status="completed" if completed else "running",
            ))
            continue
        if method in {"item/started", "item/progress", "item/completed"}:
            obj_type = {
                "item/started": "item.started",
                "item/progress": "item.progress",
                "item/completed": "item.completed",
            }[method]
            obj = {**obj, "item": params.get("item") or {}}
            millis = _nonnegative_int(
                params.get("startedAtMs")
                or params.get("updatedAtMs")
                or params.get("completedAtMs"),
                0,
            )
            if millis:
                created_at = (
                    datetime.fromtimestamp(millis / 1000, timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
        elif method == "turn/started":
            obj_type = "turn.started"
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            obj = {**obj, "turn_id": str(turn.get("id") or "")}
        elif method == "turn/completed":
            obj_type = "turn.completed"
            turn = params.get("turn") if isinstance(params.get("turn"), dict) else {}
            obj = {
                **obj,
                "turn_id": str(turn.get("id") or params.get("turnId") or ""),
                "status": str(turn.get("status") or "unknown"),
                "error": turn.get("error"),
                "usage": params.get("usage")
                if isinstance(params.get("usage"), dict)
                else {},
            }
        elif method == "thread/attached":
            obj_type = "thread.started"
            obj = {**obj, "thread_id": str(params.get("threadId") or "")}
        elif method == "thread/tokenUsage/updated":
            usage = _app_server_token_usage(params.get("tokenUsage"))
            if usage:
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="tokens",
                    title="Token update",
                    content=_token_usage_brief(usage),
                ))
            continue
        elif method:
            continue

        if obj_type == "response_item":
            if payload_type in {"function_call", "custom_tool_call"}:
                kind, namespace, name = _tool_identity(payload)
                call_id = str(payload.get("call_id") or "")
                call_labels[call_id] = name
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="tool_call",
                    title=(
                        f"MCP: {namespace + '.' if namespace else ''}{name}"
                        if kind == "mcp"
                        else f"Tool: {namespace + '.' if namespace else ''}{name}"
                    ),
                    content=_tool_call_content(payload.get("arguments")),
                    status="running",
                ))
            elif payload_type == "function_call_output":
                call_id = str(payload.get("call_id") or "")
                name = call_labels.get(call_id, "tool")
                content = str(payload.get("output") or "")
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="tool_output",
                    title=f"Tool finished: {name}",
                    content=content,
                    status=_command_output_status(content),
                ))
            continue

        if obj_type in {"turn.started", "thread.started"}:
            reason = str(obj.get("turn_start_reason") or "initial")
            details.append(_runtime_detail(
                line_number=line_number,
                created_at=created_at,
                kind="started",
                title=(
                    TURN_START_TITLES.get(reason, "继续执行")
                    if obj_type == "turn.started"
                    else "Thread attached"
                ),
                content=str(obj.get("thread_id") or ""),
                status="running",
                turn_id=str(obj.get("turn_id") or ""),
                turn_start_reason=reason if obj_type == "turn.started" else "",
            ))
        elif obj_type == "turn.completed":
            usage = _token_usage_brief(
                obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            )
            details.append(_turn_terminal_detail(
                line_number=line_number,
                created_at=created_at,
                turn_id=str(obj.get("turn_id") or ""),
                status=str(obj.get("status", "completed")),
                interruption_reason=str(obj.get("interruption_reason") or ""),
                usage=usage,
                error=obj.get("error"),
            ))
        elif obj_type in {"item.started", "item.progress", "item.completed"}:
            item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
            item_type = str(item.get("type") or "")
            default_status = (
                "completed" if obj_type == "item.completed" else "running"
            )
            if item_type == "agentMessage":
                if not include_updates:
                    continue
                item_id = str(item.get("id") or "").strip()
                update_id = scoped_update_id(item_id, event_execution_id)
                content = (
                    _runtime_item_text(item)
                    or stream_text.get(update_id, "")
                )
                detail = _runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="update",
                    title="Update",
                    content=content,
                    status=_runtime_item_status(item, default_status),
                )
                detail["execution_id"] = event_execution_id
                detail["message_phase"] = str(item.get("phase") or "")
                detail["turn_id"] = str(
                    params.get("turnId")
                    or item.get("turnId")
                    or item.get("turn_id")
                    or ""
                )
                detail["provider_item_id"] = item_id
                questions = normalize_async_questions(item.get("questions"))
                if questions:
                    detail["questions"] = questions
                append_stream_detail(detail, {
                    "update_id": update_id,
                    "replace_previous": bool(item_id),
                })
            elif item_type == "reasoning":
                item_id = str(item.get("id") or "").strip()
                append_stream_detail(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="reasoning",
                    title="Thinking",
                    content=_runtime_item_text(item),
                    status=_runtime_item_status(item, default_status),
                ), {
                    "update_id": item_id,
                    "replace_previous": bool(item_id),
                })
            elif item_type in {"command_execution", "commandExecution"}:
                command = str(item.get("command") or "")
                exit_code = (
                    item.get("exit_code")
                    if "exit_code" in item
                    else item.get("exitCode")
                )
                status = _runtime_item_status(item, default_status)
                title = {
                    "item.started": "Command started",
                    "item.progress": "Command progress",
                    "item.completed": "Command finished",
                }[obj_type]
                if obj_type == "item.completed" and exit_code not in (None, ""):
                    status = "completed" if str(exit_code) == "0" else "failed"
                content = _runtime_item_progress(item) or (
                    f"$ {command}" if command else ""
                )
                if obj_type == "item.completed" and exit_code not in (None, ""):
                    content = f"{content}\nexit {exit_code}".strip()
                detail = _runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="command",
                    title=title,
                    content=content,
                    status=status,
                    turn_id=str(
                        params.get("turnId")
                        or item.get("turnId")
                        or item.get("turn_id")
                        or ""
                    ),
                )
                detail["execution_id"] = event_execution_id
                detail["provider_item_id"] = str(item.get("id") or "").strip()
                detail["provider_event_type"] = obj_type
                native_identity = _stored_activity_identity({
                    "type": "runtime_activity", "activity": detail,
                })
                if native_identity is not None:
                    detail["id"] = native_identity[0]
                details.append(detail)
            elif item_type in {
                "tool_call",
                "toolCall",
                "mcp_tool_call",
                "mcpToolCall",
                "dynamicToolCall",
            }:
                name = str(
                    item.get("name") or item.get("tool") or "Tool"
                ).strip() or "Tool"
                server = str(item.get("server") or "").strip()
                if server:
                    name = f"{server}.{name}"
                title = {
                    "item.started": f"Tool: {name}",
                    "item.progress": f"Tool progress: {name}",
                    "item.completed": f"Tool finished: {name}",
                }[obj_type]
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind=(
                        "tool_output"
                        if obj_type == "item.completed"
                        else "tool_call"
                    ),
                    title=title,
                    content=_runtime_tool_item_content(item),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "fileChange":
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="file_change",
                    title=(
                        "File changes completed"
                        if obj_type == "item.completed"
                        else "Changing files"
                    ),
                    content=_runtime_file_change_content(item),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "webSearch":
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="web_search",
                    title=(
                        "Web search completed"
                        if obj_type == "item.completed"
                        else "Searching the web"
                    ),
                    content=_runtime_web_search_content(item),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "imageView":
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="image",
                    title=(
                        "Image inspected"
                        if obj_type == "item.completed"
                        else "Inspecting image"
                    ),
                    content=str(item.get("path") or ""),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "imageGeneration":
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="image",
                    title=(
                        "Image generated"
                        if obj_type == "item.completed"
                        else "Generating image"
                    ),
                    content=str(item.get("revisedPrompt") or ""),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "contextCompaction":
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="compact",
                    title=(
                        "Context compacted"
                        if obj_type == "item.completed"
                        else "Compacting context"
                    ),
                    status=_runtime_item_status(item, default_status),
                ))
            elif item_type == "agentExecution":
                name = str(item.get("name") or "Subagent").strip() or "Subagent"
                title = {
                    "item.started": f"Subagent: {name}",
                    "item.progress": f"Subagent update: {name}",
                    "item.completed": f"Subagent finished: {name}",
                }[obj_type]
                details.append(_runtime_detail(
                    line_number=line_number,
                    created_at=created_at,
                    kind="subagent",
                    title=title,
                    content=str(
                        item.get("description")
                        or item.get("command")
                        or item.get("output")
                        or ""
                    ).strip(),
                    status=_runtime_item_status(item, default_status),
                ))

    shown = [detail for detail in details if detail is not None]
    return last_runtime_details(shown, limit) if limit and limit > 0 else shown


def _running_subagent(detail: dict[str, Any]) -> bool:
    return detail.get("kind") == "subagent" and detail.get("status") == "running"


def last_runtime_details(details: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """The last ``limit`` details, after every subagent still running: its card stays in view."""

    return [detail for detail in details[:-limit] if _running_subagent(detail)] + details[-limit:]






def execution_projection_from_path(
    path: Path,
    *,
    runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    message_limit: int = 0,
) -> dict[str, Any]:
    """Project one Execution transcript into independent message/activity lanes.

    Completed Agent messages remain available for the whole Execution and are
    never evicted by tool or reasoning activity. Runtime details retain only
    the bounded activity window plus any currently streaming Agent message.
    """

    execution_id = (
        path.stem
        if re.fullmatch(r"execution-[0-9a-f]{24}", path.stem)
        else ""
    )
    try:
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            details = runtime_details_from_objects(
                (
                    (line_number, obj)
                    for line_number, line in enumerate(stream, 1)
                    if isinstance(
                        obj := _parse_json_object(line),
                        dict,
                    )
                    and obj
                ),
                include_updates=True,
                execution_id=execution_id,
                limit=0,
            )
    except OSError:
        return {
            "execution_id": execution_id,
            "outputs": [],
            "runtime_details": [],
        }

    outputs = [
        output
        for detail in details
        if (
            output := _execution_output_from_runtime_detail(
                detail,
                execution_id=execution_id,
            )
        )
    ]
    if message_limit and message_limit > 0:
        outputs = outputs[-message_limit:]
    runtime_details = [
        detail
        for detail in details
        if _execution_output_from_runtime_detail(
            detail,
            execution_id=execution_id,
        ) is None
    ]
    if runtime_limit and runtime_limit > 0:
        runtime_details = last_runtime_details(runtime_details, runtime_limit)
    return {
        "execution_id": execution_id,
        "outputs": outputs,
        "runtime_details": runtime_details,
    }


class ExecutionActivityProjection:
    """Incrementally consume the normalized, append-only Execution activity log.

    One completion waiter owns this reader. A read is acknowledged only after
    its messages have been committed; failed batches remain available for retry.
    Recreating a waiter safely replays the durable file through Message identity
    validation, independently of the Scheduler's transient event window.
    """

    def __init__(
        self,
        execution_id: str,
        *,
        runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    ) -> None:
        self.execution_id = execution_id
        self.path = execution_activity_path(execution_id)
        self.runtime_limit = max(1, runtime_limit)
        self._identity: tuple[int, int] | None = None
        self._offset = 0
        self._line_number = 0
        self._runtime_details: list[dict[str, Any]] = []
        self._pending: dict[str, Any] | None = None
        self._pending_position: tuple[tuple[int, int], int, int] | None = None

    def read(self) -> dict[str, Any]:
        if self._pending is not None:
            return self._pending
        empty = {
            "execution_id": self.execution_id,
            "outputs": [],
            "runtime_details": self._runtime_details,
        }
        if self.path is None:
            return empty
        try:
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = (stat.st_dev, stat.st_ino)
                reset = identity != self._identity or stat.st_size < self._offset
                offset = 0 if reset else self._offset
                line_number = 0 if reset else self._line_number
                stream.seek(offset)
                objects = []
                while stream.tell() < stat.st_size:
                    line = stream.readline(stat.st_size - stream.tell())
                    if not line.endswith(b"\n"):
                        # An append in progress is retried from its start.
                        break
                    offset = stream.tell()
                    line_number += 1
                    obj = _parse_json_object(line.decode("utf-8", errors="replace"))
                    if obj:
                        objects.append((line_number, obj))
        except OSError:
            return empty

        details = runtime_details_from_objects(
            objects, include_updates=True, execution_id=self.execution_id, limit=0,
        ) if objects else []
        outputs = []
        runtime = {} if reset else {
            self._runtime_identity(detail): detail for detail in self._runtime_details
        }
        for detail in details:
            output = _execution_output_from_runtime_detail(
                detail, execution_id=self.execution_id,
            )
            if output is not None:
                outputs.append(output)
            else:
                key = self._runtime_identity(detail)
                if detail.get("kind") == "subagent":
                    # A subagent's latest activity is the newest one.
                    runtime.pop(key, None)
                runtime[key] = detail
        self._pending_position = (identity, offset, line_number)
        self._pending = {
            "execution_id": self.execution_id,
            "outputs": outputs,
            "runtime_details": last_runtime_details(list(runtime.values()), self.runtime_limit),
        }
        return self._pending

    @staticmethod
    def _runtime_identity(detail: dict[str, Any]) -> tuple[str, str]:
        identity = str(detail.get("id") or "")
        return identity, (
            "" if identity.startswith("runtime-update:")
            else str(detail.get("created_at") or "")
        )

    def acknowledge(self) -> None:
        if self._pending is None or self._pending_position is None:
            return
        self._identity, self._offset, self._line_number = self._pending_position
        self._runtime_details = self._pending["runtime_details"]
        self._pending = None
        self._pending_position = None


def execution_projection_from_execution(
    execution_id: str,
    *,
    runtime_limit: int = DEFAULT_RUNTIME_DETAIL_LIMIT,
    message_limit: int = 0,
) -> dict[str, Any]:
    path = execution_activity_path(execution_id)
    if path is None:
        return {
            "execution_id": str(execution_id or ""),
            "outputs": [],
            "runtime_details": [],
        }
    return execution_projection_from_path(
        path,
        runtime_limit=runtime_limit,
        message_limit=message_limit,
    )


def append_execution_activity(
    path: Path,
    events: Iterable[dict[str, Any]],
) -> int:
    """Persist semantic activity with retention independent of page display limits."""

    objects = [
        (
            _nonnegative_int(event.get("sequence"), index),
            event,
        )
        for index, event in enumerate(events, 1)
        if isinstance(event, dict)
    ]
    if not objects:
        return 0
    # Scheduler sequences are stable across repeated delivery. Keep each
    # record's source sequence so a partial batch can be resumed safely.
    details = [
        (number if "sequence" in event else 0, detail)
        for number, event in objects
        for detail in runtime_details_from_objects([(number, event)], include_updates=True, limit=0)
    ]
    records: list[tuple[int, tuple[str, str] | None, bytes]] = []
    pending_identities: set[tuple[str, str]] = set()
    for sequence, detail in details:
        kind = str(detail.get("kind") or "").strip()
        status = str(detail.get("status") or "").strip().lower()
        if kind == "tokens":
            continue
        if kind in {"update", "reasoning"} and status in {
            "",
            "running",
            "in_progress",
            "inprogress",
        }:
            continue
        activity = {
            key: detail[key]
            for key in (
                "id",
                "kind",
                "title",
                "content",
                "status",
                "phase",
                "execution_id",
                "message_phase",
                "turn_id",
                "turn_start_reason",
                "interruption_reason",
                "provider_item_id",
                "provider_event_type",
                "questions",
                "content_parts",
                "subagent",
            )
            if detail.get(key) not in (None, "")
        }
        record = {
            "type": "runtime_activity",
            "sequence": sequence,
            "created_at": str(detail.get("created_at") or ""),
            "activity": activity,
        }
        identity = _stored_activity_identity(record)
        if identity is not None and identity in pending_identities:
            continue
        if identity is not None:
            pending_identities.add(identity)
        records.append((
            sequence, identity,
            (
                json.dumps(
                    record,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
        ))
    if not records:
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("a+b") as stream:
        with lock_stream(stream):
            stat = os.fstat(stream.fileno())
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size)
            with _ACTIVITY_WRITE_LOCK:
                cached = _ACTIVITY_WRITES.pop(path, None)
            last_sequence = 0
            existing_identities: set[tuple[str, str]] = set()
            if cached is not None and cached[0] == signature:
                _, last_sequence, existing_identities = cached
            else:
                stream.seek(0)
                for line in stream:
                    try:
                        existing = json.loads(line)
                    except (TypeError, ValueError, UnicodeDecodeError):
                        continue
                    if isinstance(existing, dict):
                        last_sequence = max(last_sequence, _nonnegative_int(existing.get("sequence")))
                    identity = _stored_activity_identity(existing)
                    if identity is not None:
                        existing_identities.add(identity)
            stream.seek(0, 2)
            size = stream.tell()
            batch_start_sequence = last_sequence
            for sequence, identity, record in records:
                if sequence and sequence <= batch_start_sequence:
                    continue
                if identity is not None and identity in existing_identities:
                    continue
                if size + len(record) > MAX_ACTIVITY_TRACE_BYTES:
                    break
                stream.write(record)
                size += len(record)
                written += 1
                last_sequence = max(last_sequence, sequence)
                if identity is not None:
                    existing_identities.add(identity)
            stream.flush()
            stat = os.fstat(stream.fileno())
            with _ACTIVITY_WRITE_LOCK:
                _ACTIVITY_WRITES[path] = (
                    (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size),
                    last_sequence, existing_identities,
                )
                while len(_ACTIVITY_WRITES) > 128:
                    _ACTIVITY_WRITES.popitem(last=False)
    return written


def execution_activity_path(
    execution_id: str,
) -> Path | None:
    clean_execution_id = str(execution_id or "").strip()
    if not re.fullmatch(r"execution-[0-9a-f]{24}", clean_execution_id):
        return None
    return (
        execution_state_root()
        / "activity"
        / f"{clean_execution_id}.jsonl"
    )


def prune_execution_activity(
    *,
    older_than_epoch: float,
    time_budget_seconds: float = 2.0,
) -> int:
    probe = execution_activity_path("execution-" + "0" * 24)
    if probe is None:
        raise RuntimeError("execution activity root is unavailable")
    root = probe.parent
    removed = 0
    deadline = time.monotonic() + max(0.01, float(time_budget_seconds))
    with _ACTIVITY_PRUNE_LOCK:
        entries = _ACTIVITY_PRUNE_SCANS.get(root)
        if entries is None:
            try:
                entries = os.scandir(root)
            except FileNotFoundError:
                return 0
            _ACTIVITY_PRUNE_SCANS[root] = entries
        while time.monotonic() < deadline:
            try:
                entry = next(entries)
            except StopIteration:
                entries.close()
                _ACTIVITY_PRUNE_SCANS.pop(root, None)
                break
            if not re.fullmatch(r"execution-[0-9a-f]{24}\.jsonl", entry.name):
                continue
            try:
                if entry.is_file(follow_symlinks=False) and entry.stat(follow_symlinks=False).st_mtime < older_than_epoch:
                    os.unlink(entry.path)
                    removed += 1
            except FileNotFoundError:
                continue
    return removed
