from __future__ import annotations

import re
from typing import Any, Iterable, Mapping


RESOURCE_INDEX_SCHEMA = "tfi.portal.resource_index.v2"
# Read access is decided by the ISA permission policy; a resource carries no
# visibility attribute of its own.
FORBIDDEN_COMMON_PROPERTY_NAMES = frozenset(
    {"project", "project_id", "owner", "owner_id", "visibility"}
)
READ_SCOPES = frozenset({"project", "global"})
RELATION_KIND_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _text(value: object) -> str:
    return str(value or "").strip()


def _dedupe(values: Iterable[object]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _text(value)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def _id_list(name: str, value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"resource attribute {name} must be an array")
    return _dedupe(value)


def resource_relation(kind: object, target_id: object) -> dict[str, str]:
    clean_kind = _text(kind)
    clean_target = _text(target_id)
    if not RELATION_KIND_RE.fullmatch(clean_kind):
        raise ValueError(f"invalid resource relation kind: {clean_kind!r}")
    if not clean_target or "\x00" in clean_target:
        raise ValueError("resource relation target_id is required")
    return {"kind": clean_kind, "target_id": clean_target}


def _relations(value: object) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("resource attribute relations must be an array")
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value:
        if not isinstance(raw, Mapping):
            raise ValueError("each resource relation must be an object")
        unknown = set(raw) - {"kind", "target_id"}
        if unknown:
            raise ValueError(
                f"unsupported resource relation fields: {', '.join(sorted(unknown))}"
            )
        relation = resource_relation(raw.get("kind"), raw.get("target_id"))
        key = (relation["kind"], relation["target_id"])
        if key not in seen:
            seen.add(key)
            output.append(relation)
    return output


def normalize_resource_attributes(
    attributes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Apply the one public common-property contract at the index boundary."""

    if attributes is not None and not isinstance(attributes, Mapping):
        raise ValueError("resource attributes must be an object")
    source = dict(attributes or {})
    forbidden = sorted(set(source) & FORBIDDEN_COMMON_PROPERTY_NAMES)
    if forbidden:
        raise ValueError(
            "unsupported resource attributes: "
            + ", ".join(forbidden)
        )

    read_scope = _text(source.pop("read_scope", "")).casefold()
    if read_scope and read_scope not in READ_SCOPES:
        raise ValueError(f"invalid resource read_scope: {read_scope!r}")
    normalized: dict[str, Any] = {
        "project_ids": _id_list("project_ids", source.pop("project_ids", [])),
        "relations": _relations(source.pop("relations", [])),
    }
    if read_scope:
        normalized["read_scope"] = read_scope
    if "status" in source:
        normalized["status"] = _text(source.pop("status"))
    for name in ("created_at", "updated_at"):
        if name not in source:
            continue
        value = source.pop(name)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError(f"resource attribute {name} must be a timestamp scalar")
        normalized[name] = value
    normalized.update(source)
    return normalized
