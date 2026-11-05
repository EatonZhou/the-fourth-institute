from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from management_common.principal import is_user_or_resident
from management_portal.file_access_policy import require_identity


class ResourceAccessPolicy:
    """Resource discovery and direct reads ask the active permission scheme.

    Lists use the same ResolveResource requirement as an exact identity read,
    in a batch before pagination. Domain resolvers retain their business rules.
    """

    def readable_ids(self, identifiers, *, actor: str = "") -> set[str]:
        from management_portal import permission_system

        if not is_user_or_resident(actor):
            return set()
        return permission_system.permitted_ids(actor, "ResolveResource", "address", identifiers)

    def can_read(self, value: Mapping[str, Any], *, actor: str = "") -> bool:
        identifier = str(value.get("resource_id") or value.get("root_id") or "")
        return bool(identifier) and identifier in self.readable_ids([identifier], actor=actor)

    def authorize_read(self, value: Mapping[str, Any], *, actor: str = "") -> None:
        if not self.can_read(value, actor=actor):
            raise PermissionError("Resource is not accessible to this authenticated actor")
