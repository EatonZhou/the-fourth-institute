"""Authored Lineage graph values; persistence and revision locks live in the domain."""

from __future__ import annotations

from typing import Any, Callable

from . import lineage_state_domains
from .lineage_values import _clone_json, _safe_actor, _text


def new_lineage_base(
    lineage_id: str,
    title: str,
    description: str,
    actor: str,
    *,
    schema: str,
    schema_version: int,
    now: str,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    return {
        "schema": schema,
        "schema_version": schema_version,
        "lineage_id": lineage_id,
        "lineage_membership": "formal",
        "project_id": "",
        "title": _text(title, 140) or "未命名 Lineage",
        "description": _text(description, 3000),
        "leader_resident_id": "",
        "nodes": [],
        "edges": [],
        "function_blocks": [],
        "fragments": [],
        "revision": 0,
        "layout_revision": 0,
        "runtime_revision": 0,
        "created_at": now,
        "updated_at": now,
        "graph_updated_at": now,
        "layout_updated_at": "",
        "runtime_updated_at": "",
        "created_by": actor_id,
    }


def authored_graph_patch(
    payload: dict[str, Any],
    *,
    instruction: str,
    fields: frozenset[str] | set[str],
) -> dict[str, Any]:
    patch = payload.get("patch") if isinstance(payload.get("patch"), dict) else {}
    if not patch:
        raise ValueError(f"{instruction} 需要非空 patch")
    unknown = sorted(set(patch) - fields)
    if unknown:
        raise ValueError(f"{instruction} 不接受字段：" + "、".join(unknown))
    return patch


def graph_patch_candidate(
    current: dict[str, Any], patch: dict[str, Any]
) -> dict[str, Any]:
    body = _clone_json(current)
    for field, value in patch.items():
        body[field] = _clone_json(value)
    return body


def normalized_graph_update(
    previous: dict[str, Any],
    raw_body: dict[str, Any],
    actor: str,
    *,
    membership_transition: bool,
    require_unchanged_membership: Callable[[dict[str, Any], dict[str, Any]], None],
    normalize: Callable[[dict[str, Any], dict[str, Any], str], dict[str, Any]],
) -> dict[str, Any]:
    body = _clone_json(raw_body)
    lineage_state_domains.prepare_graph_candidate(previous, body)
    if not membership_transition:
        require_unchanged_membership(previous, body)
    lineage = normalize(body, previous, actor)
    lineage_state_domains.ensure_metadata(lineage, previous)
    return lineage
