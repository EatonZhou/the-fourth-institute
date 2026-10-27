"""Graph targets in Effect scripts must not be hidden in author constants.

This is a graph-authoring contract, not an authorization boundary. Runtime
inputs and ISA results may select targets dynamically; ISA still authorizes
each call. Track constant origins through local data and control flow so an
assignment cannot conceal a fixed target from the shared script analyzer.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from typing import Any, Callable

from . import lineage_effect_script as ast


@dataclass(frozen=True)
class _Source:
    fixed: bool = False
    fields: dict[str, "_Source"] | None = None
    sequence: bool = False
    endpoints: frozenset[str] = frozenset()


_DYNAMIC = _Source()
_FIXED = _Source(True)
_COMPONENT_ARGUMENTS = frozenset({"component_id", "effect_id", "event_source_id"})


def _field(source: _Source, name: str) -> _Source:
    if source.fields is not None:
        return source.fields.get(name, _DYNAMIC)
    return _Source(source.fixed, endpoints=source.endpoints)


def _merge(*sources: _Source) -> _Source:
    keys = {key for source in sources for key in (source.fields or {})}
    return _Source(any(source.fixed for source in sources), {
        key: _merge(*(_field(source, key) for source in sources)) for key in keys
    } or None, any(source.sequence for source in sources),
        frozenset(name for source in sources for name in source.endpoints))


def _prune(source: _Source, depth: int) -> _Source:
    if depth == 0 or source.fields is None:
        return _Source(source.fixed, endpoints=source.endpoints)
    return _Source(source.fixed, {key: _prune(child, depth - 1)
                                 for key, child in source.fields.items()}, source.sequence, source.endpoints)


def _reference_depth(value: Any) -> int:
    if isinstance(value, ast.Ref):
        return len(value.path)
    if is_dataclass(value):
        return max((_reference_depth(getattr(value, field.name)) for field in fields(value)), default=0)
    if isinstance(value, tuple):
        return max((_reference_depth(item) for item in value), default=0)
    return 0


def _value(value: Any, env: dict[str, _Source]) -> _Source:
    def reference(ref: ast.Ref) -> _Source:
        source = env.get(ref.path[0], _DYNAMIC)
        for part in ref.path[1:]:
            source = _field(source, part)
        if ref.span is not None and source.endpoints:
            return _FIXED  # A slice is not the object bound to the endpoint.
        return source

    if isinstance(value, ast.Word):
        parts = [part for part in value.parts if part != ""]
        if len(parts) == 1 and isinstance(parts[0], ast.Ref):
            return reference(parts[0])
        return _merge(*(reference(part) if isinstance(part, ast.Ref) else _FIXED
                        for part in parts))
    if isinstance(value, ast.JsonValue):
        # Use collision-free placeholders only while decoding author JSON.
        literal = "".join(part for part in value.segments if isinstance(part, str))
        marker = "__effect_target_ref__"
        while marker in literal:
            marker += "_"
        refs: dict[str, _Source] = {}
        pieces = []
        for part in value.segments:
            if isinstance(part, ast.Ref):
                key = marker + str(len(refs))
                refs[key] = reference(part)
                pieces.append(json.dumps(key))
            else:
                pieces.append(part)

        def source(item: Any) -> _Source:
            if isinstance(item, str) and item in refs:
                return refs[item]
            if isinstance(item, (dict, list)):
                fields = {str(key): source(child) for key, child in
                          (item.items() if isinstance(item, dict) else enumerate(item))}
                return _Source(any(child.fixed for child in fields.values()), fields, isinstance(item, list),
                               frozenset(name for child in fields.values() for name in child.endpoints))
            return _FIXED if item not in (None, "") else _DYNAMIC

        return source(json.loads("".join(pieces)))
    return _DYNAMIC  # An instruction result is a runtime value.


def target_diagnostics(script: ast.Script, declaration_for: Callable[[str], Any],
                       endpoint_targets: dict[str, str] | None = None) -> list[tuple[str, str, int]]:
    """Use formal ISA Resident slots and graph component parameter names.

    Lineage/User/Workspace targets are not target-wire endpoints. Message
    bodies, descriptions and other arbitrary data are never scanned for IDs.
    """
    diagnostics: set[tuple[str, str, int]] = set()
    lookup_depth = _reference_depth(script)

    def check(call: ast.Call, env: dict[str, _Source]) -> None:
        declaration = declaration_for(call.instruction)
        if declaration is None:
            return
        resident_targets = {slot.argument for slot in getattr(declaration, "decides", ())
                   if "resident" in slot.types and not slot.via}
        parameters = getattr(declaration, "parameter_map", {})
        targets = resident_targets | _COMPONENT_ARGUMENTS.intersection(parameters)
        for argument in call.arguments:
            if isinstance(argument.value, ast.Call):
                check(argument.value, env)
            if argument.name not in targets:
                continue
            source = _value(argument.value, env)
            if source.fixed:
                diagnostics.add(("effect.target_endpoint_required",
                    f"{call.instruction}.{argument.name} 的固定目标必须声明具名 endpoint，"
                    "通过 target 连线绑定，并使用 ${endpoint.<名称>} 引用；不能在脚本中硬编码目标", argument.line))
            for endpoint in source.endpoints:
                target = (endpoint_targets or {}).get(endpoint)
                if target and target.startswith("resident_") != (argument.name in resident_targets):
                    expected = "Resident" if argument.name in resident_targets else "组件"
                    diagnostics.add(("effect.target_type_mismatch",
                        f"{call.instruction}.{argument.name} 需要 {expected}，端点 {endpoint} 绑定的对象类型不匹配", argument.line))

    def condition(value: Any, env: dict[str, _Source]) -> None:
        if isinstance(value, ast.CallCondition):
            block((value.target,), env)
        elif isinstance(value, ast.Not):
            condition(value.condition, env)
        elif isinstance(value, ast.AndOr):
            before = dict(env)
            condition(value.left, env)
            condition(value.right, env)
            merge_env(env, before, dict(env))

    def merge_env(env: dict[str, _Source], *alternatives: dict[str, _Source]) -> None:
        keys = {key for alternative in alternatives for key in alternative}
        env.clear()
        env.update({key: _merge(*(alternative.get(key, _DYNAMIC) for alternative in alternatives))
                    for key in keys})

    def block(statements: tuple, env: dict[str, _Source]) -> None:
        for statement in statements:
            if isinstance(statement, ast.Call):
                check(statement, env)
            elif isinstance(statement, ast.Assign):
                if isinstance(statement.value, ast.Call):
                    check(statement.value, env)
                env[statement.name] = _prune(_value(statement.value, env), lookup_depth)
            elif isinstance(statement, ast.If):
                remaining = dict(env)
                alternatives = []
                for test, body in statement.branches:
                    condition(test, remaining)
                    branch = dict(remaining)
                    block(body, branch)
                    alternatives.append(branch)
                block(statement.otherwise, remaining)
                merge_env(env, *alternatives, remaining)
            elif isinstance(statement, ast.For):
                item_sources = [_value(item, env) for item in statement.items]
                item_source = _merge(*(_merge(*(source.fields or {}).values()) if source.sequence else source
                                       for source in item_sources))
                entry = dict(env)
                # Monotone join covers zero iterations and values carried
                # between iterations, without executing any instruction.
                while True:
                    iteration = dict(env)
                    iteration[statement.name] = item_source
                    block(statement.body, iteration)
                    previous = dict(env)
                    merge_env(env, entry, previous, iteration)
                    if env == previous:
                        break

    block(script.statements, {"endpoint": _Source(fields={
        name: _Source(endpoints=frozenset({name})) for name in (endpoint_targets or {})
    })})
    return sorted(diagnostics, key=lambda item: (item[2], item[1]))
