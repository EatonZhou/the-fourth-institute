"""Bounds and ownership for editing one duty inside an atomic Lineage edit."""
from copy import deepcopy

from . import function_block_authoring as blocks


def program_items(body):
    program = body.get("program") or {}
    return {item["id"]: item for kind in ("components", "wires") for item in program.get(kind) or []}


def begin(body, block_id, operations, resident_ids):
    block = blocks.find_block(body, block_id)
    if not block.get("duty"):
        raise ValueError("Function Block is not a duty")
    if resident_ids:
        raise ValueError("Duty edits cannot delete Resident identities")
    owned = {item["id"] for item in block["elements"] if item["kind"] in {"component", "wire"}}
    known = blocks.element_kinds(body)
    created = set()
    for operation in operations:
        if operation["op"] != "add" and operation.get("id") not in owned | created:
            raise ValueError("Duty edit is outside its owned elements: " + str(operation.get("id")))
        if operation["op"] in {"add", "clone"}:
            new_id = operation.get("new_id") or operation.get("value", {}).get("id")
            if new_id in known or new_id in created:
                raise ValueError("Duty edit cannot replace an existing element")
            created.add(new_id)
    return deepcopy(block), program_items(body), owned


def finish(body, scope, changes, actor):
    old_block, before, owned = scope
    after = program_items(body)
    # Removing a component can cascade to wires. References owned elsewhere
    # must be detached explicitly by their owner, never deleted by this editor.
    if any(after.get(key) != value for key, value in before.items() if key not in owned):
        raise ValueError("Duty edit would change an external element or shared wire")
    block = blocks.find_block(body, old_block["id"])
    block["elements"] = [item for item in block["elements"] if item["id"] not in before or item["id"] in after]
    kinds = blocks.element_kinds(body)
    block["elements"].extend({"id": key, "kind": kinds[key], "key": None} for key in after.keys() - before.keys())
    changes = deepcopy(changes)
    if set(changes) - {"title", "description", "category", "prompt", "memory_level"}:
        raise ValueError("Unknown duty edit fields")
    if "title" in changes:
        title = changes.pop("title")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Duty title must not be empty")
        block["title"] = title.strip()[:140]
    updated = blocks.update_duty_in_body(body, block["id"], actor, **changes)
    return {"function_block": updated, "preview": {
        "elements": [{"id": key, "before": before.get(key), "after": after.get(key)}
                     for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)],
        "duty": {"before": old_block, "after": updated},
    }}
