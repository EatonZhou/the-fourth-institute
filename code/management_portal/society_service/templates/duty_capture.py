"""Capture a duty's program and memory, replacing external identities with slots."""
from copy import deepcopy

from .registry import TemplateError


def capture(lineage, block):
    nodes = {item["id"]: item for item in lineage.get("nodes", [])}
    components = {item["id"]: item for item in lineage["program"]["components"]}
    wires = {item["id"]: item for item in lineage["program"]["wires"]}
    items = {**nodes, **components, **wires}
    owned = {element["id"] for element in block["elements"]}
    if owned - components.keys() - wires.keys():
        raise TemplateError("含 Resident 或嵌套职责的模板请在角色／组织模板中整理")
    holder = block["duty"]["holder_id"]
    keys = {identity: f"item_{index}" for index, identity in enumerate(sorted(owned))}
    roles, matches = [], []
    if holder in nodes:
        keys[holder] = "target"
        roles.append({"key": "target", "from": "target"})

    def endpoint(identity):
        if identity in keys:
            return keys[identity]
        item = items.get(identity)
        if item is None:
            raise TemplateError("无法将这个外部对象转换为模板引用")
        key = keys[identity] = f"external_{len(matches)}"
        row = {"key": key, "kind": "resident" if identity in nodes else item["kind"],
               "op": "keep", "where": {"title": item["title"]} if item.get("title") else {}}
        matches.append(row)
        if identity in wires:
            row.update(source=endpoint(item["source"]), target=endpoint(item["target"]))
        return key

    copied_wires = []
    for identity in sorted(owned & wires.keys()):
        wire = wires[identity]
        copied_wires.append({"key": keys[identity], "kind": wire["kind"],
                             "source": endpoint(wire["source"]), "target": endpoint(wire["target"]),
                             **{field: deepcopy(wire[field]) for field in ("source_port", "target_port", "config", "label") if field in wire}})
    copied_components = [{"key": keys[identity], "kind": components[identity]["kind"],
                          "title": components[identity].get("title", ""), "config": deepcopy(components[identity].get("config", {}))}
                         for identity in sorted(owned & components.keys())]

    def references(value):
        if isinstance(value, dict):
            return {key: references(item) for key, item in value.items()}
        if isinstance(value, list):
            return [references(item) for item in value]
        if isinstance(value, str):
            for identity, key in keys.items():
                # Template-owned component IDs are resolved by endpoints; tokens
                # only name roles and matches, whose identities exist at binding.
                if identity not in owned:
                    namespace = "role" if identity == holder and roles else "match"
                    value = value.replace(identity, "{{" + namespace + "." + key + ".id}}")
        return value

    body = {"roles": roles, "components": references(copied_components), "wires": references(copied_wires),
            "match": matches, "function_block": True,
            "layout": {keys[identity]: {axis: items[identity][axis] for axis in ("x", "y")}
                       for identity in owned & components.keys() if all(isinstance(items[identity].get(axis), (int, float)) for axis in ("x", "y"))}}
    if roles:
        body["duty"] = references({key: deepcopy(value) for key, value in block["duty"].items() if key in {"description", "category", "memory"}})
    return {"kind": "structure", "category": "structure", "title": block["title"],
            "description": block["duty"]["description"], "parameters": [],
            "targets": ["resident"] if roles else ["canvas"], "body": body}
