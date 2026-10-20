"""Resource Backup's bindings to its Portal System workflow.

Bindings use the graph's existing client_key. Event/Effect runtime and Event
definitions remain the sole authority for switches and schedules.
"""
from __future__ import annotations
from management_common.portal_paths import PORTAL_ROOT, resolve_directory
from management_common.site_config import read_site_config
import re

from collections.abc import Callable
from typing import Any

from management_portal.hatg import routing
from management_portal.hatg.contracts import InstructionContext
from management_portal.society_service.lineage_effect_script import quote


BACKUP_OPERATIONS = ("critical", "content")
VERIFICATION_OPERATIONS = ("check", "deep-check")
OPERATIONS = (*BACKUP_OPERATIONS, *VERIFICATION_OPERATIONS, "retention", "prune")
LABELS = {
    "critical": "关键状态备份", "content": "完整内容备份", "check": "仓库校验",
    "deep-check": "深度校验", "retention": "备份保留策略", "prune": "备份空间回收",
}
SCHEDULES = {
    "content": ("daily", "03:00", 0, "skip"),
    "check": ("daily", "03:05", 900, "coalesce_latest"),
    "retention": ("daily", "02:15", 900, "coalesce_latest"),
    "prune": ("weekly", "04:15", 1200, "coalesce_latest"),
    "deep-check": ("weekly", "05:30", 1200, "coalesce_latest"),
}


def _configuration() -> dict:
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict) or not isinstance(deployment.get("backup", {}), dict):
        raise ValueError("deployment.backup must be an object")
    return deployment.get("backup", {})


def lineage_id(*, required: bool = True) -> str:
    value = _configuration().get("lineage_id", "")
    if not isinstance(value, str) or (value and not re.fullmatch(r"lineage_[0-9a-f]{12}", value)):
        raise ValueError("deployment.backup.lineage_id must be a Lineage ID")
    if required and not value:
        raise ValueError("deployment.backup.lineage_id is not configured")
    return value


def script_path() -> str:
    value = _configuration().get("script_path", str(PORTAL_ROOT / "scripts" / "resource_backup_lineage.py"))
    return str(resolve_directory(value))


def restore_guide() -> dict[str, str]:
    resource_id = _configuration().get("restore_guide_resource_id", "")
    if not isinstance(resource_id, str) or (resource_id and not re.fullmatch(r"[a-z][a-z0-9_]*_[0-9a-f]{12}", resource_id)):
        raise ValueError("deployment.backup.restore_guide_resource_id must be a Resource ID")
    return {"resource_id": resource_id, "title": "Resource 系统恢复顺序",
            "url": f"/resource/knowledge?item={resource_id}" if resource_id else ""}


def _invoke(instruction: str, arguments: dict, actor: str) -> dict:
    return routing.execute_or_raise(
        {"instruction": instruction, "arguments": arguments},
        InstructionContext(caller=actor, entry="web"),
    )


def _calendar(config: dict) -> str:
    if config.get("schedule_mode") != "recurring":
        return str(config.get("at") or "")
    if config.get("recurrence_kind") == "interval":
        return f"每 {config.get('interval_seconds')} 秒"
    frequency = config.get("calendar_frequency", "daily")
    prefix = {"daily": "每天", "weekly": "每周 " + str(config.get("calendar_weekdays") or ""),
              "monthly": "每月 " + str(config.get("calendar_day") or "")}.get(frequency, str(frequency))
    return f"{prefix} {config.get('calendar_time', '')} ({config.get('timezone', 'UTC')})"


class BackupLineage:
    def __init__(self, invoke: Callable = _invoke):
        self._invoke = invoke

    def schedule(self, actor: str) -> dict[str, Any]:
        target = lineage_id()
        lineage = self._invoke("GetLineage", {"lineage_id": target}, actor)["lineage"]
        components = {item["id"]: item for item in lineage.get("program", {}).get("components", [])}
        bindings = component_bindings(lineage)
        events = lineage.get("program_event_source_runtime") or {}
        effects = lineage.get("program_effect_runtime") or {}
        wires = lineage.get("program", {}).get("wires", [])
        actions, timers = [], []
        for operation in OPERATIONS:
            binding = bindings.get(operation) or {}
            effect_id = binding.get("effect_id", "")
            effect = components.get(effect_id, {})
            effect_state = effects.get(effect_id) or {}
            for role, event_type, collection in (("manual", "manual.triggered", actions), ("timer", "time.reached", timers)):
                if role == "timer" and operation not in SCHEDULES:
                    continue
                event_id = binding.get(f"{role}_event_id", "")
                event = components.get(event_id, {})
                state = events.get(event_id) or {}
                config = event.get("config") or {}
                connected = any(w.get("kind") == "route" and w.get("source") == event_id
                                and w.get("target") == effect_id for w in wires)
                configured = (event.get("kind") == "event_source" and config.get("event_type") == event_type
                              and effect.get("kind") == "effect" and connected
                              and event_id in events and effect_id in effects)
                paused = bool(state.get("paused") or effect_state.get("paused"))
                available = bool(configured and not paused and not state.get("exhausted"))
                collection.append({
                    "name": event_id, "operation": operation, "event_source_id": event_id,
                    "effect_id": effect_id, "configured": configured, "available": available,
                    "paused": paused, "active": False,
                    "calendar": _calendar(config) if role == "timer" else "",
                    "last_trigger_at": str(state.get("last_fired_at") or ""),
                    "next_trigger_at": str(state.get("next_due_at") or state.get("next_scheduled_at") or "") if available else "",
                })
        return {"readable": True, "lineage_id": target, "actions": actions, "timers": timers}

    def trigger(self, event_source_id: str, actor: str) -> dict:
        return self._invoke("TriggerEvent", {"lineage_id": lineage_id(), "event_source_id": event_source_id}, actor)


def migration_operations() -> list[dict]:
    """Atomic graph draft. Apply once with a stable ISA idempotency key.

    Functionalize all created_ids as '资源备份'. Stable client keys make the graph
    itself the binding; no separate registry is installed. The caller
    controls the cutover and must stop old timers before applying this draft.
    """
    operations = []
    script_argument = quote(script_path())
    for operation in OPERATIONS:
        effect_ref = f"backup_{operation}_effect"
        operations.append({"op": "create", "kind": "component", "ref": effect_ref, "value": {
            "kind": "effect", "client_key": f"system:backup:{operation}:effect", "title": LABELS[operation], "config": {
                "script": f'run=$(RunScript path={script_argument} runtime=python argv:=\'["{operation}"]\' exclusive_key="resource-backup")\n'
                          'output script_run_id="${run.script_run_id}" status="${run.status}"',
                "endpoints": [],
            },
        }})
        for role in ("manual", "timer"):
            if role == "timer" and operation not in SCHEDULES:
                continue
            ref = f"backup_{operation}_{role}"
            config: dict[str, Any] = {"event_type": "manual.triggered"}
            if role == "timer":
                frequency, clock, jitter, misfire = SCHEDULES[operation]
                config = {"event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "calendar",
                          "calendar_frequency": frequency, "calendar_time": clock, "timezone": "Asia/Hong_Kong",
                          "jitter_seconds": jitter, "misfire_policy": misfire, "overlap_policy": "drop"}
                if frequency == "weekly":
                    config["calendar_weekdays"] = "sun"
            operations.append({"op": "create", "kind": "component", "ref": ref, "value": {
                "kind": "event_source", "client_key": f"system:backup:{operation}:{role}",
                "title": ("立即" if role == "manual" else "定时") + LABELS[operation], "config": config,
            }})
            operations.append({"op": "create", "kind": "wire", "value": {
                "kind": "route", "client_key": f"system:backup:{operation}:{role}:route",
                "source": f"$ref:{ref}", "source_port": "output",
                "target": f"$ref:{effect_ref}", "target_port": "invoke",
            }})
    return operations


def component_bindings(lineage: dict) -> dict:
    components = {c.get("client_key"): c["id"] for c in lineage.get("program", {}).get("components", [])}
    return {operation: {
        "effect_id": components.get(f"system:backup:{operation}:effect", ""),
        "manual_event_id": components.get(f"system:backup:{operation}:manual", ""),
        **({"timer_event_id": components.get(f"system:backup:{operation}:timer", "")} if operation in SCHEDULES else {}),
    } for operation in OPERATIONS}
