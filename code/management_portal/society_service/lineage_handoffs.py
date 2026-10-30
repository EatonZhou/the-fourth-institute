"""Handoffs between Lineage nodes: creating, reading, changing, deleting and executing them."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .errors import LineageConflictError
from .lineage_values import _bool, _clone_json, _item_id, _safe_actor, _text
from . import lineage_handoff_preparation, lineage_handoff_execution, lineage_node_workspaces
from .lineage_workspace_files import MAX_WORKSPACE_COPY_BYTES, MAX_WORKSPACE_COPY_FILES
from . import lineage_aggregate
from . import lineage_state, lineage_resident_workspaces


def _handoff_allowed_target_roots(lineage_id: str) -> list[Path]:
    roots = [
        lineage_state._lineage_dir(lineage_id) / "handoff-workspaces",
        lineage_handoff_preparation.EXPLORATIONS_ROOT,
    ]
    configured = os.getenv("PORTAL_HANDOFF_TARGET_ROOTS", "")
    roots.extend(Path(value.strip()).expanduser() for value in configured.split(os.pathsep) if value.strip())
    return [root.resolve(strict=False) for root in roots]


def _handoff_workspace_root(
    lineage_id: str,
    resident_id: str,
    actor: str,
    selector: dict[str, Any],
) -> dict[str, Any]:
    workspace = lineage_resident_workspaces.get_node_workspaces(lineage_id, resident_id, actor)
    workspace_id = _text(selector.get("workspace_id"), 80).lower()
    raw_path = lineage_node_workspaces.root_path(selector.get("path"))
    roots = workspace["workspaces"]
    if not (workspace_id or raw_path):
        # An item that names no source hands over the Workspace the node works in.
        matches = [root for root in roots if root.get("is_default_cwd")]
    else:
        matches = [
            root for root in roots
            if (
                (workspace_id and str(root.get("workspace_id") or "").lower() == workspace_id)
                or (raw_path and lineage_node_workspaces.root_path(root.get("path")) == raw_path)
            )
        ]
    if not matches:
        raise ValueError("来源必须是该节点已经绑定的 Workspace；Resident Folder、Lineage Folder 和知识库不能直接交接")
    root = matches[0]
    if not Path(str(root.get("path") or "")).is_dir():
        raise FileNotFoundError("来源 Workspace 不存在")
    return root


def _handoff_edge(lineage: dict[str, Any], handoff_id: str) -> dict[str, Any]:
    edge = next(
        (
            item for item in lineage.get("edges") or []
            if isinstance(item, dict)
            and item.get("type") == "handoff"
            and str(item.get("id") or "") == handoff_id
        ),
        None,
    )
    if not edge:
        raise KeyError("Handoff not found")
    return edge


def _save_handoff_lineage(
    previous: dict[str, Any],
    lineage: dict[str, Any],
    actor: str,
    reason: str,
    *,
    runtime: bool = False,
) -> dict[str, Any]:
    if runtime:
        # Handoff execution mutates only runtime fields on an already
        # normalized edge. Revalidate the changed Handoff edge itself, not
        # unrelated Graph authoring objects such as Program components or
        # reusable Function Blocks.
        saved = _clone_json(lineage)
        resident_ids = {
            str(node.get("id") or "")
            for node in saved.get("nodes") or []
            if isinstance(node, dict) and str(node.get("id") or "")
        }
        previous_edges = {
            str(edge.get("id") or ""): edge
            for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("id") or "")
        }
        saved["edges"] = [
            lineage_state._normalize_edge(
                edge,
                resident_ids,
                previous_edges.get(str(edge.get("id") or "")),
            )
            if (
                isinstance(edge, dict)
                and str(edge.get("type") or "") == "handoff"
                and edge
                != previous_edges.get(str(edge.get("id") or ""))
            )
            else edge
            for edge in saved.get("edges") or []
        ]
        lineage_state._persist_lineage_runtime_state(previous, saved, reason)
        return saved
    normalized = lineage_state._normalize_lineage_payload(lineage, previous, actor)
    lineage_state._persist_lineage_state(previous, normalized, reason)
    return normalized


def create_handoff(
    lineage_id: str, payload: dict[str, Any], actor: str, *,
    full_view: bool = True, idempotent: bool = False,
) -> dict[str, Any]:
    """Create one canonical Handoff edge and optionally prepare its Workspace items."""
    actor_id = _safe_actor(actor)
    source_resident_id = _text(payload.get("source_resident_id"), 80).lower()
    target_resident_id = _text(payload.get("target_resident_id"), 80).lower()
    if not source_resident_id or not target_resident_id:
        raise ValueError("Handoff 需要来源节点和目标节点")
    if source_resident_id == target_resident_id:
        raise ValueError("Handoff 不能交接给来源节点自身")
    handoff_id = _item_id(payload.get("handoff_id"), "handoff")
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        nodes = {
            str(node.get("id") or ""): node
            for node in previous.get("nodes") or []
            if isinstance(node, dict)
        }
        if source_resident_id not in nodes or target_resident_id not in nodes:
            raise KeyError("Handoff 的来源或目标 Lineage 节点不存在")
        if any(str(edge.get("id") or "") == handoff_id for edge in previous.get("edges") or []):
            raise ValueError("Handoff id 已存在")
        existing = next((edge for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("type") or "") == "handoff"
            and str(edge.get("source") or "") == source_resident_id
            and str(edge.get("target") or "") == target_resident_id), None)
        if existing is not None:
            if not idempotent:
                raise ValueError("相同来源和接收方只能有一个 Workspace Handoff")
            result = {"handoff": existing, "created": False}
            if full_view:
                result["lineage"] = lineage_state._verified_lineage_view(previous, actor_id)
            return result

    items = lineage_handoff_preparation.draft_items(
        lineage_id=lineage_id,
        handoff_id=handoff_id,
        source_id=source_resident_id,
        target_title=nodes[target_resident_id].get("title"),
        payload=payload,
        actor=actor_id,
        lineage_folder=lineage_state._lineage_dir(lineage_id),
        source_workspace=_handoff_workspace_root,
        root_path=lineage_node_workspaces.root_path,
    )

    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        current_nodes = {str(node.get("id") or "") for node in previous.get("nodes") or []}
        if source_resident_id not in current_nodes or target_resident_id not in current_nodes:
            raise LineageConflictError("Handoff 的来源或目标 Lineage 节点已变化")
        if any(str(edge.get("id") or "") == handoff_id for edge in previous.get("edges") or []):
            raise LineageConflictError("Handoff id 已存在")
        existing = next((edge for edge in previous.get("edges") or []
            if isinstance(edge, dict) and str(edge.get("type") or "") == "handoff"
            and str(edge.get("source") or "") == source_resident_id
            and str(edge.get("target") or "") == target_resident_id), None)
        if existing is not None:
            if not idempotent:
                raise LineageConflictError("相同来源和接收方只能有一个 Workspace Handoff")
            result = {"handoff": existing, "created": False}
            if full_view:
                result["lineage"] = lineage_state._verified_lineage_view(previous, actor_id)
            return result
        edge = {
            "id": handoff_id,
            "source": source_resident_id,
            "target": target_resident_id,
            "type": "handoff",
            "label": _text(payload.get("label"), 120) or "工作区交接",
            "explanation": _text(payload.get("explanation"), 8000),
            "items": items,
            "status": "draft",
            "error": "",
            "created_at": lineage_state._now_iso(),
        }
        lineage = _clone_json(previous)
        lineage.setdefault("edges", []).append(edge)
        saved = _save_handoff_lineage(previous, lineage, actor_id, "创建工作区 Handoff")

    if _bool(payload.get("execute_now"), False):
        return execute_handoff(
            lineage_id,
            handoff_id,
            actor_id,
        )
    result = {"handoff": _handoff_edge(saved, handoff_id), "created": True}
    if full_view:
        result["lineage"] = lineage_state._verified_lineage_view(saved, actor_id)
    return result


def get_handoff(lineage_id: str, handoff_id: str, actor: str, *, full_view: bool = True) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
    lineage_state._ensure_visible(lineage, actor_id)
    result = {"handoff": _handoff_edge(lineage, handoff_id)}
    if full_view:
        result["lineage"] = lineage_state._verified_lineage_view(lineage, actor_id)
    return result


def update_handoff(
    lineage_id: str, handoff_id: str, changes: dict[str, Any], actor: str, *, full_view: bool = True,
) -> dict[str, Any]:
    """Change a handoff's items, explanation or label; prepared results stay as they are."""

    actor_id = _safe_actor(actor)
    unknown = sorted(set(changes) - {"items", "explanation", "label"})
    if unknown:
        raise ValueError("交接关系只能修改 items、explanation、label：" + "、".join(unknown))
    items = None
    if "items" in changes:
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
        edge = _handoff_edge(current, handoff_id)
        target = next(
            (node for node in current.get("nodes") or [] if node.get("id") == edge.get("target")), {},
        )
        items = lineage_handoff_preparation.draft_items(
            lineage_id=lineage_id,
            handoff_id=handoff_id,
            source_id=str(edge.get("source") or ""),
            target_title=target.get("title"),
            payload={"items": changes.get("items")},
            actor=actor_id,
            lineage_folder=lineage_state._lineage_dir(lineage_id),
            source_workspace=_handoff_workspace_root,
            root_path=lineage_node_workspaces.root_path,
        )
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        lineage = _clone_json(previous)
        edge = _handoff_edge(lineage, handoff_id)
        if items is not None and edge.get("status") in {"ready", "preparing"}:
            # Prepared results belong to the items they were prepared from;
            # changing items afterwards would show them as ready unprepared.
            raise ValueError(
                "交接已准备或正在准备，不能再修改交接项；需要交接其他内容时删除这条交接后重新建立"
            )
        if items is not None:
            edge["items"] = items
        if "explanation" in changes:
            edge["explanation"] = _text(changes.get("explanation"), 8000)
        if "label" in changes:
            edge["label"] = _text(changes.get("label"), 120) or "工作区交接"
        edge["updated_at"] = lineage_state._now_iso()
        saved = _save_handoff_lineage(previous, lineage, actor_id, "修改工作区 Handoff")
    result = {"handoff": _handoff_edge(saved, handoff_id)}
    if full_view:
        result["lineage"] = lineage_state._verified_lineage_view(saved, actor_id)
    return result


def delete_handoff(lineage_id: str, handoff_id: str, actor: str, *, full_view: bool = True) -> dict[str, Any]:
    """Remove a handoff relation; prepared files and bindings keep their own owners."""

    actor_id = _safe_actor(actor)
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        _handoff_edge(previous, handoff_id)
        lineage = _clone_json(previous)
        lineage["edges"] = [
            edge for edge in lineage.get("edges") or [] if str(edge.get("id") or "") != handoff_id
        ]
        saved = _save_handoff_lineage(previous, lineage, actor_id, "删除工作区 Handoff")
    result = {"handoff_id": handoff_id}
    if full_view:
        result["lineage"] = lineage_state._verified_lineage_view(saved, actor_id)
    return result


def _set_handoff_failure(
    lineage_id: str,
    handoff_id: str,
    actor: str,
    error: str,
    prepared: list[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    lineage_handoff_preparation.cleanup_items(prepared)
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage = _clone_json(previous)
        handoff = _handoff_edge(lineage, handoff_id)
        lineage_handoff_execution.fail_attempt(handoff, error, lineage_state._now_iso())
        saved = _save_handoff_lineage(
            previous,
            lineage,
            actor,
            "Handoff 准备失败",
            runtime=True,
        )
    return {
        "lineage": lineage_state._verified_lineage_view(saved, actor),
        "handoff": _handoff_edge(saved, handoff_id),
        "created": False,
    }


def execute_handoff(
    lineage_id: str,
    handoff_id: str,
    actor: str,
) -> dict[str, Any]:
    """Prepare and bind Workspace items without dispatching a Resident task."""
    actor_id = _safe_actor(actor)
    target_handoff_id = _text(handoff_id, 80).lower()
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        handoff = _handoff_edge(previous, target_handoff_id)
        if handoff.get("status") == "preparing":
            raise LineageConflictError("Handoff 正在准备，请等待当前 attempt 完成")
        if handoff.get("status") == "ready":
            return {
                "lineage": lineage_state._verified_lineage_view(previous, actor_id),
                "handoff": handoff,
                "created": False,
            }
        else:
            lineage = _clone_json(previous)
            preparing = _handoff_edge(lineage, target_handoff_id)
            lineage_handoff_execution.begin_attempt(preparing, lineage_state._now_iso())
            previous = _save_handoff_lineage(
                previous,
                lineage,
                actor_id,
                "开始准备 Handoff",
                runtime=True,
            )
            handoff = _handoff_edge(previous, target_handoff_id)

    if handoff.get("status") != "ready":
        prepared: list[tuple[dict[str, Any], dict[str, Any]]] = []
        try:
            lineage_handoff_preparation.prepare_items(
                lineage_id, handoff, actor_id,
                lineage_handoff_preparation.HandoffWorkspaceAccess(
                    workspaces=lineage_resident_workspaces.get_node_workspaces,
                    bind=lineage_resident_workspaces.add_node_reference_workspace,
                    allowed_target_roots=_handoff_allowed_target_roots,
                    root_path=lineage_node_workspaces.root_path,
                    now=lineage_state._now_iso,
                    max_files=MAX_WORKSPACE_COPY_FILES,
                    max_bytes=MAX_WORKSPACE_COPY_BYTES,
                ),
                prepared,
            )
        except Exception as exc:
            _set_handoff_failure(
                lineage_id,
                target_handoff_id,
                actor_id,
                _text(exc, 4000) or "Handoff 准备失败",
                prepared,
            )
            raise

        with lineage_aggregate.lineage_scope(lineage_id):
            previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            lineage = _clone_json(previous)
            ready = _handoff_edge(lineage, target_handoff_id)
            lineage_handoff_execution.complete_attempt(ready, prepared, lineage_state._now_iso())
            saved = _save_handoff_lineage(
                previous,
                lineage,
                actor_id,
                "Handoff Workspace 已就绪",
                runtime=True,
            )
            handoff = _handoff_edge(saved, target_handoff_id)
    else:
        saved = previous

    return {
        "lineage": lineage_state._verified_lineage_view(saved, actor_id),
        "handoff": handoff,
        "created": False,
    }
