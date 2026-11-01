from __future__ import annotations

from management_common.principal import actor_principal
from management_portal.avatar_store import MAX_IMAGE_BYTES, MAX_JSON_BYTES, SPRITE_RARITIES, AvatarSubjects, sprite

__all__ = ["MAX_IMAGE_BYTES", "MAX_JSON_BYTES", "SPRITE_RARITIES", "UserAvatarSubjects", "get_user_avatar", "sprite", "user_avatar_store"]

SCHEMA = 'tfi.user-avatar.v1'


class UserAvatarSubjects(AvatarSubjects):
    """A User without a saved avatar shows the first sprite."""

    schema = SCHEMA
    identity_field = 'username'

    def check(self, username):
        if not isinstance(username, str) or not username or username != username.strip():
            raise PermissionError('Authenticated User required')
        if actor_principal(username).kind != 'user':
            raise PermissionError('Authenticated User required')
        return username

    def address(self, username):
        return {'user': username}


def get_user_avatar(username):
    return user_avatar_store().get(username)


def user_avatar_store():
    from management_portal.avatar_database import DatabaseAvatarStore
    return DatabaseAvatarStore(UserAvatarSubjects(), 'user', default_sprite=lambda: 0)
