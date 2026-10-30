"""Resident nodes of a Lineage: adding registered Residents, creating and renaming them, and their titles."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from management_common.projection_receipts import CommittedProjectionError, project_committed

from .lineage_values import _clone_json, _safe_actor, _text
from .lineage_view_projection import (
    node_resident_id as _node_resident_id,
    node_status as _lineage_node_status,
)
from .lineage_program import _position
from . import identity_contract, lineage_placement, resident_protocol
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state


def commit_resident_title_projection(
    resident_id: str,
    title: str,
    actor: str,
) -> dict[str, Any]:
    """Commit one canonical Resident title into its owning HATG definition."""

    actor_id = _safe_actor(actor)
    exact_resident_id = _text(resident_id, 80).lower()
    next_title = _text(title, 120)
    if not exact_resident_id or not next_title:
        raise ValueError("Resident title projection requires identity and title")
    lineage_ids = lineage_aggregate.lineage_catalog_store().lineage_ids_for_resident(exact_resident_id)
    if len(lineage_ids) != 1:
        raise ValueError("Resident must belong to exactly one Lineage")
    lineage_id = lineage_ids[0]
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        lineage = _clone_json(previous)
        node = next(
            (
                item
                for item in lineage.get("nodes") or []
                if isinstance(item, dict)
                and _node_resident_id(item) == exact_resident_id
            ),
            None,
        )
        if node is None:
            raise KeyError("Resident is not attached to its Lineage")
        if _text(node.get("title"), 120) == next_title:
            return lineage
        node["title"] = next_title
        node["resident_title"] = next_title
        lineage_state._persist_lineage_state(
            previous,
            lineage,
            "重命名 Resident",
        )
        return lineage


def _rename_resident_lineage_node(node: dict[str, Any], title: str, actor: str) -> object:
    return resident_domain.rename_resident_identity(
        _node_resident_id(node),
        actor,
        title,
    )


def _node_from_resident(
    record: dict[str, Any],
    *,
    initial_work_state: object = None,
    x: object = 20,
    y: object = 35,
) -> dict[str, Any]:
    resident_id = _text(record.get("resident_id"), 80).lower()
    if not identity_contract.is_valid_id(resident_id, "resident"):
        raise ValueError("Resident 注册结果缺少规范的 resident_id")
    title = _text(record.get("title"), 120) or resident_id
    work_state = initial_work_state
    if work_state is None:
        work_state = resident_protocol.read_resident_states([resident_id]).get(
            resident_id,
            {},
        ).get("work_state", "idle")
    return {
        "id": resident_id,
        "title": title,
        "status": _lineage_node_status(work_state),
        "x": _position(x, 20),
        "y": _position(y, 35),
        "backend": _text(record.get("backend"), 40) or "codex",
        "created_at": _text(record.get("created_at"), 80) or lineage_state._now_iso(),
        "updated_at": _text(record.get("updated_at"), 80) or lineage_state._now_iso(),
        "last_execution": {},
    }


def _new_lineage_node_position(
    lineage: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[float, float]:
    """Resolve a spatial anchor only; creation does not imply a relationship."""
    near_id = _text(payload.get("near_resident_id"), 80).lower()
    nodes = lineage.get("nodes") or []
    if near_id:
        anchor = lineage_state._lineage_node(lineage, near_id)
    else:
        anchor = next(
            (node for node in nodes if node.get("id") == lineage.get("leader_resident_id")),
            nodes[0] if nodes else {"x": 20, "y": 35},
        )
    return lineage_placement.node_position(lineage, anchor, payload, child=payload)


def _append_registered_resident_node_locked(
    lineage: dict[str, Any],
    record: dict[str, Any],
    actor: str,
    *,
    position: tuple[float, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    resident_id = _text(record.get("resident_id"), 80).lower()
    existing = next(
        (
            item for item in lineage.get("nodes") or []
            if isinstance(item, dict) and str(item.get("id") or "") == resident_id
        ),
        None,
    )
    previous = _clone_json(lineage)
    body = _clone_json(lineage)
    node_created = not isinstance(existing, dict)
    if node_created:
        node_x, node_y = position or _new_lineage_node_position(lineage, {})
        node = _node_from_resident(
            record,
            x=node_x,
            y=node_y,
        )
        body.setdefault("nodes", []).append(node)
    else:
        return lineage, existing
    # The Resident authority is publishing this identity in the same write
    # transaction as membership. Committed readers cannot see it yet; use its
    # exact bounded projection, as initial Lineage publication already does.
    # Every other node still resolves through the canonical catalog.
    normalized = lineage_state._normalize_lineage_payload(
        body, previous, actor,
        resident_references={resident_id: dict(record)},
    )
    lineage_state._persist_lineage_state(
        previous,
        normalized,
        (
            f"新增 Resident 节点：{node['title']}"
            if node_created
            else f"同步 Resident 节点：{node['title']}"
        ),
        resident_references={resident_id: dict(record)},
    )
    created = next(
        item for item in normalized.get("nodes") or []
        if isinstance(item, dict) and str(item.get("id") or "") == resident_id
    )
    return normalized, created


def add_registered_resident_node(
    record: dict[str, Any],
    actor: str,
    *,
    node_options: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Add an already-created Resident to its managed HATG when one exists."""
    lineage_id = _text(record.get("lineage_id"), 80).lower()
    if not identity_contract.is_valid_id(lineage_id, "lineage"):
        return None
    expected_folder = lineage_state._lineage_dir(lineage_id).resolve(strict=False)
    actual_folder = Path(str(record.get("workspace_path") or "")).expanduser().resolve(strict=False)
    if actual_folder != expected_folder:
        return None
    try:
        lineage_aggregate.lineage_catalog_store().get(lineage_id)
    except KeyError:
        return None
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(lineage, actor)
        if node_options is not None:
            if str(record.get("project_id") or "") != str(lineage.get("project_id") or ""):
                raise ValueError("Lineage Project changed during Resident preparation; retry")
        if not isinstance(lineage.get("nodes"), list) or not isinstance(lineage.get("edges"), list):
            return None
        updated, _node = _append_registered_resident_node_locked(
            lineage,
            record,
            actor,
            position=_new_lineage_node_position(lineage, node_options) if node_options is not None else None,
        )
        return updated


def create_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Create a Resident Folder and HATG node under one canonical Lineage.

    ``creation_key`` makes a Portal service's frozen creation request
    idempotent: repeating it returns the Resident it already created.
    """
    creation_key = _text(payload.get("creation_key"), 160)
    if not creation_key:
        return _create_lineage_resident(lineage_id, payload, actor)
    store = lineage_aggregate.lineage_catalog_store().state_store
    # A session lock serializes the frozen request without delaying the domain
    # commit until after external Resource publication.
    lock_key = int.from_bytes(hashlib.sha256(
        f"{store.database.schema}:resident_creation:{lineage_id}:{creation_key}".encode()
    ).digest()[:8], "big", signed=True)
    with store.database.advisory_lock(lock_key):
        receipt = store.resident_creation_operations.get(lineage_id, creation_key)
        resident_id = str(receipt.get("resident_id") or "")
        if resident_id:
            updated = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            lineage_aggregate.ensure_manager(updated, actor)
            if resident_id not in {str(node.get("id") or "") for node in updated.get("nodes") or []}:
                raise ValueError("该创建请求建立的 Resident 已移走或删除，请重新发起")
            try:
                resident = resident_domain.confirm_resident_registration(resident_id, actor)
                project_committed(
                    {"projection": "lineage.resource_identity", "lineage_id": lineage_id,
                     "committed_revision": int(updated["revision"])},
                    lambda: lineage_state._publish_lineage_resource_projection(lineage_id, None, actor=actor),
                )
            except CommittedProjectionError as exc:
                exc.details["resident_creation"] = {
                    "lineage_id": lineage_id, "request_id": creation_key, "resident_id": resident_id,
                }
                raise
            return {"lineage": updated, "resident": resident,
                    "node": lineage_state._lineage_node(updated, resident_id)}
        try:
            result = _create_lineage_resident(lineage_id, payload, actor)
        except CommittedProjectionError as exc:
            committed = store.resident_creation_operations.get(lineage_id, creation_key)
            exc.details["resident_creation"] = {
                "lineage_id": lineage_id, "request_id": creation_key,
                **({"resident_id": committed["resident_id"]} if committed.get("resident_id") else {}),
            }
            raise
        return result


def _create_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    actor_id = _safe_actor(actor)
    with lineage_aggregate.lineage_scope(lineage_id):
        lineage = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(lineage, actor_id)


        lineage_project_id = _text(lineage.get("project_id"), 120)
        if not lineage_project_id and lineage_state._lineage_membership(lineage) != "personal":
            raise ValueError("Lineage 必须属于 Project")
        requested_project_id = _text(payload.get("project_id"), 120)
        if requested_project_id and requested_project_id != lineage_project_id:
            raise ValueError("Resident Project 必须与 Lineage 一致")

        _new_lineage_node_position(lineage, payload)
        registration = {
            key: value
            for key, value in payload.items()
            if key not in {
                "x",
                "y",
                "near_resident_id",
                "placement",
                "workspace_root",
                "creation_key",
            }
        }
        registration.update({
            # Identity and membership are published by registration's final
            # transaction; no temporary identity escapes this operation.
            "lineage_id": lineage_id,
            "project_id": lineage_project_id,
            "lineage_folder_path": str(lineage_state._lineage_dir(lineage_id).resolve(strict=False)),
            "lineage_title": str(lineage.get("title") or ""),
        })
    record = resident_domain.register_resident(
        registration, actor_id,
        **({"creation_key": _text(payload.get("creation_key"), 160)} if payload.get("creation_key") else {}),
        lineage_node={key: payload[key] for key in (
            "x", "y",
            "near_resident_id", "placement",
        ) if key in payload},
    )
    with lineage_aggregate.lineage_scope(lineage_id):
        updated = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        node = lineage_state._lineage_node(updated, record["resident_id"])
    return {"lineage": updated, "resident": record, "node": node}


def rename_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Validate graph concurrency before the canonical Resident rename."""

    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, _safe_actor(actor))
        node = lineage_state._lineage_node(current, payload.get("resident_id"))
        identity = resident_domain.resolve_resident_identity(_node_resident_id(node), actor)
        if identity["lineage_id"] != lineage_id:
            raise ValueError("Resident does not belong to the target Lineage")
        return resident_domain.rename_resident_title(
            _node_resident_id(node), actor, payload.get("title"),
        )
