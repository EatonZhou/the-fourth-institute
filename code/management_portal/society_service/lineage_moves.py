"""Moving Residents and subgraphs between Lineages, and reading those moves."""

from __future__ import annotations

import logging
import re
import secrets
from contextlib import nullcontext
from typing import Any, Callable

from . import lineage_graph_authoring
from . import lineage_subgraph
from .lineage_program_contracts import _program_execution_contract_change
from . import identity_contract, lineage_placement, lineage_program, lineage_program_runtime, resident_catalog
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state, lineage_resident_nodes, lineage_lifecycle, lineage_graph_edits


LOGGER = logging.getLogger(__name__)


def _resident_move_patch(
    source: dict[str, Any], resident_id: str, *, allow_empty_source: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _resident_move_plan(source, resident_id, allow_empty_source=allow_empty_source)
    return plan["node"], plan["patch"]


def _moving_family_member(source: dict[str, Any], resident_id: str) -> dict[str, Any] | None:
    """The Family a moving Resident leaves; refuses its facade, Router, steward and personal AMA.

    A member leaves its Family as it moves (decision_44cf587c1721): the Family
    keeps its roles and other members, and its generated structure follows
    the roster as when a member is deleted.
    """

    from .family_familize import role_of, family_blocks, member_ids

    names = {"facade": "门面", "router": "Router", "steward": "steward"}
    for block in family_blocks(source):
        title = str(block["family"].get("title") or block.get("title") or block["id"])
        role = next((role for role in names if role_of(block, role) == resident_id), "")
        if role:
            raise ValueError(
                f"这个 Resident 是 Family「{title}」的{names[role]}，随 Family 一起存在，不能单独迁移；"
                "可以迁移整个 Family，或先解除 Family。没有做任何修改"
            )
        if resident_id not in member_ids(block):
            continue
        if block["family"].get("personal_ama_id") == resident_id:
            raise ValueError("这是个人助手的默认 AMA，不能迁出私人 Lineage。没有做任何修改")
        return block
    return None


def _resident_move_plan(
    source: dict[str, Any], resident_id: str, *, allow_empty_source: bool = False,
) -> dict[str, Any]:
    """Check one Resident's move and describe it: node, source patch, Family left, relations detached."""

    if not isinstance(allow_empty_source, bool):
        raise ValueError("allow_empty_source must be a boolean")
    allow_empty_source = allow_empty_source and [node["id"] for node in source.get("nodes") or []] == [resident_id]
    if allow_empty_source:
        program = source.get("program") or {}
        if source.get("edges") or source.get("function_blocks") or any(program.get(key) for key in (
            "wires", "components",
        )):
            raise ValueError("allow_empty_source requires a source without HATG flows")
        runtime = lineage_program_runtime.read_runtime(source["lineage_id"])
        if any(item.get("status") not in {"completed", "failed", "cancelled"}
               for item in lineage_program_runtime.pending_resident_handoffs(source["lineage_id"])) or any(
            state.get("pending_events") for state in (runtime.get("event_wires") or {}).values()
        ) or any(state.get("pending_deliveries") or state.get("inbox")
                 for state in (runtime.get("operators") or {}).values()):
            raise ValueError("allow_empty_source requires a source without pending HATG work")
    family = _moving_family_member(source, resident_id)
    if not allow_empty_source and resident_id == source.get("leader_resident_id"):
        raise ValueError("这个 Resident 是源 Lineage 的 Leader，不能迁移；请先指定新的 Leader。没有做任何修改")

    def references(value: Any) -> bool:
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return value == resident_id

    # The Family's own block and generated wiring follow its roster; a
    # workflow the Family collected still names the member and must be
    # migrated or disconnected first.
    owned: set[str] = set()
    if family is not None:
        from . import family_familize

        owned = {family["id"]} | {
            element["id"] for element in family.get("elements") or [] if family_familize.is_generated(element)
        }
    program = source.get("program") or {}
    collections = {"function_blocks": source.get("function_blocks") or []}
    collections.update({key: program.get(key) or [] for key in (
        "wires", "components",
    )})
    blockers = [
        f"{kind}/{item.get('id', '')}"
        for kind, items in collections.items()
        for item in items if item.get("id") not in owned and references(item)
    ]
    # Relations record where a Resident came from; like MoveLineageObjects,
    # the move detaches them without reconnecting the source graph.
    detached = [
        {"id": str(edge.get("id") or ""), "source": str(edge.get("source") or ""),
         "target": str(edge.get("target") or ""), "type": str(edge.get("type") or "")}
        for edge in source.get("edges") or []
        if resident_id in {edge.get("source"), edge.get("target")}
    ]
    node, patch = lineage_graph_edits._resident_projection_removal_patch(source, resident_id)
    if node.get("operator_executor"):
        blockers.append(f"nodes/{resident_id}")
    if blockers:
        raise ValueError(
            "这个 Resident 仍被流程引用：" + "、".join(blockers)
            + "。请先迁移或断开这些流程；没有做任何修改"
        )
    return {"node": node, "patch": patch, "family": family, "detached": detached}


def move_lineage_subgraph(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    target_id = str(payload.get("target_lineage_id") or "")
    if not target_id:
        source = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(source, actor)
        # Validate the selection before publishing a destination identity.
        lineage_subgraph.plan(source, {"program": lineage_program.empty_program()}, payload.get("object_ids"), source_member_ids={
            record["resident_id"] for record in resident_catalog.get_catalog().for_lineage(lineage_id)
        })
        target = lineage_lifecycle.create_lineage({
            "title": f"{source.get('title') or lineage_id} · 迁出",
            "project_id": source.get("project_id"), "lineage_membership": source["lineage_membership"],
        }, actor)
        target_id = str(target["lineage_id"])
        try:
            return move_lineage_subgraph(lineage_id, {**payload, "target_lineage_id": target_id}, actor)
        except Exception:
            current_target = lineage_aggregate.read_lineage(target_id, actor=actor)
            if not current_target.get("nodes") and not (current_target.get("program") or {}).get("components"):
                lineage_lifecycle.delete_lineage(target_id, actor)
            raise
    identity_contract.validate_id(target_id, "lineage")
    if target_id == lineage_id:
        raise ValueError("请选择不同的目标 Lineage")
    # Match normal graph edits: execution fences precede graph scopes.
    with lineage_aggregate.execution_authorities([lineage_id, target_id]), lineage_state._lineage_scopes(
        [lineage_id, target_id], operation="subgraph.move",
    ):
        source, target = lineage_aggregate.read_lineage(lineage_id, actor=actor), lineage_aggregate.read_lineage(target_id, actor=actor)
        lineage_aggregate.ensure_manager(source, actor)
        lineage_aggregate.ensure_manager(target, actor)
        plan = lineage_subgraph.plan(source, target, payload.get("object_ids"),
            source_member_ids={record["resident_id"]
                               for record in resident_catalog.get_catalog().for_lineage(lineage_id)})
        lineage_placement.place_moved_objects(target, plan["target"], set(plan["moved_ids"]))

        def commit_graphs(references):
            from . import graph_storage
            store = resident_catalog.get_catalog().state_store
            store.database.write(lambda c: graph_storage.transfer(c, lineage_id, target_id, list(plan["moved_ids"])))
            normalized_source = lineage_state._normalize_lineage_payload(
                plan["source"], source, actor, reconnect_removed_inheritance=False,
            )
            normalized_target = lineage_state._normalize_lineage_payload(plan["target"], target, actor, resident_references=references)
            lineage_program_runtime.migrate_objects(
                lineage_id, target_id, set(plan["moved_ids"]), dropped_wire_ids=set(plan["detached_wire_ids"]),
            )
            lineage_state._persist_lineage_state(source, normalized_source, "迁出 HATG 子图")
            lineage_state._persist_lineage_state(target, normalized_target, "迁入 HATG 子图")
            # Publishing Resident membership may have allocated initial layout
            # rows. The move owns the final positions, including hidden members;
            # commit them through the layout owner in this same transaction.
            destination = plan["target"]
            lineage_aggregate.lineage_catalog_store().update_layout(target_id, [
                {"object_id": item["id"], "x": item["x"], "y": item["y"]}
                for item in [*destination.get("nodes", []),
                             *destination["program"].get("components", []),
                             *destination.get("function_blocks", [])]
                if item["id"] in plan["moved_ids"] and "x" in item and "y" in item
            ], updated_at=lineage_state._now_iso())
            lineage_state.invalidate_lineage_read_cache(target_id)

        operations = resident_domain.move_resident_identities(lineage_id, target_id, plan["resident_ids"], actor, commit_graphs)
        operation_id = _record_move_operation(lineage_id, target_id, plan, operations)
        return {key: value for key, value in plan.items() if key not in {"source", "target"}} | {
            "source_lineage_id": lineage_id, "target_lineage_id": target_id,
            "operations": operations, "workspace_pending": bool(operations),
            "operation_id": operation_id,
        }


def _record_move_operation(
    source_id: str, target_id: str, plan: dict[str, Any], operations: list[dict[str, Any]],
) -> str:
    """One trackable ID for a whole MoveLineageObjects batch, read by GetMoveOperation."""

    operation_id = "lineage-move_" + secrets.token_hex(12)
    with lineage_aggregate.lineage_catalog_store().state_store.lineage_move_operations.transaction(target_id, operation_id) as record:
        record.update({
            "operation_id": operation_id,
            "source_lineage_id": source_id,
            "target_lineage_id": target_id,
            "object_ids": list(plan.get("moved_ids") or []),
            "resident_operation_ids": [
                str(item.get("operation_id") or "") for item in operations if isinstance(item, dict)
            ],
            "committed_at": lineage_state._now_iso(),
        })
    return operation_id


def move_lineage_resident(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    resident_id = str(payload.get("resident_id") or "").strip()
    target_id = str(payload.get("target_lineage_id") or "").strip()
    identity_contract.validate_id(resident_id, "resident")
    identity_contract.validate_id(target_id, "lineage")
    if target_id == lineage_id:
        raise ValueError("MoveResident requires different source and target Lineages")
    baselines: list[dict[str, Any]] = []
    with lineage_state._lineage_scopes([lineage_id, target_id], operation="resident.move"):
        source, target = lineage_aggregate.read_lineage(lineage_id, actor=actor), lineage_aggregate.read_lineage(target_id, actor=actor)
        lineage_aggregate.ensure_manager(source, actor)
        lineage_aggregate.ensure_manager(target, actor)
        allow_empty_source = payload.get("allow_empty_source", False)
        plan = _resident_move_plan(source, resident_id, allow_empty_source=allow_empty_source)
        family = plan["family"]
        # Leaving a Family changes its generated wiring; old Program work is
        # fenced as for any graph edit that changes the execution contract.
        with lineage_aggregate.execution_authority(lineage_id) if family is not None else nullcontext():
            result = resident_domain.update_resident(resident_id, actor, {
                "target_lineage_id": target_id,
                "allow_empty_source": allow_empty_source,
            })
            if family is not None:
                current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
                baselines = lineage_state._execute_program_contract_cleanup(
                    lineage_id, _program_execution_contract_change(source, current),
                    previous_lineage=source, current_lineage=current,
                )
    if family is not None:
        lineage_state._establish_resident_completion_baselines(lineage_id, actor, baselines)
        from .templates.familize import member_moved_out
        try:
            member_moved_out(family, resident_id, target, actor)
        except Exception:  # The move has committed; a retry would fail on the moved Resident.
            LOGGER.exception("Family %s could not settle the move of member %s", family["id"], resident_id)
    return {
        "resident_id": resident_id,
        "source_lineage_id": lineage_id,
        "target_lineage_id": target_id,
        "operation_id": result["operation_id"],
        "workspace_pending": result["workspace_pending"],
        "family_id": family["id"] if family is not None else "",
        "detached": plan["detached"],
    }


def get_move_operation(operation_id: str, actor: str) -> dict[str, Any]:
    """Read one committed Resident move by its operation ID, without recovery or mutation."""
    from .resident_lifecycle import ResidentLifecycleOperations

    operation_id = str(operation_id or "").strip()
    if re.fullmatch(r"lineage-move_[0-9a-f]{24}", operation_id):
        return _get_batch_move_operation(operation_id, actor)
    if not re.fullmatch(r"resident-operation_[0-9a-f]{24}", operation_id):
        raise ValueError("operation_id must be a resident-operation or lineage-move ID")
    try:
        transfer = ResidentLifecycleOperations(lineage_aggregate.lineage_catalog_store().state_store).transfer(operation_id)
    except KeyError:
        raise KeyError("迁移操作不存在或已被回收") from None
    source_id = str(transfer.get("source_lineage_id") or "")
    target_id = str(transfer.get("target_lineage_id") or "")
    resident_id = str(transfer.get("resident_id") or "")
    identity_contract.validate_id(source_id, "lineage")
    identity_contract.validate_id(target_id, "lineage")
    identity_contract.validate_id(resident_id, "resident")
    lineage_state._ensure_visible(lineage_aggregate.read_lineage(source_id, actor=actor), actor)
    lineage_state._ensure_visible(lineage_aggregate.read_lineage(target_id, actor=actor), actor)
    return {
        **resident_domain.observe_committed_resident_transfer(operation_id, resident_id),
        "source_lineage_id": source_id,
        "target_lineage_id": target_id,
        "instruction_id": transfer["instruction_id"],
    }


def _get_batch_move_operation(operation_id: str, actor: str) -> dict[str, Any]:
    """A batch is complete once every Resident it moved has finished its workspace move."""

    store = lineage_aggregate.lineage_catalog_store().state_store
    target_id = store.lineage_move_operations.lineage_id(operation_id)
    if not target_id:
        raise KeyError("迁移操作不存在或已被回收")
    record = store.lineage_move_operations.get(target_id, operation_id) or {}
    source_id = str(record.get("source_lineage_id") or "")
    lineage_state._ensure_visible(lineage_aggregate.read_lineage(source_id, actor=actor), actor)
    lineage_state._ensure_visible(lineage_aggregate.read_lineage(target_id, actor=actor), actor)
    parts = [get_move_operation(item, actor) for item in record.get("resident_operation_ids") or [] if item]
    failed = next((part for part in parts if part.get("status") == "failed"), None)
    pending = next((part for part in parts if part.get("status") != "completed"), None)
    current = failed or pending
    return {
        "operation_id": operation_id,
        "source_lineage_id": source_id,
        "target_lineage_id": target_id,
        "object_ids": list(record.get("object_ids") or []),
        "phase": str(current.get("phase") or "") if current else "completed",
        "status": str(current.get("status") or "") if current else "completed",
        "error": (current or {}).get("error"),
        "resident_operations": parts,
    }


def move_resident_membership(
    source_lineage_id: str, record: dict[str, Any], actor: str, *, allow_empty_source: bool = False,
    publish_identity: Callable[[], None],
) -> None:
    """Commit both graph projections with Resident Domain's membership transaction.

    Resident Domain already holds both ordered graph scopes and the exact
    Resident scope. File transfer and external projections follow via outbox.
    """
    target_lineage_id = str(record["lineage_id"])
    source, target = lineage_aggregate.read_lineage(source_lineage_id, actor=actor), lineage_aggregate.read_lineage(target_lineage_id, actor=actor)
    lineage_aggregate.ensure_manager(source, actor)
    lineage_aggregate.ensure_manager(target, actor)
    node, patch = _resident_move_patch(
        source, str(record["resident_id"]), allow_empty_source=allow_empty_source,
    )
    # Capture and validate both aggregates before publishing the new home.
    # Afterwards the source's current membership already excludes this node.
    body = lineage_graph_authoring.graph_patch_candidate(source, patch)
    from .lineage_definition_scope import check_before_membership_write
    check_before_membership_write(lineage_aggregate.lineage_catalog_store().state_store, source_lineage_id)
    publish_identity()
    normalized = lineage_state._normalize_lineage_payload(
        body, source, actor, reconnect_removed_inheritance=False,
    )
    lineage_state._persist_lineage_state(source, normalized, "迁出 Resident")
    position = lineage_placement.node_position(target, node, {}, child=node)
    lineage_resident_nodes._append_registered_resident_node_locked(
        target, record, actor,
        position=position,
    )
    lineage_aggregate.lineage_catalog_store().update_layout(target_lineage_id, [
        {"object_id": record["resident_id"], "x": position[0], "y": position[1]},
    ], updated_at=lineage_state._now_iso())
    lineage_state.invalidate_lineage_read_cache(target_lineage_id)
