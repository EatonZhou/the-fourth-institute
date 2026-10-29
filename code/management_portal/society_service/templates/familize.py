"""Family template and lifecycle: roster-owned structure in one Function Block."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import math
import time
from typing import Any, Iterable, Mapping

from management_common.actor_identity import canonical_actor_id
from management_common.principal import actor_principal
from .. import family_facade, family_familize as facts, family_workflows, family_tasks
from ..family_definition import (FAMILY_MEMBER_SIGNALS, FAMILY_ROUTER_SCRIPT,
                                FAMILY_ROUTE_RELAY_SCRIPT, FAMILY_SUMMARY_CHECK_SCRIPT,
                                FAMILY_SUMMARY_ROLLUP_SCRIPT)
from .registry import ApplyContext, TemplateError, result

TEMPLATE_ID = "template_" + hashlib.sha256(b"builtin-template:family").hexdigest()[:12]
REWIRED_KINDS = frozenset({"route", "target"})
ROLE_STOP_WAIT_SECONDS = 90.0
# Replaying 10-29 Router requests in fresh sessions, gpt-5.6-luna mostly
# answered without a <packet>, which sends the request to route review;
# gpt-6.1-sol at medium effort chose like the real Routers most often, and
# as fast as at low effort.
RESIDENT_ROLES = [
    {"key": "steward", "title": "steward", "from": "new", "provider": "codex"},
    {"key": "router", "title": "Router", "from": "new", "provider": "codex",
     "settings": {"model": "gpt-6.1-sol", "reasoning_effort": "medium"}},
]


class FamilyBusy(RuntimeError):
    """A Family role has not stopped; its graph remains unchanged."""


def _domain():
    from .. import lineage_domain
    return lineage_domain


def _read(lineage_id: str, actor: str) -> dict:
    return _domain()._read_lineage(lineage_id, actor=actor)


def read_family(lineage_id: str, family_id: str, actor: str) -> tuple[dict, dict]:
    lineage = _read(lineage_id, actor)
    return lineage, _block(lineage, family_id)


def _block(body: dict, family_id: str) -> dict:
    for block in body.get("function_blocks", []):
        if block.get("family") and family_id in {block["id"], block["family"].get("resource_id")}:
            return block
    raise ValueError(f"Lineage 中没有这个 Family：{family_id}")


def _values(values: Mapping) -> dict:
    unknown = set(values) - {"goal", "route_threshold", "review_interval_seconds", "auto_migrate", "auto_migrate_priority"}
    if unknown:
        raise TemplateError("未知 Family 参数：" + "、".join(sorted(unknown)))
    resolved = {"goal": "", "route_threshold": 60, "review_interval_seconds": 86400, "auto_migrate": True, "auto_migrate_priority": [], **values}
    from management_portal.execution.account_migration_types import validate_priority
    resolved["auto_migrate_priority"] = validate_priority(resolved["auto_migrate_priority"])
    if type(resolved["auto_migrate"]) is not bool:
        raise TemplateError("auto_migrate 必须是布尔值")
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


def _validate_resident_settings(value: Mapping | None) -> dict:
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


def _resident_settings(*layers: Mapping | None) -> dict:
    """Built-in defaults, saved template defaults, then this application's choices."""
    resolved = {role["key"]: {key: copy.deepcopy(role[key]) for key in ("provider", "account", "settings") if key in role}
                for role in RESIDENT_ROLES}
    for layer in layers:
        for role, chosen in _validate_resident_settings(layer).items():
            previous = resolved[role]
            # Accounts and model settings belong to one Provider; never carry
            # them to another when only the Provider is overridden.
            if chosen.get("provider", previous.get("provider")) != previous.get("provider"):
                previous = {}
            resolved[role] = {**previous, **chosen,
                              "settings": {**previous.get("settings", {}), **chosen.get("settings", {})}}
    return resolved


def _selection(lineage: dict, ids: Iterable[str]) -> list[str]:
    blocks = {b["id"]: b for b in lineage.get("function_blocks", [])}
    selected = []
    for item in ids:
        block = blocks.get(item)
        if block:
            if not block.get("family"):
                raise TemplateError("Familize 选区只接受 Resident 或 Family")
            item = block["family"]["facade"]
        selected.append(item)
    return list(dict.fromkeys(selected))


def _member_elements(body: dict, members: list[str], *, lineage_scope: bool = False, family_id: str = "") -> dict[str, str]:
    nested = {b["family"]["facade"]: b["id"] for b in facts.family_blocks(body)}
    keys = {nested.get(rid, rid): "member:" + rid for rid in members}
    if not lineage_scope:
        return keys
    # Whole-Lineage grouping preserves existing ordinary Function Blocks, too.
    parents = {e["id"]: b["id"] for b in body.get("function_blocks", []) if b["id"] != family_id for e in b["elements"]}
    result = {}
    for eid, key in keys.items():
        root = eid
        while root in parents:
            root = parents[root]
        result[root] = key if root == eid else "member_group:" + root
    return result


def _internal_sources(body: dict, block: dict) -> set[str]:
    """Family scope determines the boundary, not ownership of generated wires."""
    if block["family"].get("lineage_scope"):
        return {item["id"] for item in body.get("nodes", [])} | {
            item["id"] for item in body.get("program", {}).get("components", [])}
    blocks = {item["id"]: item for item in body.get("function_blocks", [])}
    internal, pending = set(), [block]
    while pending:
        for element in pending.pop().get("elements", []):
            eid = element["id"]
            if eid in internal:
                continue
            internal.add(eid)
            if eid in blocks:
                pending.append(blocks[eid])
    return internal


def _rewire(body: dict, sources: set[str], target: str, block: dict, *, removing: bool = False) -> list[str]:
    """Redirect external entry points; internal workflows keep their executors.

    Removing a facade must also redirect its retained internal references before
    deleting it. Generated Family wires are deleted by the lifecycle itself.
    """
    owned = {e["id"] for e in block.get("elements", [])
             if not removing or family_workflows.is_generated(e)}
    internal = set() if removing else _internal_sources(body, block)
    changed = []
    for wire in body.get("program", {}).get("wires", []):
        if (wire["kind"] in REWIRED_KINDS and wire["target"] in sources
                and wire["id"] not in owned and wire["source"] not in internal):
            wire["target"] = target
            changed.append(wire["id"])
    return changed


def reconcile_in_body(body: dict, block: dict, actor: str) -> None:
    """Reconcile generated components and wires by local key; preserve their IDs."""
    family = block["family"]
    from .. import personal_assistant
    personal = bool(family.get("personal_ama_id"))
    members, facade, steward, router = (family[k] for k in ("members", "facade", "steward", "router"))
    elements = block.setdefault("elements", [])
    previous = {e.get("key"): e for e in elements if family_workflows.is_generated(e)}
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
        trigger = "queue" if key == "notify_member_queue" else "review"
        effect = put(key + "_task", "effect", title="派发 Family 工作", config={"script":
            'DispatchFamilyTask resident_id="${arguments.facade}" trigger="${arguments.trigger}" event:=${event}',
            "endpoints": []})
        put(key, "route", source=source, target=effect, config={
            "invoke_profiles": {"effect": {"arguments": {"facade": {"value": facade},
                "trigger": {"value": trigger}}}}, "unavailable_policy": "latest"})

    for key, title, config in FAMILY_MEMBER_SIGNALS:
        # An empty Family has no business-member subject. Reconciliation retires
        # these signals until a member joins, including graphs from older versions.
        if not members:
            continue
        source = put(key, "event_source", title=title, config=config)
        for member in members:
            put(f"watch_{key}:{member}", "watch", source=source, target=member, config={})
        notify("notify_" + key, source)
    if members:
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
        notify("notify_periodic_review", review)
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
        "script": 'CheckFamilyRecovery lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}" event="${event}"',
        "endpoints": []})

    def recovery_route(key, source):
        put(key, "route", source=source, target=recovery, config={"invoke_profiles": {"effect": {"arguments": {
            "lineage_id": {"value": body.get("lineage_id") or body.get("id")}, "family_id": {"value": block["id"]}}}}})

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
    timer = put("recovery_timer", "event_source", title="待恢复任务复查", config={
        "event_type": "time.reached", "schedule_mode": "recurring", "recurrence_kind": "interval",
        "interval_seconds": 60, "misfire_policy": "coalesce_latest", "overlap_policy": "drop"})
    recovery_route("recheck_recovery", timer)
    availability = put("role_availability", "effect", title="保障 Router／Steward 可接单", config={
        "script": 'CheckFamilyRoleAvailability lineage_id="${arguments.lineage_id}" family_id="${arguments.family_id}"',
        "endpoints": []})
    intake = put("role_intake", "event_source", title="Router／Steward 接单状态变化", config={
        "event_type": "resident.state_changed", "state_domain": "intake"})
    for member in (router, steward):
        put(f"watch_role_intake:{member}", "watch", source=intake, target=member, config={})
    for key, source in (("check_role_intake", intake), ("recheck_role_availability", timer)):
        put(key, "route", source=source, target=availability, config={"invoke_profiles": {"effect": {"arguments": {
            "lineage_id": {"value": body.get("lineage_id") or body.get("id")}, "family_id": {"value": block["id"]}}}}})
    # Roster reconciliation owns only generated structure. Ordinary workflows
    # retain their objects, contracts and occurrence identities.
    owned = {e["id"] for e in elements if family_workflows.is_generated(e)}
    retired = owned - {e["id"] for e in generated}
    program["components"] = [c for c in components if c["id"] not in retired]
    program["wires"] = [w for w in wires if w["id"] not in retired and w["source"] not in retired and w["target"] not in retired]
    block["elements"] = [e for e in elements if not family_workflows.is_generated(e)] + generated


class _Unchanged(Exception):
    """The Lineage already has the structure the running template generates."""


def _structure(lineage: Mapping) -> tuple:
    """What a structure sync can change, compared without element order.

    Storage keeps no nulls: a field normalized to null (a plain Function
    Block's template_id) is read back without it, and both mean the same.
    """
    def present(item: Mapping) -> dict:
        return {key: value for key, value in item.items() if value is not None}

    program = lineage.get("program") or {}
    return ({c["id"]: present(c) for c in program.get("components") or []},
            {w["id"]: present(w) for w in program.get("wires") or []},
            {b["id"]: {**present(b), "elements": sorted(json.dumps(e, sort_keys=True) for e in b.get("elements") or [])}
             for b in lineage.get("function_blocks") or []})


def _changed_fields(new: Mapping, old: Mapping, prefix: str = "") -> list[str]:
    """Differing fields, one level into dict fields; Family elements by their id."""
    fields: list[str] = []
    for field in sorted(set(new) | set(old)):
        left, right = new.get(field), old.get(field)
        if left == right:
            continue
        if field == "elements" and not prefix:
            by_id = lambda items: {str(item.get("id") or ""): item for item in map(json.loads, items or [])}  # noqa: E731
            left, right = by_id(left), by_id(right)
            fields += [f"elements.{key}{'' if key in left and key in right else '+' if key in left else '-'}"
                       for key in sorted(set(left) | set(right)) if left.get(key) != right.get(key)]
        elif isinstance(left, Mapping) and isinstance(right, Mapping) and not prefix:
            fields += _changed_fields(left, right, f"{field}.")
        else:
            fields.append(prefix + field)
    return fields


def _changes(after: Mapping, before: Mapping, *, limit: int = 20) -> dict:
    """Which objects a structure sync changes and in which fields, for its report."""
    changes: dict[str, dict[str, list[str]]] = {}
    for kind, new_objects, old_objects in zip(("components", "wires", "function_blocks"),
                                              _structure(after), _structure(before)):
        changed = {}
        for key in sorted(set(new_objects) | set(old_objects)):
            new, old = new_objects.get(key), old_objects.get(key)
            if new != old and len(changed) < limit:
                changed[key] = (["added" if old is None else "removed"] if new is None or old is None
                                else _changed_fields(new, old)[:limit])
        if changed:
            changes[kind] = changed
    return changes


def sync_generated_structure(lineage_id: str) -> dict:
    """Regenerate every Family's structure with the running template, as the Lineage's own identity.

    The roster and values decide generated structure; ordinarily only Familize
    and UpdateFamily regenerate it. A template change in code reaches existing
    Families here instead. The edit runs as the Lineage's unattended identity
    (its current Leader), so it reaches personal
    assistants too. A Lineage already in step commits nothing.
    """
    domain = _domain()
    state = domain._read_lineage_state(lineage_id)
    if state.get("deleted_at") or not any(block.get("family") for block in state.get("function_blocks") or []):
        return {"lineage_id": lineage_id, "status": "none"}
    actor = domain._lineage_runtime_actor(state)

    def edit(body):
        before = copy.deepcopy(body)
        families = [block for block in body.get("function_blocks") or [] if block.get("family")]
        for block in families:
            reconcile_in_body(body, block, actor)
        changes = _changes(domain._normalize_lineage_payload(copy.deepcopy(body), before, actor), before)
        if not changes:
            raise _Unchanged
        return [block["id"] for block in families], changes

    try:
        _view, (family_ids, changes) = domain.commit_lineage_edit(
            lineage_id, actor, edit, reason="模板结构同步：按当前 Family 模板重新生成", full_view=False)
    except _Unchanged:
        return {"lineage_id": lineage_id, "status": "unchanged"}
    return {"lineage_id": lineage_id, "status": "synced", "actor": actor, "family_ids": family_ids,
            "changes": changes}


def sync_all_generated_structure() -> dict:
    """Bring every Lineage's Family structure up to the running template; one failure leaves the others."""
    from management_common.operation_errors import OperationError
    from management_common.service_shutdown import shutdown_requested

    report: dict[str, Any] = {"synced": [], "unchanged": 0, "failed": {}}
    for record in _domain()._lineage_catalog_store().listing_records():
        if shutdown_requested():
            report["stopped"] = True
            break
        lineage_id = str(record.get("lineage_id") or "")
        try:
            outcome = sync_generated_structure(lineage_id)
        except (KeyError, OSError, OperationError, PermissionError, RuntimeError, ValueError) as exc:
            if isinstance(exc, KeyError) and not _lineage_exists(lineage_id):
                # Deleted after the listing was read: nothing left to sync.
                continue
            report["failed"][lineage_id] = f"{exc.__class__.__name__}: {exc}"[:300]
            continue
        if outcome["status"] == "synced":
            report["synced"].append(outcome)
        elif outcome["status"] == "unchanged":
            report["unchanged"] += 1
    return report


def _lineage_exists(lineage_id: str) -> bool:
    try:
        return not _domain()._lineage_catalog_store().get(lineage_id).get("deleted_at")
    except KeyError:
        return False


def _new_role(lineage_id: str, role: str, title: str, actor: str, near: str, settings: dict, placement: dict, *, creation_key: str = "", follow_default_backend: bool = False) -> str:
    # Personal enrollment precedes AI setup. Use the same default-role binding
    # as the AMA itself, including its pending state on a fresh installation.
    # Template-specific Codex/model defaults must not override that site role.
    chosen = {} if follow_default_backend else dict(settings.get(role) or {})
    payload = {"title": title if role == "facade" else f"{title} · {'Router' if role == 'router' else role}",
               "near_resident_id": near}
    if not follow_default_backend:
        payload["backend"] = chosen.get("provider") or "codex"
    if creation_key:
        payload["creation_key"] = creation_key
    starting = chosen.get("settings", {})
    if starting:
        payload["runtime_settings"] = starting
    if role == "facade":
        payload.update(placement)
    if chosen.get("account"):
        payload["backend_route_id"] = chosen["account"]
    created = _domain().create_lineage_resident(lineage_id, payload, actor)
    return str((created.get("resident") or {}).get("resident_id") or created["node"]["id"])


def familize(lineage_id: str, resident_ids: Iterable[str], actor: str, *, title: str = "", goal: str = "",
             parameters: Mapping | None = None, placement_request: dict | None = None,
             resident_settings: Mapping | None = None, template_id: str = TEMPLATE_ID, scope: str = "selection",
             personal_ama_id: str = "") -> dict:
    from .. import function_block_domain
    lineage = _read(lineage_id, actor)
    if scope not in {"selection", "lineage"}:
        raise TemplateError("Family scope 必须是 selection 或 lineage")
    if scope == "lineage":
        if resident_ids:
            raise TemplateError("整条 Lineage Familize 不接受 resident_ids")
        existing = facts.lineage_family(lineage)
        if existing:
            family = existing["family"]
            return result("Lineage 已有 Family", family_id=existing["id"], resource_id=family["resource_id"], function_block_ids=[existing["id"]],
                **{role + "_resident_id": family[role] for role in ("facade", "steward", "router")},
                member_resident_ids=family["members"], leader_resident_id=lineage.get("leader_resident_id", ""),
                rewired_wire_ids=[])
        resident_ids = facts.lineage_members(lineage)
    members = facts.validate_members(lineage, _selection(lineage, resident_ids))
    if personal_ama_id and (lineage.get("lineage_membership") != "personal" or members != [personal_ama_id]):
        raise TemplateError("个人助手只以当前 AMA 为初始成员")
    if not members and scope != "lineage":
        raise TemplateError("Familize 至少需要 1 个 Resident")
    values = _values({**dict(parameters or {}), **({"goal": goal} if goal else {})})
    settings = _resident_settings(resident_settings)
    # A selection is named after one of its members; a Lineage-wide Family after its Lineage.
    title = str(title or (lineage.get("title") if scope == "lineage" else "") or _member_title(members)).strip()
    element_keys = _member_elements(lineage, members, lineage_scope=scope == "lineage")
    # Validate ownership and nesting before creating any roles.
    if element_keys:
        function_block_domain.functionalize_in_body(copy.deepcopy(lineage), list(element_keys), actor, title=title)
    placement = dict(placement_request or {})
    near = str(placement.get("near_resident_id") or _central_member(lineage, members))
    roles = {role: _new_role(lineage_id, role, title, actor, near, settings, placement,
                           creation_key=f"personal-assistant:{personal_ama_id}:{role}" if personal_ama_id else "",
                           follow_default_backend=bool(personal_ama_id))
             for role in ("facade", "steward", "router")}
    leader = str(lineage.get("leader_resident_id") or "")
    from .. import identity_contract
    family = {"resource_id": identity_contract.new_id("family"), "title": title, **values, **roles, "members": members, "lineage_scope": scope == "lineage",
              "previous_leader_id": leader if leader in members else "",
              "human_familizer_id": canonical_actor_id(actor) if actor_principal(actor).kind == "user" else ""}
    if personal_ama_id:
        from .. import resident_conversation_stream
        from ..lineage_primitives import now_iso
        page = resident_conversation_stream.get_repository().agent_data.read_chat_message_page(personal_ama_id, limit=1)
        family.update(personal_ama_id=personal_ama_id, history_before=now_iso(),
                      history_before_ordinal=(page.get("ordinals") or [0])[-1] + 1)

    def edit(body):
        facts.validate_members(body, members)
        keys = {**_member_elements(body, members, lineage_scope=scope == "lineage"),
                **{rid: role + "_resident" for role, rid in roles.items()}}
        block = function_block_domain.functionalize_in_body(body, list(keys), actor, title=title,
                    template_id=template_id, element_keys=keys, x=placement.get("x"), y=placement.get("y"))
        block["family"] = copy.deepcopy(family)
        family_workflows.update(body, block, actor, collect=True)
        reconcile_in_body(body, block, actor)
        if leader in members or scope == "lineage" or personal_ama_id:
            body["leader_resident_id"] = roles["facade"]
        return {"family_id": block["id"], "rewired_wire_ids": _rewire(body, set(members), roles["facade"], block)}

    view, outcome = _domain().commit_lineage_edit(lineage_id, actor, edit, reason="Familize：创建 Family")
    block = _block(view, outcome["family_id"])
    family_tasks.dispatch(roles["facade"], "organization", actor, facts={"change": "Family 已创建，检查分工并等待工作触发。"})
    return result(f"已创建 Family「{title}」", **outcome, resource_id=block["family"]["resource_id"], function_block_ids=[block["id"]],
        facade_resident_id=roles["facade"], steward_resident_id=roles["steward"], router_resident_id=roles["router"],
        member_resident_ids=members, leader_resident_id=view.get("leader_resident_id", ""),
        created={"resident_ids": list(roles.values()),
                 "component_ids": [e["id"] for e in block["elements"] if e["kind"] == "component" and family_workflows.is_generated(e)],
                 "wire_ids": [e["id"] for e in block["elements"] if e["kind"] == "wire" and family_workflows.is_generated(e)]})


def update_family(lineage_id: str, family_id: str, actor: str, *, add_member_ids=(), remove_member_ids=(),
                  title=None, goal=None, route_threshold=None, review_interval_seconds=None, auto_migrate=None, auto_migrate_priority=None,
                  add_element_ids=(), remove_element_ids=()) -> dict:
    from .. import function_block_domain
    _, selected = read_family(lineage_id, family_id, actor)
    family_id = selected["id"]
    def edit(body):
        block = _block(body, family_id)
        family = block["family"]
        added = _selection(body, add_member_ids)
        removed = set(_selection(body, remove_member_ids))
        if removed & set(added):
            raise TemplateError("同一成员不能同时加入和移出")
        members = list(dict.fromkeys([rid for rid in family["members"] if rid not in removed] + added))
        facts.validate_members(body, members, family_id=family_id)
        values = _values({key: family.get(key, []) if value is None else value for key, value in
                          (("goal", goal), ("route_threshold", route_threshold), ("review_interval_seconds", review_interval_seconds), ("auto_migrate", auto_migrate), ("auto_migrate_priority", auto_migrate_priority))})
        if title is not None and not str(title).strip():
            raise TemplateError("Family 名称不能为空")
        grouping = {"lineage_scope": family.get("lineage_scope", False), "family_id": family_id}
        old_keys, new_keys = _member_elements(body, family["members"], **grouping), _member_elements(body, members, **grouping)
        # The Family owns its roster. The generic block editor protects this projection.
        block.pop("family")
        try:
            block = function_block_domain.update_in_body(body, family_id, actor, title=title,
                add_element_ids=[rid for rid in new_keys if rid not in old_keys],
                remove_element_ids=[rid for rid in old_keys if rid not in new_keys], element_keys=new_keys)
        finally:
            block["family"] = family
        actually_added = set(members) - set(family["members"])
        actually_removed = set(family["members"]) - set(members)
        family.update(values, members=members, title=block["title"])
        family_workflows.update(body, block, actor, add=add_element_ids, remove=remove_element_ids,
                                collect=bool(actually_added))
        ownership_only = bool(add_element_ids or remove_element_ids) and not (
            add_member_ids or remove_member_ids or any(value is not None for value in
            (title, goal, route_threshold, review_interval_seconds, auto_migrate, auto_migrate_priority)))
        if not ownership_only:
            reconcile_in_body(body, block, actor)
            if review_interval_seconds is not None:
                event_id = next(e["id"] for e in block["elements"] if e.get("key") == "periodic_review")
                event = next(c for c in body["program"]["components"] if c["id"] == event_id)
                event["config"]["interval_seconds"] = max(86400, review_interval_seconds) if family.get("personal_ama_id") else review_interval_seconds
        rewired = _rewire(body, actually_added, family["facade"], block)
        return {"family_id": family_id, "rewired_wire_ids": rewired, "added": sorted(actually_added),
                "removed": sorted(actually_removed)}
    view, outcome = _domain().commit_lineage_edit(lineage_id, actor, edit, reason="UpdateFamily：更新 Family")
    block = _block(view, family_id)
    family = block["family"]
    if title is not None:
        _domain().rename_lineage_resident(lineage_id, {"resident_id": family["facade"], "title": family["title"]}, actor)
    if outcome["added"] and canonical_actor_id(actor) != family["steward"]:
        creator_id = canonical_actor_id(actor)
        maker = str(facts._find(creator_id).get("title") or "") if creator_id.startswith("resident_") else ""
        creator = f"成员 {maker}（{creator_id}）" if maker else creator_id
        joined = "、".join(f"{facts._find(rid).get('title') or rid}（{rid}）" for rid in outcome["added"])
        family_facade.tell_steward(family["facade"], family["steward"], actor,
            f"结构信号：{creator} 加入的 {joined} 已加入 Family。请明确它的职责和协作关系；工作摘要由成员本人维护，遗漏由 Effect 挂到下一轮提醒。")
    return {"family_id": family_id, "resource_id": block["family"]["resource_id"], "function_block": block}


def unfamilize(lineage_id: str, family_id: str, actor: str, *, successor_id: str = "", rewire_to: str = "") -> dict:
    lineage, block = read_family(lineage_id, family_id, actor)
    family_id = block["id"]
    family = block["family"]
    members, facade = family["members"], family["facade"]
    roles = [family[role] for role in ("facade", "steward", "router")]
    remaining = {n["id"] for n in lineage.get("nodes", [])} - set(roles)
    successor = ""
    if lineage.get("leader_resident_id") == facade:
        successor = successor_id or family.get("previous_leader_id", "")
        if not remaining and not successor_id:
            successor = ""
        if family.get("lineage_scope") and not successor and members:
            successor = members[0]
        if remaining and successor not in remaining:
            raise TemplateError("门面是 leader：原 leader 已不在 Lineage 中，需要指定继任 leader")
    target = rewire_to or successor
    if target and target not in members:
        raise TemplateError("门面上的外部连线只能改接到 Family 成员")
    owned = {e["id"] for e in block["elements"] if family_workflows.is_generated(e)}
    external = [w for w in lineage.get("program", {}).get("wires", [])
                if w["kind"] in REWIRED_KINDS and w["target"] == facade and w["id"] not in owned]
    parents = [b for b in lineage.get("function_blocks", []) if any(e["id"] == family_id for e in b["elements"])]
    if (external or any(b.get("family") for b in parents)) and not target:
        raise TemplateError("门面上有外部连线或外层 Family 引用，需要指定改接的成员")
    _stop_roles(lineage_id, roles, actor)
    with _domain()._lineage_execution_authority(lineage_id):
        _stop_roles(lineage_id, roles, actor, wait_seconds=0)
        def edit(body):
            current = _block(body, family_id)
            rewired = _rewire(body, {facade}, target, current, removing=True) if target else []
            if successor:
                body["leader_resident_id"] = successor
            internals = {e["id"] for e in current["elements"] if family_workflows.is_generated(e)} | set(roles)
            kept = [e for e in current["elements"] if e["id"] not in internals]
            program = body["program"]
            program["components"] = [c for c in program["components"] if c["id"] not in internals]
            program["wires"] = [w for w in program["wires"] if w["id"] not in internals and w["source"] not in internals and w["target"] not in internals]
            for parent in body["function_blocks"]:
                if not any(e["id"] == family_id for e in parent["elements"]):
                    continue
                parent["elements"] = [e for e in parent["elements"] if e["id"] != family_id] + kept
                if parent.get("family"):
                    if parent["family"].get("previous_leader_id") == facade:
                        previous = family.get("previous_leader_id")
                        parent["family"]["previous_leader_id"] = previous if previous in remaining else target
                    parent["family"]["members"] = list(dict.fromkeys(rid for member in parent["family"]["members"]
                                                                      for rid in (members if member == facade else [member])))
                    reconcile_in_body(body, parent, actor)
            body["function_blocks"] = [b for b in body["function_blocks"] if b["id"] != family_id]
            return rewired
        view, rewired = _domain().commit_lineage_edit(lineage_id, actor, edit, reason="Unfamilize：解除 Family", resident_ids=roles)
    return {"family_id": family_id, "resource_id": family["resource_id"], "member_resident_ids": members, "deleted_resident_ids": roles,
            "leader_resident_id": view.get("leader_resident_id", ""), "rewired_wire_ids": rewired}


def delete_family(lineage_id: str, family_id: str, actor: str) -> dict:
    """Remove a Family and its contents together, after all work has stopped."""
    from .. import function_block_domain

    lineage, block = read_family(lineage_id, family_id, actor)
    _domain()._ensure_manager(lineage, canonical_actor_id(actor))
    family_id = block["id"]
    ids, residents = function_block_domain.deletion_scope(lineage, family_id)
    _stop_roles(lineage_id, sorted(residents), actor)
    with _domain()._lineage_execution_authority(lineage_id):
        _stop_roles(lineage_id, sorted(residents), actor, wait_seconds=0)

        def edit(body):
            if function_block_domain.deletion_scope(body, family_id) != (ids, residents):
                raise FamilyBusy("Family 内容已改变，未执行删除，请重新核对后重试。")
            function_block_domain.remove_elements_in_body(body, ids)

        view, _ = _domain().commit_lineage_edit(lineage_id, actor, edit,
            reason="DeleteFamily：删除 Family 及全部内部对象", resident_ids=sorted(residents))
    return {"family_id": family_id, "resource_id": block["family"]["resource_id"],
            "deleted_resident_ids": sorted(residents), "deleted_element_ids": sorted(ids),
            "leader_resident_id": view.get("leader_resident_id", "")}


class FamilizeKind:
    key = "familize"
    title = "Family"
    targets = ("selection", "resident")
    titles_from_target = True

    def normalize(self, template: dict) -> dict:
        body = dict(template.get("body") or {})
        settings = _validate_resident_settings(body.pop("resident_settings", None))
        return {**template, "body": {**_values(body), "resident_settings": settings}}

    def describe(self, template: dict) -> dict:
        settings = _resident_settings(template.get("body", {}).get("resident_settings"))
        return {"steps": ["选区成员保留，新建门面、steward 与 Router", "外部连线改接门面，按成员名册生成内部结构"],
                "persistent": True, "roles": [{**copy.deepcopy(role), **settings[role["key"]]} for role in RESIDENT_ROLES]}

    def editor(self) -> dict:
        return {"resident_roles": copy.deepcopy(RESIDENT_ROLES), "fields": [{"name": "goal", "type": "text", "label": "共同目标"},
                           {"name": "route_threshold", "type": "integer", "label": "路由置信度阈值", "min": 0, "max": 100},
                           {"name": "review_interval_seconds", "type": "integer", "label": "回顾间隔（秒）", "min": 1}]}

    def apply(self, ctx: ApplyContext) -> dict:
        program = _read(ctx.lineage_id, ctx.actor).get("program", {})
        non_members = {item["id"] for kind in ("components", "wires") for item in program.get(kind, [])}
        elements = [item for item in ctx.target.get("element_ids", []) if item not in non_members]
        ids = list(dict.fromkeys([*ctx.target.get("resident_ids", []), *elements]))
        body = dict(ctx.template.get("body", {}))
        settings = _resident_settings(body.pop("resident_settings", None), ctx.resident_settings)
        return familize(ctx.lineage_id, ids, ctx.actor, title=ctx.title,
                        parameters={**body, **ctx.parameters},
                        resident_settings=settings, template_id=ctx.template["template_id"],
                        placement_request={key: ctx.target[key] for key in ("x", "y") if ctx.target.get(key) is not None})


KIND = FamilizeKind()


def member_moved_out(block: Mapping[str, Any], resident_id: str, target: Mapping[str, Any], actor: str) -> None:
    """Tell the steward that a member moved to another Lineage (decision_44cf587c1721).

    The steward hears from the facade that the member left, so its records of
    members and duties follow.  The steward's rights on the member end with the
    move itself: the template grants the steward its own Lineage only.
    """

    steward_id, facade_id = facts._role(block, "steward"), facts._role(block, "facade")
    mover_id = canonical_actor_id(actor)
    if not steward_id or not facade_id or mover_id == steward_id:
        return
    mover = str(facts._find(mover_id).get("title") or "") if mover_id.startswith("resident_") else ""
    title = str(facts._find(resident_id).get("title") or resident_id)
    target_id = str(target.get("lineage_id") or "")
    family_facade.tell_steward(facade_id, steward_id, actor, (
        f"结构信号：{f'{mover}（{mover_id}）' if mover else mover_id} 把成员「{title}」（{resident_id}）"
        f"迁到 Lineage「{target.get('title') or target_id}」（{target_id}），它已离开 Family："
        "Router 不再选它。之前转交给它的请求仍由它完成，并照常经门面回复。"
        "请更新记忆中的成员与分工；它的职责需要有人接手时，先通过门面向所有者提出方案。"
    ))


def _member_title(members: list[str]) -> str:
    """A default Family title: a random member's name with "-family".

    The title also names the facade, so it uses the member's name; its ID
    would make the facade's name resolve to the member.
    """

    if not members:
        return "Family"
    member = secrets.choice(members)
    return f"{str(facts._find(member).get('title') or '').strip() or member}-family"


def _central_member(lineage: Mapping[str, Any], members: list[str]) -> str:
    """The member nearest the middle of the members, where the facade card goes.

    Folded, the facade stands for members that may be scattered over a large
    Lineage; beside the most central one it is closest to all of them.  Distances
    use the canvas proportions (positions are percentages of 1280 by 900).
    """
    positions: dict[str, tuple[float, float]] = {}
    for node in lineage.get("nodes") or []:
        if node.get("id") not in members:
            continue
        try:
            x, y = float(node.get("x")), float(node.get("y"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            positions[str(node["id"])] = (x * 12.8, y * 9.0)
    if not positions:
        return members[0] if members else ""
    center_x = sum(x for x, _y in positions.values()) / len(positions)
    center_y = sum(y for _x, y in positions.values()) / len(positions)
    return min(positions, key=lambda item: (
        math.hypot(positions[item][0] - center_x, positions[item][1] - center_y), members.index(item),
    ))



def _stop_roles(lineage_id: str, resident_ids: list[str], actor: str, *, wait_seconds: float | None = None) -> None:
    from .. import resident_lifecycle_domain

    from ..resident_protocol import clear_resident_queue

    roles = [item for item in resident_ids if item]
    # Even an idle role must close intake before the family can be removed.
    for resident_id in roles:
        _domain().quiesce_lineage_resident(lineage_id, {"target": resident_id}, actor)
    busy = [item for item in roles if resident_lifecycle_domain.resident_has_pending_work(item)]
    deadline = time.monotonic() + (ROLE_STOP_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    while busy and time.monotonic() < deadline:
        time.sleep(0.5)
        for resident_id in roles:
            clear_resident_queue(resident_id, actor)
        busy = [item for item in roles if resident_lifecycle_domain.resident_has_pending_work(item)]
    if busy:
        raise FamilyBusy("、".join(busy) + " 仍在停止当前工作，Family 结构未作改动，请稍后重试。")
