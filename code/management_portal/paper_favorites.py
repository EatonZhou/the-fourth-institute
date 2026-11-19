"""Personal Paper bookmarks; read access remains owned by ResourceService."""
from __future__ import annotations

import re

from management_portal.resources import get_resource_service
from management_portal import presentation_store


def archives(actor: str, paper_id: str = "") -> list[dict]:
    with presentation_store.database().read() as connection:
        names = [row[0] for row in connection.execute(
            "SELECT association_key FROM associations WHERE actor=%s AND namespace='paper-archives' ORDER BY association_key",
            (actor,),
        )]
        saved = {row[0] for row in connection.execute(
            "SELECT surface FROM favorites WHERE actor=%s AND target_id=%s", (actor, paper_id),
        )} if paper_id else set()
    return [{"name": name, "saved": _surface(name) in saved} for name in ["", *names]]


def create_archive(actor: str, name: str):
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError("Archive name must contain 1–80 characters")
    name = name.strip()
    if not actor:
        raise PermissionError("authenticated actor required")
    with presentation_store.database().transaction("paper-archives:" + actor) as connection:
        connection.execute("""INSERT INTO associations(actor,namespace,association_key,value_json)
            VALUES (%s,'paper-archives',%s,'{}') ON CONFLICT DO NOTHING""", (actor, name))
    return {"name": name}


def _surface(name):
    return "paper:" + name if name else "paper"


def favorite_ids(actor: str, archive: str = "") -> list[str]:
    if not str(actor or "").strip():
        return []
    return presentation_store.favorite_ids(actor.strip(), _surface(archive))


def set_favorite(actor: str, paper_id: str, favorite: bool, archive: str = '') -> dict:
    actor = str(actor or "").strip()
    if not actor:
        raise PermissionError("authenticated actor required")
    if not isinstance(paper_id, str) or not re.fullmatch(r"paper_[0-9a-f]{12}", paper_id):
        raise ValueError("valid paper_id required")
    if type(favorite) is not bool:
        raise ValueError("favorite must be a boolean")
    if not isinstance(archive, str) or (archive and archive not in {item["name"] for item in archives(actor)}):
        raise ValueError("Unknown archive")
    if favorite:
        # A bookmark never grants access. Removal remains possible after loss
        # of access or deletion, without resolving the now-unavailable resource.
        get_resource_service().get(paper_id, actor=actor)
    presentation_store.set_favorite(actor, _surface(archive), paper_id, favorite)
    return {"paper_id": paper_id, "favorite": favorite}
