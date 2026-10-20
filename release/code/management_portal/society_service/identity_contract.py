from __future__ import annotations

from collections.abc import Iterable

from management_common.resource_identity import get_resource_type_registry


def new_id(resource_type: str, existing_ids: Iterable[str] = ()) -> str:
    return get_resource_type_registry().new_id(resource_type, existing_ids)


def validate_id(resource_id: str, resource_type: str | None = None):
    return get_resource_type_registry().validate(resource_id, resource_type)


def is_valid_id(
    resource_id: object,
    resource_type: str | None = None,
) -> bool:
    try:
        validate_id(str(resource_id or "").strip(), resource_type)
    except ValueError:
        return False
    return True
