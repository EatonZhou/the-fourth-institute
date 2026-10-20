"""Instance-owned avatar records and PNG bytes in PostgreSQL.

The file store is an import source only. A deleted row remains as a tombstone
so that a legacy file cannot resurrect a user's removed or renamed avatar.
"""
from __future__ import annotations

import hashlib
import os
from functools import lru_cache

from management_common.domain_database import DomainDatabase
from management_portal.avatar_store import MAX_IMAGE_BYTES, normalized_image, sprite

DDL = """
CREATE TABLE IF NOT EXISTS avatars (
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('user', 'resident', 'scope')),
    subject TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('sprite', 'upload', 'deleted')),
    sprite_index INTEGER,
    version TEXT,
    image BYTEA,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (subject_kind, subject),
    CHECK (
        (kind = 'sprite' AND sprite_index BETWEEN 0 AND 41 AND sprite_index IS NOT NULL
         AND version IS NULL AND image IS NULL)
        OR (kind = 'upload' AND sprite_index IS NULL AND version IS NOT NULL
            AND version ~ '^[0-9a-f]{64}$' AND image IS NOT NULL
            AND octet_length(image) BETWEEN 1 AND 2097152)
        OR (kind = 'deleted' AND sprite_index IS NULL AND version IS NULL AND image IS NULL)
    )
);
"""


@lru_cache(maxsize=8)
def _database(dsn, namespace):
    return DomainDatabase("portal_avatars", "avatars", DDL)


def database():
    return _database(os.getenv("PORTAL_DATABASE_URL"), os.getenv("PORTAL_DATABASE_NAMESPACE"))


class DatabaseAvatarStore:
    """Use existing subject validation and URLs with a transactional owner."""

    def __init__(self, legacy, kind, *, default_sprite=None, persist_default=False):
        if kind not in ("user", "resident", "scope"):
            raise ValueError("Invalid avatar subject kind")
        self.legacy = legacy
        self.kind = kind
        self.default_sprite = default_sprite
        self.persist_default = persist_default

    def _key(self, subject):
        self.legacy.stem(subject)  # Validate even reads, deletes and moves.
        return self.kind, self.legacy.identity(subject)

    def _row(self, connection, subject, *, image=False):
        fields = "kind, sprite_index, version" + (", image" if image else "")
        row = connection.execute(
            f"SELECT {fields} FROM avatars WHERE subject_kind=%s AND subject=%s",
            self._key(subject),
        ).fetchone()
        return dict(row) if row is not None else None

    def _avatar(self, subject, row):
        if row is None or row["kind"] == "deleted":
            return None
        return self.legacy._avatar(subject, {
            "schema": self.legacy.schema,
            self.legacy.identity_field: self.legacy.identity(subject), **row,
        })

    def _write(self, connection, subject, avatar, content=None, *, only_missing=False):
        conflict = "DO NOTHING" if only_missing else """DO UPDATE SET
            kind=EXCLUDED.kind, sprite_index=EXCLUDED.sprite_index,
            version=EXCLUDED.version, image=EXCLUDED.image, updated_at=now()"""
        connection.execute(
            """INSERT INTO avatars (subject_kind, subject, kind, sprite_index, version, image)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (subject_kind, subject) """ + conflict,
            (*self._key(subject), avatar["kind"], avatar.get("sprite_index"),
             avatar.get("version"), content),
        )

    def import_legacy(self, subject):
        """Import one complete legacy record, never replacing database choices."""
        key = self._key(subject)
        with database().transaction(":".join(key)) as connection:
            existing = self._row(connection, subject)
            if existing is not None:
                return self._avatar(subject, existing)
            avatar = self.legacy.read(subject)
            if avatar is None:
                return None
            content = None
            if avatar["kind"] == "upload":
                content = self.legacy.image(subject, avatar["version"])
                if not 0 < len(content) <= MAX_IMAGE_BYTES or hashlib.sha256(content).hexdigest() != avatar["version"]:
                    raise ValueError("Legacy avatar image does not match its recorded hash")
            self._write(connection, subject, avatar, content, only_missing=True)
            return self._avatar(subject, self._row(connection, subject))

    def read(self, subject):
        self._key(subject)
        with database().read() as connection:
            row = self._row(connection, subject)
        return self._avatar(subject, row) if row is not None else self.import_legacy(subject)

    def get_existing(self, subject):
        return self.read(subject)

    def get(self, subject):
        avatar = self.read(subject)
        if avatar is not None or self.default_sprite is None:
            return avatar
        if not self.persist_default:
            return sprite(self.default_sprite())
        with database().transaction(":".join(self._key(subject))) as connection:
            row = self._row(connection, subject)
            avatar = self._avatar(subject, row)
            if avatar is None:
                avatar = sprite(self.default_sprite())
                self._write(connection, subject, avatar)
            return avatar

    def update(self, subject, payload):
        key = self._key(subject)
        if not isinstance(payload, dict):
            raise ValueError("Avatar payload must be an object")
        if payload.get("kind") == "sprite" and set(payload) == {"kind", "sprite_index"}:
            avatar, content = sprite(payload["sprite_index"]), None
        elif payload.get("kind") == "upload" and set(payload) == {"kind", "data_base64"}:
            content = normalized_image(payload["data_base64"])
            avatar = {"kind": "upload", "version": hashlib.sha256(content).hexdigest()}
        else:
            raise ValueError("Specify only a sprite index or an uploaded image")
        with database().transaction(":".join(key)) as connection:
            self._write(connection, subject, avatar, content)
        return self._avatar(subject, avatar)

    def delete(self, subject):
        with database().transaction(":".join(self._key(subject))) as connection:
            row = self._row(connection, subject)
            existed = (row is not None and row["kind"] != "deleted") or (
                row is None and self.legacy._path(subject).exists()
            )
            self._write(connection, subject, {"kind": "deleted"})
        return existed

    def move(self, subject, target):
        self._key(subject)
        self._key(target)
        self.import_legacy(subject)
        if subject == target:
            return self.read(subject) is not None
        # Sorted locks avoid inversion when two subjects are renamed concurrently.
        with database().transaction() as connection:
            for key in sorted((self._key(subject), self._key(target))):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(current_schema() || ':' || %s, 0))",
                    ("avatars:" + ":".join(key),),
                )
            row = self._row(connection, subject, image=True)
            if self._avatar(subject, row) is None:
                return False
            self._write(connection, target, row, row.get("image"))
            self._write(connection, subject, {"kind": "deleted"})
        return True

    def image(self, subject, version):
        self.read(subject)  # Import legacy bytes if this is the first request.
        with database().read() as connection:
            row = self._row(connection, subject, image=True)
        if not row or row["kind"] != "upload" or row["version"] != version:
            raise FileNotFoundError("Avatar version is not current")
        return bytes(row["image"])
