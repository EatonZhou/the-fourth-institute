"""Editable templates and their application to a Lineage."""

from __future__ import annotations

import math
from copy import deepcopy

from . import kinds, values
from .registry import ApplyContext, TemplateError
from .. import lineage_aggregate


def describe(template):
    kind = kinds.get(template["kind"])
    return {**template, "behavior": kind.describe(template), "editor": kind.editor()}


def list_templates(target_kind=None):
    from . import store

    if target_kind is not None and target_kind not in {
        "resident",
        "selection",
        "canvas",
    }:
        raise TemplateError("未知目标类型")
    return [
        describe(item)
        for item in store.all_templates()
        if target_kind is None or target_kind in item["targets"]
    ]


def get(template_id):
    from . import store

    return describe(store.get(template_id))


def create(template, actor, *, from_selection=None, dry_run=False):
    from . import store

    if from_selection is not None:
        from .structure import from_selection as capture

        template = {
            **capture(
                from_selection["lineage_id"],
                from_selection["element_ids"],
                actor,
                existing=bool(from_selection.get("rewrite")),
            ),
            **(template or {}),
        }
    normalized = kinds.normalize(template)
    return describe(normalized if dry_run else store.save(normalized, actor))


def update(template_id, changes, actor, *, dry_run=False):
    from . import store

    unknown = set(changes) - set(store.CONTENT)
    if unknown:
        raise TemplateError(f"不支持修改字段：{sorted(unknown)}")

    def transform(previous):
        if changes.get("kind", previous["kind"]) != previous["kind"]:
            raise TemplateError("模板 kind 不可修改；请另存为副本")
        return kinds.normalize({**previous, **changes})

    return describe(
        transform(store.get(template_id))
        if dry_run
        else store.save({}, actor, template_id=template_id, transform=transform)
    )


def delete(template_id):
    from . import store

    store.delete(template_id)
    return {"template_id": template_id}


def restore(template_id, actor):
    from . import store

    return describe(store.restore(template_id, actor))


def normalize_target(lineage_id, raw, actor):
    if not isinstance(raw, dict) or raw.get("kind") not in {
        "resident",
        "selection",
        "canvas",
    }:
        raise TemplateError("target 需要有效 kind")
    body = lineage_aggregate.read_lineage(lineage_id, actor=actor)
    members = {
        node["id"]
        for node in body.get("nodes", [])
        if node.get("resident_id") or str(node.get("id", "")).startswith("resident_")
    }
    target = {
        **raw,
        "resident_ids": list(dict.fromkeys(raw.get("resident_ids") or [])),
        "element_ids": list(dict.fromkeys(raw.get("element_ids") or [])),
    }
    blocks = {item["id"]: item for item in body.get("function_blocks", [])}

    def collect(identity, visited):
        if identity in visited:
            return
        visited.add(identity)
        if identity in members:
            target["resident_ids"].append(identity)
        elif identity in blocks:
            block = blocks[identity]
            facade = (block.get("family") or {}).get("facade")
            if facade:
                target["resident_ids"].append(facade)
            else:
                for element in block["elements"]:
                    collect(element["id"], visited)

    for identity in target["element_ids"]:
        collect(identity, set())
    target["resident_ids"] = list(dict.fromkeys(target["resident_ids"]))
    if set(target["resident_ids"]) - members:
        raise TemplateError("目标 Resident 不属于当前 Lineage")
    if target["kind"] == "resident" and len(target["resident_ids"]) != 1:
        raise TemplateError("resident 目标需要且只接受一个 Resident")
    if (
        target["kind"] == "selection"
        and not target["resident_ids"]
        and not target["element_ids"]
    ):
        raise TemplateError("选区不能为空")
    if target["kind"] == "canvas" and (target["resident_ids"] or target["element_ids"]):
        raise TemplateError("canvas 目标不接受元素")
    for axis in ("x", "y"):
        value = target.get(axis)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise TemplateError("目标坐标必须是有限数值")
    return target


def apply(
    lineage_id,
    template_id,
    target,
    actor,
    *,
    parameters=None,
    title="",
    resident_settings=None,
    match_choices=None,
    dry_run=False,
):
    from . import store

    template = kinds.normalize(store.get(template_id))
    target = normalize_target(lineage_id, target, actor)
    if target["kind"] not in template["targets"]:
        raise TemplateError("此模板不支持当前目标类型")
    choices = match_choices or {}
    found = {item["key"] for item in template["body"].get("match", [])}
    if not isinstance(choices, dict) or any(
        key not in found
        or not isinstance(ids, list)
        or not all(isinstance(item, str) for item in ids)
        for key, ids in choices.items()
    ):
        raise TemplateError("match_choices 需要 {已有元素 key: [元素 ID]}")
    kind = kinds.get(template["kind"])
    ctx = ApplyContext(
        lineage_id,
        template,
        values.parameters(template["parameters"], parameters),
        target,
        actor,
        # A kind that names its result after the target gets no template title.
        title or ("" if getattr(kind, "titles_from_target", False) else template["title"]),
        resident_settings or {},
        {key: set(ids) for key, ids in choices.items()},
    )
    if dry_run:
        if not hasattr(kind, "preview"):
            raise TemplateError("只有结构模板支持施加预览")
        return kind.preview(ctx)
    return kind.apply(ctx)
