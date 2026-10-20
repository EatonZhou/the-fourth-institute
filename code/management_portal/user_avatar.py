from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

from management_common.principal import actor_principal
from management_portal.avatar_store import MAX_IMAGE_BYTES, MAX_JSON_BYTES, SPRITE_RARITIES, AvatarStore, avatar_root, sprite

__all__ = ["MAX_IMAGE_BYTES", "MAX_JSON_BYTES", "SPRITE_RARITIES", "UserAvatarStore", "get_user_avatar", "sprite", "user_avatar_store"]

SCHEMA = 'tfi.user-avatar.v1'


def _user(username):
    if not isinstance(username, str) or not username or username != username.strip():
        raise PermissionError('Authenticated User required')
    if actor_principal(username).kind != 'user':
        raise PermissionError('Authenticated User required')
    return hashlib.sha256(username.encode('utf-8')).hexdigest()


class UserAvatarStore(AvatarStore):
    """A User without a saved avatar shows the first sprite."""

    schema = SCHEMA
    identity_field = 'username'

    def stem(self, username):
        return _user(username)

    def address(self, username):
        return {'user': username}

    def get(self, username):
        return self.read(username) or sprite(0)


@lru_cache(maxsize=8)
def _store(root):
    return UserAvatarStore(Path(root))


def get_user_avatar(username):
    return user_avatar_store().get(username)


def user_avatar_store():
    return _store(str(avatar_root('user')))
