"""Home "Scheduled & Triggered" panel: a read-only projection of Lineage flows (decision_481dc4e6292c).

A row is any visible Resident or RunScript-calling Effect whose Lineage wires
a ``time.reached`` or ``file.changed`` Event Source to its Invoke port.
Residents remain ordinary Residents; Effects use their own script history.  Rows are grouped
by Lineage, and each row carries the Lineage's Resident count so the page can
show whether the Resident is one member of a team or alone in its Lineage.
Candidate flows come from an actor-authorized Lineage definition projection;
runtime details come from HATG ISA reads. The projection keeps no state of its own.
Page actions do not pass through here: the page submits
the matching instruction to the instruction address directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from typing import Any, Callable

from . import isa_client, permission_system
from .hatg.catalog import declaration
from .hatg.contracts import InstructionContext, InstructionError
from .society_service import get_society_service
from .society_service.lineage_effect_script import expand, parse
from .society_service import lineage_effect_syntax
from management_common.text_values import stripped_text


SCHEMA = "tfi.scheduled_triggered_monitor.v1"
TRIGGER_EVENTS = frozenset({"time.reached", "file.changed"})
RECENT_RUNS = 10
# Runs read per Resident to count consecutive failures and find the last success.
RUN_SCAN = 50
# Each row's buttons, each asking only its own instruction (decision_b820faf42218 §3.1).
LINEAGE_ACTIONS = ("UpdateComponent", "PauseComponent", "TriggerEvent", "CancelScriptRun")
RESIDENT_ACTIONS = ("UpdateResidentSettings", "CancelInput")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: object) -> datetime | None:
    text = stripped_text(value)
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _period_seconds(config: dict[str, Any]) -> float | None:
    """Length of one planned period of a recurring schedule, when it has one."""

    if stripped_text(config.get("schedule_mode")) != "recurring":
        return None
    interval = config.get("interval_seconds")
    if stripped_text(config.get("recurrence_kind")) == "interval" or interval:
        try:
            return float(interval) if float(interval) > 0 else None
        except (TypeError, ValueError):
            return None
    return {"daily": 86400.0, "weekly": 7 * 86400.0, "monthly": 31 * 86400.0}.get(
        stripped_text(config.get("calendar_frequency")) or "daily"
    )


def _wire_task(wire: dict[str, Any]) -> str:
    """The task text written on an Event → Resident wire."""

    config = wire.get("config") if isinstance(wire.get("config"), dict) else {}
    profiles = config.get("invoke_profiles") if isinstance(config.get("invoke_profiles"), dict) else {}
    arguments = ((profiles.get("agent") or {}).get("arguments") or {}) if isinstance(profiles.get("agent"), dict) else {}
    prompt = arguments.get("prompt")
    if isinstance(prompt, dict):
        if "value" in prompt:
            return stripped_text(prompt.get("value"))
        parts = [stripped_text(part.get("value")) for part in prompt.get("concat") or [] if isinstance(part, dict) and "value" in part]
        return "\n\n".join(part for part in parts if part)
    return stripped_text(prompt)


def _cleanup_hints(item: dict[str, Any], now: datetime) -> list[str]:
    """Timers that will not run again, or have failed for a long time, ask for their owners."""

    sources = item.get("sources") or []
    if sources and all(
        stripped_text((source.get("runtime") or {}).get("state")) == "exhausted" for source in sources
    ):
        return ["定时已达到最大触发次数，不会再运行；可以归档或删除"]
    if sources and all(
        source.get("event_type") == "time.reached"
        and stripped_text(source.get("config", {}).get("schedule_mode") or "at") == "at"
        and int((source.get("runtime") or {}).get("fire_count") or 0) > 0
        for source in sources
    ):
        return ["一次性定时已经运行过，不会再运行；可以归档或删除"]
    last_success = _parse(item.get("last_success_at"))
    if item["consecutive_failures"] >= 5 and (last_success is None or now - last_success > timedelta(days=7)):
        return ["长期失败：超过 7 天没有成功运行；请检查任务或停用定时"]
    return []


def _health(item: dict[str, Any], now: datetime) -> tuple[str, list[str]]:
    """Combine the facts of one row; no single fact stands for health."""

    problems: list[str] = list(item["read_errors"])
    warnings: list[str] = []
    if any(stripped_text(entry.get("severity") or entry.get("level")).lower() == "error"
           for entry in item["diagnostics"] if isinstance(entry, dict)):
        problems.append("Lineage 诊断存在错误")
    last = item.get("last_run") or {}
    if stripped_text(last.get("status")) in {"failed", "timed_out", "lost", "cancelled", "delivery_expired"}:
        problems.append(f"最近一次运行{'被取消' if last.get('status') == 'cancelled' else '失败'}")
    if item["consecutive_failures"] >= 2:
        problems.append(f"已连续失败 {item['consecutive_failures']} 次")
    active_sources = [source for source in item["sources"] if not source["paused"]]
    for source in item["sources"]:
        runtime = source.get("runtime") or {}
        if runtime.get("state") == "blocked":
            reason = stripped_text(runtime.get("error")) or "自动触发暂不可用，请检查 Lineage 配置"
            problems.append(f"自动触发受阻：{source['title']}：{reason}")
        delivery = runtime.get("last_delivery") or {}
        if (delivery.get("statuses") or {}).get("failed") or delivery.get("failed_count"):
            problems.append(f"触发投递失败：{source['title']}")
    effect = item.get("effect_runtime") or {}
    if effect.get("status") == "failed":
        problems.append(stripped_text(effect.get("error")) or "Effect 执行失败")
    periods = [period for period in (_period_seconds(source["config"]) for source in active_sources) if period]
    last_success = _parse(item.get("last_success_at"))
    if periods and last_success is not None and now - last_success > timedelta(seconds=2 * min(periods)):
        warnings.append("运行陈旧：距离上一次成功运行已超过两个计划周期")
    warnings.extend(_cleanup_hints(item, now))
    intake = (item.get("state") or {}).get("intake_state")
    if isinstance(intake, dict):
        intake = intake.get("status")
    if stripped_text(intake) == "closed":
        warnings.append("接单已关闭：任务只排队、不执行")
    if problems:
        return "error", problems + warnings
    if warnings:
        return "warn", warnings
    if item["sources"] and not active_sources:
        return "paused", []
    return "ok", []


class _Reader:
    """Read instructions on behalf of the viewer; failures stay on their row."""

    def __init__(self, actor: str, call: Callable[..., dict[str, Any]] | None = None) -> None:
        self.actor = actor
        self._call = call or (lambda instruction, arguments: isa_client.call(actor, instruction, arguments))

    def __call__(self, instruction: str, **arguments: Any) -> dict[str, Any]:
        return dict(self._call(instruction, arguments) or {})


def _script_paths(component: dict[str, Any]) -> list[str]:
    """Inspect the canonical Effect DSL without executing any instruction."""
    script = (component.get("config") or {}).get("script") or ""
    try:
        calls = parse(script).calls
    except ValueError:
        return []  # Compilation diagnostics explain invalid definitions.
    paths = []
    for call in calls:
        if call.instruction != "RunScript":
            continue
        value = next((arg.value for arg in call.arguments if arg.name == "path"), None)
        path = None
        if isinstance(value, lineage_effect_syntax.Word):
            path = value.text
        elif isinstance(value, lineage_effect_syntax.JsonValue) and all(isinstance(part, str) for part in value.segments):
            path = expand(value, {})
        label = path if isinstance(path, str) and path else "动态路径（运行时确定）"
        if label not in paths:
            paths.append(label)
    return paths


def _lineage_rows(lineage: dict[str, Any], read: _Reader, now: datetime) -> list[dict[str, Any]]:
    lineage_id = stripped_text(lineage.get("lineage_id"))
    program = lineage.get("program") or {}
    components = {
        stripped_text(component.get("id")): component
        for component in program.get("components") or []
        if isinstance(component, dict) and component.get("kind") == "event_source"
    }
    residents = {
        stripped_text(node.get("id")): node for node in lineage.get("nodes") or [] if isinstance(node, dict)
    }
    effects = {
        stripped_text(component.get("id")): component
        for component in program.get("components") or []
        if isinstance(component, dict) and component.get("kind") == "effect" and _script_paths(component)
    }
    subjects = {**residents, **effects}
    invoke_wires = [
        wire for wire in program.get("wires") or []
        if isinstance(wire, dict) and wire.get("kind") == "route"
        and stripped_text(wire.get("target")) in subjects and stripped_text(wire.get("target_port") or "invoke") == "invoke"
        and stripped_text(wire.get("source")) in components
    ]
    rows: dict[str, dict[str, Any]] = {}
    for wire in invoke_wires:
        component = components[stripped_text(wire.get("source"))]
        if stripped_text((component.get("config") or {}).get("event_type")) not in TRIGGER_EVENTS:
            continue
        resident_id = stripped_text(wire.get("target"))
        rows.setdefault(resident_id, {
            **({"effect_id": resident_id, "row_id": f"{lineage_id}/{resident_id}", "kind": "effect",
                "script_paths": _script_paths(effects[resident_id])}
               if resident_id in effects else {"resident_id": resident_id, "kind": "resident"}),
            "lineage_id": lineage_id,
            "lineage_title": stripped_text(lineage.get("title")),
            "lineage_resident_count": len(residents),
            "lineage_version": lineage.get("revision"),
            "title": stripped_text(subjects[resident_id].get("title")) or resident_id,
            "sources": [], "manual_sources": [], "tasks": [], "read_errors": [], "diagnostics": [],
            "recent_runs": [], "consecutive_failures": 0,
        })
    if not rows:
        return []
    for wire in invoke_wires:
        row = rows.get(stripped_text(wire.get("target")))
        if row is None:
            continue
        component = components[stripped_text(wire.get("source"))]
        config = dict(component.get("config") or {})
        entry = {
            "component_id": stripped_text(component.get("id")),
            "wire_id": stripped_text(wire.get("id")),
            "title": stripped_text(component.get("title")) or stripped_text(component.get("id")),
            "event_type": stripped_text(config.get("event_type")),
        }
        task = _wire_task(wire)
        if task:
            row["tasks"].append({"wire_id": entry["wire_id"], "component_id": entry["component_id"], "text": task})
        if entry["event_type"] == "manual.triggered":
            row["manual_sources"].append(entry)
        elif entry["event_type"] in TRIGGER_EVENTS:
            row["sources"].append({**entry, "config": config, "runtime": {}, "paused": False})
    try:
        runtime = {
            stripped_text(view.get("component_id")): view
            for view in read("GetComponentRuntime", lineage_id=lineage_id).get("components") or []
            if isinstance(view, dict)
        }
        for row in rows.values():
            for source in row["sources"]:
                source["runtime"] = dict(runtime.get(source["component_id"]) or {})
                source["paused"] = bool(source["runtime"].get("paused"))
    except Exception as exc:  # noqa: BLE001 - one failed read marks the rows it affects
        for row in rows.values():
            row["read_errors"].append(f"组件运行状态读取失败：{exc}")
    try:
        diagnostics = list(read("GetLineageDiagnostics", lineage_id=lineage_id).get("diagnostics") or [])
        for row in rows.values():
            row["diagnostics"] = diagnostics
    except Exception as exc:  # noqa: BLE001
        for row in rows.values():
            row["read_errors"].append(f"Lineage 诊断读取失败：{exc}")
    if effects:
        try:
            effect_runtime = (read("GetLineage", lineage_id=lineage_id).get("lineage") or {}).get("program_effect_runtime") or {}
            for effect_id in effects:
                if effect_id in rows:
                    rows[effect_id]["effect_runtime"] = dict(effect_runtime.get(effect_id) or {})
        except Exception as exc:  # noqa: BLE001
            for effect_id in effects:
                if effect_id in rows:
                    rows[effect_id]["read_errors"].append(f"Effect 运行状态读取失败：{exc}")
    for resident_id, row in rows.items():
        # Read this Resident's own runs: a Lineage-wide page misses a daily run
        # in a busy Lineage (the Knowledge Base Lineage has ~600 runs a day).
        try:
            is_effect = row["kind"] == "effect"
            own = [
                run for run in read(
                    "ListScriptRuns" if is_effect else "ListTriggerPaths", lineage_id=lineage_id,
                    **({"effect_id": resident_id} if is_effect else {"resident_id": resident_id}), limit=RUN_SCAN,
                ).get("runs" if is_effect else "paths") or []
                if isinstance(run, dict)
            ]
        except Exception as exc:  # noqa: BLE001
            own = []
            row["read_errors"].append(f"运行记录读取失败：{exc}")
        if not is_effect:
            own = [{**path, "started_at": path["occurred_at"]} for path in own]
        row["recent_runs"] = [
            {key: run.get(key) for key in ("trigger_path_id", "script_run_id", "path", "status", "started_at", "finished_at", "duration_seconds", "counts")}
            for run in own[:RECENT_RUNS]
        ]
        row["last_run"] = dict(row["recent_runs"][0]) if row["recent_runs"] else {}
        failures = 0
        for run in own:
            status = stripped_text(run.get("status"))
            if status in {"running", "active"}:
                continue
            if status in {"failed", "timed_out", "lost", "cancelled", "delivery_expired"}:
                failures += 1
                continue
            break
        row["consecutive_failures"] = failures
        success = next((run for run in own if stripped_text(run.get("status")) in {"completed", "settled", "succeeded"}), None)
        row["last_success_at"] = stripped_text((success or {}).get("finished_at") or (success or {}).get("started_at"))
        if row["kind"] == "effect":
            row["state"] = {"work_state": "failed" if (row.get("effect_runtime") or {}).get("status") == "failed" else row["last_run"].get("status") or "尚未运行"}
            row["sources"].sort(key=lambda source: (source["title"].casefold(), source["component_id"]))
            row["health"], row["warnings"] = _health(row, now)
            row["read_at"] = _iso(now)
            continue
        for name, instruction, arguments, pick in (
            ("resident", "GetResident", {"resident_id": resident_id}, lambda result: result.get("resident")),
            ("state", "ListResidentStates", {"resident_ids": [resident_id], "include_viewer_state": False},
             lambda result: next(iter(result.get("states") or []), None)),
            ("settings", "GetResidentSettings", {"resident_id": resident_id}, lambda result: result.get("settings")),
        ):
            try:
                row[name] = dict(pick(read(instruction, **arguments)) or {})
            except InstructionError as exc:
                row[name] = {}
                if exc.code != "instruction.forbidden":
                    row["read_errors"].append(f"{instruction} 失败：{exc}")
            except Exception as exc:  # noqa: BLE001
                row[name] = {}
                row["read_errors"].append(f"{instruction} 失败：{exc}")
        state = row.get("state") or {}
        inner = state.get("state") if isinstance(state.get("state"), dict) else state
        row["state"] = dict(inner)
        row["controls"] = dict(state.get("controls") or {})
        row["active_message_id"] = stripped_text(row["state"].get("active_message_id"))
        row["title"] = stripped_text((row.get("resident") or {}).get("title")) or row["title"]
        row["sources"].sort(key=lambda source: (source["title"].casefold(), source["component_id"]))
        row["health"], row["warnings"] = _health(row, now)
        row["read_at"] = _iso(now)
    return list(rows.values())


def get_scheduled_triggered_status(actor: str, *, read: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    now = _now()
    reader = _Reader(actor, read)
    lineage_domain = get_society_service().lineage
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    summaries = [summary for summary in lineage_domain.list_lineages(actor).get("lineages") or []
                 if stripped_text(summary.get("lineage_id"))]
    identifiers = [stripped_text(summary.get("lineage_id")) for summary in summaries]
    try:
        authorization_errors = permission_system.authorize_reads(
            declaration("GetLineage"), [{"lineage_id": value} for value in identifiers],
            InstructionContext(caller=actor, entry="web"),
        )
        allowed = [value for value, error in zip(identifiers, authorization_errors) if error is None]
        batch = lineage_domain.get_lineage_monitor_definitions(allowed, actor) if allowed else {}
    except Exception as exc:  # noqa: BLE001 - unavailable owner is not an empty success
        authorization_errors = [exc] * len(summaries)
        batch = {}
    definitions = batch.get("definitions") or {}
    definition_errors = batch.get("lineage_errors") or {}
    def collect(summary: dict[str, Any], authorization_error: Exception | None):
        lineage_id = stripped_text(summary.get("lineage_id"))
        try:
            if authorization_error is not None:
                raise authorization_error
            if lineage_id in definition_errors:
                raise RuntimeError(definition_errors[lineage_id])
            if lineage_id not in definitions:
                raise KeyError("Lineage monitor definition missing from batch read")
            definition = definitions[lineage_id]
            rows = _lineage_rows(definition, reader, now)
            on_lineage = permission_system.allows_each(actor, LINEAGE_ACTIONS, {"lineage_id": lineage_id})
            for row in rows:
                resident_id = stripped_text(row.get("resident_id"))
                on_resident = (permission_system.allows_each(actor, RESIDENT_ACTIONS, {"resident_id": resident_id})
                               if resident_id else dict.fromkeys(RESIDENT_ACTIONS, False))
                row["allows"] = {**on_lineage, **on_resident}
            return rows, None
        except Exception as exc:  # noqa: BLE001 - one unreadable Lineage affects only itself
            return [], {"lineage_id": lineage_id, "title": stripped_text(summary.get("title")), "error": str(exc)}

    # Each Lineage has independent read-only facts. Serial ISA round trips
    # across every schedule made even a healthy panel exceed the HTTP timeout.
    # Each bounded worker keeps the caller's context (its operation identity).
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="scheduled-monitor") as pool:
        pending = [pool.submit(copy_context().run, collect, summary, error)
                   for summary, error in zip(summaries, authorization_errors)]
        for future in pending:
            rows, error = future.result()
            items.extend(rows)
            if error is not None:
                errors.append(error)
    items.sort(key=lambda item: (
        (item.get("lineage_title") or item["lineage_id"]).casefold(), item["lineage_id"],
        (item.get("title") or item.get("effect_id") or item.get("resident_id", "")).casefold(),
        item.get("effect_id") or item.get("resident_id", ""),
    ))
    return {
        "schema": SCHEMA,
        "status": "error" if errors or any(item["health"] == "error" for item in items) else "ok",
        "loaded": True,
        "generated_at": _iso(now),
        "items": items,
        "lineage_errors": errors,
        "counts": {
            "agents": sum(item["kind"] == "resident" for item in items),
            "effects": sum(item["kind"] == "effect" for item in items),
            "sources": sum(len(item["sources"]) for item in items),
            "paused": sum(item["health"] == "paused" for item in items),
            "errors": len(errors) + sum(item["health"] == "error" for item in items),
        },
    }
