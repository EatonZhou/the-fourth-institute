"""Existing elements a structure template finds in the Lineage it is applied to.

Besides the roles, components and wires it creates, a structure body may list
`match` items: elements the Lineage already has. A node item is a Resident or a
component of one kind; a wire item connects the target or Leader role and other
found elements. Applying walks from the target and Leader along the listed wires,
binds every item to a real element, and keeps, deletes or updates it in the same
graph commit that adds the new elements. Elements inside a Function Block are
never found: an applied block is independent of every template.
"""

from __future__ import annotations

from copy import deepcopy

from .registry import TemplateError
from .values import NAME, match_references

ANCHORS = {"target", "lineage_leader"}
OPS = {"keep", "delete", "update"}
COUNTS = {"one", "all"}
# Watch wires carry no config a template could patch.
UPDATABLE_WIRES = {"route", "route", "target"}
MAX_BINDINGS = 200


def _wire_kinds():
    from ..lineage_program import PROGRAM_WIRE_KINDS

    return PROGRAM_WIRE_KINDS


def _node_kinds():
    from ..lineage_program import PROGRAM_COMPONENT_KINDS

    return PROGRAM_COMPONENT_KINDS | {"resident"}


def is_wire(item) -> bool:
    return item["kind"] in _wire_kinds()


def normalize(body, keys: set) -> None:
    """Validate `body["match"]` in place; `keys` holds every other local key."""
    items = body.setdefault("match", [])
    if not isinstance(items, list):
        raise TemplateError("match 必须是列表")
    for item in items:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("key"), str)
            or not NAME.fullmatch(item["key"])
            or item["key"] in keys
        ):
            raise TemplateError("结构元素需要唯一的局部 key")
        keys.add(item["key"])
    anchors = {role["key"] for role in body["roles"] if role["from"] in ANCHORS}
    found = {item["key"] for item in items}
    for item in items:
        key = item["key"]
        if item.get("kind") not in _node_kinds() | _wire_kinds():
            raise TemplateError(f"已有元素 {key} 的类型未知")
        item.setdefault("op", "keep")
        if item["op"] not in OPS:
            raise TemplateError(f"已有元素 {key} 的操作只能是保留、删除或修改")
        if item["kind"] == "resident" and item["op"] != "keep":
            raise TemplateError(f"已有 Resident {key} 只能保留")
        if item["op"] == "update" and is_wire(item) and item["kind"] not in UPDATABLE_WIRES:
            raise TemplateError(f"已有 {item['kind']} 连线 {key} 只能保留或删除")
        for field in ("where", "config"):
            if not isinstance(item.setdefault(field, {}), dict):
                raise TemplateError(f"已有元素 {key} 的 {field} 必须是对象")
        if match_references(item["where"]):
            raise TemplateError(f"已有元素 {key} 的匹配条件不能引用其他已有元素")
        if item["op"] == "update":
            update = item.get("update")
            if not isinstance(update, dict) or not isinstance(update.get("config"), dict) or not update["config"]:
                raise TemplateError(f"修改已有元素 {key} 需要 update.config")
            item["update"] = {"config": update["config"]}
        else:
            item.pop("update", None)
        if is_wire(item):
            item.setdefault("count", "one")
            if item["count"] not in COUNTS:
                raise TemplateError(f"已有连线 {key} 的数量只能是 one 或 all")
            for end in ("source", "target"):
                if item.get(end) not in anchors | found:
                    raise TemplateError(f"已有连线 {key} 只能连接目标、Leader 或其他已有元素")
            for port in ("source_port", "target_port"):
                if not isinstance(item.setdefault(port, ""), str):
                    raise TemplateError(f"已有连线 {key} 的端口必须是文本")
        else:
            for field in ("count", "source", "target", "source_port", "target_port"):
                item.pop(field, None)
    order(body)


def order(body) -> list[tuple[str, str]]:
    """(key, via) steps that bind each found element from one bound before it.

    A node or wire that ends a bound wire is fixed by that wire, so those steps
    come first; a wire leaving a bound element is searched among its wires.
    """
    items = {item["key"]: item for item in body.get("match", [])}
    if not items:
        return []
    bound = {role["key"] for role in body["roles"] if role["from"] in ANCHORS}
    if not bound:
        raise TemplateError("已有元素需要目标或 Lineage Leader 角色作为查找起点")
    steps = []
    while len(steps) < len(items):
        step = next(
            (
                (key, via)
                for via in (step[0] for step in steps)
                if is_wire(items[via])
                for key in (items[via]["source"], items[via]["target"])
                if key in items and key not in bound
            ),
            None,
        ) or next(
            (
                (key, end)
                for key, item in items.items()
                if key not in bound and is_wire(item)
                for end in (item["source"], item["target"])
                if end in bound
            ),
            None,
        )
        if step is None:
            missing = "、".join(key for key in items if key not in bound)
            raise TemplateError(f"已有元素 {missing} 需要通过已有连线连到目标或 Leader")
        steps.append(step)
        bound.add(step[0])
    return steps


def graph(lineage) -> dict:
    """The Lineage's Residents, components and wires, and what Function Blocks hold."""
    from ..lineage_program import normalize_program

    nodes = lineage.get("nodes") or []
    program = normalize_program(lineage.get("program"), lineage_nodes=nodes)
    elements = {
        node["id"]: {"id": node["id"], "kind": "resident", "title": node.get("title") or node["id"], "config": {}}
        for node in nodes
        if str(node.get("id") or "").startswith("resident_")
    }
    for item in program.get("components") or []:
        elements[item["id"]] = {
            "id": item["id"], "kind": item["kind"], "title": item.get("title") or item["id"],
            "config": item.get("config") or {},
        }
    wires = program.get("wires") or []
    for item in wires:
        elements[item["id"]] = {
            "id": item["id"], "kind": item["kind"], "label": item.get("label") or "",
            "config": item.get("config") or {}, "source": item["source"], "target": item["target"],
            "source_port": item.get("source_port") or "", "target_port": item.get("target_port") or "",
        }
    # An unlabeled wire is named by its ends, so a preview can tell two inputs apart.
    for item in wires:
        wire = elements[item["id"]]
        ends = [elements.get(end, {}).get("title") or end for end in (wire["source"], wire["target"])]
        wire["title"] = wire["label"] or " → ".join(ends)
    blocks = {item["id"]: item for item in lineage.get("function_blocks") or []}
    held = set()

    def hold(block_id):
        for element in blocks[block_id].get("elements") or []:
            if element["id"] not in held:
                held.add(element["id"])
                if element["id"] in blocks:
                    hold(element["id"])

    for block_id in blocks:
        held.add(block_id)
        hold(block_id)
    return {"elements": elements, "held": held}


def find(body, found_graph, anchors: dict, render, choices=None) -> list[dict]:
    """Every binding of the match items to real element IDs.

    `anchors` maps the target and Leader role keys to Resident IDs; `render` turns
    a where condition into concrete values; `choices` limits a key to some IDs.
    Each real element binds at most one key, and none is an anchor.
    """
    items = {item["key"]: item for item in body.get("match", [])}
    steps = order(body)
    if not steps:
        return [{}]
    elements, held = found_graph["elements"], found_graph["held"]
    choices = choices or {}
    wheres = {key: render(item["where"]) for key, item in items.items()}
    bindings = []
    deepest = [0]

    def fits(key, element):
        item = items[key]
        if element["id"] in held or element["kind"] != item["kind"]:
            return False
        if key in choices and element["id"] not in choices[key]:
            return False
        if is_wire(item) and any(item[port] and item[port] != element[port] for port in ("source_port", "target_port")):
            return False
        return all(element["config"].get(field) == value for field, value in wheres[key].items())

    def walk(index, binding, used):
        deepest[0] = max(deepest[0], index)
        if index == len(steps):
            bindings.append(dict(binding))
            if len(bindings) > MAX_BINDINGS:
                raise TemplateError("已有结构的匹配组合过多，请收紧匹配条件")
            return
        key, via = steps[index]
        item = items[key]
        if via in items and is_wire(items[via]) and key in (items[via]["source"], items[via]["target"]):
            wire = elements[binding[via]]
            candidates = [wire["source"] if items[via]["source"] == key else wire["target"]]
        else:
            ends = {end: anchors.get(item[end]) or binding.get(item[end]) for end in ("source", "target")}
            candidates = [
                identity for identity, element in elements.items()
                if "source" in element and all(bound in (None, element[end]) for end, bound in ends.items())
            ]
        for identity in candidates:
            element = elements.get(identity)
            if element is None or identity in used or not fits(key, element):
                continue
            binding[key] = identity
            used.add(identity)
            walk(index + 1, binding, used)
            del binding[key]
            used.discard(identity)

    walk(0, {}, set(anchors.values()))
    if not bindings:
        key = steps[min(deepest[0], len(steps) - 1)][0]
        raise TemplateError(f"目标附近没有找到模板描述的已有元素 {key}（{items[key]['kind']}）")
    return bindings


def ambiguous(body, bindings) -> list[dict]:
    """Wires listed as `one` that several elements satisfy, with every candidate."""
    items = {item["key"]: item for item in body.get("match", [])}
    steps = order(body)
    result = []
    for index, (key, _via) in enumerate(steps):
        if not is_wire(items[key]) or items[key]["count"] != "one":
            continue
        before = [step[0] for step in steps[:index]]
        groups = {}
        for binding in bindings:
            groups.setdefault(tuple(binding[name] for name in before), set()).add(binding[key])
        if any(len(group) > 1 for group in groups.values()):
            result.append({"key": key, "candidates": sorted(set().union(*groups.values()))})
    return result


def changes(body, found_graph, bindings, render) -> dict:
    """The deletions and config patches the bindings ask for.

    `render(value, binding)` resolves an update in one binding. Deleting a node
    removes its wires, and removing a wire removes the Watch wires observing it;
    none of them may belong to a Function Block.
    """
    items = {item["key"]: item for item in body.get("match", [])}
    elements = found_graph["elements"]
    deleted, updated, kept = [], {}, []
    for binding in bindings:
        for key, identity in binding.items():
            op = items[key]["op"]
            if op == "delete" and identity not in deleted:
                deleted.append(identity)
            elif op == "update":
                updated[identity] = render(items[key]["update"]["config"], binding)
            elif op == "keep" and identity not in kept:
                kept.append(identity)
    removed = set(deleted)
    grown = True
    while grown:
        grown = False
        for identity, element in elements.items():
            if "source" in element and identity not in removed and {element["source"], element["target"]} & removed:
                removed.add(identity)
                grown = True
    if removed & found_graph["held"]:
        raise TemplateError("删除这些已有元素会连带删除 Function Block 内的连线；模板不修改已施加的 Function Block")
    return {
        "deleted": deleted,
        "removed": sorted(removed),
        "updated": {identity: patch for identity, patch in updated.items() if identity not in removed},
        "kept": [identity for identity in kept if identity not in removed],
    }


def commit(current, planned) -> None:
    """Apply planned deletions and config patches to a Lineage definition."""
    program = current.setdefault("program", {})
    removed = set(planned["removed"])
    for collection in ("components", "wires"):
        kept = []
        for item in program.get(collection) or []:
            if item.get("id") in removed:
                continue
            patch = planned["updated"].get(item.get("id"))
            if patch:
                config = dict(item.get("config") or {})
                for field, value in patch.items():
                    if value is None:
                        config.pop(field, None)
                    else:
                        config[field] = deepcopy(value)
                item["config"] = config
            kept.append(item)
        program[collection] = kept


def dependencies(body) -> dict[str, tuple[str, ...]]:
    """Found elements each new component and wire is built from.

    A new element is created once for every distinct binding of the found keys it
    depends on: its endpoints, the element it copies config from, its
    {{match.*}} references, and those of the new components it connects.
    """
    found = {item["key"] for item in body.get("match", [])}
    result = {}
    for item in body["components"]:
        result[item["key"]] = match_references(item) | ({item["config_from"]} if item.get("config_from") else set())
    for item in body["wires"]:
        keys = match_references(item) | ({item["config_from"]} if item.get("config_from") else set())
        for end in (item["source"], item["target"]):
            keys |= {end} if end in found else result.get(end, set())
        result[item["key"]] = keys
    return {key: tuple(sorted(keys)) for key, keys in result.items()}


def projections(keys, bindings) -> list[tuple]:
    """Distinct (key, element ID) tuples the bindings give to `keys`, in binding order."""
    return list(dict.fromkeys(tuple((key, binding[key]) for key in keys) for binding in bindings))
