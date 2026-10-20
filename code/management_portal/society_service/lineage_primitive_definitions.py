from __future__ import annotations

from management_common.format_tags import schema_matches

import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "tfi.hatg-primitive-definitions.v1"
_DEFINITIONS_PATH = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "lineage_primitive_definitions.json"
)
_FIELD_TYPES = {
    "checkbox",
    "datetime-local",
    "json",
    "json-array",
    "json-object",
    "number",
    "optional-limit",
    "relative-path",
    "resident-id",
    "resident-set",
    "select",
    "text",
    "textarea",
    "watch-reference",
}


class PrimitiveDefinitionError(ValueError):
    """Raised when the canonical primitive contract is malformed."""


def _mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PrimitiveDefinitionError(f"{field} 必须是 JSON object")
    return value


def _items(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PrimitiveDefinitionError(f"{field} 必须是 JSON array")
    if not all(isinstance(item, dict) for item in value):
        raise PrimitiveDefinitionError(f"{field} 只能包含 JSON object")
    return value


def _validate_fields(
    fields: object,
    *,
    owner: str,
) -> None:
    seen_paths: set[str] = set()
    seen_names: set[str] = set()
    for item in _items(fields, field=f"{owner}.fields"):
        path = str(item.get("path") or "").strip()
        if not (
            path.startswith("config.")
            or path == "watch.subject"
        ):
            raise PrimitiveDefinitionError(
                f"{owner} 字段必须属于 config 或 watch：{path or '（空）'}"
            )
        if path in seen_paths:
            raise PrimitiveDefinitionError(f"{owner} 字段重复：{path}")
        seen_paths.add(path)
        name = str(item.get("name") or path.rsplit(".", 1)[-1]).strip()
        if name in seen_names:
            raise PrimitiveDefinitionError(
                f"{owner} 表单字段名重复：{name}"
            )
        seen_names.add(name)
        field_type = str(item.get("type") or "").strip()
        if not field_type:
            raise PrimitiveDefinitionError(f"{owner}.{path} 缺少字段类型")
        if field_type not in _FIELD_TYPES:
            raise PrimitiveDefinitionError(
                f"{owner}.{path} 字段类型无效：{field_type}"
            )
        if (path == "watch.subject") != (field_type == "watch-reference"):
            raise PrimitiveDefinitionError(
                f"{owner}.{path} 的 Watch 字段类型不一致"
            )
        if not str(item.get("label") or "").strip():
            raise PrimitiveDefinitionError(f"{owner}.{path} 缺少字段名称")


def _validate_composed_fields(
    fields: list[dict[str, Any]],
    *,
    owner: str,
) -> None:
    paths = [str(field.get("path") or "").strip() for field in fields]
    if len(paths) != len(set(paths)):
        raise PrimitiveDefinitionError(f"{owner} 与公共字段存在重复 path")
    known_paths = set(paths)
    for field in fields:
        path = str(field.get("path") or "").strip()
        for dependency_key in ("visible_when", "options_by"):
            dependency = field.get(dependency_key)
            if dependency is None:
                continue
            if not isinstance(dependency, dict):
                raise PrimitiveDefinitionError(
                    f"{owner}.{path}.{dependency_key} 必须是 JSON object"
                )
            dependency_path = str(dependency.get("path") or "").strip()
            if dependency_path not in known_paths:
                raise PrimitiveDefinitionError(
                    f"{owner}.{path} 引用了未知字段："
                    f"{dependency_path or '（空）'}"
                )


WATCH_KINDS = frozenset({"resident", "event_source", "wire", "effect", "handoff"})
COMPONENT_KINDS = ("event_source", "effect", "filter", "selector", "reducer", "loop", "seed")


def _validate_event_types(payload: dict[str, Any]) -> None:
    definitions = _mapping(payload.get("event_types"), field="event_types")
    categories = _mapping(
        payload.get("event_type_categories"),
        field="event_type_categories",
    )
    _validate_fields(payload.get("event_common_fields"), owner="event.common")
    common = _items(
        payload.get("event_common_fields"),
        field="event_common_fields",
    )
    for type_id, raw_definition in definitions.items():
        definition = _mapping(raw_definition, field=f"event_types.{type_id}")
        category = str(definition.get("category") or "")
        if category not in categories:
            raise PrimitiveDefinitionError(
                f"event {type_id} 使用未知分类：{category or '（空）'}"
            )
        if not str(definition.get("title") or "").strip():
            raise PrimitiveDefinitionError(f"event {type_id} 缺少 title")
        _validate_fields(definition.get("fields") or [], owner=f"event.{type_id}")
        _validate_composed_fields(
            [*common, *(definition.get("fields") or [])],
            owner=f"event.{type_id}",
        )
        if definition.get("watch_contract") is not None:
            watch_contract = _mapping(
                definition.get("watch_contract"),
                field=f"event.{type_id}.watch_contract",
            )
            allowed_kinds = watch_contract.get("allowed_kinds")
            if not isinstance(allowed_kinds, list) or not allowed_kinds:
                raise PrimitiveDefinitionError(
                    f"event {type_id} Watch contract 缺少 allowed_kinds"
                )
            unknown_kinds = set(allowed_kinds) - WATCH_KINDS
            if unknown_kinds:
                raise PrimitiveDefinitionError(
                    f"event {type_id} Watch contract kind 无效："
                    f"{sorted(unknown_kinds)}"
                )
            if "multiple" in watch_contract and (
                not isinstance(watch_contract["multiple"], bool)
                or (watch_contract["multiple"] and allowed_kinds not in (["resident"], ["effect"]))
            ):
                raise PrimitiveDefinitionError(
                    f"event {type_id} Watch contract 的 multiple 只能用于只观察 Resident 或 Effect 的类型"
                )
            required_when = watch_contract.get("required_when")
            if required_when is not None:
                known = {
                    str(field.get("path") or "")
                    for field in [*common, *(definition.get("fields") or [])]
                }
                if (
                    not isinstance(required_when, dict)
                    or str(required_when.get("path") or "") not in known
                    or not str(required_when.get("path") or "").startswith("config.")
                ):
                    raise PrimitiveDefinitionError(
                        f"event {type_id} Watch contract 的 required_when 必须引用本类型的 config 字段"
                    )
        ports = _items(definition.get("ports") or [], field=f"event.{type_id}.ports")
        port_ids = [str(port.get("id") or "") for port in ports]
        if len(port_ids) != len(set(port_ids)):
            raise PrimitiveDefinitionError(f"event {type_id} 端口 ID 重复")


def _validate_component_types(payload: dict[str, Any]) -> None:
    components = _mapping(payload.get("component_types"), field="component_types")
    if tuple(components) != COMPONENT_KINDS:
        raise PrimitiveDefinitionError(
            f"component_types 必须依次是 {list(COMPONENT_KINDS)}"
        )
    for kind, raw in components.items():
        definition = _mapping(raw, field=f"component_types.{kind}")
        if not str(definition.get("title") or "").strip():
            raise PrimitiveDefinitionError(f"组件 {kind} 缺少 title")
        ports = _items(definition.get("ports") or [], field=f"component_types.{kind}.ports")
        port_ids = [str(port.get("id") or "") for port in ports]
        if len(port_ids) != len(set(port_ids)):
            raise PrimitiveDefinitionError(f"组件 {kind} 端口 ID 重复")


def _load_and_validate() -> dict[str, Any]:
    try:
        payload = json.loads(_DEFINITIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrimitiveDefinitionError(
            f"无法读取 HATG 原语定义：{exc}"
        ) from exc
    payload = _mapping(payload, field="primitive definitions")
    if not schema_matches(payload.get("schema"), "tfi.hatg-primitive-definitions.v1"):
        raise PrimitiveDefinitionError(
            f"HATG 原语定义 schema 必须是 {SCHEMA}"
        )
    _validate_event_types(payload)
    _validate_component_types(payload)
    default_types = _mapping(payload.get("default_types"), field="default_types")
    if set(default_types) != {"event"}:
        raise PrimitiveDefinitionError("default_types 只能声明 event")
    type_id = str(default_types.get("event") or "").strip().lower()
    if type_id not in payload["event_types"]:
        raise PrimitiveDefinitionError(f"event 默认类型无效：{type_id or '（空）'}")
    return payload


@lru_cache(maxsize=1)
def _definitions() -> dict[str, Any]:
    return _load_and_validate()


def definitions() -> dict[str, Any]:
    """Return an isolated projection of the canonical primitive contract."""

    return deepcopy(_definitions())


def _type_definitions_for(kind: str) -> dict[str, dict[str, Any]]:
    normalized = str(kind or "").strip().lower()
    if normalized != "event":
        raise KeyError(f"unknown primitive kind: {kind}")
    return _definitions()["event_types"]


def type_definitions(kind: str) -> dict[str, dict[str, Any]]:
    return deepcopy(_type_definitions_for(kind))


def type_definition(kind: str, type_id: object) -> dict[str, Any]:
    normalized_id = str(type_id or "").strip().lower()
    definition = _type_definitions_for(kind).get(normalized_id)
    if not isinstance(definition, dict):
        raise KeyError(f"unknown {kind} primitive type: {normalized_id}")
    return deepcopy(definition)


def type_catalog(kind: str) -> tuple[dict[str, Any], ...]:
    """Return the canonical public Event Source type catalog.

    Public type ids, authoring fields, ports, labels and descriptions are all
    owned by the primitive definition manifest.
    """

    normalized = str(kind or "").strip().lower()
    if normalized != "event":
        raise KeyError(f"unknown primitive kind: {kind}")
    result: list[dict[str, Any]] = []
    for type_id, definition in sorted(type_definitions(normalized).items()):
        result.append({
            "type": type_id,
            "category": str(definition.get("category") or ""),
            "title": str(definition.get("title") or type_id),
            "description": str(definition.get("description") or ""),
            "fields": [deepcopy(field) for field in fields_for(normalized, type_id)],
            "ports": deepcopy(list(definition.get("ports") or [])),
            "default": type_id == default_type(normalized),
            "watch_contract": deepcopy(definition.get("watch_contract") or {}),
        })
    return tuple(result)


def component_type_catalog() -> list[dict[str, Any]]:
    """Return the component kinds a Lineage definition may contain."""

    return [
        {"kind": kind, **deepcopy(definition)}
        for kind, definition in _definitions()["component_types"].items()
    ]


def default_type(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized != "event":
        raise KeyError(f"unknown primitive kind: {kind}")
    return str(_definitions()["default_types"][normalized])


def common_fields(kind: str) -> tuple[dict[str, Any], ...]:
    normalized = str(kind or "").strip().lower()
    if normalized != "event":
        raise KeyError(f"unknown primitive kind: {kind}")
    return tuple(
        deepcopy(_definitions()[f"{normalized}_common_fields"])
    )


def fields_for(kind: str, type_id: object) -> tuple[dict[str, Any], ...]:
    definition = type_definition(kind, type_id)
    omitted_common_fields = {
        str(item or "").strip()
        for item in definition.get("omit_common_fields") or []
    }
    return (
        *tuple(
            field
            for field in common_fields(kind)
            if (
                str(field.get("name") or "").strip()
                or str(field.get("path") or "").removeprefix("config.")
            ) not in omitted_common_fields
        ),
        *tuple(definition.get("fields") or []),
    )


@lru_cache(maxsize=None)
def _read_only_fields(kind: str, type_id: str) -> tuple[dict[str, Any], ...]:
    """``fields_for`` copied once: every program compile asks for the same fields.

    Only this module's readers use it, and they never change the fields.
    """
    return fields_for(kind, type_id)


def config_field_names(kind: str) -> frozenset[str]:
    result = {"event_type"}
    for type_id in type_definitions(kind):
        for field in fields_for(kind, type_id):
            path = str(field.get("path") or "")
            if path.startswith("config."):
                result.add(path.removeprefix("config."))
    return frozenset(result)


def event_types_watching(subject_kind: object) -> frozenset[str]:
    normalized_kind = str(subject_kind or "").strip().lower()
    return frozenset(
        type_id
        for type_id, definition in _type_definitions_for("event").items()
        if normalized_kind in (
            (definition.get("watch_contract") or {}).get("allowed_kinds")
            or []
        )
    )


def watch_required(type_id: object, config: Mapping[str, Any] | None = None) -> bool:
    """Whether an Event Source of ``type_id`` with ``config`` needs a Watch wire.

    ``required_when`` makes the requirement depend on one config field, such
    as a terminal Resident Event watching the Lineage instead of named
    Residents.
    """

    definition = _type_definitions_for("event").get(str(type_id or "").strip().lower()) or {}
    contract = definition.get("watch_contract") or {}
    if not contract.get("required"):
        return False
    condition = contract.get("required_when")
    if not isinstance(condition, dict):
        return True
    return _field_is_active(
        {"visible_when": condition},
        all_fields=tuple(fields_for("event", type_id)),
        config=config or {},
    )


def event_types_watching_many() -> frozenset[str]:
    """Event types whose Event Source may watch several Residents; any one of them fires it."""

    return frozenset(
        type_id
        for type_id, definition in _type_definitions_for("event").items()
        if (definition.get("watch_contract") or {}).get("multiple") is True
    )


def config_paths(
    kind: str,
    type_id: object,
    *,
    include_advanced: bool = True,
) -> tuple[str, ...]:
    return tuple(
        str(field.get("path") or "")
        for field in fields_for(kind, type_id)
        if (
            str(field.get("path") or "").startswith("config.")
            and (include_advanced or not field.get("advanced_only"))
        )
    )


def _field_is_active(
    field: Mapping[str, Any],
    *,
    all_fields: tuple[dict[str, Any], ...],
    config: Mapping[str, Any],
) -> bool:
    visibility = field.get("visible_when")
    if not isinstance(visibility, dict) or not visibility.get("path"):
        return True
    dependency_key = str(visibility["path"]).removeprefix("config.")
    dependency = next(
        (
            item
            for item in all_fields
            if str(item.get("path") or "").removeprefix("config.")
            == dependency_key
        ),
        {},
    )
    value = (
        config[dependency_key]
        if dependency_key in config
        else dependency.get("default")
    )
    return str(value if value is not None else "") == str(
        visibility.get("equals")
        if visibility.get("equals") is not None
        else ""
    )


def authoring_config_fields(
    kind: str,
    type_id: object,
    *,
    config: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    """Return the exact config keys active for one selected primitive type."""

    normalized = str(kind or "").strip().lower()
    owned_fields = _read_only_fields(normalized, str(type_id or "").strip().lower())
    return frozenset({
        "event_type",
        *(
            str(field.get("path") or "").removeprefix("config.")
            for field in owned_fields
            if (
                str(field.get("path") or "").startswith("config.")
                and (
                    config is None
                    or _field_is_active(
                        field,
                        all_fields=owned_fields,
                        config=config,
                    )
                )
            )
        ),
    })


def manifest_fragment() -> Mapping[str, Any]:
    """Return the exact fragment installed into the browser Program manifest."""

    payload = _definitions()
    return {
        key: deepcopy(payload[key])
        for key in (
            "default_types",
            "event_type_categories",
            "event_common_fields",
            "event_types",
            "component_types",
        )
    }
