"""Avatars of Users, Residents, Lineages and Projects.

An avatar is one of the 42 ocean sprites or an uploaded image, normalized to a
PNG of at most 256 px.  Each subject has one JSON record in its store's root.
An uploaded image is kept beside the record and named by its content hash, so
the image address of one version never changes and a browser may keep it.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import tempfile
import warnings
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image, UnidentifiedImageError

from management_common.format_tags import schema_matches
from management_portal.shared_state import exclusive_file_lock

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


def avatar_root(kind: str) -> Path:
    """Use the explicit deployment path or canonical root, without moving data."""
    configured = os.environ.get(f"PORTAL_{kind.upper()}_AVATAR_ROOT")
    if configured is not None:
        return Path(configured)
    return Path.home() / ".tfi" / "portal" / f"{kind}_avatars"


class AvatarStore:
    """One subject kind's avatar records.

    Subclasses say how a subject names its files (``stem``), the value its
    record is bound to (``identity``) and its image address (``address``).
    """

    schema = ""
    identity_field = ""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._cache: dict[str, tuple[tuple[int, int, int], dict[str, Any]]] = {}

    def stem(self, subject: str) -> str:
        raise NotImplementedError

    def identity(self, subject: str) -> str:
        return subject

    def address(self, subject: str) -> dict[str, str]:
        raise NotImplementedError

    def _path(self, subject: str) -> Path:
        return self.root / (self.stem(subject) + ".json")

    def _image_path(self, subject: str, version: str) -> Path:
        return self.root / f"{self.stem(subject)}-{version}.png"

    def read(self, subject: str) -> dict[str, Any] | None:
        """The subject's saved avatar, or None when it has none."""

        path = self._path(subject)
        try:
            status = path.stat()
        except FileNotFoundError:
            return None
        # Records are replaced, never edited in place, so this tells a change
        # made by any Portal process apart without reading the file again.
        signature = (status.st_ino, status.st_mtime_ns, status.st_size)
        cached = self._cache.get(path.name)
        if cached is not None and cached[0] == signature:
            return dict(cached[1])
        avatar = self._avatar(subject, json.loads(path.read_text(encoding="utf-8")))
        if len(self._cache) >= 4096:
            self._cache.clear()
        self._cache[path.name] = (signature, avatar)
        return dict(avatar)

    def _avatar(self, subject: str, record: Any) -> dict[str, Any]:
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

    def _write(self, path: Path, content: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(dir=self.root, prefix=".avatar-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    def _save(self, subject: str, avatar: dict[str, Any], content: bytes | None = None) -> None:
        """Write a record under the subject's lock; images of other versions go."""

        path = self._path(subject)
        record = {"schema": self.schema, self.identity_field: self.identity(subject), **avatar}
        if content is not None:
            self._write(self._image_path(subject, avatar["version"]), content)
        self._write(path, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        for image in self.root.glob(self.stem(subject) + "-*.png"):
            if image.name != f"{self.stem(subject)}-{avatar.get('version')}.png":
                image.unlink(missing_ok=True)

    def update(self, subject: str, payload: Any) -> dict[str, Any]:
        path = self._path(subject)
        if not isinstance(payload, dict):
            raise ValueError("Avatar payload must be an object")
        if payload.get("kind") == "sprite" and set(payload) == {"kind", "sprite_index"}:
            avatar = sprite(payload["sprite_index"])
            content = None
        elif payload.get("kind") == "upload" and set(payload) == {"kind", "data_base64"}:
            content = normalized_image(payload["data_base64"])
            avatar = {"kind": "upload", "version": hashlib.sha256(content).hexdigest()}
        else:
            raise ValueError("Specify only a sprite index or an uploaded image")
        self._prepare_root()
        with exclusive_file_lock(path.with_suffix(".lock")):
            self._save(subject, avatar, content)
        return self.read(subject)

    def delete(self, subject: str) -> bool:
        path = self._path(subject)
        if not self.root.exists():
            return False
        removed = False
        with exclusive_file_lock(path.with_suffix(".lock")):
            for candidate in [path, *self.root.glob(self.stem(subject) + "-*.png")]:
                try:
                    candidate.unlink()
                    removed = True
                except FileNotFoundError:
                    continue
        return removed

    def move(self, subject: str, target: str) -> bool:
        """Give the subject's avatar to ``target`` (a renamed subject)."""

        avatar = self.read(subject)
        if avatar is None:
            return False
        content = self._image_path(subject, avatar["version"]).read_bytes() if avatar["kind"] == "upload" else None
        saved = {key: avatar[key] for key in ("kind", "sprite_index", "sprite_rarity", "version") if key in avatar}
        self._prepare_root()
        with exclusive_file_lock(self._path(target).with_suffix(".lock")):
            self._save(target, saved, content)
        self.delete(subject)
        return True

    def image(self, subject: str, version: str) -> bytes:
        avatar = self.read(subject)
        if not avatar or avatar["kind"] != "upload" or avatar["version"] != version:
            raise FileNotFoundError("Avatar version is not current")
        return self._image_path(subject, version).read_bytes()


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


class ScopeAvatarStore(AvatarStore):
    """Lineage and Project avatars; without one the Menu shows the title's first letter."""

    schema = SCOPE_SCHEMA
    identity_field = "subject"

    def identity(self, subject: str) -> str:
        kind, _, object_id = str(subject).partition(":")
        return scope_subject(kind, object_id)

    def stem(self, subject: str) -> str:
        return hashlib.sha256(self.identity(subject).encode("utf-8")).hexdigest()

    def address(self, subject: str) -> dict[str, str]:
        kind, _, object_id = self.identity(subject).partition(":")
        return {kind: object_id}


@lru_cache(maxsize=8)
def _scope_store(root: str) -> ScopeAvatarStore:
    return ScopeAvatarStore(Path(root))


def scope_avatar_store() -> ScopeAvatarStore:
    return _scope_store(str(avatar_root("scope")))


def get_scope_avatar(kind: str, object_id: str) -> dict[str, Any] | None:
    try:
        return scope_avatar_store().read(scope_subject(kind, object_id))
    except (OSError, ValueError):
        logging.getLogger(__name__).warning("%s avatar unavailable for %s", kind, object_id)
        return None


def change_avatar(store: AvatarStore, subject: str, value: Any, caller: str) -> dict[str, Any] | None:
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
