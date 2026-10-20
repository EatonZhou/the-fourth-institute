"""Lineage admission for Portal's classification and Resident recommendation.

ApplicationTarget owns the worker identity. The worker's Lineage owns the
Event, Effect and wires, located by client_key; no second enable flag is kept.
Installing or repairing those objects is an explicit graph edit.
"""
from __future__ import annotations

from . import application_targets, isa_client
from .society_service.lineage_effect_script import deliver_message_script
from .lineage_workflows import WorkflowBindings


APPLICATIONS = {
    "notice_classifier": "Notice Board 自动分类",
    "resident_discovery": "Which Resident 推荐",
}


class ApplicationFlowUnavailable(RuntimeError):
    """No new invocation was admitted; a durable caller may wait for repair."""


def component_key(application: str, role: str) -> str:
    if application not in APPLICATIONS:
        raise ValueError("Unsupported System application flow")
    return f"system:{application}:{role}"


def component_binding(application: str, target: dict, graph: dict) -> dict:
    """Resolve and validate the actual graph, including both persisted controls."""
    components = graph.get("program", {}).get("components", [])
    selected = {}
    for role, kind in (("event", "event_source"), ("effect", "effect")):
        matches = [item for item in components if item.get("client_key") == component_key(application, role)]
        if len(matches) != 1 or matches[0].get("kind") != kind:
            raise ApplicationFlowUnavailable(f"{APPLICATIONS[application]}缺少唯一的 {role} 绑定")
        selected[role] = matches[0]
    event, effect = selected["event"], selected["effect"]
    if event.get("config", {}).get("event_type") != "manual.triggered":
        raise ApplicationFlowUnavailable("应用请求入口必须是 manual.triggered Event")
    resident_id = target["resident_id"]
    wires = graph.get("program", {}).get("wires", [])
    routes = [w for w in wires if w.get("source") == event["id"] and w.get("kind") == "route"]
    endpoints = [w for w in wires if w.get("source") == effect["id"] and w.get("kind") == "target"
                 and w.get("source_port") == "target"]
    if (len(routes) != 1 or routes[0].get("target") != effect["id"]
            or routes[0].get("source_port", "output") != "output"
            or routes[0].get("target_port", "invoke") != "invoke"
            or len(endpoints) != 1 or endpoints[0].get("target") != resident_id):
        raise ApplicationFlowUnavailable("应用 Event → Effect → 当前目标 Resident 的连接不完整")
    for role, projection in (("event", "program_event_source_runtime"), ("effect", "program_effect_runtime")):
        state = graph.get(projection, {}).get(selected[role]["id"], {})
        if state.get("paused") or state.get("state") == "paused":
            raise ApplicationFlowUnavailable(f"{APPLICATIONS[application]}的 {role} 已关闭")
    return {**target, "event_source_id": event["id"], "effect_id": effect["id"]}


def binding(application: str, *, actor: str, entry: str = "web", target: dict | None = None) -> dict:
    try:
        target = target or application_targets.resident(application)
    except application_targets.ApplicationUnconfigured as exc:
        raise ApplicationFlowUnavailable(str(exc)) from exc
    graph = isa_client.call(actor, "GetLineage", {"lineage_id": target["lineage_id"]}, entry=entry)["lineage"]
    return component_binding(application, target, graph)


def submit(application: str, *, actor: str, prompt: str, request_id: str,
           entry: str = "web", content_parts: list | None = None) -> dict:
    flow = binding(application, actor=actor, entry=entry)
    return WorkflowBindings().trigger(flow, actor=actor, prompt=prompt,
                                     request_id=request_id, content_parts=content_parts, entry=entry)


def migration_operations(application: str, resident_id: str) -> list[dict]:
    """Draft only; ApplyLineageChanges allocates identities and resolves refs."""
    title = APPLICATIONS[application]
    event_ref, effect_ref = application + "_event", application + "_effect"
    return [
        {"op": "create", "kind": "component", "ref": event_ref, "value": {
            "kind": "event_source", "title": title + " · 请求入口",
            "client_key": component_key(application, "event"),
            "config": {"event_type": "manual.triggered"},
        }},
        {"op": "create", "kind": "component", "ref": effect_ref, "value": {
            "kind": "effect", "title": title + " · 派发",
            "client_key": component_key(application, "effect"),
            "config": {"script": deliver_message_script(""), "endpoints": ["target"]},
        }},
        {"op": "create", "kind": "wire", "value": {
            "kind": "route", "client_key": component_key(application, "invoke"),
            "source": "$ref:" + event_ref, "source_port": "output",
            "target": "$ref:" + effect_ref, "target_port": "invoke",
            "config": {"include_envelope": False, "unavailable_policy": "queue", "queue_limit": 100},
        }},
        {"op": "create", "kind": "wire", "value": {
            "kind": "target", "client_key": component_key(application, "target"),
            "source": "$ref:" + effect_ref, "source_port": "target", "target": resident_id,
        }},
    ]
