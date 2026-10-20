from __future__ import annotations

import json
import logging
import re
import secrets
from functools import lru_cache
from pathlib import Path

from management_portal.avatar_store import SPRITE_RARITIES, AvatarStore, avatar_root
from management_portal.shared_state import exclusive_file_lock

__all__ = ["SPRITE_RARITIES", "ResidentAvatarStore", "get_avatar", "get_existing_avatar", "resident_avatar_store"]

SCHEMA = "tfi.resident-avatar.v1"


def _draw_sprite() -> int:
    if secrets.randbelow(100) == 0:
        rarity = "SSSR"
    else:
        roll = secrets.randbelow(100)
        rarity = "R" if roll < 75 else "SR" if roll < 95 else "SSR"
    return secrets.choice([i for i, value in enumerate(SPRITE_RARITIES) if value == rarity])


class ResidentAvatarStore(AvatarStore):
    """A Resident draws a random sprite on its first read; UpdateResident may change it."""

    schema = SCHEMA
    identity_field = "resident_id"

    def stem(self, resident_id: str) -> str:
        if not isinstance(resident_id, str) or not re.fullmatch(r"resident_[0-9a-f]{12}", resident_id):
            raise ValueError("Invalid Resident avatar identity")
        return resident_id

    def address(self, resident_id: str) -> dict[str, str]:
        return {"resident": resident_id}

    def get_existing(self, resident_id: str) -> dict[str, object] | None:
        return self.read(resident_id)

    def get(self, resident_id: str) -> dict[str, object]:
        avatar = self.read(resident_id)
        if avatar is not None:
            return avatar
        path = self._path(resident_id)
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(path.with_suffix(".lock")):
            if not path.exists():
                record = {"schema": SCHEMA, "resident_id": resident_id, "sprite_index": _draw_sprite()}
                self._write(path, json.dumps(record).encode("utf-8"))
        return self.read(resident_id)


@lru_cache(maxsize=8)
def _store(root: str) -> ResidentAvatarStore:
    return ResidentAvatarStore(Path(root))


def resident_avatar_store() -> ResidentAvatarStore:
    return _store(str(avatar_root("resident")))


def get_avatar(resident_id: str) -> dict[str, object] | None:
    try:
        return resident_avatar_store().get(resident_id)
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("Resident avatar binding unavailable for %s", resident_id)
        return None


def get_existing_avatar(resident_id: str) -> dict[str, object] | None:
    try:
        return resident_avatar_store().get_existing(resident_id)
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("Resident avatar binding unavailable for %s", resident_id)
        return None


def projection_fields(avatar: dict[str, object] | None) -> dict[str, object]:
    """The avatar fields merged into run and conversation entries, which have
    their own ``kind`` and ``url``: a sprite, or ``avatar_url`` of an upload."""

    if not avatar:
        return {}
    if avatar.get("kind") == "upload":
        return {"avatar_url": avatar["url"]}
    return {"sprite_index": avatar["sprite_index"], "sprite_rarity": avatar["sprite_rarity"]}
