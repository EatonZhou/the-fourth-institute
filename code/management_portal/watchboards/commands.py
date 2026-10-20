"""Declared command arguments for the Watchboard RunScript entrypoint."""
from __future__ import annotations

from typing import Any


def command_words(command_id: str, spec: dict[str, Any]) -> list[str]:
    value = spec.get("map_to")
    if isinstance(value, str) and value.strip():
        return value.split()
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [command_id]


def argument_words(spec: dict[str, Any], args: dict[str, Any]) -> list[str]:
    schema = spec.get("args") if isinstance(spec.get("args"), dict) else spec.get("parameters")
    schema = schema if isinstance(schema, dict) else {}
    parts = []
    for key, value in args.items():
        key = str(key or "").strip()
        if not key or key not in schema:
            continue
        definition = schema[key] if isinstance(schema[key], dict) else {}
        option = str(definition.get("option") or "--" + key.replace("_", "-"))
        if isinstance(value, bool):
            if value:
                parts.append(option)
        elif isinstance(value, list):
            for item in value:
                parts.extend([option, str(item)])
        elif value is not None:
            parts.extend([option, str(value)])
    return parts
