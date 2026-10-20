"""Ownership of ordinary workflows inside a Family, independent of its roster machinery.

The graph remains executable in place. Local element keys distinguish adopted
workflows from generated Family structure; no component or delivery is recreated.
"""
from .function_block_domain import element_kinds


def is_workflow(element):
    return str(element.get("key") or "").startswith("workflow:")


def is_generated(element):
    return element["kind"] in {"component", "wire"} and not is_workflow(element)


def descendants(body, block):
    blocks = {b["id"]: b for b in body.get("function_blocks", [])}
    found, pending = set(), [block]
    while pending:
        for element in pending.pop().get("elements", []):
            eid = element["id"]
            if eid in found:
                continue
            found.add(eid)
            if eid in blocks:
                pending.append(blocks[eid])
    return found


def _ownership(body, block):
    owners = {e["id"]: b for b in body.get("function_blocks", []) for e in b["elements"]}
    ancestors, current = [], block["id"]
    while current in owners:
        parent = owners[current]
        ancestors.append(parent)
        current = parent["id"]
    return owners, ancestors


def _can_adopt(eid, owners, ancestors, block):
    owner = owners.get(eid)
    if owner is None:
        return True
    element = next(e for e in owner["elements"] if e["id"] == eid)
    return bool(owner.get("family") and is_workflow(element)
                and (owner["id"] == block["id"] or owner in ancestors))


def _adopt(body, block, ids, actor):
    kinds = element_kinds(body)
    owners, ancestors = _ownership(body, block)
    for eid in ids:
        if kinds.get(eid) not in {"component", "wire"}:
            raise ValueError("Family workflow elements must be components or wires in this Lineage: " + eid)
        if not _can_adopt(eid, owners, ancestors, block):
            raise ValueError("Element belongs to another block or generated Family structure: " + eid)
    for eid in sorted(ids):
        owner = owners.get(eid)
        if owner is block:
            continue
        if owner:
            owner["elements"] = [e for e in owner["elements"] if e["id"] != eid]
            owner["updated_by"] = actor
        block["elements"].append({"id": eid, "kind": kinds[eid], "key": "workflow:" + eid})


def update(body, block, actor, *, add=(), remove=(), collect=False):
    """Explicit ownership edits plus conservative topology-based collection.

    Automatic collection takes a connected group of ordinary components only
    when it has a Resident boundary wholly inside this Family. Shared or opaque
    workflows require an explicit owner decision. Other blocks are never raided.
    """
    if len(set(add)) != len(add) or len(set(remove)) != len(remove) or set(add) & set(remove):
        raise ValueError("Family workflow additions/removals must be unique and disjoint")
    current = {e["id"]: e for e in block["elements"]}
    if any(eid not in current or not is_workflow(current[eid]) for eid in remove):
        raise ValueError("Only directly owned Family workflow elements can be removed")
    owners, ancestors = _ownership(body, block)
    release = set(remove)
    # Wires with a removed endpoint leave the container with that endpoint.
    wires = body.get("program", {}).get("wires", [])
    release.update(w["id"] for w in wires if w["id"] in current and is_workflow(current[w["id"]])
                   and (w["source"] in release or w["target"] in release))
    released = [e for e in block["elements"] if e["id"] in release]
    block["elements"] = [e for e in block["elements"] if e["id"] not in release]
    if ancestors:
        ancestors[0]["elements"].extend(released)
        ancestors[0]["updated_by"] = actor
    _adopt(body, block, set(add), actor)

    owners, ancestors = _ownership(body, block)
    inside = descendants(body, block)
    residents = {n["id"] for n in body.get("nodes", [])}
    components = {c["id"] for c in body.get("program", {}).get("components", [])}
    eligible = {eid for eid in components if eid not in release and _can_adopt(eid, owners, ancestors, block)}
    if collect:
        adjacent = {eid: set() for eid in components}
        for wire in wires:
            source, target = wire["source"], wire["target"]
            if source in adjacent:
                adjacent[source].add(target)
            if target in adjacent:
                adjacent[target].add(source)
        pending, selected = set(eligible), set()
        while pending:
            stack, group, boundary = [pending.pop()], set(), set()
            while stack:
                eid = stack.pop()
                if eid in group:
                    continue
                group.add(eid)
                for neighbor in adjacent[eid]:
                    if neighbor in components:
                        if neighbor not in group:
                            stack.append(neighbor)
                    else:
                        boundary.add(neighbor)
            pending.difference_update(group)
            wires_available = all(_can_adopt(w["id"], owners, ancestors, block)
                                  for w in wires if w["source"] in group or w["target"] in group)
            if group <= eligible and boundary and boundary <= inside & residents and wires_available:
                selected.update(group)
        _adopt(body, block, selected, actor)
        inside = descendants(body, block)
    # Include all ordinary internal wires, including wires explicitly selected.
    # Explicit removal wins even when the endpoints remain inside.
    if not add and not collect:
        return
    for wire in wires:
        if wire["id"] in add and not {wire["source"], wire["target"]} <= inside:
            raise ValueError("Family workflow wires must have both endpoints inside the Family")
    internal = {w["id"] for w in wires if w["id"] not in release and w["source"] in inside
                and w["target"] in inside and _can_adopt(w["id"], owners, ancestors, block)}
    _adopt(body, block, internal, actor)
