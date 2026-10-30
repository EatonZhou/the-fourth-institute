"""Whole-graph edits of a Lineage: graph bodies and patches, one-commit edits, and removing graph objects
together with their Residents.
"""

from __future__ import annotations

from typing import Any, Iterable

from . import lineage_graph_authoring, lineage_program_graph_mutation
from . import lineage_subgraph
from .lineage_program_contracts import _program_execution_contract_change
from .lineage_values import _clone_json, _safe_actor, _text
from . import lineage_program_authoring
from .lineage_view_projection import node_resident_id as _node_resident_id
from . import identity_contract, invariant_locks, lineage_program_graph_mutation, resident_catalog
from . import lineage_aggregate
from .domain_ports import resident_domain_port as resident_domain
from . import lineage_state, lineage_resident_nodes


LINEAGE_GRAPH_PATCH_FIELDS = frozenset(
    {
        "title",
        "description",
        "nodes",
        "edges",
        "program",
        "fragments",
        "leader_resident_id",
    }
)


def _synchronize_canonical_resident_titles(
    previous: dict[str, Any],
    lineage: dict[str, Any],
    actor: str,
) -> None:
    """Commit HATG title edits through the exact Resident authority."""

    nodes = [node for node in lineage.get("nodes") or [] if isinstance(node, dict)]
    previous_nodes = {
        _node_resident_id(node): node
        for node in previous.get("nodes") or []
        if isinstance(node, dict) and _node_resident_id(node)
    }
    records = lineage_state._resident_targets([_node_resident_id(node) for node in nodes])
    for node in nodes:
        resident_id = _node_resident_id(node)
        record = records.get(resident_id)
        title = _text(node.get("title"), 120)
        previous_node = previous_nodes.get(resident_id)
        previous_title = _text(
            previous_node.get("title") if previous_node else "",
            120,
        )
        if (
            not record
            or not title
            or not previous_node
            or title == previous_title
        ):
            continue
        canonical_title = _text(record.get("title"), 120)
        if title != canonical_title:
            renamed = lineage_resident_nodes._rename_resident_lineage_node(node, title, actor)
            if isinstance(renamed, dict):
                title = _text(renamed.get("title"), 120) or title
        node["title"] = title
        node["resident_title"] = title


def apply_lineage_graph_patch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _apply_lineage_graph_patch(
        lineage_id,
        payload,
        actor,
        synchronize_external_projections=True,
        previous=previous,
    )


def _apply_lineage_graph_patch(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    synchronize_external_projections: bool,
    membership_transition: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically apply an authored HATG graph projection.

    The patch deliberately remains a state projection, not a second graph
    mutation language. Fine-grained Instructions use the component/wire
    authorities; macro expansion can submit the resulting program, nodes, and
    edges together through this one commit.
    """

    actor_id = _safe_actor(actor)
    raw_patch = lineage_graph_authoring.authored_graph_patch(
        payload, instruction="ApplyLineageChanges", fields=LINEAGE_GRAPH_PATCH_FIELDS,
    )
    with lineage_aggregate.lineage_scope(lineage_id):
        current = lineage_aggregate.read_lineage(lineage_id, actor=actor_id)
        lineage_aggregate.ensure_manager(current, actor_id)
        base = previous if previous is not None else current
        body = lineage_graph_authoring.graph_patch_candidate(base, raw_patch)
    return update_lineage(
        lineage_id,
        {
            "lineage": body,
            "history_reason": _text(
                payload.get("history_reason"),
                180,
            )
            or "应用 HATG Graph Patch",
            "placement": payload.get("placement", {}),
            "near_resident_id": payload.get("near_resident_id"),
        },
        actor_id,
        synchronize_external_projections=(
            synchronize_external_projections
        ),
        membership_transition=membership_transition,
        previous=base,
    )


def commit_lineage_edit(
    lineage_id: str,
    actor: str,
    edit: Any,
    *,
    reason: str,
    resident_ids: Iterable[str] = (),
    placement_request: dict[str, Any] | None = None,
    preserve_event_source_ids: Iterable[str] = (),
    full_view: bool = True,
) -> tuple[dict[str, Any], Any]:
    """Apply ``edit(body)`` to a copy of the definition and commit it once.

    ``resident_ids`` are deleted with the full ``DeleteResident`` semantics in
    the same transaction.  ``edit`` may raise to abandon the whole change.
    Returns the verified Lineage view (or the stored definition when
    ``full_view`` is false) and whatever ``edit`` returned.
    """

    actor_id = _safe_actor(actor)
    deleted = [str(item) for item in resident_ids]
    if len(set(deleted)) != len(deleted):
        raise ValueError("resident_ids must contain unique IDs")
    for resident_id in deleted:
        identity_contract.validate_id(resident_id, "resident")
    completion_baselines: list[dict[str, Any]] = []
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor_id)
        body = _clone_json(previous)
        outcome = edit(body)
        if deleted:
            for record in resident_catalog.get_catalog().get_many(deleted):
                if record["lineage_id"] != lineage_id:
                    raise ValueError("Resident 不属于这个 Lineage")
            present = {str(node.get("id") or "") for node in body.get("nodes") or []}
            _nodes, patch = _resident_projections_removal_patch(body, set(deleted) & present)
            body.update(patch)
        lineage = lineage_state._normalize_lineage_payload(body, previous, actor_id)
        impact = _program_execution_contract_change(previous, lineage)

        def commit() -> None:
            if deleted:
                removed_family_ids = frozenset(
                    item["id"] for item in previous.get("function_blocks", [])
                ) - frozenset(item["id"] for item in lineage.get("function_blocks", []))
                resident_domain.delete_resident_identities(
                    lineage_id, deleted, actor_id, removed_family_ids=removed_family_ids,
                )
            lineage_state._persist_lineage_state(
                previous,
                lineage,
                reason,
                placement_request=placement_request or {},
            )

        with (
            lineage_state._program_contract_authority(lineage_id, impact),
            invariant_locks.resident_locks(deleted, operation="lineage.edit"),
        ):
            lineage_aggregate.lineage_catalog_store().state_store.transaction(commit)
            completion_baselines = lineage_state._execute_program_contract_cleanup(
                lineage_id,
                impact,
                previous_lineage=previous,
                current_lineage=lineage,
                preserve_event_source_fire_budget_ids={str(item) for item in preserve_event_source_ids},
            )
        verified = lineage_state._verified_lineage_view(lineage, actor_id) if full_view else lineage
    lineage_state._establish_resident_completion_baselines(lineage_id, actor, completion_baselines)
    return verified, outcome


def apply_lineage_changes(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    include_lineage: bool = True,
) -> dict[str, Any]:
    """Commit Program operations and Resident deletions as one Lineage version.

    ``payload["program_operations"]`` uses the Program mutation language
    (set/add/clone/remove on components and wires) and
    ``payload["resident_ids"]`` lists Residents deleted with the full
    ``DeleteResident`` semantics.  Either every change commits under one new
    revision or none does.
    """

    raw_operations = payload.get("program_operations") or []
    operations = lineage_program_graph_mutation.normalize_graph_mutations(raw_operations) if raw_operations else []
    resident_ids = [str(item) for item in payload.get("resident_ids") or []]
    if not operations and not resident_ids:
        raise ValueError("ApplyLineageChanges 需要至少一项修改")
    preserve: set[str] = set()
    previous_ids: set[str] = set()

    def graph_ids(body: dict[str, Any]) -> set[str]:
        program = body.get("program") or {}
        return {
            str(item.get("id") or "")
            for collection in (body.get("nodes"), body.get("edges"), program.get("components"),
                               program.get("wires"), body.get("function_blocks"))
            for item in collection or [] if isinstance(item, dict) and item.get("id")
        }

    def edit(body: dict[str, Any]) -> dict[str, set[tuple[str, str]]]:
        previous_ids.update(graph_ids(body))
        if not operations:
            return {"created": set(), "changed": set(), "removed": set()}
        mutation = lineage_program_graph_mutation.mutate_program_graph(body, operations, True)
        body.clear()
        body.update(_clone_json(mutation.candidate))
        preserve.update(mutation.preserve_event_source_ids)
        return {"created": set(mutation.created), "changed": set(mutation.changed), "removed": set(mutation.removed)}

    verified, outcome = commit_lineage_edit(
        lineage_id,
        actor,
        edit,
        reason="ApplyLineageChanges",
        resident_ids=resident_ids,
        placement_request=payload,
        preserve_event_source_ids=preserve,
        full_view=include_lineage,
    )
    created, changed, removed = outcome["created"], outcome["changed"], outcome["removed"]
    return {
        "lineage": verified,
        "revision": int(verified.get("revision") or 0),
        "created_ids": sorted(item_id for _kind, item_id in created),
        "updated_ids": sorted(item_id for _kind, item_id in changed - created),
        "deleted_ids": sorted({item_id for _kind, item_id in removed} | set(resident_ids) | (previous_ids - graph_ids(verified))),
        "leader_resident_id": str(verified.get("leader_resident_id") or ""),
    }


def _resident_projection_removal_patch(
    lineage: dict[str, Any],
    resident_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    nodes, patch = _resident_projections_removal_patch(lineage, {resident_id})
    return nodes[0], patch


def _resident_projections_removal_patch(
    lineage: dict[str, Any], resident_ids: set[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    removed = [item for item in lineage.get("nodes") or [] if item.get("id") in resident_ids]
    if len(removed) != len(resident_ids):
        raise KeyError("Lineage node not found")
    nodes = [
        _clone_json(item)
        for item in lineage.get("nodes") or []
        if (
            isinstance(item, dict)
            and str(item.get("id") or "") not in resident_ids
        )
    ]

    def references_resident(item: dict[str, Any]) -> bool:
        return (
            str(item.get("source") or "") in resident_ids
            or str(item.get("target") or "") in resident_ids
        )

    edges = [
        _clone_json(item)
        for item in lineage.get("edges") or []
        if isinstance(item, dict) and not references_resident(item)
    ]
    program = lineage_program_authoring.program_without_resident_references(
        lineage.get("program"),
        resident_ids,
    )
    member_ids = {record["resident_id"]
                  for record in resident_catalog.get_catalog().for_lineage(lineage["lineage_id"])}
    leader_id = lineage_subgraph.successor_coordinator(
        str(lineage.get("leader_resident_id") or ""), resident_ids, nodes, member_ids=member_ids,
    )
    if not leader_id and (lineage.get("principal_hierarchy") or {}).get("level") == "project":
        # A Project-level Lineage always has its own Leader; its only Resident is that Leader.
        raise ValueError(
            "Project 层 Lineage 必须有 Leader：要移除的是它最后的成员。"
            "先在这个 Lineage 中加入其他 Resident，或用 DeleteProject 删除整个 Project"
        )
    return removed, {
        "nodes": nodes,
        "edges": edges,
        "program": program,
        "leader_resident_id": leader_id,
    }


def _finalize_deleted_resident_projection(lineage_id: str) -> None:
    """Publish projections after the enclosing delete transaction commits."""

    lineage = lineage_aggregate.read_lineage_state(lineage_id)
    lineage_state._publish_lineage_resource_projection(lineage_id, int(lineage["revision"]))
    resident_domain.sync_lineage_member_credentials(
        lineage,
        previous_lineage=None,
    )


def update_lineage(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    synchronize_external_projections: bool = True,
    membership_transition: bool = False,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Commit a whole-graph body; ``previous`` is the state the body was built from.

    The commit merges the difference between ``previous`` and the body into
    the committed definition, so concurrent edits outside that difference stay.
    """

    completion_baselines: list[dict[str, Any]] = []
    with lineage_aggregate.lineage_scope(lineage_id):
        if previous is None:
            previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
            lineage_aggregate.ensure_manager(previous, actor)
        raw_body = (
            payload.get("lineage")
            if isinstance(payload.get("lineage"), dict)
            else payload
        )
        lineage = lineage_graph_authoring.normalized_graph_update(
            previous, raw_body, actor,
            membership_transition=membership_transition,
            require_unchanged_membership=lineage_state._require_unchanged_membership,
            normalize=lineage_state._normalize_lineage_payload,
        )
        program_contract_impact = (
            _program_execution_contract_change(
                previous,
                lineage,
            )
        )
        def commit_graph_authority() -> None:
            _synchronize_canonical_resident_titles(previous, lineage, actor)
            lineage_state._persist_lineage_state(
                previous,
                lineage,
                _text(payload.get("history_reason"), 180) or "更新 HATG",
                placement_request=payload,
                synchronize_resource_identities=(
                    synchronize_external_projections
                ),
                synchronize_resident_identities=(
                    synchronize_external_projections
                ),
            )

        with lineage_state._program_contract_authority(lineage_id, program_contract_impact):
            lineage_aggregate.lineage_catalog_store().state_store.transaction(
                commit_graph_authority
            )
            completion_baselines = lineage_state._execute_program_contract_cleanup(
                lineage_id,
                program_contract_impact,
                previous_lineage=previous,
                current_lineage=lineage,
                preserve_event_source_fire_budget_ids={
                    str(item)
                    for item in payload.get(
                        "preserve_event_source_fire_budget_ids",
                        [],
                    )
                    if str(item)
                },
            )
        response = _clone_json(lineage)
    lineage_state._establish_resident_completion_baselines(
        lineage_id,
        actor,
        completion_baselines,
    )
    return response


def delete_lineage_graph_objects(
    lineage_id: str,
    payload: dict[str, Any],
    actor: str,
    *,
    include_lineage: bool = True,
) -> dict[str, Any]:
    """Delete one graph selection, its identities and durable effects in one commit."""
    def selected_ids(field: str) -> list[str]:
        values = payload.get(field, [])
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not value or value != value.strip()
            for value in values
        ):
            raise ValueError(f"{field} must contain nonempty canonical IDs")
        if len(set(values)) != len(values):
            raise ValueError(f"{field} must contain unique IDs")
        return values

    resident_ids = selected_ids("resident_ids")
    component_ids = selected_ids("component_ids")
    if not resident_ids and not component_ids:
        raise ValueError("Graph deletion requires at least one object")
    for resident_id in resident_ids:
        identity_contract.validate_id(resident_id, "resident")

    completion_baselines: list[dict[str, Any]] = []
    with lineage_aggregate.lineage_scope(lineage_id):
        previous = lineage_aggregate.read_lineage(lineage_id, actor=actor)
        lineage_aggregate.ensure_manager(previous, actor)
        for record in resident_catalog.get_catalog().get_many(resident_ids):
            if record["lineage_id"] != lineage_id:
                raise ValueError("Project references cannot delete a child Lineage leader; manage it in its home Lineage")

        body = _clone_json(previous)
        if component_ids:
            lineage_program_authoring.remove_program_components(body, body["program"], set(component_ids))
        nodes, patch = _resident_projections_removal_patch(body, set(resident_ids))

        body.update(patch)
        removed_seed_count = len(
            lineage_program_authoring.program_seed_component_ids(previous.get("program"))
            - lineage_program_authoring.program_seed_component_ids(body.get("program"))
        )
        lineage = lineage_state._normalize_lineage_payload(body, previous, actor)
        program_contract_impact = _program_execution_contract_change(previous, lineage)

        def commit_identity_and_graph() -> None:
            if resident_ids:
                resident_domain.delete_resident_identities(lineage_id, resident_ids, actor)
            lineage_state._persist_lineage_state(previous, lineage, "批量删除 HATG 对象及 Resident 身份")

        with (
            lineage_state._program_contract_authority(lineage_id, program_contract_impact),
            invariant_locks.resident_locks(resident_ids, operation="lineage.objects.delete"),
        ):
            lineage_aggregate.lineage_catalog_store().state_store.transaction(commit_identity_and_graph)
            completion_baselines = lineage_state._execute_program_contract_cleanup(
                lineage_id,
                program_contract_impact,
                previous_lineage=previous,
                current_lineage=lineage,
            )

        old_edge_ids = {
            str(item.get("id") or "")
            for item in previous.get("edges") or []
            if isinstance(item, dict)
        }
        reconnected_count = sum(
            1 for item in lineage.get("edges") or []
            if isinstance(item, dict)
            and str(item.get("type") or "") == "inheritance"
            and str(item.get("id") or "") not in old_edge_ids
        )
        response = {
            "deleted_resident_ids": resident_ids,
            "leader_resident_id": str(lineage.get("leader_resident_id") or ""),
            "leader_changed": str(lineage.get("leader_resident_id") or "")
            != str(previous.get("leader_resident_id") or ""),
            "deleted_component_ids": sorted(
                {str(item["id"]) for item in previous["program"]["components"]}
                - {str(item["id"]) for item in lineage["program"]["components"]}
            ),
            "deleted_wire_ids": sorted(
                {str(item["id"]) for item in previous["program"]["wires"]}
                - {str(item["id"]) for item in lineage["program"]["wires"]}
            ),
            "reconnected_edge_count": reconnected_count,
            "removed_seed_count": removed_seed_count,
        }
        if include_lineage:
            response["lineage"] = lineage_state._verified_lineage_view(lineage, actor)
    lineage_state._establish_resident_completion_baselines(
        lineage_id,
        actor,
        completion_baselines,
    )
    return response
