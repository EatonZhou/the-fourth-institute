"""Branch transitions of a Lineage: Fork, absorbing a node's context, promoting Seeds and their candidate forks."""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Any

from management_common.projection_receipts import CommittedProjectionError

from .errors import LineageConflictError
from . import lineage_branch_transitions, lineage_definition_normalization
from .lineage_values import _clone_json, _item_id, _safe_actor, _text
from .lineage_view_projection import node_resident_id as _node_resident_id
from .lineage_program_fields import _position
from . import identity_contract, lineage_placement
from . import lineage_aggregate, resident_message_contract
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state, lineage_resident_nodes, lineage_graph_edits, lineage_resident_controls


LOGGER = logging.getLogger(__name__)


def absorb_lineage_node_context(lineage_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    """Send a normal Resident message asking the target to absorb a source."""
    actor_id = _safe_actor(actor)
    source_resident_id, target_resident_id = (
        lineage_branch_transitions.absorption_request(payload)
    )
    with lineage_aggregate.lineage_scope(lineage_id):
        baseline = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(baseline, actor_id)
        source_snapshot, target_snapshot = (
            lineage_branch_transitions.absorption_nodes(
                baseline, source_resident_id, target_resident_id,
                resident_id_of=_node_resident_id,
            )
        )

    source_reference_id = _node_resident_id(source_snapshot)
    target_reference_id = _node_resident_id(target_snapshot)
    if not identity_contract.is_valid_id(source_reference_id, "resident"):
        raise ValueError("来源节点还没有统一 Resident ID，请先同步协作资源注册表")
    if not identity_contract.is_valid_id(target_reference_id, "resident"):
        raise ValueError("目标节点还没有统一 Resident ID，请先同步协作资源注册表")
    prompt = lineage_branch_transitions.absorption_prompt(
        source_snapshot, target_snapshot, source_reference_id,
    )
    now = lineage_state._now_iso()

    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(current, actor_id)
        current_nodes = {str(node.get("id") or ""): node for node in current.get("nodes") or [] if isinstance(node, dict)}
        if source_resident_id not in current_nodes or target_resident_id not in current_nodes:
            raise LineageConflictError("来源节点或目标节点已改变，请重试拖放")
        prepared_lineage, absorption_edge_id = (
            lineage_branch_transitions.prepare_absorption(
                current,
                source_id=source_resident_id,
                target_id=target_resident_id,
                source=source_snapshot,
                target=target_snapshot,
                source_resident_id=source_reference_id,
                target_resident_id=target_reference_id,
                now=now,
                new_edge_id=lambda: _item_id("", "edge_absorption"),
                normalize_edge=lineage_state._normalize_edge,
            )
        )
        lineage_state._persist_lineage_runtime_state(
            current,
            prepared_lineage,
            "开始节点吸收",
        )

    delivery_status = "queued"
    delivery_error = ""
    target_message_id = ""
    try:
        response = lineage_resident_controls._send_node_message(target_snapshot, prompt, actor_id)
        target_message_id = resident_message_contract.delivered_message_id(response)
    except Exception as exc:
        delivery_status = "failed"
        delivery_error = _text(exc, 2000) or "目标节点运行失败"

    delivered_at = lineage_state._now_iso()
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        finalized_lineage = lineage_branch_transitions.finish_absorption(
            current, absorption_edge_id,
            status=delivery_status,
            message_id=target_message_id,
            error=delivery_error,
            delivered_at=delivered_at,
        )
        if finalized_lineage is not None:
            lineage_state._persist_lineage_runtime_state(
                current, finalized_lineage, "节点吸收投递结果",
            )
        else:
            finalized_lineage = current

    verified = lineage_state._verified_lineage_view(finalized_lineage, actor_id)
    verified_edge = next(
        (edge for edge in verified.get("edges") or [] if str(edge.get("id") or "") == absorption_edge_id),
        None,
    )
    return {
        "lineage": verified,
        "edge": verified_edge,
        "reference": {
            "source_resident_id": source_reference_id,
        },
        "delivery": {
            "status": delivery_status,
            "error": delivery_error,
            "target_message_id": target_message_id,
        },
    }


def _created_resident_id(detail: dict[str, Any]) -> str:
    return _text(detail.get("resident_id"), 180)


def _create_derived_resident(
    source: dict[str, Any],
    payload: dict[str, Any],
    actor: str,
    lineage_source: str,
) -> dict[str, Any]:
    return lineage_branch_transitions.create_derived_resident(
        _node_resident_id(source), payload, actor, lineage_source,
        resident_authority=resident_domain,
    )


def _lineage_branch_response(
    lineage: dict[str, Any],
    created_resident_id: str,
    actor: str,
    *,
    created: bool,
    title_sync_status: str = "",
    title_sync_error: str = "",
) -> dict[str, Any]:
    # The child and inheritance edge have committed. Opening Chat immediately
    # also requires the Resource identity and creator credentials to be visible.
    # Confirm outside graph transactions, including an existing-edge replay;
    # failure carries a projection-only receipt and must not recreate the Fork.
    resident_domain.confirm_resident_registration(created_resident_id, actor)
    return lineage_branch_transitions.branch_response(
        lineage_state._verified_lineage_view(lineage, actor), created_resident_id,
        created=created,
        title_sync_status=title_sync_status,
        title_sync_error=title_sync_error,
    )


def create_lineage_branch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Prepare a Fork without locking; validate and attach against committed membership."""
    actor_id = _safe_actor(actor)
    source_resident_id, context_source_resident_id, edge_id, mode = (
        lineage_branch_transitions.branch_request(payload)
    )

    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)

        # The graph and Resident membership must be checked in one short
        # transaction. Context/Provider preparation below holds no graph lock.
        def validate_source() -> dict[str, Any]:
            current = _locked_branch_definition(previous, actor_id)
            lineage_state._normalize_lineage_payload(_clone_json(current), current, actor_id)
            return current

        previous = lineage_aggregate.lineage_catalog_store().state_store.transaction(validate_source)
        source, source_node_id, existing_edge = (
            lineage_branch_transitions.branch_source(
                previous, source_resident_id, edge_id,
                resident_id_of=_node_resident_id,
            )
        )
        if not _text(payload.get("context_source_resident_id"), 80):
            context_source_resident_id = _node_resident_id(source)

        if existing_edge is not None:
            return _lineage_branch_response(
                previous,
                str(existing_edge.get("target") or ""),
                actor_id,
                created=False,
                title_sync_status="already-created",
            )

        lineage_source = f"lineage:{lineage_id}:{source_node_id}"
        requested_title = _text(payload.get("title"), 120) or _text(
            f"{source.get('title') or source_resident_id} · 分支",
            120,
        )
        child_x, child_y = lineage_placement.node_position(
            previous, source, payload, child={"title": requested_title},
        )
        creation = _create_derived_resident(
            source,
            {**payload, "mode": mode, "title": requested_title},
            actor_id,
            lineage_source,
        )
        created_resident_id = _created_resident_id(creation)
        if not created_resident_id:
            raise RuntimeError("ForkResident 响应缺少 resident_id")
        if any(
            _node_resident_id(node) == created_resident_id
            for node in previous.get("nodes") or []
            if isinstance(node, dict)
        ):
            raise ValueError("新建 Resident 已存在于当前 HATG")
        try:
            lineage, title_sync_status, title_sync_error = _attach_lineage_branch(
                previous, payload, source, source_node_id, creation, created_resident_id,
                requested_title=requested_title, position=(child_x, child_y),
                edge_id=edge_id, context_source_resident_id=context_source_resident_id,
                mode=mode, actor_id=actor_id,
            )
        except CommittedProjectionError:
            raise  # the graph committed; only its projections are pending
        except Exception:
            # A failed Fork leaves nothing behind: the child Resident exists
            # already, so it is deleted again (TAU resident.fork-handoff).
            _discard_unattached_branch(lineage_id, created_resident_id, actor_id)
            raise
        return _lineage_branch_response(
            lineage,
            created_resident_id,
            actor_id,
            created=True,
            title_sync_status=title_sync_status,
            title_sync_error=title_sync_error,
        )


def _discard_unattached_branch(lineage_id: str, resident_id: str, actor: str) -> None:
    try:
        from . import resident_execution_domain, resident_execution_work
        from . import resident_record_domain
        record = resident_record_domain.find_resident(resident_id)
        job = resident_execution_work.read(record) if record else {}
        if job.get("operation") == "context_fork_create" and not resident_execution_work.submitted_execution_id(job) and not job.get("continuation_id"):
            # This unattached child has only preparation work, never a dispatched turn.
            resident_execution_domain.cancel_resident_execution_for_target(
                resident_id, actor, str(job.get("user_message_id") or ""), continue_queue=False,
            )
        lineage_graph_edits.delete_lineage_graph_objects(lineage_id, {"resident_ids": [resident_id]}, actor)
    except Exception:  # noqa: BLE001 - the Fork's own error is the one to report
        LOGGER.exception("Failed Fork could not delete its child Resident %s in %s", resident_id, lineage_id)


def _locked_branch_definition(previous: dict[str, Any], actor: str) -> dict[str, Any]:
    """Refresh a branch's definition inside its short validation/commit transaction."""
    current = lineage_aggregate.lineage_catalog_store().state_store.lock_lineage_definition(previous["lineage_id"])
    if current is None:
        raise LineageConflictError("Fork Lineage was deleted during preparation")
    refreshed = _clone_json(previous)
    lineage_state._adopt_committed_definition(refreshed, current)
    lineage_aggregate.ensure_manager(refreshed, actor)
    return refreshed


def _attach_lineage_branch(
    previous: dict[str, Any],
    payload: dict[str, Any],
    source: dict[str, Any],
    source_node_id: str,
    creation: dict[str, Any],
    created_resident_id: str,
    *,
    requested_title: str,
    position: tuple[Any, Any],
    edge_id: str,
    context_source_resident_id: str,
    mode: str,
    actor_id: str,
) -> tuple[dict[str, Any], str, str]:
    """Add the created child and its inheritance edge to the graph; returns (lineage, title status, title error)."""
    child_x, child_y = position
    now = lineage_state._now_iso()
    title = requested_title
    child = lineage_branch_transitions.branch_child(
        source, creation,
        resident_id=created_resident_id,
        title=title,
        position=(child_x, child_y),
        now=now,
    )

    title_sync_status = ""
    title_sync_error = ""
    try:
        if str(creation.get("label") or "").strip() != title:
            lineage_resident_nodes._rename_resident_lineage_node(child, title, actor_id)
        title_sync_status = "synced"
    except Exception as exc:
        title_sync_status = "failed"
        title_sync_error = _text(exc, 1000)

    edge = lineage_branch_transitions.branch_edge(
        edge_id=edge_id,
        source_id=source_node_id,
        target_id=created_resident_id,
        context_source_resident_id=context_source_resident_id,
        mode=mode,
        message=(payload.get("message") if isinstance(payload.get("message"), dict) else {}),
        now=now,
    )
    def commit_branch() -> dict[str, Any]:
        current = _locked_branch_definition(previous, actor_id)
        if source_node_id not in {node["id"] for node in current.get("nodes") or []}:
            raise LineageConflictError("Fork source was deleted during preparation")
        candidate = _clone_json(current)
        # Registering the child already establishes graph membership. Replace
        # that default node with the Fork placement and metadata exactly once.
        candidate["nodes"] = [node for node in candidate.get("nodes") or []
                              if node["id"] != created_resident_id] + [child]
        candidate.setdefault("edges", []).append(edge)
        lineage = lineage_state._normalize_lineage_payload(candidate, current, actor_id)
        lineage_state._persist_lineage_state(current, lineage, "创建轨迹分支")
        return lineage

    lineage = lineage_aggregate.lineage_catalog_store().state_store.transaction(commit_branch)
    return lineage, title_sync_status, title_sync_error


def fork_lineage_resident(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Execute the canonical ForkResident business operation."""

    source_resident_id = _text(
        payload.get("source"),
        80,
    ).lower()
    if not source_resident_id:
        raise ValueError("ForkResident 需要 source")
    if "operation" in payload:
        raise ValueError("ForkResident 不接受 operation；Function Block 使用独立协议")
    fork_mode = _text(
        payload.get("fork_mode"),
        24,
    ).lower() or "full"
    forbidden_fields = [
        field
        for field in ("launch_after_fork", "prompt")
        if field in payload
    ]
    if "message" in payload and fork_mode != "message":
        forbidden_fields.append("message")
    if forbidden_fields:
        raise ValueError(
            "ForkResident 只创建普通 Resident，不接受字段："
            + "、".join(forbidden_fields)
        )
    if fork_mode == "message":
        selector = payload.get("message")
        if (
            not isinstance(selector, dict)
            or set(selector) != {"message_id"}
            or not isinstance(selector.get("message_id"), str)
            or not selector["message_id"].strip()
        ):
            raise ValueError(
                "ForkResident 的 message 只接受历史截止点选择器："
                "{message_id: 非空字符串}；不接受新消息正文"
            )
    effect_id = _text(payload.get("effect_id"), 110).lower()
    trigger_path_id = _text(payload.get("trigger_path_id"), 180)
    edge_id = _text(payload.get("edge_id"), 80).lower()
    if not edge_id:
        idempotency_key = _text(payload.get("idempotency_key"), 240)
        if idempotency_key or trigger_path_id:
            operation_identity = (
                f"{lineage_id}\0{effect_id}\0"
                f"{trigger_path_id}\0{source_resident_id}"
            )
            if idempotency_key:
                operation_identity += f"\0{_safe_actor(actor)}\0{idempotency_key}"
            digest = hashlib.sha256(operation_identity.encode("utf-8")).hexdigest()[:16]
            edge_id = f"edge_instruction_fork_{digest}"
        else:
            edge_id = _item_id("", "edge")
    branch_payload = {**payload}
    branch_payload.pop("source", None)
    branch_payload.pop("fork_mode", None)
    branch_payload.pop("child_title", None)
    response = create_lineage_branch(
        lineage_id,
        {
            **branch_payload,
            "source_resident_id": source_resident_id,
            "edge_id": edge_id,
            "mode": fork_mode,
            "title": _text(payload.get("child_title"), 120),
        },
        actor,
    )
    return response


def _program_seed_component(
    lineage: dict[str, Any],
    seed_id: str,
) -> dict[str, Any] | None:
    program = lineage.get("program") if isinstance(lineage.get("program"), dict) else {}
    return next(
        (
            component
            for component in program.get("components") or []
            if isinstance(component, dict)
            and str(component.get("kind") or "") == "seed"
            and str(component.get("id") or "") == seed_id
        ),
        None,
    )


def promote_seed_node(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
) -> dict[str, Any]:
    """Promote a Seed component to an idle Resident; Program decides later work."""

    actor_id = _safe_actor(actor)
    seed_id = _text(payload.get("seed_id"), 80).lower()
    if not seed_id:
        raise ValueError("采纳 Seed 需要 seed_id")
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        seed = _program_seed_component(previous, seed_id)
        if not seed:
            raise ValueError("Seed 不在当前 HATG Program 中")
        config = seed.get("config") if isinstance(seed.get("config"), dict) else {}
        if str(config.get("status") or "active") != "active":
            raise ValueError("这个 Seed 已经被采纳或抛弃")
        candidate_fork = _candidate_fork(previous, seed_id)
        source_resident_id = str((candidate_fork or {}).get("source") or "")
        source = next(
            (node for node in previous.get("nodes") or [] if str(node.get("id") or "") == source_resident_id),
            None,
        )
        if not candidate_fork or not source:
            raise ValueError("先建立候选 Fork 才能采纳这个 Seed")
        creation = resident_domain.create_resident_full_context_fork(
            _node_resident_id(source),
            actor_id,
            f"lineage:{lineage_id}:{source_resident_id}",
            defer_backend_start=True,
        )
        created_resident_id = _created_resident_id(creation)
        if not created_resident_id:
            raise RuntimeError("创建 Seed Resident 的响应缺少 resident id")
        now = lineage_state._now_iso()
        title = _text(payload.get("child_title"), 120) or _text(seed.get("title"), 120) or "Seed Resident"
        purpose = _text(payload.get("action_purpose"), 4000)
        child = {
            "id": created_resident_id,
            "title": title,
            "status": "idle",
            "backend": str(source.get("backend") or "codex"),
            "x": _position(seed.get("x"), 50),
            "y": _position(seed.get("y"), 50),
            "origin_seed_id": seed_id,
            "created_at": now,
            "updated_at": now,
        }
        candidate = _clone_json(previous)
        candidate.setdefault("nodes", []).append(child)
        # The candidate fork becomes the inheritance relation to the new Resident.
        candidate["edges"] = [
            {
                "id": candidate_fork["id"],
                "source": source_resident_id,
                "target": created_resident_id,
                "type": "inheritance",
                "label": "Seed 采纳",
                "created_at": now,
                "updated_at": now,
            }
            if str(edge.get("id") or "") == candidate_fork["id"] else edge
            for edge in candidate.get("edges") or []
        ]
        promoted = _program_seed_component(candidate, seed_id)
        promoted.setdefault("config", {}).update({
            "status": "promoted",
            "promoted_resident_id": created_resident_id,
            **({"promotion_purpose": purpose} if purpose else {}),
            "promoted_at": now,
            "updated_at": now,
        })
        lineage = lineage_state._normalize_lineage_payload(candidate, previous, actor_id)
        lineage_state._persist_lineage_state(previous, lineage, f"采纳 Seed：{title}")
    verified = lineage_state._verified_lineage_view(lineage, actor_id)
    return {
        "lineage": verified,
        "seed": _program_seed_component(verified, seed_id),
        "resident": next((node for node in verified.get("nodes") or [] if str(node.get("id") or "") == created_resident_id), child),
        "started": False,
        # Where the candidate is written; the caller sends it to the new Resident with its task.
        "candidate_path": _seed_candidate_path(lineage_id, previous, {**seed, **config}),
    }


def _candidate_fork(lineage: dict[str, Any], seed_id: str) -> dict[str, Any] | None:
    return next((
        edge for edge in lineage.get("edges") or []
        if lineage_definition_normalization.is_candidate_fork(edge) and str(edge.get("target") or "") == seed_id
    ), None)


def set_candidate_fork(lineage_id: str, seed_id: str, source_resident_id: str, actor: str) -> dict[str, Any]:
    """Make ``source_resident_id`` the Resident a Seed's candidate starts from.

    A Seed has one candidate fork; a different source replaces it with a new
    relation.  Returns the candidate fork and the ID of the one it replaced.
    """

    seed_id = _text(seed_id, 80).lower()
    source_resident_id = _text(source_resident_id, 80).lower()

    def edit(body: dict[str, Any]) -> tuple[str, str]:
        seed = _program_seed_component(body, seed_id)
        if not seed:
            raise ValueError("Seed 不在当前 HATG Program 中")
        if str((seed.get("config") or {}).get("status") or "active") != "active":
            raise ValueError("这个 Seed 已经被采纳或抛弃，不能再建立候选 Fork")
        if not any(str(node.get("id") or "") == source_resident_id for node in body.get("nodes") or []):
            raise ValueError("候选 Fork 的来源必须是这个 Lineage 中的 Resident")
        existing = _candidate_fork(body, seed_id)
        if existing and existing.get("source") == source_resident_id:
            return str(existing["id"]), ""
        edge_id = f"edge_candidate_{secrets.token_hex(6)}"
        body["edges"] = [
            edge for edge in body.get("edges") or [] if edge is not existing
        ] + [{"id": edge_id, "source": source_resident_id, "target": seed_id, "type": "candidate_fork"}]
        return edge_id, str((existing or {}).get("id") or "")

    verified, (edge_id, replaced) = lineage_graph_edits.commit_lineage_edit(
        lineage_id, actor, edit, reason="建立候选 Fork", full_view=False,
    )
    edge = next((item for item in verified.get("edges") or [] if item.get("id") == edge_id), {})
    return {"edge": edge, "replaced_edge_id": replaced}


def delete_candidate_fork(lineage_id: str, seed_id: str, actor: str) -> str:
    """Remove a Seed's candidate fork; returns its ID.  The Seed stays, without a source."""

    seed_id = _text(seed_id, 80).lower()

    def edit(body: dict[str, Any]) -> str:
        existing = _candidate_fork(body, seed_id)
        if existing is None:
            raise KeyError(f"Seed 没有候选 Fork：{seed_id}")
        body["edges"] = [edge for edge in body.get("edges") or [] if edge is not existing]
        return str(existing["id"])

    _verified, edge_id = lineage_graph_edits.commit_lineage_edit(lineage_id, actor, edit, reason="删除候选 Fork", full_view=False)
    return edge_id


def _seed_candidate_path(lineage_id: str, lineage: dict[str, Any], seed: dict[str, Any]) -> str:
    try:
        root = lineage_state._seed_folder(lineage_id)
        path = (root / lineage_state._workspace_relative_path(seed.get("artifact_path"), suffix=".md")).resolve(strict=False)
        path.relative_to(root)
    except (OSError, ValueError):
        return ""
    return str(path) if path.is_file() else ""
