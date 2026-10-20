"""Lineage definition instructions (decision_4637ca4a69e2)."""
from __future__ import annotations

import copy
import secrets
import time
from management_common.service_shutdown import wait_for_shutdown
from typing import Any, Iterator

from management_portal.hatg.contracts import InstructionError, invalid, not_found
from management_portal.hatg.kernel import Invocation

from .common import domain, lineage_revision, placement, store


STREAM_POLL_SECONDS = 1.0
STREAM_HEARTBEAT_SECONDS = 15.0
LINEAGE_CHANGE_FIELDS = frozenset({
    "title", "description", "project_id", "leader_resident_id", "run_as_principal", "fragments",
})


def create_lineage(inv: Invocation) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": inv.argument("title")}
    for name in (
        "description", "project_id", "leader_resident_id",
    ):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    definition = inv.argument("definition") or {}
    unknown = sorted(set(definition) - {"nodes", "edges", "program", "run_as_principal"})
    if unknown:
        raise invalid("definition 只包含 nodes、edges、program、run_as_principal", unknown_fields=unknown)
    payload.update(copy.deepcopy(definition))
    lineage = domain().create_lineage(payload, inv.caller)
    lineage_id = str(lineage.get("lineage_id") or "")
    result = {"lineage_id": lineage_id}
    if inv.argument("familize", False):
        from ..templates.familize import familize
        try:
            result["family_id"] = familize(lineage_id, [], inv.caller, scope="lineage",
                                           goal=payload.get("description") or "")["family_id"]
        except Exception as exc:
            raise InstructionError("instruction.failed", f"Lineage 已创建，但 Familize 未完成：{exc}",
                                   commit_state="committed", details={"lineage_id": lineage_id}) from exc
    return result




def get_lineage(inv: Invocation) -> dict[str, Any]:
    lineage = domain().get_lineage(inv.argument("lineage_id"), inv.caller)
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
        result = domain().update_lineage_metadata(
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
    result = domain().update_lineage_layout(inv.argument("lineage_id"), {"positions": inv.argument("positions")}, inv.caller)
    return {key: result[key] for key in ("applied", "missing_object_ids", "layout_revision", "layout_updated_at")}


def delete_lineage(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    result = domain().delete_lineage(lineage_id, inv.caller)
    from management_portal.avatar_store import scope_avatar_store, scope_subject

    scope_avatar_store().delete(scope_subject("lineage", lineage_id))
    return {
        "lineage_id": lineage_id,
        "resident_ids": list(result.get("deleted_resident_ids") or []),
    }


def list_component_types(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import lineage_primitive_definitions, lineage_program

    return {
        "component_types": lineage_primitive_definitions.component_type_catalog(),
        "event_types": list(lineage_primitive_definitions.type_catalog("event")),
        "component_kinds": list(lineage_program.PROGRAM_COMPONENT_KINDS),
    }


def _lineage_for_object(object_id: str, lineage_id: str = "") -> str:
    if lineage_id:
        return lineage_id
    found = store().lineage_id_for_object(object_id)
    if not found:
        raise not_found(f"对象不存在：{object_id}")
    return found


def create_component(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    payload: dict[str, Any] = {
        "kind": inv.argument("kind"),
        **placement(inv, lineage_id),
    }
    for name in ("title", "config", "client_key"):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    if inv.context.is_resident and inv.entry != "lineage":
        payload["created_by_principal_id"] = inv.caller
    result = domain().create_lineage_program_component(lineage_id, payload, inv.caller, full_view=False)
    component = result.get("component") or {}
    return {"component_id": str(component.get("id") or ""), "component": component}


EFFECT_FIELDS = ("script", "endpoints", "failure_recipients")


def update_component(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    component_id = inv.argument("component_id")
    changes = inv.argument("changes") or {}
    unknown = sorted(set(changes) - {"title", "client_key", "config", *EFFECT_FIELDS, "watch_subject"})
    if unknown:
        raise invalid("changes 不接受字段：" + "、".join(unknown), unknown_fields=unknown)
    component_patch: dict[str, Any] = {key: changes[key] for key in ("title", "client_key") if key in changes}
    effect_changes = {key: changes[key] for key in EFFECT_FIELDS if key in changes}
    if "config" in changes or effect_changes:
        config = copy.deepcopy(changes.get("config")) if isinstance(changes.get("config"), dict) else None
        if effect_changes and config is not None:
            config.update(copy.deepcopy(effect_changes))
        if config is not None or "config" in changes:
            component_patch["config"] = config
    payload: dict[str, Any] = {
        "component_id": component_id,
        "component": component_patch,
    }
    if effect_changes and "config" not in component_patch:
        payload["effect_changes"] = copy.deepcopy(effect_changes)
    if "watch_subject" in changes:
        payload["watch_subject"] = changes["watch_subject"]
    result = domain().save_lineage_program_component(lineage_id, payload, inv.caller, full_view=False)
    return {"component": result.get("component") or {}}


def delete_component(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    result = domain().delete_lineage_program_component(
        lineage_id,
        {"component_id": inv.argument("component_id")},
        inv.caller,
        full_view=False,
    )
    return {
        "component_id": inv.argument("component_id"),
        "wire_ids": list(result.get("deleted_wire_ids") or []),
    }


def create_wire(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    wire = {
        name: inv.argument(name)
        for name in ("source", "source_port", "target", "target_port", "kind", "config", "label", "client_key")
        if inv.argument(name) is not None
    }
    result = domain().save_lineage_program_wire(
        lineage_id, {"wire": wire}, inv.caller, operation="create", full_view=False,
    )
    return {"wire_id": str((result.get("wire") or {}).get("id") or ""), "wire": result.get("wire") or {}}


def update_wire(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    changes = inv.argument("changes") or {}
    unknown = sorted(set(changes) - {"source", "source_port", "target", "target_port", "kind", "config", "label", "client_key"})
    if unknown:
        raise invalid("changes 不接受字段：" + "、".join(unknown), unknown_fields=unknown)
    result = domain().save_lineage_program_wire(
        lineage_id,
        {"wire_id": inv.argument("wire_id"), "wire": dict(changes)},
        inv.caller,
        operation="update", full_view=False,
    )
    return {"wire": result.get("wire") or {}}


def delete_wire(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    domain().delete_lineage_program_wire(
        lineage_id, {"wire_id": inv.argument("wire_id")}, inv.caller, full_view=False,
    )
    return {"wire_id": inv.argument("wire_id")}


# -- candidate fork ----------------------------------------------------------------


def create_candidate_fork(inv: Invocation) -> dict[str, Any]:
    seed_id = inv.argument("seed_id")
    result = domain().set_candidate_fork(
        inv.argument("lineage_id"), seed_id, inv.argument("source_resident_id"), inv.caller,
    )
    return {"edge_id": str((result.get("edge") or {}).get("id") or ""),
            "replaced_edge_id": str(result.get("replaced_edge_id") or "")}


def delete_candidate_fork(inv: Invocation) -> dict[str, Any]:
    seed_id = inv.argument("seed_id")
    try:
        edge_id = domain().delete_candidate_fork(inv.argument("lineage_id"), seed_id, inv.caller)
    except KeyError as exc:
        raise not_found(str(exc.args[0] if exc.args else exc)) from exc
    return {"edge_id": edge_id}


# -- workspace handoff -----------------------------------------------------------


def _create_handoff(inv: Invocation, source: str, target: str) -> tuple[str, bool]:
    lineage_id = inv.argument("lineage_id")
    payload: dict[str, Any] = {
        "source_resident_id": source,
        "target_resident_id": target,
    }
    for name in ("items", "explanation", "label"):
        if inv.argument(name) is not None:
            payload[name] = inv.argument(name)
    result = domain().create_handoff(lineage_id, payload, inv.caller, full_view=False, idempotent=True)
    return str((result.get("handoff") or {}).get("id") or ""), bool(result.get("created"))


def create_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id, created = _create_handoff(
        inv, inv.argument("source_resident_id"), inv.argument("target_resident_id"),
    )
    return {"handoff_id": handoff_id, "created": created}


def update_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = inv.argument("lineage_id")
    result = domain().update_handoff(lineage_id, handoff_id, inv.argument("changes") or {}, inv.caller, full_view=False)
    return {"handoff": result.get("handoff") or {}}


def delete_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = inv.argument("lineage_id")
    domain().delete_handoff(lineage_id, handoff_id, inv.caller, full_view=False)
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
    if not handoff_id:
        handoff_id, _created = _create_handoff(
            inv, inv.argument("source_resident_id"), inv.argument("target_resident_id"),
        )
    lineage_id = inv.argument("lineage_id")
    try:
        result = domain().execute_handoff(lineage_id, handoff_id, inv.caller)
    except Exception:
        # A failed preparation is recorded on the handoff with each item's
        # reason; that recorded state is this instruction's result.
        handoff = domain().get_handoff(lineage_id, handoff_id, inv.caller).get("handoff") or {}
        if handoff.get("status") != "failed":
            raise
        return _handoff_result(handoff)
    return _handoff_result(result.get("handoff") or {})


def get_workspace_handoff(inv: Invocation) -> dict[str, Any]:
    handoff_id = inv.argument("handoff_id")
    lineage_id = _lineage_for_object(handoff_id)
    handoff = domain().get_handoff(lineage_id, handoff_id, inv.caller, full_view=False)["handoff"]
    return {"handoff": handoff, **_handoff_result(handoff), "status": str(handoff.get("status") or "draft")}


# -- compile, diagnose, batch -------------------------------------------------------


def get_lineage_diagnostics(inv: Invocation) -> dict[str, Any]:
    from management_portal.society_service import lineage_program
    from ..lineage_program_runtime_projection import (
        _program_event_source_runtime_view, event_source_diagnostics,
    )

    lineage = domain().get_lineage_definition_for_analysis(inv.argument("lineage_id"), inv.caller)
    plan = lineage_program.compile_program(lineage.get("program"), lineage.get("nodes") or [])
    diagnostics = lineage_program.program_diagnostics(
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


def apply_lineage_changes(inv: Invocation) -> dict[str, Any]:
    lineage_id = inv.argument("lineage_id")
    program, residents, refs = translate_changes(inv.argument("operations") or [])
    payload: dict[str, Any] = {
        "program_operations": program,
        "resident_ids": residents,
        **placement(inv, lineage_id),
    }
    result = domain().apply_lineage_changes(lineage_id, payload, inv.caller, include_lineage=False)
    return {
        "created_ids": result.get("created_ids") or [],
        "updated_ids": result.get("updated_ids") or [],
        "deleted_ids": result.get("deleted_ids") or [],
        "refs": refs,
    }


def watch_lineage(inv: Invocation) -> Iterator[list[dict[str, Any]]]:
    lineage_id = inv.argument("lineage_id")
    # Ownership of the stream depends only on the Lineage identity projection.
    domain()._ensure_visible(domain().lineage_access_projection(lineage_id), inv.caller)
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
    "CreateLineage": create_lineage,
    "GetLineage": get_lineage,
    "UpdateLineage": update_lineage,
    "UpdateLineageLayout": update_lineage_layout,
    "DeleteLineage": delete_lineage,
    "ListComponentTypes": list_component_types,
    "CreateComponent": create_component,
    "UpdateComponent": update_component,
    "DeleteComponent": delete_component,
    "CreateWire": create_wire,
    "UpdateWire": update_wire,
    "DeleteWire": delete_wire,
    "CreateCandidateFork": create_candidate_fork,
    "DeleteCandidateFork": delete_candidate_fork,
    "CreateWorkspaceHandoff": create_workspace_handoff,
    "UpdateWorkspaceHandoff": update_workspace_handoff,
    "DeleteWorkspaceHandoff": delete_workspace_handoff,
    "PrepareWorkspaceHandoff": prepare_workspace_handoff,
    "GetWorkspaceHandoff": get_workspace_handoff,
    "GetLineageDiagnostics": get_lineage_diagnostics,
    "ApplyLineageChanges": apply_lineage_changes,
}

STREAMS = {"WatchLineage": watch_lineage}
