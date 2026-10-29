"""The template kinds and the normalization every template goes through.

Kinds load on first use; ``store`` installs builtins in their normalized form.
"""
from __future__ import annotations

from copy import deepcopy

from . import values
from .registry import TemplateError, TemplateKind


_KINDS: dict[str, TemplateKind] = {}
_loaded = False


def register(kind: TemplateKind) -> None:
    if kind.key in _KINDS and _KINDS[kind.key] is not kind:
        raise TemplateError(f"模板模块重复注册：{kind.key}")
    _KINDS[kind.key] = kind


def _load() -> None:
    global _loaded
    if _loaded:
        return
    from . import explore, familize, prompt, script, structure

    for module in (prompt, structure, script, explore, familize):
        register(module.KIND)
    _loaded = True


def get(key: str) -> TemplateKind:
    _load()
    try:
        return _KINDS[key]
    except KeyError:
        raise TemplateError(f"未知模板模块：{key}") from None


def available() -> list[dict]:
    _load()
    return [
        {
            "key": kind.key,
            "title": kind.title,
            "targets": list(kind.targets),
            "editor": kind.editor(),
        }
        for kind in _KINDS.values()
    ]


def normalize(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise TemplateError("template 必须是对象")
    template = deepcopy(raw)
    kind = get(str(template.get("kind") or ""))
    template.setdefault(
        "category",
        "structure"
        if kind.key == "structure"
        else "team"
        if kind.key == "familize"
        else "apply",
    )
    if template["category"] not in {"apply", "structure", "team"}:
        raise TemplateError("未知模板分组")
    title = template.get("title")
    if not isinstance(title, str) or not title.strip():
        raise TemplateError("模板需要标题")
    template["title"] = title.strip()
    template.setdefault("description", "")
    template.setdefault("targets", list(kind.targets))
    if (
        not isinstance(template["targets"], list)
        or not template["targets"]
        or any(item not in kind.targets for item in template["targets"])
    ):
        raise TemplateError("模板目标必须是模块支持的目标子集")
    template["targets"] = list(dict.fromkeys(template["targets"]))
    template["parameters"] = values.normalize_parameters(template.get("parameters", []))
    template.setdefault("body", {})
    if not isinstance(template["body"], dict):
        raise TemplateError("body 必须是对象")
    template = kind.normalize(template)
    roles = (
        {item["key"] for item in template["body"].get("roles", [])}
        if kind.key == "structure"
        else {"target"}
    )
    found = {item["key"] for item in template["body"].get("match", [])} if kind.key == "structure" else set()
    values.validate_references(template["body"], template["parameters"], roles, found)
    return template
