"""Test helpers for Effect scripts (decision_4adcc1ff2f2c).

``effect_config`` writes an Effect from an instruction and argument table
(``value``, ``from`` with an optional ``default``, or ``concat``), the way
tests written against single-instruction Effects describe their calls.  ``lower_effect_calls`` interprets a
script without executing anything and returns each call as the router would
receive it.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

from management_portal.society_service.lineage_effect_script import (
    ARGUMENT_RE,
    INSTRUCTION_RE,
    PATH_SEGMENT_RE,
    CallResult,
    Interpreter,
    call_line,
    literal_argument,
    parse,
    reference,
    render_template_value,
    stringify,
)
from management_portal.society_service.lineage_program_effect_rules import (
    effect_call_key,
    effect_idempotency_key,
    effect_script_inputs,
    prepare_call_arguments,
)


_SOURCE_ROOTS = {"invocation": "arguments", "endpoint": "endpoint", "event": "event", "packet": "packet"}
_EVENT_OUTPUT_STRING_RE = re.compile(r'"\$event\.outputs\.([A-Za-z0-9_]{1,120})"')


class ConversionError(ValueError):
    """An argument table that has no exact script spelling."""


def _source_path(source: str) -> str:
    root, _, rest = str(source).partition(".")
    if root not in _SOURCE_ROOTS or not rest or not all(PATH_SEGMENT_RE.fullmatch(item) for item in rest.split(".")):
        raise ConversionError(f"参数来源无法改写：{source}")
    return _SOURCE_ROOTS[root] + "." + rest


def _concat_text(value: Any) -> str:
    return stringify(value)


def script_from_argument_table(instruction: str, arguments: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Rewrite ``instruction`` + argument table as an equivalent script.

    Returns the script and notes on the cases where an absent source now
    expands to an empty value instead of failing the activation.
    """

    if not INSTRUCTION_RE.fullmatch(str(instruction or "")):
        raise ConversionError(f"指令名无效：{instruction!r}")
    prefix: list[str] = []
    rendered: list[str] = []
    notes: list[str] = []
    for index, (name, binding) in enumerate(arguments.items(), start=1):
        if not ARGUMENT_RE.fullmatch(str(name)) or not isinstance(binding, Mapping):
            raise ConversionError(f"参数 {name!r} 无法改写")
        if "value" in binding:
            value = binding["value"]
            if isinstance(value, str):
                rendered.append(literal_argument(name, value))
            else:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if _EVENT_OUTPUT_STRING_RE.search(text):
                    text = _EVENT_OUTPUT_STRING_RE.sub(lambda match: "${event.outputs." + match.group(1) + "}", text)
                    notes.append(f"{name}: $event.outputs 引用改为 ${{event.outputs.…}}；缺少该输出时为 null")
                rendered.append(f"{name}:={text}")
        elif "from" in binding:
            path = _source_path(str(binding["from"]))
            if "default" in binding:
                variable = f"_default_{index}"
                default = binding["default"]
                prefix.append(f"{variable}={reference(path)}")
                fallback = literal_argument(variable, default)
                prefix.append(f'if [ -z "${variable}" ]; then\n  {fallback}\nfi')
                rendered.append(f"{name}=" + '"$' + variable + '"')
                notes.append(f"{name}: 来源为空时使用默认值（原规则只在来源不存在时使用）")
            else:
                rendered.append(f"{name}={reference(path)}")
                notes.append(f"{name}: 来源 {binding['from']} 不存在时展开为空值（原规则直接失败）")
        elif "concat" in binding:
            separator = render_template_value(str(binding.get("separator") or ""))
            pieces: list[str] = []
            for part in binding.get("concat") or []:
                if not isinstance(part, Mapping):
                    raise ConversionError(f"参数 {name} 的 concat 项无法改写")
                if "value" in part:
                    pieces.append(render_template_value(_concat_text(part["value"])))
                elif "from" in part and "default" not in part:
                    pieces.append("${" + _source_path(str(part["from"])) + "}")
                else:
                    raise ConversionError(f"参数 {name} 的 concat 项无法改写")
            rendered.append(f'{name}="' + separator.join(pieces) + '"')
            if any("from" in part for part in binding.get("concat") or []):
                notes.append(f"{name}: concat 中来源不存在时展开为空文字（原规则直接失败）")
        else:
            raise ConversionError(f"参数 {name} 无法改写")
    return "\n".join([*prefix, call_line(str(instruction), rendered)]), notes

def effect_config(instruction: str, arguments: dict[str, Any] | None = None, endpoints: Iterable[str] = ()) -> dict[str, Any]:
    script, _notes = script_from_argument_table(instruction, arguments or {})
    return {"script": script, "endpoints": list(endpoints)}


def lower_effect_calls(lineage_id: str, effect_id: str, definition: dict[str, Any], invocation: Any) -> list[dict[str, Any]]:
    activation_key = effect_idempotency_key(lineage_id, effect_id, invocation)
    calls: list[dict[str, Any]] = []

    def execute(request):
        calls.append({
            "instruction": request.instruction,
            "arguments": prepare_call_arguments(request.instruction, request.arguments, invocation),
            "idempotency_key": effect_call_key(activation_key, request.position),
        })
        return CallResult(True, {})

    outcome = Interpreter(parse(definition["script"]), effect_script_inputs(definition, invocation), execute).run()
    if outcome.error is not None:
        raise ValueError(outcome.error.message)
    return calls


def lower_effect_call(lineage_id: str, effect_id: str, definition: dict[str, Any], invocation: Any) -> dict[str, Any]:
    return lower_effect_calls(lineage_id, effect_id, definition, invocation)[0]
