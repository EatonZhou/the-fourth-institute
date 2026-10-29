from __future__ import annotations
from management_common.portal_paths import path as portal_path

import copy
import hashlib
import json
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping

from .shared_state import exclusive_file_lock
from .presentation_store import database
from management_common.database_time import iso_time
from management_common.clock import utc_now_iso


SCHEMA = "tfi.portal-ui-state.v1"
DEFAULT_LOCK_ROOT = (portal_path("staging_root") / "shared" / "portal_data" / "ui-association-locks")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_MAX_KEY_CHARS = 600
_MAX_VALUE_BYTES = 1024 * 1024
_MAX_ASSOCIATIONS_PER_NAMESPACE = 2_000


def _name(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not _NAME_RE.fullmatch(normalized):
        raise ValueError(f"{field} is invalid")
    return normalized


def _actor(value: object) -> str:
    normalized = str(value or "").strip()
    if (
        not normalized
        or len(normalized) > 160
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("actor is invalid")
    return normalized


def _key(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > _MAX_KEY_CHARS:
        raise ValueError("key is required and must be at most 600 characters")
    return normalized


def _value(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("value must be an object")
    normalized = copy.deepcopy(dict(value))
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _MAX_VALUE_BYTES:
        raise ValueError(
            f"value is too large: {len(encoded)} bytes (maximum {_MAX_VALUE_BYTES} bytes)"
        )
    return normalized


class PortalUIStateStore:
    """Cross-device, actor-scoped associations owned by Portal presentation."""

    def __init__(self, *, lock_root: str | Path = DEFAULT_LOCK_ROOT) -> None:
        self.database = database()
        self.key_lock_root = Path(lock_root).expanduser()

    def list(self, actor: object, namespace: object) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return [{"key": row[0], "value": row[1], "updated_at": iso_time(row[2])} for row in connection.execute(
                "SELECT association_key,value_json,updated_at FROM associations WHERE actor=%s AND namespace=%s ORDER BY association_key",
                (_actor(actor), _name(namespace, "namespace")))]

    def list_namespace(self, namespace: object) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return [{"actor": row[0], "key": row[1], "value": row[2]} for row in connection.execute(
                "SELECT actor,association_key,value_json FROM associations WHERE namespace=%s ORDER BY actor,association_key",
                (_name(namespace, "namespace"),))]

    def get(self, actor: object, namespace: object, key: object) -> dict[str, Any] | None:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT value_json,updated_at FROM associations WHERE actor=%s AND namespace=%s AND association_key=%s",
                (_actor(actor), _name(namespace, "namespace"), _key(key))).fetchone()
            return {"key": _key(key), "value": row[0], "updated_at": iso_time(row[1])} if row else None

    def put(self, actor: object, namespace: object, key: object, value: object) -> dict[str, Any]:
        actor_id, namespace_id, target = _actor(actor), _name(namespace, "namespace"), _key(key)
        normalized = _value(value)
        updated = utc_now_iso()
        with self.database.transaction("associations:" + actor_id + ":" + namespace_id) as connection:
            count = connection.execute("SELECT count(*) FROM associations WHERE actor=%s AND namespace=%s",
                                       (actor_id, namespace_id)).fetchone()[0]
            exists = connection.execute("SELECT 1 FROM associations WHERE actor=%s AND namespace=%s AND association_key=%s",
                                        (actor_id, namespace_id, target)).fetchone()
            if not exists and count >= _MAX_ASSOCIATIONS_PER_NAMESPACE:
                raise ValueError("namespace contains too many associations")
            connection.execute("""INSERT INTO associations VALUES (%s,%s,%s,%s::jsonb,%s)
                ON CONFLICT(actor,namespace,association_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at""",
                (actor_id, namespace_id, target, json.dumps(normalized), updated))
        return {"key": target, "value": normalized, "updated_at": updated}

    def remove(self, actor: object, namespace: object, key: object) -> bool:
        actor_id, namespace_id, target = _actor(actor), _name(namespace, "namespace"), _key(key)
        with self.database.transaction("associations:" + actor_id + ":" + namespace_id) as connection:
            return bool(connection.execute("DELETE FROM associations WHERE actor=%s AND namespace=%s AND association_key=%s",
                                           (actor_id, namespace_id, target)).rowcount)

    @contextmanager
    def association_lock(
        self,
        actor: object,
        namespace: object,
        key: object,
    ) -> Iterator[None]:
        identity = "\0".join(
            (_actor(actor), _name(namespace, "namespace"), _key(key))
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        self.key_lock_root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.key_lock_root / f"{digest}.lock"):
            yield


_STORE: PortalUIStateStore | None = None
_STORE_LOCK = threading.Lock()


def get_portal_ui_state_store() -> PortalUIStateStore:
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                _STORE = PortalUIStateStore()
    return _STORE
