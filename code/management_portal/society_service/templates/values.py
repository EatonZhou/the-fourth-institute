"""Parameter definitions, typed values and recursive template substitution."""

from __future__ import annotations

import math
import re
from copy import deepcopy

from .registry import TemplateError

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
TOKEN = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
TYPES = {"text", "integer", "number", "boolean", "enum", "duration"}


def _value(spec: dict, value):
    kind = spec["type"]
    valid = (
        isinstance(value, str)
        if kind == "text"
        else isinstance(value, bool)
        if kind == "boolean"
        else isinstance(value, int) and not isinstance(value, bool)
        if kind == "integer"
        else isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (kind != "duration" or value > 0)
        if kind in {"number", "duration"}
        else value in spec.get("options", [])
    )
    if not valid:
        raise TemplateError(f"参数 {spec['name']} 需要 {kind} 类型的有效值")
    return value


def normalize_parameters(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise TemplateError("parameters 必须是参数说明列表")
    seen = set()
    result = []
    for value in raw:
        if not isinstance(value, dict):
            raise TemplateError("参数说明必须是对象")
        spec = deepcopy(value)
        name = spec.get("name", "")
        if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
            raise TemplateError(f"参数名称无效或重复：{name}")
        seen.add(name)
        spec.setdefault("type", "text")
        spec.setdefault("required", False)
        spec.setdefault("description", "")
        if spec["type"] not in TYPES or not isinstance(spec["required"], bool):
            raise TemplateError(f"参数说明无效：{name}")
        if spec["type"] == "enum" and (
            not isinstance(spec.get("options"), list) or not spec["options"]
        ):
            raise TemplateError(f"枚举参数 {name} 需要 options")
        if "default" in spec:
            _value(spec, spec["default"])
        result.append(spec)
    return result


def parameters(specs: list[dict], supplied: dict | None) -> dict:
    specs = normalize_parameters(specs)
    supplied = supplied if supplied is not None else {}
    if not isinstance(supplied, dict):
        raise TemplateError("parameters 必须是对象")
    unknown = set(supplied) - {spec["name"] for spec in specs}
    if unknown:
        raise TemplateError(f"未知参数：{', '.join(sorted(unknown))}")
    result = {}
    for spec in specs:
        name = spec["name"]
        if name in supplied or "default" in spec:
            result[name] = _value(spec, supplied.get(name, spec.get("default")))
        elif spec["required"]:
            raise TemplateError(f"缺少参数：{name}")
    return result


def duration(seconds) -> str:
    for unit, label in ((86400, "天"), (3600, "小时"), (60, "分钟")):
        if seconds >= unit and seconds % unit == 0:
            return f"{seconds / unit:g} {label}"
    return f"{seconds:g} 秒"


def render(
    value,
    params: dict,
    *,
    roles: dict | None = None,
    matches: dict | None = None,
    lineage_id: str = "",
    title: str = "",
):
    def resolve(match):
        token = match.group(1).strip()
        path, _, modifier = token.partition("|")
        parts = path.strip().split(".")
        if parts[0] == "param" and len(parts) == 2 and parts[1] in params:
            item = params[parts[1]]
        elif parts[0] == "role" and len(parts) == 3 and parts[1] in (roles or {}):
            members = roles[parts[1]]
            members = members if isinstance(members, list) else [members]
            if not members or parts[2] not in {"id", "ids", "title"}:
                raise TemplateError(f"角色引用无效：{token}")
            item = (
                ",".join(str(m["id"]) for m in members)
                if parts[2] == "ids"
                else members[0][parts[2]]
            )
        elif (
            parts[0] == "match"
            and len(parts) == 3
            and parts[1] in (matches or {})
            and parts[2] in {"id", "title"}
        ):
            item = matches[parts[1]][parts[2]]
        elif path == "lineage.id":
            item = lineage_id
        elif path == "block.title":
            item = title
        else:
            raise TemplateError(f"无法替换模板引用：{token}")
        if modifier:
            if (
                modifier != "duration"
                or parts[0] != "param"
                or not isinstance(item, (int, float))
            ):
                raise TemplateError(f"模板引用修饰符无效：{token}")
            item = duration(item)
        return item

    context = {"roles": roles, "matches": matches, "lineage_id": lineage_id, "title": title}
    if isinstance(value, dict):
        return {key: render(item, params, **context) for key, item in value.items()}
    if isinstance(value, list):
        return [render(item, params, **context) for item in value]
    if not isinstance(value, str):
        return deepcopy(value)
    exact = TOKEN.fullmatch(value)
    if exact:
        return deepcopy(resolve(exact))
    return TOKEN.sub(lambda match: str(resolve(match)), value)


def render_script(source, params, **context):
    from ..lineage_effect_script import render_template_value

    return TOKEN.sub(
        lambda match: render_template_value(
            str(render(match.group(0), params, **context))
        ),
        source,
    )


def match_references(value) -> set[str]:
    """Keys of found elements that `value` refers to with {{match.key.*}}."""
    if isinstance(value, dict):
        return set().union(*map(match_references, value.values()))
    if isinstance(value, list):
        return set().union(*map(match_references, value))
    if not isinstance(value, str):
        return set()
    return {
        parts[1]
        for token in TOKEN.finditer(value)
        if len(parts := token.group(1).strip().partition("|")[0].split(".")) == 3
        and parts[0] == "match"
    }


def validate_references(value, specs, role_keys, match_keys=()):
    names = {spec["name"]: spec for spec in specs}
    if isinstance(value, dict):
        for item in value.values():
            validate_references(item, specs, role_keys, match_keys)
    elif isinstance(value, list):
        for item in value:
            validate_references(item, specs, role_keys, match_keys)
    elif isinstance(value, str):
        for match in TOKEN.finditer(value):
            path, _, modifier = match.group(1).strip().partition("|")
            parts = path.split(".")
            valid = (
                path in {"lineage.id", "block.title"}
                or len(parts) == 2
                and parts[0] == "param"
                and parts[1] in names
                or len(parts) == 3
                and parts[0] == "role"
                and parts[1] in role_keys
                and parts[2] in {"id", "ids", "title"}
                or len(parts) == 3
                and parts[0] == "match"
                and parts[1] in match_keys
                and parts[2] in {"id", "title"}
            )
            if not valid:
                raise TemplateError(f"无效模板引用：{match.group(0)}")
            if modifier and (
                modifier != "duration"
                or len(parts) != 2
                or parts[0] != "param"
                or names[parts[1]]["type"] not in {"integer", "number", "duration"}
            ):
                raise TemplateError(f"无效描述修饰符：{match.group(0)}")
