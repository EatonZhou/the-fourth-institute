"""Function Block boundaries and exclusive element ownership.

Templates may edit a definition and group the new elements in the same commit.
Executable components and wires remain ordinary graph objects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import secrets
from typing import Any

BLOCK_ID_RE = re.compile(r"^fbi_[0-9a-f]{12}$")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def element_kinds(body):
    program = body.get("program") or {}
    return {**{n["id"]: "resident" for n in body.get("nodes") or []},
            **{n["id"]: "component" for n in program.get("components") or []},
            **{n["id"]: "wire" for n in program.get("wires") or []},
            **{n["id"]: "function_block" for n in body.get("function_blocks") or []}}


def _find(body, function_block_id):
    for block in body.get("function_blocks") or []:
        if block["id"] == function_block_id:
            return block
    raise ValueError("Function Block does not belong to this Lineage")


def _coordinate(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Function Block coordinates must be finite numbers")
    return float(value)


def normalize_blocks(body, actor, *, previous=None):
    """Validate boundaries and prune memberships whose graph objects were removed."""
    kinds = element_kinds(body)
    before = {b["id"]: b for b in (previous or {}).get("function_blocks") or []}
    blocks, owners = {}, {}
    now = _now()
    for raw in body.get("function_blocks") or []:
        bid = raw.get("id", "")
        if not BLOCK_ID_RE.fullmatch(bid) or bid in blocks:
            raise ValueError("Function Block id must be unique and use fbi_<12hex>")
        unknown = set(raw) - {"id", "title", "template_id", "x", "y", "elements", "family",
                              "created_at", "created_by", "updated_at", "updated_by"}
        if unknown:
            raise ValueError("Unknown Function Block fields: " + ", ".join(sorted(unknown)))
        old = before.get(bid, {})
        block = {"id": bid, "title": str(raw.get("title") or "Function Block")[:140],
                 "template_id": raw.get("template_id") or None,
                 "x": _coordinate(raw.get("x")), "y": _coordinate(raw.get("y")),
                 "created_at": old.get("created_at") or raw.get("created_at") or now,
                 "created_by": old.get("created_by") or raw.get("created_by") or actor,
                 "updated_at": raw.get("updated_at") or now,
                 "updated_by": raw.get("updated_by") or actor, "elements": []}
        if raw.get("family") is not None:
            block["family"] = deepcopy(raw["family"])
            from .family_tasks import validate_routes
            validate_routes(body, raw)
        for element in raw.get("elements") or []:
            if set(element) - {"id", "kind", "key"}:
                raise ValueError("Unknown Function Block element fields")
            eid = element["id"]
            if eid not in kinds:
                continue
            if kinds[eid] != element.get("kind"):
                raise ValueError("Function Block element kind does not match graph")
            if eid in owners:
                raise ValueError("Element already belongs to a Function Block: " + eid)
            owners[eid] = bid
            block["elements"].append({"id": eid, "kind": kinds[eid], "key": element.get("key") or None})
        blocks[bid] = block
    visiting, visited = set(), set()

    def visit(bid):
        if bid in visiting:
            raise ValueError("Function Block nesting cycle")
        if bid in visited:
            return
        visiting.add(bid)
        for e in blocks[bid]["elements"]:
            if e["kind"] == "function_block":
                visit(e["id"])
        visiting.remove(bid)
        visited.add(bid)

    for bid in blocks:
        visit(bid)
    while True:
        empty = {bid for bid, b in blocks.items() if not b["elements"] and "family" not in b}
        if not empty:
            break
        blocks = {bid: b for bid, b in blocks.items() if bid not in empty}
        for block in blocks.values():
            block["elements"] = [e for e in block["elements"] if e["id"] not in empty]
    return list(blocks.values())


def _elements(body, element_ids, element_keys=None):
    kinds = element_kinds(body)
    ids = list(element_ids)
    if len(set(ids)) != len(ids):
        raise ValueError("element_ids must be unique")
    missing = set(ids) - kinds.keys()
    if missing:
        raise ValueError("Elements do not belong to this Lineage: " + ", ".join(sorted(missing)))
    return [{"id": eid, "kind": kinds[eid], "key": (element_keys or {}).get(eid)} for eid in ids]


def functionalize_in_body(body, element_ids, actor, *, title="", x=None, y=None,
                        template_id=None, element_keys=None, function_block_id=None):
    elements = _elements(body, element_ids, element_keys)
    if not elements:
        raise ValueError("Functionalize requires at least one element")
    bid = function_block_id or ("fbi_" + secrets.token_hex(6))
    now = _now()
    block = {"id": bid, "title": title or "Function Block", "template_id": template_id,
             "x": x, "y": y, "elements": elements, "created_at": now, "created_by": actor,
             "updated_at": now, "updated_by": actor}
    candidate = {**body, "function_blocks": [*(body.get("function_blocks") or []), block]}
    body["function_blocks"] = normalize_blocks(candidate, actor, previous=body)
    return _find(body, bid)


def update_in_body(body, function_block_id, actor, *, title=None, x=None, y=None,
                   add_element_ids=(), remove_element_ids=(), element_keys=None):
    candidate = deepcopy(body)
    block = _find(candidate, function_block_id)
    removed = set(remove_element_ids)
    current = {e["id"] for e in block["elements"]}
    if removed - current:
        raise ValueError("Removed elements must belong to this Function Block")
    block["elements"] = [e for e in block["elements"] if e["id"] not in removed]
    block["elements"].extend(_elements(candidate, add_element_ids, element_keys))
    for key, value in (("title", title), ("x", x), ("y", y)):
        if value is not None:
            block[key] = value
    block.update(updated_at=_now(), updated_by=actor)
    body["function_blocks"] = normalize_blocks(candidate, actor, previous=body)
    return next((b for b in body["function_blocks"] if b["id"] == function_block_id), None)


def unfunctionalize_in_body(body, function_block_id):
    block = _find(body, function_block_id)
    if "family" in block:
        raise ValueError("Family requires Unfamilize")
    elements = deepcopy(block["elements"])
    body["function_blocks"] = [b for b in body["function_blocks"] if b["id"] != function_block_id]
    for parent in body["function_blocks"]:
        if any(e["id"] == function_block_id for e in parent["elements"]):
            parent["elements"] = [e for e in parent["elements"] if e["id"] != function_block_id] + elements
    return [e["id"] for e in elements]


def functionalize(lineage_id, element_ids, actor, *, title="", x=None, y=None,
                template_id=None, element_keys=None, edit=None, function_block_id=None):
    from . import lineage_domain

    def apply(body):
        if edit:
            edit(body)
        return functionalize_in_body(body, element_ids, actor, title=title, x=x, y=y,
                                   template_id=template_id, element_keys=element_keys,
                                   function_block_id=function_block_id)
    _, block = lineage_domain.commit_lineage_edit(lineage_id, actor, apply, reason="Functionalize", full_view=False)
    return block


def update(lineage_id, function_block_id, actor, **changes):
    from . import lineage_domain

    def apply(body):
        if "family" in _find(body, function_block_id) and any(changes.get(k) for k in ("add_element_ids", "remove_element_ids")):
            raise ValueError("Family membership requires UpdateFamily")
        if "family" in _find(body, function_block_id) and changes.get("title") is not None:
            raise ValueError("Family title requires UpdateFamily")
        return update_in_body(body, function_block_id, actor, **changes)
    _, block = lineage_domain.commit_lineage_edit(lineage_id, actor, apply, reason="UpdateFunctionBlock", full_view=False)
    return block


def unfunctionalize(lineage_id, function_block_id, actor):
    from . import lineage_domain
    _, ids = lineage_domain.commit_lineage_edit(
        lineage_id, actor, lambda body: unfunctionalize_in_body(body, function_block_id),
        reason="Unfunctionalize", full_view=False)
    return ids


def deletion_scope(body, function_block_id):
    """The block and its recursively owned elements, including Residents."""
    ids, residents = set(), set()

    def collect(bid):
        block = _find(body, bid)
        ids.add(bid)
        for element in block["elements"]:
            ids.add(element["id"])
            if element["kind"] == "function_block":
                collect(element["id"])
            elif element["kind"] == "resident":
                residents.add(element["id"])
    collect(function_block_id)
    return ids, residents


def remove_elements_in_body(body, ids):
    from . import lineage_program_authoring

    body["function_blocks"] = [b for b in body["function_blocks"] if b["id"] not in ids]
    program = body["program"]
    component_ids = {c["id"] for c in program["components"] if c["id"] in ids}
    lineage_program_authoring.remove_program_components(body, program, component_ids)
    lineage_program_authoring.delete_program_objects_with_observer_dependents(
        program, wire_ids={w["id"] for w in program["wires"] if w["id"] in ids})
    body["edges"] = [e for e in body.get("edges") or [] if e["id"] not in ids and not {e["source"], e["target"]} & ids]


def delete(lineage_id, function_block_id, actor):
    from . import lineage_domain
    with lineage_domain._lineage_scope(lineage_id):
        previous = lineage_domain._read_lineage(lineage_id, actor=actor)
        ids, residents = deletion_scope(previous, function_block_id)
        if any("family" in b for b in previous["function_blocks"] if b["id"] in ids):
            raise ValueError("Family requires Unfamilize or DeleteFamily")
        lineage_domain.commit_lineage_edit(lineage_id, actor, lambda body: remove_elements_in_body(body, ids),
            reason="DeleteFunctionBlock", resident_ids=residents, full_view=False)
    return {"element_ids": sorted(ids - {function_block_id}), "resident_ids": sorted(residents)}
