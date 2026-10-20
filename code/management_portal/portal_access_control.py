from __future__ import annotations
from management_common.format_tags import schema_matches

from . import access_audit_store
from .identity_store import IdentityState

import copy
import hashlib
import hmac
import os
import re
import threading
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Any, Callable, Iterable, Iterator, Mapping


from management_common.principal import is_user_or_resident, validate_user_id
from management_portal.permission_system.categories import CATEGORIES as USER_CATEGORIES, DEFAULT as USER_CATEGORY_DEFAULT


ACCESS_SCHEMA = "tfi.portal-access.v1"
USER_STATUSES = frozenset({"active", "disabled", "deleted"})
USERNAME_RE = re.compile(r"[A-Za-z0-9_.@+-]{1,80}")
EMAIL_RE = re.compile(r"[^\s@]{1,64}@[^\s@]{1,189}")
PORTAL_SERVICE_ACTORS = frozenset({"portal"})


def is_active_user_or_resident(actor: object) -> bool:
    """A Resident, or a User whose account is not disabled or deleted.

    Callers ask it before the permission system decides the rest
    (decision_b820faf42218). A Portal service identity never passes.
    """
    identity = str(actor or "").strip()
    return bool(identity) and get_portal_access_store().is_active(identity) and is_user_or_resident(identity)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: object) -> str:
    return str(value or "").strip()






def _normalize_user(username: str, value: object) -> dict[str, Any]:
    if value is None:
        raw: Mapping[str, Any] = {}
        new_user = True
    elif isinstance(value, Mapping):
        raw = value
        new_user = False
    else:
        raise ValueError("Portal user does not match current contract")
    allowed_fields = {
        "username", "display_name", "email", "status", "session_version", "created_at",
        "updated_at", "updated_by", "category",
    }
    if not new_user and (set(raw) - allowed_fields or not allowed_fields <= set(raw)):
        raise ValueError("Portal user does not match current contract")
    status = _text(raw.get("status") or "active").casefold()
    if status not in USER_STATUSES:
        raise ValueError("Portal user status is invalid")
    # Portal 管理员, 一般用户 or 实习用户 (decision_600ae27b0538 §2); the permission scheme places Users by it.
    category = _text(raw.get("category")) or USER_CATEGORY_DEFAULT
    if category not in USER_CATEGORIES:
        raise ValueError("Portal user category is invalid")
    try:
        session_version = max(0, int(raw.get("session_version") or 0))
    except (TypeError, ValueError):
        raise ValueError("Portal user session_version is invalid") from None
    return {
        "username": username,
        "display_name": _text(raw.get("display_name")) or username,
        "email": _text(raw.get("email")),
        "status": status,
        "session_version": session_version,
        "created_at": _text(raw.get("created_at")),
        "updated_at": _text(raw.get("updated_at")),
        "updated_by": _text(raw.get("updated_by")),
        "category": category,
    }


class PortalAccessStore:
    """Shared User identity, active-account state and session invalidation."""

    def __init__(self) -> None:
        self.state = IdentityState("access")
        self._lock = threading.RLock()
        self._cached: dict[str, Any] | None = None
        self._signature = -1





    def _transaction(self):
        return self.state.transaction()

    @contextmanager
    def _writing(self) -> Iterator[Any]:
        """The account transaction for writes that other records commit with; a rollback drops the cache ``_write`` filled."""
        try:
            with self._transaction() as connection:
                yield connection
        except BaseException:
            with self._lock:
                self._cached = None
            raise

    def _revision(self) -> int:
        return self.state.revision()

    def _normalize(self, raw: object) -> dict[str, Any]:
        if (
            not isinstance(raw, Mapping)
            or set(raw) != {"schema", "users", "updated_at"}
            or not schema_matches(raw.get("schema"), "tfi.portal-access.v1")
        ):
            raise ValueError("Portal access store does not match current contract")
        payload = raw
        users = payload.get("users")
        normalized_users: dict[str, dict[str, Any]] = {}
        if not isinstance(users, Mapping):
            raise ValueError("Portal access users must be an object")
        for raw_username, value in users.items():
            username = _text(raw_username)
            if not USERNAME_RE.fullmatch(username):
                raise ValueError("Portal access username is invalid")
            normalized_users[username] = _normalize_user(username, value)
        return {
            "schema": ACCESS_SCHEMA,
            "users": normalized_users,
            "updated_at": _text(payload.get("updated_at")),
        }

    def _read_uncached(self) -> dict[str, Any]:
        return self._normalize(self.state.read({"schema": ACCESS_SCHEMA, "users": {}, "updated_at": ""}))

    def _current(self) -> dict[str, Any]:
        """The cached state; shared, so callers copy what they hand out."""
        signature = self._revision()
        with self._lock:
            if self._cached is not None and signature == self._signature:
                return self._cached
        payload = self._read_uncached()
        with self._lock:
            self._cached = payload
            self._signature = signature
            return payload

    def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self._current())

    def user(self, username: object) -> dict[str, Any]:
        actor = _text(username)
        # Copy only this user: a read-access check calls this several times per
        # record, and copying every user each time dominated large queries.
        configured = self._current()["users"].get(actor)
        if configured is not None:
            user = copy.deepcopy(configured)
        else:
            user = _normalize_user(actor, None)
        user["configured"] = configured is not None
        return user

    def _account(self, actor: str) -> Mapping[str, Any]:
        """The account fields a session check reads, shared and read-only (``user`` copies them)."""
        return self._current()["users"].get(actor) or _normalize_user(actor, None)

    def is_configured(self, username: object) -> bool:
        return _text(username) in self._current()["users"]

    @staticmethod
    def is_service_actor(username: object) -> bool:
        """Return whether the identity is an internal Portal service principal."""

        return _text(username).casefold() in PORTAL_SERVICE_ACTORS

    def is_active(self, username: object) -> bool:
        actor = _text(username)
        return bool(actor and self._account(actor)["status"] == "active")


    def session_version(self, username: object) -> int:
        return int(self._account(_text(username)).get("session_version") or 0)








    def project_catalog(self) -> list[dict[str, str]]:
        from management_portal import project_registry

        catalog: list[dict[str, str]] = []
        for item in project_registry.projects():
            if not isinstance(item, Mapping):
                continue
            project_id = _text(item.get("id"))
            if not project_id:
                continue
            catalog.append({"id": project_id})
        return catalog

    def project_ids(self) -> list[str]:
        return [project["id"] for project in self.project_catalog()]

    def list_users(self, credential_usernames: Iterable[object] = ()) -> list[dict[str, Any]]:
        configured = self.snapshot()["users"]
        names = {
            _text(value)
            for value in credential_usernames
            if _text(value)
        } | set(configured)
        return [
            user
            for username in sorted(names, key=str.casefold)
            if (user := self.user(username))["status"] != "deleted"
        ]

    def _write(self, payload: Mapping[str, Any]) -> None:
        normalized = self._normalize(payload)
        normalized["updated_at"] = _now_iso()
        self.state.replace(normalized)
        with self._lock:
            self._cached = normalized
            self._signature = self._revision()

    def upsert_user(
        self,
        username: str,
        *,
        actor: str,
        display_name: object | None = None,
        email: object | None = None,
        status: object | None = None,
        revoke_sessions: bool = False,
        revoke_personal_tokens: bool = True,
        require_new: bool = False,
    ) -> dict[str, Any]:
        clean_username = validate_user_id(username)
        with self._transaction():
            payload = self._read_uncached()
            if require_new and clean_username in payload["users"]:
                raise FileExistsError("username already exists or has been retired")
            current = _normalize_user(
                clean_username,
                payload["users"].get(clean_username),
            )
            now = _now_iso()
            if display_name is not None:
                current["display_name"] = _text(display_name) or clean_username
            if email is not None:
                normalized_email = _text(email)
                if normalized_email and (
                    len(normalized_email) > 254
                    or not EMAIL_RE.fullmatch(normalized_email)
                ):
                    raise ValueError("email address is invalid")
                if normalized_email:
                    duplicate = next((
                        other_username
                        for other_username, other in payload["users"].items()
                        if other_username != clean_username
                        and _text(other.get("email")).casefold() == normalized_email.casefold()
                    ), "")
                    if duplicate:
                        raise ValueError("email address is already assigned to another user")
                current["email"] = normalized_email
            if status is not None:
                normalized_status = _text(status).casefold()
                if normalized_status not in {"active", "disabled"}:
                    raise ValueError("status must be active or disabled")
                current["status"] = normalized_status
            if revoke_sessions:
                current["session_version"] += 1
            if (revoke_sessions and revoke_personal_tokens) or current["status"] == "disabled":
                from .user_enrollment import revoke_user_tokens
                revoke_user_tokens(clean_username)
            current["created_at"] = current["created_at"] or now
            current["updated_at"] = now
            current["updated_by"] = _text(actor)
            payload["users"][clean_username] = current
            self._write(payload)
        self.audit(
            actor,
            "user_access_updated",
            target=clean_username,
            details={
                "status": current["status"],
                "email_configured": bool(current["email"]),
                "sessions_revoked": bool(revoke_sessions),
            },
        )
        return self.user(clean_username)

    def set_category(self, username: str, category: str, *, actor: str,
                     before_commit: Callable[[Any], None] | None = None) -> bool:
        """Write a User's category into the account record; returns whether it changed.

        ``before_commit`` gets the open transaction, for records that commit
        with the account (the User's place in the permission trees).
        """
        clean_username = _text(username)
        if category not in USER_CATEGORIES:
            raise ValueError("Portal user category is invalid")
        with self._writing() as connection:
            payload = self._read_uncached()
            current = payload["users"].get(clean_username)
            if current is None or current["status"] == "deleted":
                raise KeyError(clean_username)
            if current["category"] == category:
                return False
            current.update({"category": category, "updated_at": _now_iso(), "updated_by": _text(actor)})
            self._write(payload)
            if before_commit is not None:
                before_commit(connection)
        return True

    def retire_user(self, username: str, *, actor: str) -> dict[str, Any]:
        """Retire a login identity while retaining a non-reusable audit tombstone.

        The User leaves the permission trees in the same transaction (decision_b820faf42218 §5.7).
        """
        from management_portal import permission_system

        clean_username = _text(username)
        if not USERNAME_RE.fullmatch(clean_username):
            raise ValueError("invalid username")
        with self._writing() as connection:
            payload = self._read_uncached()
            current = _normalize_user(
                clean_username,
                payload["users"].get(clean_username),
            )
            now = _now_iso()
            from .user_enrollment import revoke_user_tokens
            revoke_user_tokens(clean_username)
            current.update(
                {
                    "display_name": clean_username,
                    "status": "deleted",
                    "session_version": current["session_version"] + 1,
                    "created_at": current["created_at"] or now,
                    "updated_at": now,
                    "updated_by": _text(actor),
                    "category": USER_CATEGORY_DEFAULT,
                }
            )
            payload["users"][clean_username] = current
            self._write(payload)
            permission_system.sync_user(clean_username, deleted=True, connection=connection)
        self.audit(actor, "user_identity_retired", target=clean_username)
        return self.user(clean_username)

    def audit(
        self,
        actor: object,
        action: object,
        *,
        target: object = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        event = {
            "at": _now_iso(),
            "actor": _text(actor),
            "action": _text(action),
            "target": _text(target),
            "details": dict(details or {}),
        }
        with access_audit_store.database().transaction() as connection:
            access_audit_store.append(connection, event)

    def recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        return access_audit_store.recent(max(1, min(int(limit or 50), 200)))


class PortalUserDirectory:
    def __init__(self) -> None:
        self.state = IdentityState("user_directory")

    def load(self) -> dict:
        return self.state.read({"humans": [], "agents": []})

    def ensure_human(self, username: str) -> bool:
        username = validate_user_id(username)
        with self.state.transaction():
            payload = self.load()
            if any(item["id"] == username for item in payload["humans"]):
                return False
            payload["humans"].append({"id": username, "name": username})
            self.state.replace(payload)
            return True


def list_users() -> list[dict]:
    cfg = PortalUserDirectory().load()
    accounts = PortalCredentialStore().load()
    users = [{"id": username, "name": username, "type": "human"} for username in sorted(accounts)]

    seen_humans = {str(user.get("id") or "") for user in users if str(user.get("type") or "") == "human"}
    for human in cfg.get("humans", []):
        user_id = str(human.get("id") or "").strip()
        if not user_id or user_id in seen_humans:
            continue
        users.append({
            "id": user_id,
            "name": str(human.get("name") or user_id),
            "type": "human",
            "login_enabled": False,
        })
        seen_humans.add(user_id)

    return users



class PortalCredentialStore:
    """Atomic access to the existing Portal password authority."""

    MIN_PASSWORD_LENGTH = 12
    MAX_PASSWORD_LENGTH = 256

    def __init__(self) -> None:
        self.state = IdentityState("credentials")



    def load(self) -> dict[str, str]:
        payload = self.state.read({})
        if not isinstance(payload, Mapping):
            raise RuntimeError("Portal credentials are unreadable")
        return {_text(username): _text(value) for username, value in payload.items() if _text(username) and _text(value)}

    @staticmethod
    def validate_password(password: str) -> None:
        value = str(password or "")
        if len(value) < PortalCredentialStore.MIN_PASSWORD_LENGTH:
            raise ValueError("password must contain at least 12 characters")
        if len(value) > PortalCredentialStore.MAX_PASSWORD_LENGTH:
            raise ValueError("password must contain no more than 256 characters")
        character_classes = sum((
            any(char.islower() for char in value),
            any(char.isupper() for char in value),
            any(char.isdigit() for char in value),
            any(not char.isalnum() for char in value),
        ))
        if character_classes < 3:
            raise ValueError(
                "password must include at least three of: uppercase letters, "
                "lowercase letters, numbers, and symbols"
            )

    @classmethod
    def hash_password(cls, password: str) -> str:
        cls.validate_password(password)
        salt = os.urandom(16)
        key = hashlib.pbkdf2_hmac(
            "sha256", str(password).encode("utf-8"), salt, 260_000
        )
        return f"pbkdf2:sha256:{salt.hex()}:{key.hex()}"

    @staticmethod
    def verify_password(stored: str, provided: str) -> bool:
        # Unknown users perform the same PBKDF2 work as registered users.
        fallback = 'pbkdf2:sha256:' + '00' * 16 + ':' + '00' * 32
        valid = bool(stored)
        try:
            scheme, algorithm, salt_hex, key_hex = (stored or fallback).split(':')
            salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(key_hex)
            if scheme != 'pbkdf2' or algorithm != 'sha256' or len(salt) != 16 or len(expected) != 32:
                raise ValueError('invalid password digest')
        except (ValueError, AttributeError):
            valid, salt, expected = False, bytes(16), bytes(32)
        actual = hashlib.pbkdf2_hmac('sha256', str(provided).encode('utf-8'), salt, 260_000)
        return hmac.compare_digest(actual, expected) and valid

    def verify(self, username: str, password: str) -> bool:
        return self.verify_password(self.load().get(username, ""), password)

    def set_password(self, username: str, password: str) -> None:
        clean_username = validate_user_id(username)
        with self.state.transaction():
            users = self.load()
            users[clean_username] = self.hash_password(password)
            self.state.replace(users)

    def delete_user(self, username: str) -> bool:
        clean_username = _text(username)
        if not USERNAME_RE.fullmatch(clean_username):
            raise ValueError("invalid username")
        with self.state.transaction():
            users = self.load()
            removed = users.pop(clean_username, None) is not None
            if removed:
                self.state.replace(users)
            return removed


_ACCESS_STORE = PortalAccessStore()


def get_portal_access_store() -> PortalAccessStore:
    return _ACCESS_STORE

