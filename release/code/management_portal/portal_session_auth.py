from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

from management_common.portal_paths import path as portal_path, resolve_directory
from management_common.site_config import read_site_config

from .portal_access_control import PortalAccessStore, PortalCredentialStore, get_portal_access_store
SESSION_COOKIE = "db_session"
SESSION_TOKEN_VERSION = 1
_AUTH_ENV = {
    "session_cookie_name": "PORTAL_SESSION_COOKIE_NAME",
    "session_signing_key_path": "PORTAL_SESSION_SIGNING_KEY_PATH",
    "two_factor_key_path": "PORTAL_TWO_FACTOR_KEY_PATH",
    "public_origin": "PORTAL_PUBLIC_ORIGIN",
}


def authentication_setting(name: str) -> str | None:
    """Read deployment-owned auth settings without touching credential material."""
    environment_name = _AUTH_ENV[name]
    if environment_name in os.environ:
        return os.environ[environment_name]
    deployment = read_site_config().get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("deployment must be an object")
    auth = deployment.get("auth", {})
    if not isinstance(auth, dict):
        raise ValueError("deployment.auth must be an object")
    if name not in auth:
        return None
    value = auth[name]
    if not isinstance(value, str):
        raise ValueError(f"deployment.auth.{name} must be a string")
    return value


def session_cookie_name() -> str:
    """Cookie scope is a hostname, not a port; independent sites need distinct names."""
    value = authentication_setting("session_cookie_name")
    if value is None:
        return SESSION_COOKIE
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError("Invalid deployment session_cookie_name")
    return value


def login_challenge_cookie_name() -> str:
    name = session_cookie_name()
    return "db_login_challenge" if name == SESSION_COOKIE else name + "_login_challenge"


def session_signing_key_path() -> Path:
    value = authentication_setting("session_signing_key_path")
    return portal_path("secrets_dir") / "session_signing.key" if value is None else resolve_directory(value)


def two_factor_key_path() -> Path:
    value = authentication_setting("two_factor_key_path")
    return portal_path("secrets_dir") / "two_factor.key" if value is None else resolve_directory(value)


def cookie_value(cookie_header: str, name: str) -> str:
    for part in str(cookie_header or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key.strip() == name:
            return value.strip()
    return ""


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(raw: str) -> bytes:
    return base64.urlsafe_b64decode((raw + "=" * (-len(raw) % 4)).encode("ascii"))


class PortalSessionAuthority:
    def __init__(
        self,
        signing_key_path: Path | None = None,
        *,
        access_store: PortalAccessStore | None = None,
    ):
        self.credentials = PortalCredentialStore()
        self.cookie_name = session_cookie_name()
        self.signing_key_path = session_signing_key_path() if signing_key_path is None else Path(signing_key_path)
        self.access_store = access_store or get_portal_access_store()
        self._lock = threading.RLock()
        self._credential_revision = -1
        self._key_signature: tuple[int, int] = (-1, -1)
        self._signing_key = b""
        self._users: set[str] = set()

    def _reload(self) -> None:
        revision = self.credentials.state.revision()
        if revision != self._credential_revision:
            self._users = set(self.credentials.load())
            self._credential_revision = revision

        key_stat = self.signing_key_path.stat()
        key_signature = (int(key_stat.st_mtime_ns), int(key_stat.st_size))
        if key_signature != self._key_signature:
            signing_key = self.signing_key_path.read_bytes()
            if len(signing_key) < 32:
                raise ValueError("Portal session signing key must be at least 32 bytes")
            self._signing_key = signing_key
            self._key_signature = key_signature

    def issue(
        self,
        username: str,
        *,
        max_age: int,
        now: int | None = None,
    ) -> str:
        actor = str(username or "").strip()
        issued_at = int(time.time() if now is None else now)
        with self._lock:
            self._reload()
            if (actor not in self._users and not self.access_store.is_configured(actor)) or not self.access_store.is_active(actor):
                raise PermissionError("Portal user is not active")
            payload = {
                "v": SESSION_TOKEN_VERSION,
                "u": actor,
                "iat": issued_at,
                "exp": issued_at + max(60, int(max_age)),
                "sv": self.access_store.session_version(actor),
                "nonce": secrets.token_urlsafe(12),
            }
            payload_b64 = _b64url_encode(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            signature = _b64url_encode(
                hmac.new(
                    self._signing_key,
                    payload_b64.encode("ascii"),
                    hashlib.sha256,
                ).digest()
            )
        return f"{payload_b64}.{signature}"

    def verify(self, token: str, now: int | None = None) -> str:
        payload_b64, separator, signature = str(token or "").strip().partition(".")
        if not separator or not payload_b64 or not signature:
            return ""
        try:
            with self._lock:
                self._reload()
                expected = _b64url_encode(
                    hmac.new(
                        self._signing_key,
                        payload_b64.encode("ascii"),
                        hashlib.sha256,
                    ).digest()
                )
                users = set(self._users)
            if not hmac.compare_digest(expected, signature):
                return ""
            payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
            username = str(payload.get("u") or "").strip()
            if int(payload.get("v") or 0) != SESSION_TOKEN_VERSION:
                return ""
            if not username or (username not in users and not self.access_store.is_configured(username)):
                return ""
            if not self.access_store.is_active(username):
                return ""
            if int(payload.get("sv", -1)) != self.access_store.session_version(username):
                return ""
            if int(payload.get("exp") or 0) < int(time.time() if now is None else now):
                return ""
            return username
        except Exception:
            return ""

    def verify_cookie(self, cookie_header: str, now: int | None = None) -> str:
        return self.verify(cookie_value(cookie_header, self.cookie_name), now=now)
