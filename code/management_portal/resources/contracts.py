from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .address import ResourceAddress
from .schema import RESOURCE_INDEX_SCHEMA
from management_common.text_values import stripped_text


def _string_set(values: object) -> frozenset[str]:
    if values is None:
        candidates: Iterable[object] = ()
    elif isinstance(values, str):
        candidates = (values,)
    elif isinstance(values, Iterable) and not isinstance(values, Mapping):
        candidates = values
    else:
        candidates = (values,)
    return frozenset(stripped_text(value) for value in candidates if stripped_text(value))


@dataclass(frozen=True)
class ResourceQuery:
    actor: str = ""
    q: str = ""
    resource_types: frozenset[str] = frozenset()
    facets: frozenset[str] = frozenset()
    projects: frozenset[str] = frozenset()
    statuses: frozenset[str] = frozenset()
    attribute_filters: tuple[tuple[str, frozenset[str]], ...] = ()
    limit: int = 50
    offset: int = 0
    include_attributes: bool = False

    @classmethod
    def from_kwargs(cls, values: Mapping[str, Any]) -> "ResourceQuery":
        raw_filters = values.get("attribute_filters")
        filters = raw_filters if isinstance(raw_filters, Mapping) else {}
        normalized_filters = tuple(
            sorted(
                (
                    stripped_text(key),
                    _string_set(raw_values),
                )
                for key, raw_values in filters.items()
                if stripped_text(key)
            )
        )
        return cls(
            actor=stripped_text(values.get("actor")),
            q=stripped_text(values.get("q")),
            resource_types=_string_set(values.get("resource_types") or ()),
            facets=_string_set(values.get("facets") or ()),
            projects=_string_set(values.get("projects") or ()),
            statuses=_string_set(values.get("statuses") or ()),
            attribute_filters=normalized_filters,
            limit=max(1, min(int(values.get("limit") or 50), 5000)),
            offset=max(0, int(values.get("offset") or 0)),
            include_attributes=bool(values.get("include_attributes")),
        )

    @property
    def filters(self) -> dict[str, frozenset[str]]:
        return dict(self.attribute_filters)


@dataclass(frozen=True)
class ResourceCapabilities:
    resource_type: str
    indexed: bool
    resolvable: bool
    readable: bool
    registerable: bool
    updatable: bool
    content_updatable: bool
    unregisterable: bool
    actions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "indexed": self.indexed,
            "resolvable": self.resolvable,
            "readable": self.readable,
            "registerable": self.registerable,
            "updatable": self.updatable,
            "content_updatable": self.content_updatable,
            "unregisterable": self.unregisterable,
            "actions": list(self.actions),
        }


def normalize_resolution(
    parsed: ResourceAddress,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise TypeError("resource resolver must return an object")
    source = dict(payload)
    root_id = stripped_text(source.get("root_id") or source.get("resource_id"))
    resource_type = stripped_text(source.get("resource_type"))
    address = stripped_text(source.get("address"))
    if root_id and root_id != parsed.root_id:
        raise ValueError(
            f"resource resolver returned root {root_id!r}, expected {parsed.root_id!r}"
        )
    if resource_type and resource_type != parsed.resource_type:
        raise ValueError(
            "resource resolver returned type "
            f"{resource_type!r}, expected {parsed.resource_type!r}"
        )
    if address and address.strip("/") != parsed.value:
        raise ValueError(
            f"resource resolver returned address {address!r}, expected {parsed.value!r}"
        )
    raw_record = source.get("record")
    if not isinstance(raw_record, Mapping):
        raise TypeError("resource resolver result requires a record object")
    record = dict(raw_record)
    record_id = stripped_text(record.get("resource_id"))
    if record_id != parsed.root_id:
        raise ValueError(
            "resource resolver record must contain the canonical resource_id "
            f"{parsed.root_id!r}"
        )
    identity_aliases = sorted(
        key
        for key, value in record.items()
        if key != "resource_id"
        and (key == "id" or key.endswith("_id"))
        and stripped_text(value) == parsed.root_id
    )
    if identity_aliases:
        raise ValueError(
            "resource resolver record contains identity aliases: "
            + ", ".join(identity_aliases)
        )
    raw_metadata = source.get("metadata", {})
    if not isinstance(raw_metadata, Mapping):
        raise TypeError("resource resolver metadata must be an object")
    normalized = {
        **source,
        "schema": "tfi.resource_resolution.v2",
        "address": parsed.value,
        "root_id": parsed.root_id,
        "resource_id": parsed.root_id,
        "resource_type": parsed.resource_type,
        "target_kind": stripped_text(source.get("target_kind")) or parsed.resource_type,
        "exists": bool(source.get("exists", True)),
        "record": record,
        "metadata": dict(raw_metadata),
    }
    return normalized


def empty_query_result(query: ResourceQuery, *, index_updated_at: str = "") -> dict[str, Any]:
    return {
        "schema": RESOURCE_INDEX_SCHEMA,
        "index_updated_at": stripped_text(index_updated_at),
        "query": query.q,
        "total": 0,
        "offset": query.offset,
        "limit": query.limit,
        "resources": [],
    }
