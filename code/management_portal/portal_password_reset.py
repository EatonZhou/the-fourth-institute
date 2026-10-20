from __future__ import annotations
from management_common.format_tags import schema_matches

import hashlib
import os
import queue
import secrets
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .identity_store import IdentityState
from management_common.site_config import site_name
from .portal_session_auth import authentication_setting


RESET_SCHEMA = "tfi.portal-password-resets.v1"
RESET_MAX_AGE = 30 * 60
RATE_WINDOW = 15 * 60
MAX_REQUESTS_PER_EMAIL = 3
MAX_REQUESTS_PER_CLIENT = 10


def _token_digest(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


class PortalPasswordResetStore:
    def __init__(self, *, max_age: int = RESET_MAX_AGE) -> None:
        self.state = IdentityState("password_resets")
        self.max_age = max(60, int(max_age))



    def _load(self) -> dict[str, Any]:
        payload = self.state.read({"schema": RESET_SCHEMA, "tokens": {}})
        if not schema_matches(payload.get("schema"), "tfi.portal-password-resets.v1") or not isinstance(payload.get("tokens"), dict):
            raise RuntimeError("Portal password reset store is unreadable")
        return payload

    @staticmethod
    def _prune(payload: dict[str, Any], now: float) -> None:
        payload["tokens"] = {
            digest: record
            for digest, record in payload["tokens"].items()
            if isinstance(record, dict) and float(record.get("expires_at") or 0) > now
        }

    def issue(self, username: str) -> str:
        clean_username = str(username or "").strip()
        if not clean_username:
            raise ValueError("username is required")
        token = secrets.token_urlsafe(48)
        now = time.time()
        with self.state.transaction():
            payload = self._load()
            self._prune(payload, now)
            payload["tokens"] = {
                digest: record
                for digest, record in payload["tokens"].items()
                if str(record.get("username") or "") != clean_username
            }
            payload["tokens"][_token_digest(token)] = {
                "username": clean_username,
                "created_at": now,
                "expires_at": now + self.max_age,
            }
            self.state.replace(payload)
        return token

    def consume(self, token: str) -> str:
        raw_token = str(token or "").strip()
        if len(raw_token) < 32 or len(raw_token) > 256:
            raise PermissionError("Password reset link is invalid or expired.")
        now = time.time()
        with self.state.transaction():
            payload = self._load()
            self._prune(payload, now)
            record = payload["tokens"].pop(_token_digest(raw_token), None)
            self.state.replace(payload)
        if not isinstance(record, dict):
            raise PermissionError("Password reset link is invalid or expired.")
        username = str(record.get("username") or "").strip()
        if not username:
            raise PermissionError("Password reset link is invalid or expired.")
        return username

    def invalidate(self, token: str) -> None:
        raw_token = str(token or "").strip()
        if not raw_token:
            return
        with self.state.transaction():
            payload = self._load()
            if payload["tokens"].pop(_token_digest(raw_token), None) is not None:
                self.state.replace(payload)

    def invalidate_user(self, username: str) -> None:
        clean_username = str(username or "").strip()
        with self.state.transaction():
            payload = self._load()
            before = len(payload["tokens"])
            payload["tokens"] = {
                digest: record
                for digest, record in payload["tokens"].items()
                if str(record.get("username") or "") != clean_username
            }
            if len(payload["tokens"]) != before:
                self.state.replace(payload)


class PortalSmtpMailer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        public_origin: str,
        security: str = "starttls",
    ) -> None:
        self.host = str(host or "").strip()
        self.port = int(port)
        self.username = str(username or "").strip()
        self.password = str(password or "")
        self.sender = str(sender or "").strip()
        self.public_origin = str(public_origin or "").strip().rstrip("/")
        self.security = str(security or "starttls").strip().casefold()
        parsed = urlparse(self.public_origin)
        if self.public_origin and (
            parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}
        ):
            raise ValueError("PORTAL_PUBLIC_ORIGIN must be an HTTPS origin")
        if self.security not in {"starttls", "ssl"}:
            raise ValueError("PORTAL_SMTP_SECURITY must be starttls or ssl")

    @property
    def configured(self) -> bool:
        return bool(self.host and self.sender and self.public_origin)

    @classmethod
    def from_environment(cls) -> "PortalSmtpMailer":
        password = os.getenv("PORTAL_SMTP_PASSWORD", "")
        password_file = os.getenv("PORTAL_SMTP_PASSWORD_FILE", "").strip()
        if password_file:
            password = Path(password_file).expanduser().read_text(encoding="utf-8").strip()
        security = os.getenv("PORTAL_SMTP_SECURITY", "starttls").strip().casefold()
        default_port = 465 if security == "ssl" else 587
        return cls(
            host=os.getenv("PORTAL_SMTP_HOST", ""),
            port=int(os.getenv("PORTAL_SMTP_PORT", str(default_port))),
            username=os.getenv("PORTAL_SMTP_USERNAME", ""),
            password=password,
            sender=os.getenv("PORTAL_SMTP_FROM", ""),
            public_origin=authentication_setting("public_origin") or "",
            security=security,
        )

    def send_reset(self, recipient: str, token: str) -> None:
        if not self.configured:
            raise RuntimeError("Portal SMTP is not configured")
        link = f"{self.public_origin}/reset-password?token={quote(token, safe='')}"
        message = EmailMessage()
        name = site_name()
        message["Subject"] = f"Reset your {name} password"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            f"A password reset was requested for your {name} account.\n\n"
            f"Open this link within 30 minutes:\n{link}\n\n"
            "If you did not request this reset, you can ignore this email."
        )
        self._send(message)

    def send_password_changed(self, recipient: str) -> None:
        if not self.configured:
            return
        message = EmailMessage()
        name = site_name()
        message["Subject"] = f"Your {name} password was changed"
        message["From"] = self.sender
        message["To"] = recipient
        message.set_content(
            f"The password for your {name} account was changed.\n\n"
            "If you did not make this change, contact a Portal administrator immediately."
        )
        self._send(message)

    def _send(self, message: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self.security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                self.host, self.port, timeout=45, context=context
            )
        else:
            client = smtplib.SMTP(self.host, self.port, timeout=45)
        with client:
            if self.security == "starttls":
                client.starttls(context=context)
            if self.username:
                client.login(self.username, self.password)
            client.send_message(message)


class PortalPasswordResetService:
    def __init__(self, store: PortalPasswordResetStore, mailer: PortalSmtpMailer) -> None:
        self.store = store
        self.mailer = mailer
        self._lock = threading.RLock()
        self._requests: dict[str, list[float]] = {}
        self._deliveries: queue.Queue[tuple[str, str, str, str]] = queue.Queue(maxsize=100)
        for index in range(2):
            threading.Thread(
                target=self._delivery_worker,
                name=f"portal-password-reset-{index + 1}",
                daemon=True,
            ).start()

    @property
    def configured(self) -> bool:
        return self.mailer.configured

    def _allow(self, key: str, limit: int, now: float) -> bool:
        for stale_key, stamps in list(self._requests.items()):
            if not stamps or stamps[-1] <= now - RATE_WINDOW:
                self._requests.pop(stale_key, None)
        if key not in self._requests and len(self._requests) >= 4096:
            return False
        recent = [stamp for stamp in self._requests.get(key, []) if stamp > now - RATE_WINDOW]
        allowed = len(recent) < limit
        if allowed:
            recent.append(now)
        self._requests[key] = recent
        return allowed

    def request(self, *, username: str, email: str, client_key: str) -> bool:
        normalized_email = str(email or "").strip().casefold()
        now = time.time()
        email_key = "email:" + hashlib.sha256(normalized_email.encode("utf-8")).hexdigest()
        client_hash = hashlib.sha256(str(client_key or "unknown").encode("utf-8")).hexdigest()
        with self._lock:
            allowed = self._allow(email_key, MAX_REQUESTS_PER_EMAIL, now)
            allowed = self._allow("client:" + client_hash, MAX_REQUESTS_PER_CLIENT, now) and allowed
        if not allowed or not username or not normalized_email or not self.configured:
            return False
        token = self.store.issue(username)
        try:
            self._deliveries.put_nowait(("reset", email, token, username))
        except queue.Full:
            self.store.invalidate(token)
            return False
        return True

    def _delivery_worker(self) -> None:
        while True:
            kind, email, token, username = self._deliveries.get()
            try:
                if kind == "reset":
                    self.mailer.send_reset(email, token)
                elif kind == "changed":
                    self.mailer.send_password_changed(email)
            except Exception:
                if kind == "reset":
                    self.store.invalidate(token)
            finally:
                self._deliveries.task_done()

    def consume(self, token: str) -> str:
        return self.store.consume(token)

    def invalidate_user(self, username: str) -> None:
        self.store.invalidate_user(username)

    def notify_password_changed(self, email: str) -> None:
        if email and self.configured:
            try:
                self._deliveries.put_nowait(("changed", email, "", ""))
            except queue.Full:
                pass
