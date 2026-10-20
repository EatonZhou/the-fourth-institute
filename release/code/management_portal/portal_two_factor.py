from __future__ import annotations
from management_common.format_tags import schema_matches

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from management_portal.identity_store import IdentityState
from management_common.site_config import site_name


SCHEMA = "tfi.portal-two-factor.v1"
TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode("ascii"))


def _base32decode(value: str) -> bytes:
    normalized = "".join(str(value or "").split()).upper().rstrip("=")
    return base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_code(
    secret: str,
    *,
    at_time: float | None = None,
    counter: int | None = None,
    digits: int = TOTP_DIGITS,
) -> str:
    if counter is None:
        counter = int((time.time() if at_time is None else at_time) // TOTP_PERIOD_SECONDS)
    digest = hmac.new(
        _base32decode(secret),
        int(counter).to_bytes(8, "big"),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    value = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(value % (10**digits)).zfill(digits)


def match_totp(
    secret: str,
    code: str,
    *,
    at_time: float | None = None,
    window: int = 1,
    last_counter: int = -1,
) -> int | None:
    clean_code = "".join(str(code or "").split())
    if len(clean_code) != TOTP_DIGITS or not clean_code.isdigit():
        return None
    current = int((time.time() if at_time is None else at_time) // TOTP_PERIOD_SECONDS)
    safe_window = max(0, int(window))
    deltas = [0]
    for distance in range(1, safe_window + 1):
        deltas.extend((-distance, distance))
    for delta in deltas:
        candidate = current + delta
        if candidate <= int(last_counter):
            continue
        if hmac.compare_digest(totp_code(secret, counter=candidate), clean_code):
            return candidate
    return None


def otpauth_uri(username: str, secret: str, *, issuer: str | None = None) -> str:
    issuer = site_name() if issuer is None else issuer
    label = quote(f"{issuer}:{username}", safe="")
    query = urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": str(TOTP_DIGITS),
        "period": str(TOTP_PERIOD_SECONDS),
    })
    return f"otpauth://totp/{label}?{query}"


def generate_recovery_codes(count: int = RECOVERY_CODE_COUNT) -> list[str]:
    codes: list[str] = []
    for _ in range(max(1, int(count))):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
        codes.append("-".join(raw[index : index + 4] for index in range(0, 16, 4)))
    return codes


def _normalize_recovery_code(value: str) -> str:
    return "".join(character for character in str(value or "").upper() if character.isalnum())


class PortalTwoFactorStore:
    """Encrypted TOTP seeds and one-time recovery-code digests."""

    def __init__(self, key_path: str | Path) -> None:
        self.key_path = Path(key_path).expanduser()
        self.state = IdentityState("two_factor")

    def _locked(self):
        return self.state.transaction()

    def _key(self) -> bytes:
        try:
            key = self.key_path.read_bytes()
        except FileNotFoundError:
            if self._load()["users"]:
                raise RuntimeError("Portal two-factor key is missing")
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            key = secrets.token_bytes(32)
            descriptor = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(descriptor, key)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        if len(key) != 32:
            raise RuntimeError("Portal two-factor key must contain exactly 32 bytes")
        return key

    def _load(self) -> dict:
        payload = self.state.read({"schema": SCHEMA, "users": {}})
        if not schema_matches(payload.get("schema"), "tfi.portal-two-factor.v1") or not isinstance(payload.get("users"), dict):
            raise RuntimeError("Unsupported two_factor schema")
        return payload

    @staticmethod
    def _encrypt_secret(key: bytes, username: str, secret: str) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(key).encrypt(nonce, secret.encode("ascii"), username.encode("utf-8"))
        return {"nonce": _b64encode(nonce), "ciphertext": _b64encode(ciphertext)}

    @staticmethod
    def _decrypt_secret(key: bytes, username: str, record: dict) -> str:
        return AESGCM(key).decrypt(
            _b64decode(str(record["nonce"])),
            _b64decode(str(record["ciphertext"])),
            username.encode("utf-8"),
        ).decode("ascii")

    @staticmethod
    def _recovery_digest(key: bytes, code: str) -> str:
        normalized = _normalize_recovery_code(code)
        return hmac.new(key, b"recovery\0" + normalized.encode("ascii"), hashlib.sha256).hexdigest()

    def status(self, username: str) -> dict:
        with self._locked():
            record = self._load()["users"].get(str(username or "").strip())
        if not isinstance(record, dict):
            return {"enabled": False, "enabled_at": None, "recovery_codes_remaining": 0}
        return {
            "enabled": True,
            "enabled_at": record.get("enabled_at"),
            "recovery_codes_remaining": len(record.get("recovery_code_digests") or []),
        }

    def enabled(self, username: str) -> bool:
        return self.status(username)["enabled"] is True

    def enable(self, username: str, secret: str, *, last_counter: int = -1) -> list[str]:
        clean_username = str(username or "").strip()
        if not clean_username:
            raise ValueError("username is required")
        _base32decode(secret)
        recovery_codes = generate_recovery_codes()
        with self._locked():
            key = self._key()
            payload = self._load()
            if clean_username in payload["users"]:
                raise ValueError("Two-factor authentication is already enabled; start again after disabling it.")
            payload["users"][clean_username] = {
                "secret": self._encrypt_secret(key, clean_username, secret),
                "enabled_at": int(time.time()),
                "last_counter": int(last_counter),
                "recovery_code_digests": [
                    self._recovery_digest(key, code) for code in recovery_codes
                ],
            }
            self.state.replace(payload)
        return recovery_codes

    def verify(self, username: str, code: str, *, at_time: float | None = None) -> str | None:
        clean_username = str(username or "").strip()
        with self._locked():
            key = self._key()
            payload = self._load()
            record = payload["users"].get(clean_username)
            if not isinstance(record, dict):
                return None
            normalized = _normalize_recovery_code(code)
            recovery_digest = self._recovery_digest(key, normalized)
            recovery_digests = list(record.get("recovery_code_digests") or [])
            for index, candidate in enumerate(recovery_digests):
                if hmac.compare_digest(str(candidate), recovery_digest):
                    recovery_digests.pop(index)
                    record["recovery_code_digests"] = recovery_digests
                    self.state.replace(payload)
                    return "recovery"
            try:
                secret = self._decrypt_secret(key, clean_username, record["secret"])
                last_counter = int(record.get("last_counter", -1))
            except Exception as exc:
                raise RuntimeError("Portal two-factor record is unreadable") from exc
            matched_counter = match_totp(
                secret,
                code,
                at_time=at_time,
                last_counter=last_counter,
            )
            if matched_counter is None:
                return None
            record["last_counter"] = matched_counter
            self.state.replace(payload)
            return "totp"

    def regenerate_recovery_codes(self, username: str) -> list[str]:
        clean_username = str(username or "").strip()
        recovery_codes = generate_recovery_codes()
        with self._locked():
            key = self._key()
            payload = self._load()
            record = payload["users"].get(clean_username)
            if not isinstance(record, dict):
                raise ValueError("two-factor authentication is not enabled")
            record["recovery_code_digests"] = [
                self._recovery_digest(key, code) for code in recovery_codes
            ]
            self.state.replace(payload)
        return recovery_codes

    def disable(self, username: str) -> bool:
        clean_username = str(username or "").strip()
        with self._locked():
            payload = self._load()
            removed = payload["users"].pop(clean_username, None) is not None
            if removed:
                self.state.replace(payload)
            return removed

    def delete_user(self, username: str) -> bool:
        return self.disable(username)
