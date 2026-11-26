"""Lineage definition instructions (decision_4637ca4a69e2)."""
from __future__ import annotations

import copy
import secrets
import time
from .. import effect_script_authoring
from management_common.service_shutdown import wait_for_shutdown
from typing import Any, Iterator, Mapping

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import Invocation

from .common import (
    lineage_branches,
    lineage_graph_edits,
    lineage_handoffs,
    lineage_lifecycle,
    lineage_reads,
    lineage_revision,
    lineage_state,
    placement,
    store,
)


STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0
LINEAGE_CHANGE_FIELDS = frozenset({
    "title", "description", "project_id", "leader_resident_id", "fragments",
})


def create_lineage(inv: Invocation) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": inv.argument("title")}
    for name in (
        "description", "project_id", "leader_resident_id",
    ):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    definition = inv.argument("definition") or {}
    unknown = sorted(set(definition) - {"nodes", "edges", "program"})
    if unknown:
        raise invalid("definition 只包含 nodes、edges、program", unknown_fields=unknown)
    payload.update(copy.deepcopy(definition))
    lineage = lineage_lifecycle().create_lineage(payload, inv.caller)
    return {"lineage_id": str(lineage.get("lineage_id") or "")}




def get_lineage(inv: Invocation) -> dict[str, Any]:
    lineage = lineage_reads().get_lineage(inv.argument("lineage_id"), inv.caller)
    return {"lineage": lineage}


def update_lineage(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    changes = dict(inv.argument("changes") or {})
    unknown = sorted(set(changes) - LINEAGE_CHANGE_FIELDS - {"avatar"})
    if unknown:
        raise invalid("changes 不接受字段：" + "、".join(unknown), unknown_fields=unknown)
    if not changes:
        raise invalid("changes 不能为空")
    has_avatar = "avatar" in changes
    avatar = changes.pop("avatar", None)
    metadata: dict[str, Any] = {"lineage_id": lineage_id}
    if changes:
        result = lineage_lifecycle().update_lineage_metadata(
            lineage_id, changes, inv.caller,
        )
        lineage = result.get("lineage") if isinstance(result.get("lineage"), dict) else result
        metadata = {key: lineage.get(key) for key in ("lineage_id", *sorted(LINEAGE_CHANGE_FIELDS)) if key in lineage}
    if has_avatar:
        # The avatar is kept beside the definition: it changes no revision.
        from management_portal.avatar_store import change_avatar, scope_avatar_store, scope_subject

        try:
            metadata["avatar"] = change_avatar(scope_avatar_store(), scope_subject("lineage", lineage_id), avatar, inv.caller)
        except ValueError as exc:
            raise invalid(str(exc)) from exc
    return {"lineage": metadata}


def update_lineage_layout(inv: Invocation) -> dict[str, Any]:
    result = lineage_lifecycle().update_lineage_layout(inv.argument("lineage_id"), {"positions": inv.argument("positions")}, inv.caller)
    return {key: result[key] for key in ("applied", "missing_object_ids", "layout_revision", "layout_updated_at")}


def delete_lineage(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    result = lineage_lifecycle().delete_lineage(lineage_id, inv.caller)
    from management_portal.avatar_store import scope_avatar_store, scope_subject

    scope_avatar_store().delete(scope_subject("lineage", lineage_id))
    return {
        "lineage_id": lineage_id,
        "resident_ids": list(result.get("deleted_resident_ids") or []),
    }


def list_component_types(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import (
        lineage_primitive_definitions, lineage_program_vocabulary,
    )

    return {
        "component_types": lineage_primitive_definitions.component_type_catalog(),
        "event_types": list(lineage_primitive_definitions.type_catalog("event")),
        "component_kinds": list(lineage_program_vocabulary.PROGRAM_COMPONENT_KINDS),
    }


def _lineage_for_object(object_id: str, lineage_id: str = "") -> str:
    if lineage_id:
        return lineage_id
    found = store().lineage_id_for_object(object_id)
    if not found:
        raise not_found(f"对象不存在：{object_id}")
    return found


# -- candidate fork ----------------------------------------------------------------


def create_candidate_fork(inv: Invocation) -> dict[str, Any]:
    seed_id = inv.argument("seed_id")
    result = lineage_branches().set_candidate_fork(
        inv.argument("lineage_id"), seed_id, inv.argument("source_resident_id"), inv.caller,
    )
    return {"edge_id": str((result.get("edge") or {}).get("id") or ""),
            "replaced_edge_id": str(result.get("replaced_edge_id") or "")}


def delete_candidate_fork(inv: Invocation) -> dict[str, Any]:
    seed_id = inv.argument("seed_id")
    try:
        edge_id = lineage_branches().delete_candidate_fork(inv.argument("lineage_id"), seed_id, inv.caller)
    except KeyError as exc:
        raise not_found(str(exc.args[0] if exc.args else exc)) from exc
    return {"edge_id": edge_id}


# -- workspace handoff -----------------------------------------------------------


def create_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    payload: dict[str, Any] = {
        "source_resident_id": inv.argument("source_resident_id"),
        "target_resident_id": inv.argument("target_resident_id"),
    }
    for name in ("items", "explanation", "label"):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    result = lineage_handoffs().create_handoff(lineage_id, payload, inv.caller, full_view=False, idempotent=True)
    return {"handoff_id": str((result.get("handoff") or {}).get("id") or ""),
            "created": bool(result.get("created"))}


def update_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = inv.argument("lineage_id")
    result = lineage_handoffs().update_handoff(lineage_id, handoff_id, inv.argument("changes") or {}, inv.caller, full_view=False)
    return {"handoff": result.get("handoff") or {}}


def delete_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = inv.argument("lineage_id")
    lineage_handoffs().delete_handoff(lineage_id, handoff_id, inv.caller, full_view=False)
    return {"handoff_id": handoff_id}


def _handoff_result(handoff: dict[str, Any]) -> dict[str, Any]:
    status = str(handoff.get("status") or "")
    public_status = "ready" if status in {"ready", "delivered", "completed"} else (
        "failed" if status == "failed" else "preparing"
    )
    return {
        "handoff_id": str(handoff.get("id") or ""),
        "status": public_status,
        "items": copy.deepcopy(handoff.get("items") or []),
        "error": str(handoff.get("error") or ""),
    }


def prepare_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = inv.argument("lineage_id")
    try:
        result = lineage_handoffs().execute_handoff(lineage_id, handoff_id, inv.caller)
    except Exception:
        # A failed preparation is recorded on the handoff with each item's
        # reason; that recorded state is this instruction's result.
        handoff = lineage_handoffs().get_handoff(lineage_id, handoff_id, inv.caller).get("handoff") or {}
        if handoff.get("status") != "failed":
            raise
        return _handoff_result(handoff)
    return _handoff_result(result.get("handoff") or {})


def get_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = _lineage_for_object(handoff_id)
    handoff = lineage_handoffs().get_handoff(lineage_id, handoff_id, inv.caller, full_view=False)["handoff"]
    return {"handoff": handoff, **_handoff_result(handoff), "status": str(handoff.get("status") or "draft")}


def relocate_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    from ..handoff_location_owner import relocate_registered_copy
    from management_portal.resources.errors import ResourceVersionConflict

    try:
        return relocate_registered_copy(
            inv.argument("lineage_id"), inv.argument("handoff_id"), inv.argument("item_id"), inv.caller,
            workspace_id=inv.argument("workspace_id"),
            expected_version=inv.argument("expected_workspace_version"), operation_id=inv.instruction_id,
        )
    except ResourceVersionConflict as error:
        raise InstructionError("instruction.version_conflict", str(error), details={
            "expected_version": error.expected_version, "current_version": error.current_version,
        }) from error


# -- compile, diagnose, batch -------------------------------------------------------


def get_lineage_diagnostics(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import (
        lineage_program_compilation, lineage_program_diagnostics,
    )
    from ..lineage_program_runtime_projection import (
        _program_event_source_runtime_view, event_source_diagnostics,
    )

    lineage = lineage_reads().get_lineage_definition_for_analysis(inv.argument("lineage_id"), inv.caller)
    plan = lineage_program_compilation.compile_program(lineage.get("program"), lineage.get("nodes") or [])
    diagnostics = lineage_program_diagnostics.program_diagnostics(
        lineage.get("program"), lineage.get("nodes") or [], _compiled_plan=plan,
    )
    diagnostics.extend(event_source_diagnostics(_program_event_source_runtime_view(lineage)))
    return {
        "diagnostics": diagnostics,
        "runnable": not any(str(item.get("level") or item.get("severity") or "") == "error" for item in diagnostics),
    }


_OPERATION_KINDS = {"component", "wire", "resident"}


def _substitute(value: Any, refs: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("$ref:"):
        name = value[5:]
        if name not in refs:
            raise invalid(f"引用了尚未创建的对象：{value}")
        return refs[name]
    if isinstance(value, dict):
        return {key: _substitute(item, refs) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, refs) for item in value]
    return value


def translate_changes(operations: list[Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, str]]:
    """Translate ISA operations into Program mutations and Resident deletions.

    Each operation is ``{"op": create|update|delete|clone, "kind": component|wire|resident, ...}``.
    ``create`` takes ``value`` (and an optional ``ref`` name); ``update`` takes
    ``id`` and ``changes``; ``delete`` and ``clone`` take ``id``.  Later
    operations reference objects created earlier with ``"$ref:<name>"``.
    """

    program: list[dict[str, Any]] = []
    residents: list[str] = []
    refs: dict[str, str] = {}
    for index, raw in enumerate(operations):
        if not isinstance(raw, dict):
            raise invalid(f"第 {index + 1} 项操作必须是对象")
        item = _substitute(raw, refs)
        op = str(item.get("op") or "")
        kind = str(item.get("kind") or "")
        if kind not in _OPERATION_KINDS:
            raise invalid(f"第 {index + 1} 项 kind 只能是 component、wire 或 resident")
        if kind == "resident":
            if op != "delete":
                raise invalid("Resident 只支持 delete；创建 Resident 使用 CreateResident 或 ForkResident")
            residents.append(str(item.get("id") or ""))
            continue
        if op == "create":
            value = copy.deepcopy(item.get("value") or {})
            if not isinstance(value, dict):
                raise invalid(f"第 {index + 1} 项 create 需要对象 value")
            if not value.get("id"):
                prefix = str(value.get("kind") or "component") if kind == "component" else "wire"
                value["id"] = f"{prefix}_{secrets.token_hex(6)}"
            if item.get("ref"):
                refs[str(item["ref"])] = value["id"]
            program.append({"op": "add", "kind": kind, "value": value})
        elif op == "update":
            changes = item.get("changes") or {}
            if not isinstance(changes, dict) or not changes:
                raise invalid(f"第 {index + 1} 项 update 需要非空 changes")
            for field, value in changes.items():
                program.append({"op": "set", "kind": kind, "id": item.get("id"), "field": field, "value": value})
        elif op == "delete":
            program.append({"op": "remove", "kind": kind, "id": item.get("id")})
        elif op == "clone":
            overrides = copy.deepcopy(item.get("value") or {})
            if not isinstance(overrides, dict):
                raise invalid(f"第 {index + 1} 项 clone 需要对象 value")
            source_id = str(item.get("id") or "")
            prefix = "wire" if kind == "wire" else (
                "event_source" if source_id.startswith("event_source_") else source_id.split("_", 1)[0]
            )
            new_id = overrides.pop("id", None) or f"{prefix}_{secrets.token_hex(6)}"
            clone = {"op": "clone", "kind": kind, "id": item.get("id"), "new_id": new_id, "overrides": overrides}
            if item.get("ref"):
                refs[str(item["ref"])] = new_id
            program.append(clone)
        else:
            raise invalid(f"第 {index + 1} 项 op 只能是 create、update、delete 或 clone")
    return program, residents, refs


def _operation_permission_checks(
    instruction: str, arguments: Mapping[str, Any], requirements: tuple[tuple[str, str], ...],
) -> list[tuple[str, str]]:
    lineage_id = arguments["lineage_id"]
    operations = arguments["operations"]
    # A one-operation replacement keeps the old operation's exact grant. The
    # original batch grant still applies to a composition, clone or duty edit scope.
    count = len(operations) + bool(arguments.get("duty_changes"))
    batch = count != 1 or any(item["op"] == "clone" for item in operations)
    scoped_graph_edit = bool(operations and arguments.get("function_block_id"))
    checks = [(f"{instruction}:lineage_id", lineage_id)] if batch or scoped_graph_edit else []
    for operation, target in requirements:
        if operation == "delete_resident":
            parameter = "lineage_id" if target == lineage_id else "resident_id"
            operation = f"delete_resident.{parameter}"
        checks.append((f"{instruction}:{operation}", target))
    return checks


def lineage_changes_permission_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    from management_portal.hatg.catalog import declaration
    from ..lineage_graph_edits import _lineage_change_requirements
    from ..lineage_program_graph_mutation import normalize_graph_mutations

    declaration("ApplyLineageChanges").validate_arguments(arguments)
    program, residents, _ = translate_changes(arguments["operations"])
    requirements = _lineage_change_requirements(
        arguments["lineage_id"], normalize_graph_mutations(program), residents, arguments.get("duty_changes") or {},
    )
    checks = _operation_permission_checks("ApplyLineageChanges", arguments, requirements)
    return {**arguments, "_permission_operations": [
        {"element": element, "resource_id": target} for element, target in checks
    ]}


def describe_lineage_change_permissions(arguments: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
    """Derive checks from the validated request without loading graph state."""
    from management_common.principal import actor_principal

    # The authenticated Resource invocation controls access to the inspection.
    # All identifiers returned here come from that request, including refs;
    # no subject identity or stored graph facts enter this calculation.
    actor_principal(actor)
    return lineage_changes_permission_arguments(arguments)


def apply_lineage_changes(inv: Invocation, *, preview: bool = False) -> dict[str, Any]:
    from management_portal import permission_system

    lineage_id = inv.argument("lineage_id")
    if preview and isinstance(inv.expected_version, dict) and set(inv.expected_version) != {lineage_id}:
        raise invalid("expected_version 映射的键必须是本次预览的 Lineage ID")
    if inv.argument("function_block_id") and inv.expected_version_for(lineage_id) is None:
        raise invalid("职责实例编辑必须携带 GetLineage revision 作为 expected_version")
    program, residents, refs = translate_changes(inv.argument("operations") or [])
    payload: dict[str, Any] = {
        "program_operations": program,
        "resident_ids": residents,
        "function_block_id": inv.argument("function_block_id"),
        "duty_changes": inv.argument("duty_changes"),
        "dry_run": preview,
        "expected_revision": inv.expected_version_for(lineage_id),
        **placement(inv, lineage_id),
    }
    def authorize(requirements: tuple[tuple[str, str], ...]) -> None:
        checks = _operation_permission_checks(inv.declaration.name, inv.call.arguments, requirements)
        if not permission_system.allows_elements(inv.caller, checks):
            raise InstructionError("instruction.forbidden", "缺少图或职责操作的权限")

    result = lineage_graph_edits().apply_lineage_changes(
        lineage_id, payload, inv.caller, include_lineage=False, authorize=authorize,
        creator_resident_id=inv.caller if inv.context.is_resident and inv.entry != "lineage" else "",
    )
    return {
        "created_ids": result.get("created_ids") or [],
        "updated_ids": result.get("updated_ids") or [],
        "deleted_ids": result.get("deleted_ids") or [],
        "refs": refs,
        "revision": result["revision"],
        **{key: result[key] for key in ("function_block", "preview") if key in result},
    }


def watch_lineage(inv: Invocation) -> Iterator[list[dict[str, Any]]]:
    lineage_id = inv.argument("lineage_id")
    # Ownership of the stream depends only on the Lineage identity projection.
    lineage_state()._ensure_visible(lineage_state().lineage_access_projection(lineage_id), inv.caller)
    cursor = inv.argument("after_cursor")
    if cursor is None:
        cursor = lineage_revision(lineage_id)
    cursor = int(cursor)
    last_yield = time.monotonic()
    while True:
        try:
            latest = lineage_revision(lineage_id)
        except InstructionError:
            return
        if latest != cursor:
            cursor = latest
            last_yield = time.monotonic()
            yield [{"cursor": cursor, "lineage_id": lineage_id}]
            continue
        if time.monotonic() - last_yield >= STREAM_HEARTBEAT_SECONDS:
            last_yield = time.monotonic()
            yield []
        if wait_for_shutdown(STREAM_POLL_SECONDS):
            return


HANDLERS = {
    "ConfirmComponentScript": lambda inv: effect_script_authoring.review(inv, decision="confirm"),
    "CancelComponentScriptDraft": lambda inv: effect_script_authoring.review(inv, decision="cancel"),
    "CreateLineage": create_lineage,
    "GetLineage": get_lineage,
    "UpdateLineage": update_lineage,
    "UpdateLineageLayout": update_lineage_layout,
    "DeleteLineage": delete_lineage,
    "ListComponentTypes": list_component_types,
    "CreateCandidateFork": create_candidate_fork,
    "DeleteCandidateFork": delete_candidate_fork,
    "CreateWorkspaceHandoff": create_workspace_handoff,
    "UpdateWorkspaceHandoff": update_workspace_handoff,
    "DeleteWorkspaceHandoff": delete_workspace_handoff,
    "RunWorkspaceHandoff": prepare_workspace_handoff,
    "GetWorkspaceHandoff": get_workspace_handoff,
    "MoveWorkspaceHandoff": relocate_workspace_handoff,
    "GetLineageDiagnostics": get_lineage_diagnostics,
    "ApplyLineageChanges": apply_lineage_changes,
    "GetLineageChangesPreview": lambda inv: apply_lineage_changes(inv, preview=True),
}

STREAMS = {"WatchLineage": watch_lineage}
