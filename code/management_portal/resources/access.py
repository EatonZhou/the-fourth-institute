from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from management_common.principal import actor_principal, is_user_or_resident


def require_identity(actor, *, descriptors=None):
    """An active registered User or a live Resident; authentication, not permission."""
    try:
        identity = actor_principal(actor) if actor else None
    except ValueError:
        identity = None
    if identity is None or identity.kind not in {'user', 'resident'}:
        raise PermissionError('Authenticated User or Resident required')
    if identity.kind == 'user':
        from management_portal.portal_access_control import get_portal_access_store
        access = get_portal_access_store()
        user = access.user(identity.id)
        if not user.get('configured') or user.get('status') != 'active':
            raise PermissionError('An active registered User is required')
    else:
        from .collaboration_registry import CollaborationDescriptorStore
        try:
            resident = (descriptors or CollaborationDescriptorStore()).get(identity.id)
        except KeyError:
            resident = None
        if not resident or resident.get('status') == 'deleted':
            raise PermissionError('An active authenticated Resident is required')
    return identity


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
