"""Build ordinary elements, then optionally group the newly created elements."""

from __future__ import annotations

import math
import secrets
from contextlib import contextmanager
from copy import deepcopy

from . import rewrite
from .registry import ApplyContext, TemplateError, result
from .values import NAME, match_references, render, render_script


def domain():
    from .. import lineage_domain

    return lineage_domain


def _info(lineage):
    return {
        item["id"]: {"id": item["id"], "title": item.get("title") or item["id"]}
        for item in lineage.get("nodes", [])
    }


def _render(value, ctx, roles, matches=None):
    return render(
        value,
        ctx.parameters,
        roles=roles,
        matches=matches,
        lineage_id=ctx.lineage_id,
        title=ctx.title,
    )


def _placeholder_roles(ctx, anchors=None):
    roles = {
        item["key"]: {"id": f"resident_{index:012x}", "title": item["key"]}
        for index, item in enumerate(ctx.template["body"]["roles"], start=1)
    }
    return {**roles, **(anchors or {})}


@contextmanager
def _created_roles(ctx):
    created = []
    try:
        yield created
    except Exception:
        if created:
            domain().commit_lineage_edit(
                ctx.lineage_id,
                ctx.actor,
                lambda body: None,
                reason="清理未能施加的模板 Resident",
                resident_ids=created,
            )
        raise


def _parts(ctx, roles, bindings=None, found=None):
    """New components and wires with fresh IDs.

    With `bindings` of found elements, an element built from found elements is
    made once per distinct binding of the keys it depends on. Without them, as
    when a template is checked before any Lineage, those elements are skipped.
    """
    body = ctx.template["body"]
    depends = rewrite.dependencies(body)
    elements = (found or {}).get("elements", {})
    components = []
    wires = []
    keys = {}
    made = {}

    def copies(definition):
        if bindings is None:
            return [] if depends[definition["key"]] else [()]
        return rewrite.projections(depends[definition["key"]], bindings)

    def build(definition, projection, skip):
        matches = {
            key: {"id": identity, "title": elements[identity]["title"]}
            for key, identity in projection
        }
        item = _render(
            {key: value for key, value in definition.items() if key not in skip},
            ctx,
            roles,
            matches,
        )
        if definition.get("config_from"):
            copied = elements[dict(projection)[definition["config_from"]]]["config"]
            item["config"] = {**deepcopy(copied), **item.get("config", {})}
        if item.get("kind") == "effect" and "script" in definition.get("config", {}):
            item["config"]["script"] = render_script(
                definition["config"]["script"],
                ctx.parameters,
                roles=roles,
                matches=matches,
                lineage_id=ctx.lineage_id,
                title=ctx.title,
            )
        return item

    for definition in body["components"]:
        for projection in copies(definition):
            identity = definition["kind"] + "_" + secrets.token_hex(6)
            item = build(definition, projection, {"key", "config_from"})
            item["id"] = identity
            made[(definition["key"], projection)] = identity
            keys[identity] = definition["key"]
            components.append(item)
    for definition in body["wires"]:
        for projection in copies(definition):
            bound = dict(projection)

            def endpoint(key, bound=bound):
                if key in roles:
                    return roles[key]["id"]
                if key in bound:
                    return bound[key]
                return made[(key, tuple((name, bound[name]) for name in depends[key]))]

            identity = "wire_" + secrets.token_hex(6)
            item = build(definition, projection, {"key", "source", "target", "config_from"})
            item.update(
                id=identity,
                source=endpoint(definition["source"]),
                target=endpoint(definition["target"]),
            )
            keys[identity] = definition["key"]
            wires.append(item)
    return components, wires, keys


def _preflight(ctx):
    from ..lineage_program import normalize_program

    roles = _placeholder_roles(ctx)
    components, wires, _ = _parts(ctx, roles)
    try:
        normalize_program(
            {"components": components, "wires": wires},
            lineage_nodes=list(roles.values()),
        )
    except ValueError as exc:
        raise TemplateError(str(exc)) from exc


def _fork_provider(role, chosen, source_provider):
    if any(
        requested and requested != source_provider
        for requested in (role.get("provider"), chosen.get("provider"))
    ):
        raise TemplateError("Fork Resident 继承来源 Provider；请选择相同 Provider")
    return source_provider


def _preflight_providers(ctx, targets, leader):
    from .. import resident_protocol

    definitions = ctx.template["body"]["roles"]
    if not any(role["from"] == "fork" for role in definitions):
        return
    residents = {}
    for target in targets:
        providers = {}
        for role in definitions:
            chosen = ctx.resident_settings.get(role["key"], {})
            if role["from"] in {"target", "lineage_leader"}:
                identity = target if role["from"] == "target" else leader
                if identity not in residents:
                    residents[identity] = resident_protocol.get_resident(
                        identity, ctx.actor
                    )["provider"]
                provider = residents[identity]
            elif role["from"] == "new":
                provider = chosen.get("provider") or role.get("provider") or "codex"
            else:
                provider = _fork_provider(role, chosen, providers[role["fork_of"]])
            providers[role["key"]] = provider


def create_role(ctx, role, roles, near=""):
    chosen = ctx.resident_settings.get(role["key"], {})
    if role["from"] == "fork":
        from .. import resident_protocol

        source = roles[role["fork_of"]]["id"]
        provider = _fork_provider(
            role, chosen, resident_protocol.get_resident(source, ctx.actor)["provider"]
        )
    else:
        provider = chosen.get("provider") or role.get("provider") or "codex"
    settings = {
        **(
            role.get("settings", {})
            if role["from"] == "fork" or provider == role.get("provider", "codex")
            else {}
        ),
        **chosen.get("settings", {}),
    }
    account = chosen.get("account") or role.get("account", "")
    title = str(_render(role.get("title") or ctx.title, ctx, roles))
    if role["from"] == "new":
        payload = {
            "title": title,
            "backend": provider,
            "runtime_settings": _render(settings, ctx, roles),
        }
        if near:
            payload["near_resident_id"] = near
        else:
            payload.update(
                {
                    axis: ctx.target[axis]
                    for axis in ("x", "y")
                    if ctx.target.get(axis) is not None
                }
            )
        if account:
            payload["backend_route_id"] = account
        created = domain().create_lineage_resident(ctx.lineage_id, payload, ctx.actor)
        resident_id = created["resident"]["resident_id"]
    else:
        payload = {
            "source": source,
            "fork_mode": role.get("fork_mode", "full"),
            "child_title": title,
        }
        if account:
            payload["backend_route_id"] = account
        created = domain().fork_lineage_resident(ctx.lineage_id, payload, ctx.actor)
        resident_id = created["created_resident"]["id"]
        if settings:
            try:
                resident_protocol.update_resident_runtime_settings(
                    resident_id,
                    ctx.actor,
                    {"runtime_settings": _render(settings, ctx, roles)},
                )
            except Exception:
                domain().commit_lineage_edit(
                    ctx.lineage_id,
                    ctx.actor,
                    lambda body: None,
                    reason="清理设置失败的模板 Fork",
                    resident_ids=[resident_id],
                )
                raise
    return {"id": resident_id, "title": title}


class StructureKind:
    key = "structure"
    title = "放入结构"
    targets = ("resident", "selection", "canvas")

    def normalize(self, template):
        from ..lineage_program import PROGRAM_COMPONENT_KINDS, PROGRAM_WIRE_KINDS

        body = template["body"]
        keys = set()
        role_keys = set()
        for collection in ("roles", "components", "wires"):
            body.setdefault(collection, [])
            if not isinstance(body[collection], list):
                raise TemplateError(f"{collection} 必须是列表")
            for item in body[collection]:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("key"), str)
                    or not NAME.fullmatch(item["key"])
                    or item["key"] in keys
                ):
                    raise TemplateError("结构元素需要唯一的局部 key")
                keys.add(item["key"])
                if collection == "roles":
                    if item.get("from") not in {
                        "target",
                        "lineage_leader",
                        "new",
                        "fork",
                    }:
                        raise TemplateError("未知角色来源")
                    if item["from"] == "fork" and item.get("fork_of") not in role_keys:
                        raise TemplateError("Fork 来源必须是前面已定义的角色")
                    if item.get("fork_mode", "full") not in {
                        "full",
                        "compacted",
                        "recent-5",
                        "recent-10",
                    }:
                        raise TemplateError("未知 Fork 模式")
                    if not isinstance(item.get("settings", {}), dict):
                        raise TemplateError("角色 settings 必须是对象")
                    role_keys.add(item["key"])
                elif item.get("kind") not in (
                    PROGRAM_COMPONENT_KINDS
                    if collection == "components"
                    else PROGRAM_WIRE_KINDS
                ):
                    raise TemplateError(f"未知 {collection} kind")
                if not isinstance(item.get("config", {}), dict):
                    raise TemplateError("config 必须是对象")
        rewrite.normalize(body, keys)
        if match_references(body["roles"]):
            raise TemplateError("角色不能引用已有元素")
        found = {item["key"]: item for item in body["match"]}
        kept = {key for key, item in found.items() if item["op"] != "delete"}
        endpoints = role_keys | {item["key"] for item in body["components"]} | kept
        for wire in body["wires"]:
            if (
                wire.get("source") not in endpoints
                or wire.get("target") not in endpoints
            ):
                raise TemplateError(f"连线 {wire['key']} 引用了不存在或将删除的角色、组件")
        for item in [*body["components"], *body["wires"]]:
            source = item.get("config_from")
            if source is None:
                continue
            if source not in kept or found[source]["kind"] != item["kind"]:
                raise TemplateError(f"{item['key']} 只能沿用同类型、未删除的已有元素的配置")
        if any(
            match_references(value) - kept
            for value in [
                *body["components"],
                *body["wires"],
                *(item.get("update", {}) for item in body["match"]),
            ]
        ):
            raise TemplateError("只能引用未删除的已有元素")
        endpoints |= {key for key, item in found.items() if not rewrite.is_wire(item)}
        # Where the graph editor draws each role and component; applying ignores it.
        if "layout" in body:
            if not isinstance(body["layout"], dict):
                raise TemplateError("layout 必须是对象")
            for point in body["layout"].values():
                if not isinstance(point, dict) or not all(
                    isinstance(point.get(axis), (int, float))
                    and not isinstance(point.get(axis), bool)
                    and math.isfinite(point[axis])
                    for axis in ("x", "y")
                ):
                    raise TemplateError("layout 坐标必须是数字")
            body["layout"] = {
                key: {"x": round(point["x"], 2), "y": round(point["y"], 2)}
                for key, point in body["layout"].items()
                if key in endpoints
            }
        body.setdefault("function_block", True)
        if not isinstance(body["function_block"], bool):
            raise TemplateError("function_block 必须是布尔值")
        samples = {
            spec["name"]: spec.get(
                "default",
                spec.get("options", [None])[0]
                if spec["type"] == "enum"
                else {
                    "integer": 1,
                    "number": 1,
                    "duration": 60,
                    "boolean": False,
                    "text": "",
                }.get(spec["type"], ""),
            )
            for spec in template["parameters"]
        }
        _preflight(
            ApplyContext(
                "lineage_000000000001", template, samples, {}, "", template["title"]
            )
        )
        return template

    def describe(self, template):
        body = template["body"]
        steps = []
        found = body.get("match", [])
        if found:
            steps.append(
                "从目标出发查找已有元素："
                + "、".join(
                    item["key"] + ("（全部）" if item.get("count") == "all" else "")
                    for item in found
                )
                + "。"
            )
        for item in found:
            label = item.get("title") or item["key"]
            if item.get("op") == "delete":
                steps.append(f"删除已有的 {item['kind']}：{label}。")
            elif item.get("op") == "update":
                fields = "、".join(item["update"]["config"])
                steps.append(f"修改已有的 {item['kind']}：{label}（{fields}）。")
        for role in body["roles"]:
            if role["from"] in {"new", "fork"}:
                steps.append(
                    ("新建" if role["from"] == "new" else f"从 {role['fork_of']} Fork")
                    + f" Resident：{role.get('title') or role['key']}。"
                )
        for component in body["components"]:
            config = component.get("config", {})
            event = config.get("event_type")
            if event == "time.reached":
                steps.append(
                    f"按周期 {config.get('interval_seconds', '')} 秒触发“{component.get('title', component['key'])}”。"
                )
            elif event == "manual.triggered":
                steps.append(
                    f"提供手动事件“{component.get('title', component['key'])}”。"
                )
            elif event:
                steps.append(f"收到 {event} 时触发后续连线。")
            elif component["kind"] == "loop":
                steps.append(f"最多推进 {config.get('max_rounds', '')} 轮。")
        for wire in body["wires"]:
            steps.append(f"{wire['source']} → {wire['target']}（{wire['kind']}）。")
        # A template that only changes existing elements leaves no block behind.
        creates = bool(
            body["components"]
            or body["wires"]
            or any(role["from"] in {"new", "fork"} for role in body["roles"])
        )
        if body["function_block"] and (creates or not found):
            steps.append("将新建元素封装为 Function Block，目标 Resident 保留在块外。")
        return {
            "steps": steps or ["放入指定结构。"],
            "persistent": creates or not found,
            "roles": [
                deepcopy(role)
                for role in body["roles"]
                if role["from"] in {"new", "fork"}
            ],
        }

    def editor(self):
        return {
            "fields": [
                {"name": "roles", "type": "array", "label": "角色"},
                {"name": "components", "type": "array", "label": "组件"},
                {"name": "wires", "type": "array", "label": "连线"},
                {"name": "match", "type": "array", "label": "已有元素"},
                {
                    "name": "function_block",
                    "type": "boolean",
                    "label": "封装为 Function Block",
                    "default": True,
                },
            ]
        }

    def _targets(self, ctx):
        body = ctx.template["body"]
        lineage = domain()._read_lineage(ctx.lineage_id, actor=ctx.actor)
        info = _info(lineage)
        needs_target = any(role["from"] == "target" for role in body["roles"])
        targets = ctx.target["resident_ids"] if needs_target else [None]
        if needs_target and not targets:
            raise TemplateError("结构需要目标 Resident")
        leader = lineage.get("leader_resident_id")
        if (
            any(role["from"] == "lineage_leader" for role in body["roles"])
            and leader not in info
        ):
            raise TemplateError("当前 Lineage 没有 Leader")
        known = {
            role["key"] for role in body["roles"] if role["from"] in {"new", "fork"}
        }
        if set(ctx.resident_settings) - known:
            raise TemplateError("resident_settings 引用了非新建角色")
        return lineage, info, targets, leader

    def _plans(self, ctx, lineage, info, targets, leader):
        """Find each target's existing elements before anything is created."""
        body = ctx.template["body"]
        found = rewrite.graph(lineage) if body["match"] else {"elements": {}, "held": set()}
        plans = {}
        for target in targets:
            anchors = {
                role["key"]: info[target if role["from"] == "target" else leader]
                for role in body["roles"]
                if role["from"] in rewrite.ANCHORS
            }
            bindings = _find(ctx, found, anchors)
            plans[target] = {
                "anchors": anchors,
                "graph": found,
                "bindings": bindings,
                "ambiguous": rewrite.ambiguous(body, bindings) if body["match"] else [],
                "changes": _changes(ctx, found, bindings, _placeholder_roles(ctx, anchors)),
            }
        return plans

    def preview(self, ctx):
        """What applying would find, delete, update and create, without changing anything."""
        body = ctx.template["body"]
        lineage, info, targets, leader = self._targets(ctx)
        _preflight(ctx)
        items = {item["key"]: item for item in body["match"]}
        entries = []
        for target in targets:
            entry = {"target_resident_id": target or "", "found": [], "removed": [], "updated": [],
                     "ambiguous": [], "created": {}, "error": ""}
            entries.append(entry)
            try:
                plan = self._plans(ctx, lineage, info, [target], leader)[target]
            except TemplateError as exc:
                entry["error"] = str(exc)
                continue
            elements = plan["graph"]["elements"]

            def describe(identity, elements=elements):
                element = elements[identity]
                return {"id": identity, "kind": element["kind"], "title": element["title"]}

            seen = set()
            for binding in plan["bindings"]:
                for key, identity in binding.items():
                    if identity not in seen:
                        seen.add(identity)
                        entry["found"].append({**describe(identity), "key": key, "op": items[key]["op"]})
            entry["removed"] = [describe(identity) for identity in plan["changes"]["removed"]]
            entry["updated"] = [describe(identity) for identity in plan["changes"]["updated"]]
            entry["ambiguous"] = [
                {"key": item["key"], "candidates": [describe(identity) for identity in item["candidates"]]}
                for item in plan["ambiguous"]
            ]
            depends = rewrite.dependencies(body)
            entry["created"] = {
                "roles": sum(role["from"] in {"new", "fork"} for role in body["roles"]),
                **{
                    collection: sum(
                        len(rewrite.projections(depends[item["key"]], plan["bindings"]))
                        for item in body[collection]
                    )
                    for collection in ("components", "wires")
                },
            }
        return result("预览施加结果。", preview=entries)

    def apply(self, ctx):
        from .. import function_block_domain, lineage_placement

        body = ctx.template["body"]
        lineage, info, targets, leader = self._targets(ctx)
        _preflight(ctx)
        _preflight_providers(ctx, targets, leader)
        plans = self._plans(ctx, lineage, info, targets, leader)
        for plan in plans.values():
            for item in plan["ambiguous"]:
                raise TemplateError(
                    f"已有连线 {item['key']} 有 {len(item['candidates'])} 个候选，请选择其中一个"
                )
        for target, plan in plans.items():
            touched = {identity for binding in plan["bindings"] for identity in binding.values()}
            for other, changed in plans.items():
                if other != target and touched & {*changed["changes"]["removed"], *changed["changes"]["updated"]}:
                    raise TemplateError("多个目标的改写涉及同一个已有元素，请分别施加")
        outcome = result(deleted_ids=[], updated_ids=[])
        for target in targets:
            plan = plans[target]
            roles = dict(plan["anchors"])
            keys = {}
            with _created_roles(ctx) as created:
                for role in body["roles"]:
                    key = role["key"]
                    if role["from"] not in rewrite.ANCHORS:
                        roles[key] = create_role(ctx, role, roles, target or "")
                        created.append(roles[key]["id"])
                        keys[roles[key]["id"]] = key
                components, wires, element_keys = _parts(ctx, roles, plan["bindings"], plan["graph"])
                keys.update(element_keys)
                changed = _changes(ctx, plan["graph"], plan["bindings"], roles)

                def edit(current, components=components, wires=wires, target=target, plan=plan, changed=changed):
                    if body["match"]:
                        # The graph may have changed since the elements were found;
                        # apply only to exactly what was found.
                        again = _find(ctx, rewrite.graph(current), plan["anchors"])
                        if _canonical(again) != _canonical(plan["bindings"]):
                            raise TemplateError("已有结构在施加时发生了变化，请重新施加")
                        rewrite.commit(current, changed)
                    current.setdefault("program", {}).setdefault(
                        "components", []
                    ).extend(deepcopy(components))
                    current["program"].setdefault("wires", []).extend(deepcopy(wires))
                    request = {
                        axis: ctx.target[axis]
                        for axis in ("x", "y")
                        if ctx.target.get(axis) is not None
                    }
                    if target:
                        request["near_resident_id"] = target
                    lineage_placement.place_new_components(lineage, current, request)

                ids = [
                    *created,
                    *(item["id"] for item in components),
                    *(item["id"] for item in wires),
                ]
                if body["function_block"] and ids:
                    block = function_block_domain.functionalize(
                        ctx.lineage_id,
                        ids,
                        ctx.actor,
                        title=ctx.title,
                        template_id=ctx.template["template_id"],
                        element_keys=keys,
                        edit=edit,
                        x=ctx.target.get("x"),
                        y=ctx.target.get("y"),
                    )
                    outcome["function_block_ids"].append(block["id"])
                else:
                    domain().commit_lineage_edit(
                        ctx.lineage_id, ctx.actor, edit, reason="施加结构模板"
                    )
                outcome["created"]["resident_ids"].extend(created)
                outcome["created"]["component_ids"].extend(
                    item["id"] for item in components
                )
                outcome["created"]["wire_ids"].extend(item["id"] for item in wires)
                outcome["deleted_ids"].extend(changed["removed"])
                outcome["updated_ids"].extend(changed["updated"])
        outcome["summary"] = (
            f"已施加 {ctx.template['title']}，生成 {len(outcome['function_block_ids'])} 个 Function Block。"
        )
        if outcome["deleted_ids"] or outcome["updated_ids"]:
            outcome["summary"] += (
                f"删除 {len(outcome['deleted_ids'])} 个、修改 {len(outcome['updated_ids'])} 个已有元素。"
            )
        return outcome


def _find(ctx, found, anchors):
    return rewrite.find(
        ctx.template["body"],
        found,
        {key: role["id"] for key, role in anchors.items()},
        lambda value: _render(value, ctx, anchors),
        ctx.match_choices,
    )


def _changes(ctx, found, bindings, roles):
    elements = found["elements"]

    def render_update(value, binding):
        matches = {
            key: {"id": identity, "title": elements[identity]["title"]}
            for key, identity in binding.items()
        }
        return _render(value, ctx, roles, matches)

    return rewrite.changes(ctx.template["body"], found, bindings, render_update)


def _canonical(bindings):
    return sorted(tuple(sorted(binding.items())) for binding in bindings)


def _layout(lineage, keys):
    nodes = {item["id"]: item for item in lineage.get("nodes", [])}
    components = {item["id"]: item for item in lineage.get("program", {}).get("components", [])}
    return {
        keys[identity]: {"x": item["x"], "y": item["y"]}
        for identity, item in {**nodes, **components}.items()
        if identity in keys
        and all(
            isinstance(item.get(axis), (int, float))
            and not isinstance(item.get(axis), bool)
            for axis in ("x", "y")
        )
    }


def _replace_ids(value, keys):
    """Turn IDs of captured elements inside configs into template references."""
    if isinstance(value, dict):
        return {key: _replace_ids(item, keys) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_ids(item, keys) for item in value]
    if isinstance(value, str):
        for identity, reference in keys.items():
            value = value.replace(identity, reference)
    return value


def _rewrite_from_selection(lineage, element_ids):
    """The selection becomes existing structure the template finds when applied.

    The first selected Resident becomes the target role and another selected
    Leader the Leader role; every other selected element, the wires between them
    and the ends of selected wires become found elements.
    """
    found = rewrite.graph(lineage)
    elements = found["elements"]
    selected = list(dict.fromkeys(element_ids))
    if set(selected) - set(elements) - found["held"]:
        raise TemplateError("选区包含不存在的元素")
    if set(selected) & found["held"]:
        raise TemplateError("Function Block 及其中的元素不能作为改写模板的已有结构")
    chosen = set(selected)
    chosen.update(
        identity for identity, element in elements.items()
        if "source" in element and identity not in found["held"]
        and element["source"] in chosen and element["target"] in chosen
    )
    grown = True
    while grown:
        ends = {
            end for identity in chosen if "source" in elements[identity]
            for end in (elements[identity]["source"], elements[identity]["target"])
        }
        grown = bool(ends - chosen)
        if ends & found["held"]:
            raise TemplateError("选中的连线连到了 Function Block 内的元素")
        if ends - set(elements):
            raise TemplateError("选中的连线连到的对象不能作为已有元素")
        chosen |= ends
    ordered = [*selected, *sorted(chosen - set(selected))]
    residents = [identity for identity in ordered if elements[identity]["kind"] == "resident"]
    if not residents:
        raise TemplateError("改写模板需要在选区中包含一个 Resident 作为施加目标")
    target = residents[0]
    leader = lineage.get("leader_resident_id")
    roles = [{"key": "target", "from": "target", "description": elements[target]["title"]}]
    keys = {target: "target"}
    if leader in chosen and leader != target:
        roles.append({"key": "leader", "from": "lineage_leader"})
        keys[leader] = "leader"
    match = []
    counters = {}
    for identity in ordered:
        element = elements[identity]
        if identity in keys or "source" in element:
            continue
        prefix = {"event_source": "event"}.get(element["kind"], element["kind"])
        counters[prefix] = counters.get(prefix, 0) + 1
        keys[identity] = f"{prefix}_{counters[prefix]}"
        item = {"key": keys[identity], "kind": element["kind"], "title": element["title"]}
        if element["kind"] != "resident":
            item["config"] = deepcopy(element["config"])
        if element["kind"] == "event_source" and element["config"].get("event_type"):
            item["where"] = {"event_type": element["config"]["event_type"]}
        match.append(item)
    wires = [identity for identity in ordered if "source" in elements[identity]]
    for index, identity in enumerate(wires, start=1):
        keys[identity] = f"wire_{index}"
    for identity in wires:
        element = elements[identity]
        match.append({
            "key": keys[identity], "kind": element["kind"], "title": element["label"],
            "source": keys[element["source"]], "target": keys[element["target"]],
            "source_port": element["source_port"],
            **({"target_port": element["target_port"]} if element["target_port"] else {}),
            "config": deepcopy(element["config"]), "count": "one",
        })
    references = {
        identity: "{{" + ("role" if key in {"target", "leader"} else "match") + f".{key}.id}}}}"
        for identity, key in keys.items()
    }
    for item in match:
        item["config"] = _replace_ids(item.get("config", {}), references)
    return {
        "kind": "structure",
        "category": "structure",
        "title": "改写模板",
        "description": "",
        "targets": ["resident", "selection"],
        "parameters": [],
        "body": {
            "roles": roles,
            "components": [],
            "wires": [],
            "match": match,
            "layout": _layout(lineage, keys),
            "function_block": True,
        },
    }


def from_selection(lineage_id, element_ids, actor, existing=False):
    from management_portal.hatg.catalog.resident import SETTINGS_FIELDS

    from .. import resident_protocol

    lineage = domain()._read_lineage(lineage_id, actor=actor)
    if existing:
        return _rewrite_from_selection(lineage, element_ids)
    blocks = {item["id"]: item for item in lineage.get("function_blocks", [])}
    selected = set()

    def expand(identity):
        if identity in selected:
            return
        selected.add(identity)
        if identity in blocks:
            for element in blocks[identity]["elements"]:
                expand(element["id"])

    for identity in element_ids:
        expand(identity)
    nodes = {item["id"]: item for item in lineage.get("nodes", [])}
    components = {
        item["id"]: item for item in lineage.get("program", {}).get("components", [])
    }
    wires = {item["id"]: item for item in lineage.get("program", {}).get("wires", [])}
    if not selected or selected - set(nodes) - set(components) - set(wires) - set(
        blocks
    ):
        raise TemplateError("选区包含不存在的元素")
    # A selected pair brings along its connecting wires.
    selected.update(
        identity
        for identity, wire in wires.items()
        if wire["source"] in selected and wire["target"] in selected
    )
    roles = []
    copied_components = []
    copied_wires = []
    keys = {}
    for identity in sorted(selected & nodes.keys()):
        node = nodes[identity]
        if not identity.startswith("resident_"):
            continue
        key = f"resident_{len(roles) + 1}"
        record = resident_protocol.get_resident(identity, actor)
        settings = resident_protocol.get_resident_runtime_settings(identity, actor)[
            "runtime_settings"
        ]
        roles.append(
            {
                "key": key,
                "from": "new",
                "title": node.get("title") or key,
                "provider": record.get("provider") or "codex",
                "account": record.get("account") or "",
                "settings": {
                    key: deepcopy(value)
                    for key, value in settings.items()
                    if key in SETTINGS_FIELDS and key != "backend_route_id"
                },
            }
        )
        keys[identity] = key
    for identity in sorted(selected & components.keys()):
        item = components[identity]
        key = f"component_{len(copied_components) + 1}"
        keys[identity] = key
        copied_components.append(
            {
                "key": key,
                "kind": item["kind"],
                "title": item.get("title", ""),
                "config": deepcopy(item.get("config", {})),
            }
        )
    for identity in sorted(selected & wires.keys()):
        wire = wires[identity]
        for endpoint in (wire["source"], wire["target"]):
            if endpoint not in keys:
                if endpoint not in nodes or not endpoint.startswith("resident_"):
                    raise TemplateError("选区外的组件也需要加入选区")
                key = f"target_{len(roles) + 1}"
                roles.append(
                    {
                        "key": key,
                        "from": "target",
                        "description": nodes[endpoint].get("title", ""),
                    }
                )
                keys[endpoint] = key
        copied_wires.append(
            {
                "key": f"wire_{len(copied_wires) + 1}",
                "kind": wire["kind"],
                "source": keys[wire["source"]],
                "target": keys[wire["target"]],
                **{
                    field: deepcopy(wire[field])
                    for field in ("source_port", "target_port", "config", "observations")
                    if field in wire
                },
            }
        )
    resident_keys = {
        identity: key
        for identity, key in keys.items()
        if identity.startswith("resident_")
    }

    def references(value):
        if isinstance(value, dict):
            return {key: references(item) for key, item in value.items()}
        if isinstance(value, list):
            return [references(item) for item in value]
        if isinstance(value, str):
            for identity, key in resident_keys.items():
                value = value.replace(identity, "{{role." + key + ".id}}")
        return value

    copied_components = references(copied_components)
    copied_wires = references(copied_wires)
    # The graph editor starts from the arrangement the selection had on the canvas.
    layout = {
        keys[identity]: {"x": item["x"], "y": item["y"]}
        for identity, item in {**nodes, **components}.items()
        if identity in keys
        and all(
            isinstance(item.get(axis), (int, float))
            and not isinstance(item.get(axis), bool)
            for axis in ("x", "y")
        )
    }
    title = (
        blocks[element_ids[0]]["title"]
        if len(element_ids) == 1 and element_ids[0] in blocks
        else "选区模板"
    )
    return {
        "kind": "structure",
        "category": "structure",
        "title": title,
        "description": "",
        "targets": ["resident", "selection"]
        if any(role["from"] == "target" for role in roles)
        else ["canvas", "resident", "selection"],
        "parameters": [],
        "body": {
            "roles": roles,
            "components": copied_components,
            "wires": copied_wires,
            "layout": layout,
            "function_block": True,
        },
    }


KIND = StructureKind()
