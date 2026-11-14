"""Function Block boundaries and exclusive element ownership in an authored Lineage definition.

Templates may edit a definition and group the new elements in the same commit;
``function_block_domain`` commits these edits.  Executable components and wires
remain ordinary graph objects.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
import secrets


BLOCK_ID_RE = re.compile(r"^fbi_[0-9a-f]{12}$")


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def element_kinds(body):
    program = body.get("program") or {}
    return {**{n["id"]: "resident" for n in body.get("nodes") or []},
            **{n["id"]: "component" for n in program.get("components") or []},
            **{n["id"]: "wire" for n in program.get("wires") or []},
            **{n["id"]: "function_block" for n in body.get("function_blocks") or []}}


def find_block(body, function_block_id):
    for block in body.get("function_blocks") or []:
        if block["id"] == function_block_id:
            return block
    raise ValueError("Function Block does not belong to this Lineage")


# A duty is a Function Block held by a Resident or by an organization's block
# (decision_13725a7900f6): its record names the holder, one sentence saying what
# the duty does, its category and its place in the holder's order.
DUTY_CATEGORIES = ("fixed", "common", "custom")
DUTY_FIELDS = {"holder_id", "description", "category", "position", "parameters", "memory"}
# A duty's prompt sits in one of four memory levels: sent with the duty's own
# triggers, every turn, at session start and after compaction, or kept in a
# file the Resident reads when it needs it (decision_13725a7900f6).
MEMORY_LEVELS = ("trigger", "turn", "session", "on_demand")
MAX_DUTY_PROMPT_CHARS = 20000


def normalize_memory(raw):
    """``{"level", "prompt"}``, or None when there is no prompt."""
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"level", "prompt"}:
        raise ValueError("Duty memory has level and prompt")
    prompt = raw.get("prompt") or ""
    if not isinstance(prompt, str):
        raise ValueError("Duty prompt must be text")
    prompt = prompt.strip()
    if len(prompt) > MAX_DUTY_PROMPT_CHARS:
        raise ValueError(f"Duty prompt must be at most {MAX_DUTY_PROMPT_CHARS} characters")
    level = raw.get("level") or "trigger"
    if level not in MEMORY_LEVELS:
        raise ValueError("Duty memory level must be trigger, turn, session or on_demand")
    return {"level": level, "prompt": prompt} if prompt else None


def normalize_duty(raw):
    if not isinstance(raw, dict) or set(raw) - DUTY_FIELDS:
        raise ValueError("Unknown duty fields")
    holder = raw.get("holder_id")
    if not isinstance(holder, str) or not holder:
        raise ValueError("A duty needs holder_id")
    category = raw.get("category") or "custom"
    if category not in DUTY_CATEGORIES:
        raise ValueError("Duty category must be fixed, common or custom")
    position = raw.get("position", 0)
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise ValueError("Duty position must be a non-negative integer")
    parameters = raw.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("Duty parameters must be an object")
    description = raw.get("description") or ""
    if not isinstance(description, str):
        raise ValueError("Duty description must be text")
    duty = {"holder_id": holder, "description": " ".join(description.split())[:280],
            "category": category, "position": position, "parameters": deepcopy(parameters)}
    memory = normalize_memory(raw.get("memory"))
    if memory:
        duty["memory"] = memory
    return duty


def _check_memory(body, block):
    """A prompt belongs to a Resident's memory; a trigger-time prompt needs a trigger."""
    memory = block["duty"].get("memory")
    if not memory:
        return
    holder = block["duty"]["holder_id"]
    if element_kinds(body).get(holder) != "resident":
        raise ValueError("Only a duty held by a Resident carries a prompt")
    if memory["level"] == "trigger":
        wires = {w.get("id"): w for w in (body.get("program") or {}).get("wires") or []}
        if not any(
            e["kind"] == "wire" and wires.get(e["id"], {}).get("target") == holder
            and wires[e["id"]].get("kind") != "watch"
            for e in block.get("elements") or []
        ):
            raise ValueError("A trigger-time prompt needs a wire in the duty that triggers its holder")


def duties(body, holder_id):
    """The holder's duty blocks, first duty first."""
    return sorted(
        (b for b in body.get("function_blocks") or [] if (b.get("duty") or {}).get("holder_id") == holder_id),
        key=lambda b: (b["duty"]["position"], b.get("created_at") or "", b["id"]),
    )


def update_duty_in_body(body, function_block_id, actor, *, description=None, category=None,
                        prompt=None, memory_level=None):
    block = find_block(body, function_block_id)
    if not block.get("duty"):
        raise ValueError("Function Block is not a duty")
    duty = dict(block["duty"])
    if description is not None:
        duty["description"] = description
    if category is not None:
        duty["category"] = category
    if prompt is not None or memory_level is not None:
        memory = dict(duty.get("memory") or {})
        if prompt is not None:
            memory["prompt"] = prompt
        if memory_level is not None:
            memory["level"] = memory_level
        duty["memory"] = memory
    duty = normalize_duty(duty)
    if not duty.get("memory") and not block.get("elements"):
        raise ValueError("A duty without elements keeps its prompt; remove the duty instead")
    block["duty"] = duty
    _check_memory(body, block)
    block.update(updated_at=_now(), updated_by=actor)
    return deepcopy(block)


def create_duty_in_body(body, holder_id, actor, *, description, prompt, memory_level="session",
                        category="custom"):
    """A duty made of a prompt alone: a Function Block without elements."""
    held = duties(body, holder_id)
    bid = "fbi_" + secrets.token_hex(6)
    now = _now()
    duty = normalize_duty({"holder_id": holder_id, "description": description, "category": category,
                           "position": held[-1]["duty"]["position"] + 1 if held else 0,
                           "memory": {"level": memory_level, "prompt": prompt}})
    if not duty["description"]:
        raise ValueError("A duty needs a description")
    if not duty.get("memory"):
        raise ValueError("A duty without elements needs a prompt")
    block = {"id": bid, "title": duty["description"][:140], "template_id": None, "x": None, "y": None,
             "elements": [], "duty": duty, "created_at": now, "created_by": actor,
             "updated_at": now, "updated_by": actor}
    candidate = {**body, "function_blocks": [*(body.get("function_blocks") or []), block]}
    _check_memory(candidate, block)
    body["function_blocks"] = normalize_blocks(candidate, actor, previous=body)
    created = find_block(body, bid)
    if "duty" not in created:
        raise ValueError("A duty's holder must be a Resident in this Lineage")
    return deepcopy(created)


def order_duties_in_body(body, holder_id, function_block_ids, actor):
    """Renumber the holder's duties in the given order; every duty is listed once."""
    held = {b["id"]: b for b in duties(body, holder_id)}
    ids = list(function_block_ids)
    if len(set(ids)) != len(ids) or set(ids) != set(held):
        raise ValueError("function_block_ids must list each of the holder's duties exactly once")
    now = _now()
    for position, bid in enumerate(ids):
        block = held[bid]
        if block["duty"]["position"] != position:
            block["duty"] = {**block["duty"], "position": position}
            block.update(updated_at=now, updated_by=actor)
    return ids


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
        unknown = set(raw) - {"id", "title", "template_id", "x", "y", "elements", "family", "duty",
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
        if raw.get("duty") is not None:
            if "family" in block:
                raise ValueError("A Family is not a duty")
            duty = normalize_duty(raw["duty"])
            # A duty whose holder left the graph is an ordinary block again.
            if kinds.get(duty["holder_id"]) in {"resident", "function_block"} and duty["holder_id"] != bid:
                block["duty"] = duty
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
        # A Family and a duty holding a prompt stand without elements.
        empty = {bid for bid, b in blocks.items() if not b["elements"] and "family" not in b
                 and not (b.get("duty") or {}).get("memory")}
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
                        template_id=None, element_keys=None, function_block_id=None, duty=None):
    elements = _elements(body, element_ids, element_keys)
    if not elements:
        raise ValueError("Functionalize requires at least one element")
    bid = function_block_id or ("fbi_" + secrets.token_hex(6))
    now = _now()
    block = {"id": bid, "title": title or "Function Block", "template_id": template_id,
             "x": x, "y": y, "elements": elements, "created_at": now, "created_by": actor,
             "updated_at": now, "updated_by": actor}
    if duty is not None:
        # A new duty goes last in its holder's order.
        held = duties(body, duty.get("holder_id"))
        block["duty"] = {**duty, "position": held[-1]["duty"]["position"] + 1 if held else 0}
    candidate = {**body, "function_blocks": [*(body.get("function_blocks") or []), block]}
    body["function_blocks"] = normalize_blocks(candidate, actor, previous=body)
    created = find_block(body, bid)
    if duty is not None and "duty" not in created:
        raise ValueError("A duty's holder must be a Resident or Function Block in this Lineage")
    return created


def update_in_body(body, function_block_id, actor, *, title=None, x=None, y=None,
                   add_element_ids=(), remove_element_ids=(), element_keys=None):
    candidate = deepcopy(body)
    block = find_block(candidate, function_block_id)
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
    block = find_block(body, function_block_id)
    if "family" in block:
        raise ValueError("Family requires Unfamilize")
    elements = deepcopy(block["elements"])
    body["function_blocks"] = [b for b in body["function_blocks"] if b["id"] != function_block_id]
    for parent in body["function_blocks"]:
        if any(e["id"] == function_block_id for e in parent["elements"]):
            parent["elements"] = [e for e in parent["elements"] if e["id"] != function_block_id] + elements
    return [e["id"] for e in elements]


def deletion_scope(body, function_block_id):
    """The block and its recursively owned elements, including Residents."""
    ids, residents = set(), set()

    def collect(bid):
        block = find_block(body, bid)
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
