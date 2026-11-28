"""Family recipe values and the roster reconciliation that generates a Family's structure."""
from __future__ import annotations

import copy
import secrets
from typing import Mapping

from management_common.actor_identity import canonical_actor_id
from .. import (
    family_familize as facts, family_recovery_effects, family_review_duty, family_review_effects,
    family_role_composition, family_tasks, personal_assistant,
)
from ..family_definition import (FAMILY_MEMBER_SIGNALS, FAMILY_ROUTER_SCRIPT,
                                FAMILY_ROUTE_RELAY_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT,
                                FAMILY_SUMMARY_ROLLUP_SCRIPT, FAMILY_ROLE_AVAILABILITY_SCRIPT,
                                FAMILY_QUEUE_NOTICE_SCRIPT, has_independent_review)
from .registry import TemplateError

# Unconfigured roles use the site's default backend, just like a new Resident.
# A saved recipe or explicit application setting can still pin each worker.
RESIDENT_ROLES = [
    {"key": "steward", "title": "steward", "from": "new"},
    {"key": "router", "title": "Router", "from": "new"},
]


def values(values: Mapping) -> dict:
    # Older persisted recipes include these two retired Family parameters.
    values = {k: v for k, v in values.items() if k not in {"auto_migrate", "auto_migrate_priority"}}
    unknown = set(values) - {"goal", "route_threshold", "review_interval_seconds"}
    if unknown:
        raise TemplateError("未知 Family 参数：" + "、".join(sorted(unknown)))
    resolved = {"goal": "", "route_threshold": 60, "review_interval_seconds": 86400, **values}
    for name in ("route_threshold", "review_interval_seconds"):
        if isinstance(resolved[name], bool) or not isinstance(resolved[name], int):
            raise TemplateError(f"{name} 必须是整数")
    if not 0 <= resolved["route_threshold"] <= 100:
        raise TemplateError("route_threshold 必须在 0–100 之间")
    if resolved["review_interval_seconds"] <= 0:
        raise TemplateError("review_interval_seconds 必须大于零")
    if not isinstance(resolved["goal"], str):
        raise TemplateError("goal 必须是文字")
    return resolved


def validate_resident_settings(value: Mapping | None) -> dict:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TemplateError("resident_settings 必须是对象")
    if set(value) - {role["key"] for role in RESIDENT_ROLES}:
        raise TemplateError("Family 起始设置只接受 steward 和 router")
    for role, chosen in value.items():
        if not isinstance(chosen, Mapping) or set(chosen) - {"provider", "account", "settings"}:
            raise TemplateError(f"{role} 起始设置只接受 provider、account、settings")
        for field in ("provider", "account"):
            if field in chosen and not isinstance(chosen[field], str):
                raise TemplateError(f"{role}.{field} 必须是文字")
        if "provider" in chosen and not chosen["provider"].strip():
            raise TemplateError(f"{role}.provider 不能为空")
        if "settings" in chosen and not isinstance(chosen["settings"], Mapping):
            raise TemplateError(f"{role}.settings 必须是对象")
    return copy.deepcopy(dict(value))


def resident_settings(*layers: Mapping | None) -> dict:
    """Built-in defaults, saved template defaults, then this application's choices."""
    resolved = {role["key"]: {key: copy.deepcopy(role[key]) for key in ("provider", "account", "settings") if key in role}
                for role in RESIDENT_ROLES}
    for layer in layers:
        for role, chosen in validate_resident_settings(layer).items():
            previous = resolved[role]
            # Accounts and model settings belong to one Provider; never carry
            # them to another when only the Provider is overridden.
            if chosen.get("provider", previous.get("provider")) != previous.get("provider"):
                previous = {}
            resolved[role] = {**previous, **chosen,
                              "settings": {**previous.get("settings", {}), **chosen.get("settings", {})}}
    return resolved


def install_review(body, steward, actor, template):
    return family_review_duty.install(body, steward, actor, template, reconcile=reconcile_in_body)


def reconcile_in_body(body: dict, block: dict, actor: str, *, installing=False) -> None:
    if block['family'].get('role_bindings'):
        family_role_composition.reconcile(body, block, actor, _reconcile_legacy_in_body, installing=installing)
    else:
        _reconcile_legacy_in_body(body, block, actor)


def _reconcile_legacy_in_body(body: dict, block: dict, actor: str) -> None:
    """Reconcile generated components and wires by local key; preserve their IDs."""
    family = block["family"]
    personal = bool(family.get("personal_ama_id"))
    members, facade, steward, router = (family[k] for k in ("members", "facade", "steward", "router"))
    elements = block.setdefault("elements", [])
    previous = {e.get("key"): e for e in elements if facts.is_generated(e)}
    generated = []
    program = body.setdefault("program", {})
    components, wires = program.setdefault("components", []), program.setdefault("wires", [])
    component_by_id = {c["id"]: c for c in components}
    wire_by_id = {w["id"]: w for w in wires}
    ids = {}

    def put(key, kind, **fields):
        element_kind = "wire" if kind in {"route", "watch", "route"} else "component"
        old = previous.get(key)
        object_id = old["id"] if old else ("wire" if element_kind == "wire" else kind) + "_" + secrets.token_hex(6)
        collection, index = (wires, wire_by_id) if element_kind == "wire" else (components, component_by_id)
        item = index.get(object_id)
        if item is None:
            item = {"id": object_id, "kind": kind}
            if element_kind == "component" and canonical_actor_id(actor).startswith("resident_"):
                item["created_by_principal_id"] = canonical_actor_id(actor)
            collection.append(item)
        fields = copy.deepcopy(fields)
        if kind == "event_source" and item.get("config"):
            # The event editor owns schedule/threshold choices. Roster sync owns watches.
            fields["config"] = {**fields.get("config", {}), **item["config"]}
        tasks = family_tasks.defaults(key, personal=personal) if kind == "route" else {}
        if tasks:
            old_profiles = item.get("config", {}).get("invoke_profiles", {})
            saved = next((p.get("arguments", {}).get("family_tasks", {}).get("value", {})
                          for p in old_profiles.values() if "family_tasks" in p.get("arguments", {})), {})
            for trigger, task in tasks.items():
                task.update(family_tasks.saved_fields(trigger, saved.get(trigger, {})))
            profile = next(iter(fields["config"]["invoke_profiles"].values()))
            profile.setdefault("arguments", {})["family_tasks"] = {"value": tasks}
        item.update(fields)
        generated.append({"id": object_id, "kind": element_kind, "key": key})
        ids[key] = object_id
        return object_id

    def notify(key, source):
        effect = put(key + "_task", "effect", title="派发 Family 工作", config={"script":
            FAMILY_QUEUE_NOTICE_SCRIPT,
            "endpoints": []})
        put(key, "route", source=source, target=effect, config={
            "invoke_profiles": {"effect": {"arguments": {"facade": {"value": facade},
                "steward": {"value": steward}}}}, "unavailable_policy": "latest"})

    for key, title, config in FAMILY_MEMBER_SIGNALS:
        # An empty Family has no business-member subject. Reconciliation deletes
        # these signals until a member joins, including graphs from older versions.
        if not members:
            continue
        source = put(key, "event_source", title=title, config=config)
        for member in members:
            put(f"watch_{key}:{member}", "watch", source=source, target=member, config={})
        notify("notify_" + key, source)
    if members or has_independent_review(family):
        done = put("member_done", "event_source", title="工作摘要覆盖检查", config={"event_type": "resident.work_finished"})
        for member in members:
            put(f"watch_member_done:{member}", "watch", source=done, target=member, config={})
        check = put("summary_check", "effect", title="检查本轮摘要", config={"script": FAMILY_SUMMARY_CHECK_SCRIPT, "endpoints": []})
        put("check_summary", "route", source=done, target=check, config={"invoke_profiles": {"effect": {
            "arguments": {"facade": {"value": facade}, "steward": {"value": steward}}}}})
    summary_timer = put("memory_timer" if personal else "summary_timer", "event_source", title="每小时检查摘要维护", config={
        "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
        "interval_seconds": 3600, "misfire_policy": "coalesce_latest", "overlap_policy": "drop"})
    rollup = put("memory_rollup" if personal else "summary_rollup", "effect", title="按需归纳工作摘要", config={"script": FAMILY_SUMMARY_ROLLUP_SCRIPT, "endpoints": []})
    put("request_memory" if personal else "request_summary", "route", source=summary_timer, target=rollup, config={"invoke_profiles": {"effect": {
        "arguments": {"facade": {"value": facade}, "steward": {"value": steward}}}}})
    if personal or not has_independent_review(family):
        review = put("periodic_review", "event_source", title="周期回顾", config={
            "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
            "interval_seconds": max(86400, family["review_interval_seconds"]) if personal else family["review_interval_seconds"],
            "misfire_policy": "coalesce_latest", "overlap_policy": "drop"})
        if personal:
            review_args = {"lineage_id": body["lineage_id"], "steward": steward, "facade": facade,
                           "activity_key": "family_activity_" + facade, "review_key": "family_review_" + facade}
            activity = put("personal_activity", "event_source", title="个人对话活动", config={"event_type": "resident.message_received"})
            for member in [facade, *members]:
                put("watch_personal_activity:" + member, "watch", source=activity, target=member, config={})
            for key, source, script in (("record_personal_activity", activity, personal_assistant.ACTIVITY_SCRIPT),
                                        ("review_personal_activity", review, personal_assistant.REVIEW_SCRIPT)):
                check = put(key, "effect", title="个人助手活动回顾", config={"script": script, "endpoints": []})
                put("invoke_" + key, "route", source=source, target=check, config={"invoke_profiles": {"effect": {
                    "arguments": {k: {"value": v} for k, v in review_args.items()}}}})
        else:
            review_args = family_review_effects.bindings(body["lineage_id"], block["id"], family)
            gated = put("notify_periodic_review_task", "effect", title="按业务活动检查组织回顾", config={
                "script": family_review_effects.NOTICE,
                "endpoints": []})
            put("notify_periodic_review", "route", source=review, target=gated, config={
                "invoke_profiles": {"effect": {"arguments": review_args}}, "unavailable_policy": "latest"})
            if members:
                activity = put("review_activity", "effect", title="记录已完成业务活动", config={
                    "script": family_review_effects.RECORD_ACTIVITY,
                    "endpoints": []})
                put("record_review_activity", "route", source=ids["member_done"], target=activity, config={
                    "invoke_profiles": {"effect": {"arguments": review_args}}})
    inbox = put("facade_inbox", "event_source", title="门面收到消息", config={"event_type": "resident.message_received"})
    put("watch_facade", "watch", source=inbox, target=facade, config={})
    effect = put("router", "effect", title="Family Router", config={"script": FAMILY_ROUTER_SCRIPT, "endpoints": []})
    relay = put("route_relay", "effect", title="转交所选成员", config={"script": FAMILY_ROUTE_RELAY_SCRIPT, "endpoints": []})
    arguments = {"members": members, "router": router, "lineage_id": body.get("lineage_id") or body.get("id"),
                 "corrections_key": "family_corrections_" + facade,
                 "family": family["title"], "goal": family["goal"],
                 "preferences_key": personal_assistant.preferences_key(facade) if personal else "",
                 }
    put("route", "route", source=inbox, target=effect, config={"parallel": True, "invoke_profiles": {"effect": {
        "arguments": {key: {"value": value} for key, value in arguments.items()}}}})
    # Each Router choice is a request of its own: one still waiting to be relayed
    # must not make the wire drop the next one.
    put("relay", "route", source=router, target=relay, config={"parallel": True, "invoke_profiles": {"effect": {"arguments": {
        "prompt": {"value": ""}, "members": {"value": members}, "steward": {"value": steward},
        "threshold": {"value": family["route_threshold"]}}}}})
    recovery = put("recovery_check", "effect", title="核验 Family 故障恢复", config={
        "script": family_recovery_effects.RECOVERY, "endpoints": []})

    def recovery_route(key, source):
        arguments = family_recovery_effects.bindings(body.get("lineage_id") or body.get("id"), block["id"], family)
        put(key, "route", source=source, target=recovery,
            config={"invoke_profiles": {"effect": {"arguments": arguments}}})

    failed = put("member_failed", "event_source", title="成员或 steward 工作失败", config={"event_type": "resident.work_failed"})
    for member in [*members, router, steward]:
        put(f"watch_member_failed:{member}", "watch", source=failed, target=member, config={})
    recovery_route("notify_member_failed", failed)
    arrived = put("recovery_arrival", "event_source", title="成员收到消息后检查接单阻塞", config={"event_type": "resident.message_received"})
    for member in [*members, router, steward]:
        put(f"watch_recovery_arrival:{member}", "watch", source=arrived, target=member, config={})
    recovery_route("check_intake_blocked", arrived)
    # A distinct source avoids suppressing a completion as caused by its own failure source.
    recovered = put("recovery_finished", "event_source", title="恢复完成后核验", config={"event_type": "resident.work_finished"})
    for member in [*members, router, steward]:
        put(f"watch_recovery_finished:{member}", "watch", source=recovered, target=member, config={})
    recovery_route("verify_recovery", recovered)
    for key, target, event_type in (("router_failed", effect, "effect.failed"), ("relay_failed", relay, "effect.failed"),
                                   ("route_failed", ids["route"], "event.delivery_failed"),
                                   ("delivery_failed", ids["relay"], "event.delivery_failed")):
        source = put(key, "event_source", title="Family 触发失败", config={"event_type": event_type})
        put("watch_" + key, "watch", source=source, target=target, config={})
        recovery_route("recover_" + key, source)
    # A fallback only: arrival, failure and intake Events detect promptly, so
    # five minutes bounds the delay without a Trigger Path every minute.
    timer = put("recovery_timer", "event_source", title="待恢复任务复查", config={
        "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
        "interval_seconds": 300, "misfire_policy": "coalesce_latest", "overlap_policy": "drop"})
    recovery_route("recheck_recovery", timer)
    availability = put("role_availability", "effect", title="保障 Router／Steward 可接单", config={
        "script": FAMILY_ROLE_AVAILABILITY_SCRIPT,
        "endpoints": []})
    intake = put("role_intake", "event_source", title="Router／Steward 接单状态变化", config={
        "event_type": "resident.state_changed", "state_domain": "intake"})
    for member in (router, steward):
        put(f"watch_role_intake:{member}", "watch", source=intake, target=member, config={})
    for key, source in (("check_role_intake", intake), ("recheck_role_availability", timer)):
        put(key, "route", source=source, target=availability, config={"invoke_profiles": {"effect": {"arguments": {
            "facade": {"value": facade}, "roles": {"value": [router, steward]}}}}})
    # Roster reconciliation owns only generated structure. Ordinary workflows
    # retain their objects, contracts and occurrence identities.
    owned = {e["id"] for e in elements if facts.is_generated(e)}
    deleted = owned - {e["id"] for e in generated}
    program["components"] = [c for c in components if c["id"] not in deleted]
    program["wires"] = [w for w in wires if w["id"] not in deleted and w["source"] not in deleted and w["target"] not in deleted]
    block["elements"] = [e for e in elements if not facts.is_generated(e)] + generated
