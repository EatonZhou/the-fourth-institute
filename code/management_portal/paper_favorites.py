"""Personal Paper bookmarks; read access remains owned by ResourceService."""
from __future__ import annotations

import re

from management_portal.resources import get_resource_service
from management_portal import presentation_store


def favorite_ids(actor: str) -> list[str]:
    if not str(actor or "").strip():
        return []
    return presentation_store.favorite_ids(actor.strip(), 'paper')


def set_favorite(actor: str, paper_id: str, favorite: bool) -> dict:
    actor = str(actor or "").strip()
    if not actor:
        raise PermissionError("authenticated actor required")
    if not isinstance(paper_id, str) or not re.fullmatch(r"paper_[0-9a-f]{12}", paper_id):
        raise ValueError("valid paper_id required")
    if type(favorite) is not bool:
        raise ValueError("favorite must be a boolean")
    if favorite:
        # A bookmark never grants access. Removal remains possible after loss
        # of access or deletion, without resolving the now-unavailable resource.
        get_resource_service().get(paper_id, actor=actor)
    presentation_store.set_favorite(actor, 'paper', paper_id, favorite)
    return {"paper_id": paper_id, "favorite": favorite}
