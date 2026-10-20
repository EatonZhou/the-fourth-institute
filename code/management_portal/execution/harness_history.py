from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Iterator


HARNESS_PLACEHOLDER_PREFIX = "[AgentHarness "






def approximate_value_tokens(value: object) -> int:
    """Approximate tokens locally without treating base64 as normal text."""

    if value is None:
        return 1
    if isinstance(value, str):
        return max(1, math.ceil(len(value) / 4)) if value else 0
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return max(1, math.ceil(len(str(value)) / 4))
    if isinstance(value, (list, tuple)):
        return sum(approximate_value_tokens(item) for item in value)
    if isinstance(value, dict):
        image_url = str(value.get("image_url") or "")
        image_tokens = 0
        skip_image_url = False
        if (
            str(value.get("type") or "")
            in {"image", "image_url", "input_image"}
            and image_url.startswith("data:")
        ):
            image_tokens = 85
            skip_image_url = True
        token_count = image_tokens
        for key, item in value.items():
            token_count += max(1, math.ceil(len(str(key)) / 4))
            if skip_image_url and key == "image_url":
                continue
            token_count += approximate_value_tokens(item)
        return token_count
    return max(1, math.ceil(len(repr(value)) / 4))


def serialized_bytes(value: object) -> int:
    return len(canonical_json(value).encode("utf-8"))


def canonical_json(value: object, *, pretty: bool = False) -> str:
    options: dict[str, Any] = {
        "ensure_ascii": False,
        "sort_keys": True,
        "default": str,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return json.dumps(value, **options)


def value_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()






def output_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: list[str] = []
        for part in value:
            if not isinstance(part, dict):
                continue
            text = str(
                part.get("text")
                or part.get("input_text")
                or part.get("output_text")
                or ""
            )
            if text:
                chunks.append(text)
        if chunks:
            return "\n".join(chunks)
    return canonical_json(value, pretty=True)




def is_harness_placeholder(value: object) -> bool:
    return output_text(value).startswith(HARNESS_PLACEHOLDER_PREFIX)


def iter_strings(value: object, *, max_depth: int = 12) -> Iterator[str]:
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        if isinstance(current, str):
            yield current
            continue
        if depth >= max_depth:
            continue
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend((item, depth + 1) for item in current)


def clean_tool_names(value: object) -> tuple[str, ...]:
    values: Iterable[object] = (
        value if isinstance(value, (list, tuple, set)) else ()
    )
    names: list[str] = []
    for raw_name in values:
        name = str(raw_name or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 100:
            break
    return tuple(names)


def bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def positive_int(value: object) -> int:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0
