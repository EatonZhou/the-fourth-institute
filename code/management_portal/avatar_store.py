"""Avatars of Users, Residents, Lineages and Projects.

An avatar is one of the 42 ocean sprites or an uploaded PNG of at most 256 px.
Records and image bytes are in PostgreSQL (``avatar_database``). Image URLs
carry their content hashes.
"""
from __future__ import annotations

import base64
import io
import logging
import re
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image, UnidentifiedImageError

from management_common.format_tags import schema_matches

SPRITE_RARITIES = (
    "R", "SR", "SR", "SSR", "R", "R", "R", "R", "R",
    "R", "R", "R", "R", "SR", "R", "R", "R", "R",
    "R", "R", "SSR", "SR", "R", "SR", "SR", "R", "R",
    "SR", "R", "SR", "SSR", "R", "R", "SR", "SR", "SSR",
    "SSSR", "SSSR", "SSSR", "SSSR", "SSSR", "SSSR",
)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_JSON_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 1024
UPLOAD_PURPOSE = "avatar"
_VERSION = re.compile(r"[0-9a-f]{64}")


def sprite(index: Any) -> dict[str, Any]:
    if type(index) is not int or not 0 <= index < len(SPRITE_RARITIES):
        raise ValueError("Invalid avatar sprite")
    return {"kind": "sprite", "sprite_index": index, "sprite_rarity": SPRITE_RARITIES[index]}


def normalized_image(encoded: Any) -> bytes:
    """A PNG of at most 256 px, stripped of metadata, from a base64 PNG, JPEG or WebP."""

    if not isinstance(encoded, str) or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError("Avatar image exceeds 2 MiB")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid base64 image") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Avatar image must be at most 2 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError("Only PNG, JPEG and WebP images are supported")
                if not all(16 <= side <= 2048 for side in image.size):
                    raise ValueError("Avatar dimensions must be 16..2048 pixels")
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError("Animated avatars are not supported")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
                normalized = image.convert("RGBA")
                normalized.thumbnail((256, 256))
                clean = Image.new("RGBA", normalized.size)
                clean.paste(normalized)
                output = io.BytesIO()
                clean.save(output, format="PNG")
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid avatar image") from exc


class AvatarSubjects:
    """How one subject kind is named: its validation, identity and image address.

    Records and image bytes live in PostgreSQL (``avatar_database``); this class
    only knows the subjects. ``schema`` and ``identity_field`` name the record
    format a database row is presented in.
    """

    schema = ""
    identity_field = ""

    def check(self, subject: str) -> str:
        """Validate the subject, raising ValueError (or PermissionError for Users)."""
        raise NotImplementedError

    def identity(self, subject: str) -> str:
        return subject

    def address(self, subject: str) -> dict[str, str]:
        raise NotImplementedError

    def avatar(self, subject: str, record: Any) -> dict[str, Any]:
        """The avatar a stored record stands for, with its image URL."""
        if not isinstance(record, dict) or not schema_matches(record.get("schema"), self.schema) \
                or record.get(self.identity_field) != self.identity(subject):
            raise ValueError("Invalid avatar record")
        kind = record.get("kind", "sprite")
        if kind == "sprite":
            return sprite(record.get("sprite_index"))
        version = record.get("version")
        if kind != "upload" or not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("Invalid avatar version")
        query = urlencode({**self.address(subject), "v": version})
        return {"kind": "upload", "version": version, "url": "/api/account/avatar/image?" + query}


SCOPE_SCHEMA = "tfi.scope-avatar.v1"
SCOPE_KINDS = ("lineage", "project")


def scope_subject(kind: str, object_id: str) -> str:
    """``lineage:<lineage_id>`` or ``project:<project_id>``."""

    if kind == "lineage":
        if not re.fullmatch(r"lineage_[0-9a-f]{12}", str(object_id or "")):
            raise ValueError("Invalid Lineage avatar identity")
    elif kind == "project":
        if not isinstance(object_id, str) or not object_id or object_id != object_id.strip() \
                or len(object_id) > 200 or any(ord(char) < 32 for char in object_id):
            raise ValueError("Invalid Project avatar identity")
    else:
        raise ValueError("Avatar scope must be a Lineage or Project")
    return f"{kind}:{object_id}"


class ScopeAvatarSubjects(AvatarSubjects):
    """Lineage and Project avatars; without one the Menu shows the title's first letter."""

    schema = SCOPE_SCHEMA
    identity_field = "subject"

    def identity(self, subject: str) -> str:
        kind, _, object_id = str(subject).partition(":")
        return scope_subject(kind, object_id)

    def check(self, subject: str) -> str:
        return self.identity(subject)

    def address(self, subject: str) -> dict[str, str]:
        kind, _, object_id = self.identity(subject).partition(":")
        return {kind: object_id}


def scope_avatar_store():
    from management_portal.avatar_database import DatabaseAvatarStore
    return DatabaseAvatarStore(ScopeAvatarSubjects(), "scope")


def get_scope_avatar(kind: str, object_id: str) -> dict[str, Any] | None:
    try:
        return scope_avatar_store().read(scope_subject(kind, object_id))
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("%s avatar unavailable for %s", kind, object_id)
        return None


def change_avatar(store, subject: str, value: Any, caller: str) -> dict[str, Any] | None:
    """Apply an ISA ``avatar`` change and return the saved avatar.

    ``value`` is ``{"sprite_index": n}``, ``{"upload_id": id}`` of a finished
    ``purpose=avatar`` upload by ``caller`` (a bare upload ID also works), or
    null, which removes the saved avatar.
    """

    if value is None:
        store.delete(subject)
        return None
    if isinstance(value, str):
        value = {"upload_id": value}
    if not isinstance(value, dict) or len(value) != 1 or not {"sprite_index", "upload_id"} & set(value):
        raise ValueError("avatar 只能是 {sprite_index}、{upload_id} 或 null")
    if "sprite_index" in value:
        return store.update(subject, {"kind": "sprite", "sprite_index": value["sprite_index"]})
    from management_common.actor_identity import same_actor
    from management_portal import permission_system
    from management_portal.resources.isa import read_upload

    upload = read_upload(str(value["upload_id"]))
    if upload.get("purpose") != UPLOAD_PURPOSE:
        raise ValueError(f"头像需要 purpose={UPLOAD_PURPOSE} 的图片上传")
    # Where uploads stay their creator's, an avatar may only publish the caller's own.
    if permission_system.owners_only() and not same_actor(upload.get("created_by"), caller):
        raise ValueError("头像只能使用自己上传的图片")
    if not upload.get("ready"):
        raise ValueError("头像上传尚未完成")
    data = Path(str(upload["path"])).read_bytes()
    return store.update(subject, {"kind": "upload", "data_base64": base64.b64encode(data).decode("ascii")})
