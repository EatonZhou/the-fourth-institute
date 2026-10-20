from __future__ import annotations
from management_common.format_tags import schema_matches

import base64
import copy
import json
import secrets
import threading
import time

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from management_portal.identity_store import IdentityState
from management_common.site_config import site_name


SCHEMA = "tfi.portal-passkeys.v1"
CEREMONY_MAX_AGE = 5 * 60
MAX_PENDING_CEREMONIES = 1000
MAX_CREDENTIALS_PER_USER = 10


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    clean = str(value or "")
    return base64.urlsafe_b64decode((clean + "=" * (-len(clean) % 4)).encode("ascii"))


class PortalPasskeyStore:
    """Persistent public-key credentials for Portal users."""

    def __init__(self) -> None:
        self.state = IdentityState("passkeys")

    def _locked(self):
        return self.state.transaction()

    def _load(self) -> dict:
        payload = self.state.read({"schema": SCHEMA, "users": {}})
        if not schema_matches(payload.get("schema"), "tfi.portal-passkeys.v1") or not isinstance(payload.get("users"), dict):
            raise RuntimeError("Unsupported passkeys schema")
        return payload

    @staticmethod
    def _public_credential(record: dict) -> dict:
        return {
            "credential_id": str(record.get("credential_id") or ""),
            "name": str(record.get("name") or "Passkey"),
            "created_at": int(record.get("created_at") or 0),
            "last_used_at": int(record.get("last_used_at") or 0),
            "device_type": str(record.get("device_type") or ""),
            "backed_up": bool(record.get("backed_up")),
        }

    def status(self, username: str) -> dict:
        clean_username = str(username or "").strip()
        with self._locked():
            user = self._load()["users"].get(clean_username)
        credentials = list(user.get("credentials") or []) if isinstance(user, dict) else []
        return {
            "enabled": bool(credentials),
            "count": len(credentials),
            "credentials": [self._public_credential(item) for item in credentials],
        }

    def user_handle(self, username: str) -> bytes:
        clean_username = str(username or "").strip()
        if not clean_username:
            raise ValueError("username is required")
        with self._locked():
            payload = self._load()
            user = payload["users"].get(clean_username)
            if not isinstance(user, dict):
                user = {"user_handle": _b64encode(secrets.token_bytes(32)), "credentials": []}
                payload["users"][clean_username] = user
                self.state.replace(payload)
            return _b64decode(str(user.get("user_handle") or ""))

    def credential_descriptors(self, username: str) -> list[PublicKeyCredentialDescriptor]:
        clean_username = str(username or "").strip()
        with self._locked():
            user = self._load()["users"].get(clean_username)
        records = list(user.get("credentials") or []) if isinstance(user, dict) else []
        output: list[PublicKeyCredentialDescriptor] = []
        for record in records:
            transports = []
            for value in record.get("transports") or []:
                try:
                    transports.append(AuthenticatorTransport(str(value)))
                except ValueError:
                    continue
            output.append(PublicKeyCredentialDescriptor(
                id=_b64decode(str(record.get("credential_id") or "")),
                transports=transports or None,
            ))
        return output

    def credential(self, credential_id: str) -> tuple[str, dict] | None:
        clean_id = str(credential_id or "").strip()
        with self._locked():
            users = self._load()["users"]
        for username, user in users.items():
            for record in user.get("credentials") or []:
                if secrets.compare_digest(str(record.get("credential_id") or ""), clean_id):
                    result = copy.deepcopy(record)
                    result["user_handle"] = str(user.get("user_handle") or "")
                    return str(username), result
        return None

    def add(
        self,
        username: str,
        *,
        user_handle: bytes,
        credential_id: bytes,
        public_key: bytes,
        sign_count: int,
        name: str,
        transports: list[str],
        device_type: str,
        backed_up: bool,
    ) -> dict:
        clean_username = str(username or "").strip()
        encoded_id = _b64encode(credential_id)
        clean_name = " ".join(str(name or "").split())[:80] or "Passkey"
        with self._locked():
            payload = self._load()
            for owner, candidate in payload["users"].items():
                for record in candidate.get("credentials") or []:
                    if secrets.compare_digest(str(record.get("credential_id") or ""), encoded_id):
                        if owner == clean_username:
                            raise ValueError("This Passkey is already registered.")
                        raise ValueError("This Passkey belongs to another account.")
            user = payload["users"].setdefault(clean_username, {
                "user_handle": _b64encode(user_handle),
                "credentials": [],
            })
            credentials = user.setdefault("credentials", [])
            if len(credentials) >= MAX_CREDENTIALS_PER_USER:
                raise ValueError("A maximum of 10 Passkeys can be registered.")
            record = {
                "credential_id": encoded_id,
                "public_key": _b64encode(public_key),
                "sign_count": max(0, int(sign_count)),
                "name": clean_name,
                "transports": sorted(set(str(item) for item in transports if item)),
                "device_type": str(device_type or ""),
                "backed_up": bool(backed_up),
                "created_at": int(time.time()),
                "last_used_at": 0,
            }
            credentials.append(record)
            self.state.replace(payload)
        return self._public_credential(record)

    def mark_used(
        self,
        credential_id: str,
        *,
        sign_count: int,
        device_type: str,
        backed_up: bool,
    ) -> None:
        clean_id = str(credential_id or "").strip()
        with self._locked():
            payload = self._load()
            for user in payload["users"].values():
                for record in user.get("credentials") or []:
                    if secrets.compare_digest(str(record.get("credential_id") or ""), clean_id):
                        record["sign_count"] = max(0, int(sign_count))
                        record["device_type"] = str(device_type or "")
                        record["backed_up"] = bool(backed_up)
                        record["last_used_at"] = int(time.time())
                        self.state.replace(payload)
                        return
        raise KeyError("Passkey not found")

    def delete_credential(self, username: str, credential_id: str) -> bool:
        clean_username = str(username or "").strip()
        clean_id = str(credential_id or "").strip()
        with self._locked():
            payload = self._load()
            user = payload["users"].get(clean_username)
            if not isinstance(user, dict):
                return False
            credentials = list(user.get("credentials") or [])
            retained = [
                record for record in credentials
                if not secrets.compare_digest(str(record.get("credential_id") or ""), clean_id)
            ]
            if len(retained) == len(credentials):
                return False
            user["credentials"] = retained
            self.state.replace(payload)
            return True

    def delete_user(self, username: str) -> bool:
        clean_username = str(username or "").strip()
        with self._locked():
            payload = self._load()
            removed = payload["users"].pop(clean_username, None) is not None
            if removed:
                self.state.replace(payload)
            return removed


class PortalPasskeyService:
    """WebAuthn ceremonies and credential lifecycle for Portal."""

    def __init__(self) -> None:
        self.store = PortalPasskeyStore()
        self._pending: dict[str, dict] = {}
        self._lock = threading.RLock()

    def status(self, username: str) -> dict:
        return self.store.status(username)

    def _remember(self, record: dict) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            expired = [
                key for key, item in self._pending.items()
                if float(item.get("expires_at") or 0) <= now
            ]
            for key in expired:
                self._pending.pop(key, None)
            while len(self._pending) >= MAX_PENDING_CEREMONIES:
                oldest = min(self._pending, key=lambda key: self._pending[key]["created_at"])
                self._pending.pop(oldest, None)
            self._pending[token] = {
                **record,
                "created_at": now,
                "expires_at": now + CEREMONY_MAX_AGE,
            }
        return token

    def _consume(self, token: str, ceremony: str, username: str | None = None) -> dict:
        with self._lock:
            record = self._pending.pop(str(token or ""), None)
        if not isinstance(record, dict) or float(record.get("expires_at") or 0) <= time.time():
            raise PermissionError("Passkey request has expired; start again.")
        if record.get("ceremony") != ceremony:
            raise PermissionError("Passkey request is invalid.")
        if username is not None and not secrets.compare_digest(
            str(record.get("username") or ""), str(username or "")
        ):
            raise PermissionError("Passkey request is invalid.")
        return record

    def begin_registration(
        self,
        username: str,
        *,
        display_name: str,
        rp_id: str,
        origin: str,
    ) -> dict:
        user_handle = self.store.user_handle(username)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=site_name(),
            user_name=username,
            user_display_name=display_name or username,
            user_id=user_handle,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                require_resident_key=True,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
            exclude_credentials=self.store.credential_descriptors(username),
        )
        token = self._remember({
            "ceremony": "registration",
            "username": username,
            "challenge": options.challenge,
            "user_handle": user_handle,
            "rp_id": rp_id,
            "origin": origin,
        })
        return {"ceremony_token": token, "options": json.loads(options_to_json(options))}

    def finish_registration(
        self,
        username: str,
        *,
        ceremony_token: str,
        credential: dict,
        name: str,
    ) -> dict:
        record = self._consume(ceremony_token, "registration", username)
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=record["challenge"],
            expected_rp_id=record["rp_id"],
            expected_origin=record["origin"],
            require_user_verification=True,
        )
        response = credential.get("response") if isinstance(credential, dict) else {}
        transports = response.get("transports") if isinstance(response, dict) else []
        return self.store.add(
            username,
            user_handle=record["user_handle"],
            credential_id=verification.credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            name=name,
            transports=list(transports or []),
            device_type=verification.credential_device_type.value,
            backed_up=verification.credential_backed_up,
        )

    def begin_authentication(self, *, rp_id: str, origin: str) -> dict:
        options = generate_authentication_options(
            rp_id=rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        token = self._remember({
            "ceremony": "authentication",
            "challenge": options.challenge,
            "rp_id": rp_id,
            "origin": origin,
        })
        return {"ceremony_token": token, "options": json.loads(options_to_json(options))}

    def finish_authentication(
        self,
        *,
        ceremony_token: str,
        credential: dict,
    ) -> str:
        record = self._consume(ceremony_token, "authentication")
        credential_id = str(credential.get("id") or "") if isinstance(credential, dict) else ""
        stored = self.store.credential(credential_id)
        if not stored:
            raise PermissionError("Passkey authentication failed.")
        username, passkey = stored
        response = credential.get("response") if isinstance(credential, dict) else {}
        user_handle = str(response.get("userHandle") or "") if isinstance(response, dict) else ""
        if user_handle and not secrets.compare_digest(user_handle, str(passkey["user_handle"])):
            raise PermissionError("Passkey authentication failed.")
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=record["challenge"],
            expected_rp_id=record["rp_id"],
            expected_origin=record["origin"],
            credential_public_key=_b64decode(str(passkey["public_key"])),
            credential_current_sign_count=int(passkey.get("sign_count") or 0),
            require_user_verification=True,
        )
        self.store.mark_used(
            credential_id,
            sign_count=verification.new_sign_count,
            device_type=verification.credential_device_type.value,
            backed_up=verification.credential_backed_up,
        )
        return username

    def delete_credential(self, username: str, credential_id: str) -> bool:
        return self.store.delete_credential(username, credential_id)

    def delete_user(self, username: str) -> bool:
        return self.store.delete_user(username)
