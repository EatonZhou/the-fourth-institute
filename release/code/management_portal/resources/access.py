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
    """Every authenticated User or Resident reads resources.

    No store supplies resource authorization: the permission system decides
    it (decision_b820faf42218), the facade authenticates
    actors and domain resolvers enforce their own business rules.
    """

    def can_read(self, value: Mapping[str, Any], *, actor: str = "") -> bool:
        # ``value`` is the record callers ask about; no read rule depends on it.
        return is_user_or_resident(actor)

    def authorize_read(self, value: Mapping[str, Any], *, actor: str = "") -> None:
        if not self.can_read(value, actor=actor):
            raise PermissionError("Resource is not accessible to this authenticated actor")
