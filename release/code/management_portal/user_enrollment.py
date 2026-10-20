"""User invitations and machine credentials, owned by Portal identity."""
from __future__ import annotations

import hashlib
import re
import secrets
import time

from management_common.principal import validate_user_id
from .portal_access_control import PortalAccessStore, PortalCredentialStore
from .identity_store import IdentityState


def revoke_user_tokens(username):
    """Persist revocation independently of the browser session version."""
    state = IdentityState("user_enrollment")
    with state.transaction():
        payload = state.read({"invitations": {}, "tokens": {}})
        changed = 0
        for row in payload["tokens"].values():
            if row["username"] == username and row["status"] == "active":
                row["status"] = "revoked"
                changed += 1
        if changed:
            state.replace(payload)
        return changed


class UserEnrollment:
    def __init__(self, access: PortalAccessStore, credentials: PortalCredentialStore):
        self.access = access
        self.credentials = credentials
        self.state = IdentityState("user_enrollment")

    def _read(self):
        return self.state.read({"invitations": {}, "tokens": {}})

    @staticmethod
    def _digest(value):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            raise ValueError("secret_sha256 must be a lowercase SHA-256 digest")
        return value

    @staticmethod
    def _duration(value, maximum):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"expires_in_days must be between 1 and {maximum}")
        return value * 86400

    @staticmethod
    def _public(record):
        return {key: value for key, value in record.items() if key != "secret_sha256"}

    def _active(self, username):
        validate_user_id(username)
        user = self.access.user(username)
        if not user["configured"] or user["status"] != "active":
            raise PermissionError("Active User required")
        return user

    def has_accounts(self):
        """Whether any User account was ever made (deleted ones leave a record); before that the setup link works."""
        return bool(self.access._read_uncached()["users"])

    def create_invitation(self, actor, username, secret_sha256, *, display_name="", expires_in_days=7, first_admin=False):
        username = validate_user_id(username)
        digest = self._digest(secret_sha256)
        duration = self._duration(expires_in_days, 30)
        if first_admin and self.has_accounts():
            raise PermissionError("This Portal already has accounts; its administrators grant administrator rights")
        with self.state.transaction():
            state = self._read()
            if first_admin:
                # Only the newest setup link works; running the setup command again replaces it.
                for item in state["invitations"].values():
                    if item.get("first_admin") and item["status"] == "pending":
                        item["status"] = "revoked"
            if self.access.user(username)["configured"] or username in self.credentials.load():
                raise FileExistsError("Username already exists or has been retired")
            if any(item["username"] == username and item["status"] == "pending" and item["expires_at"] > time.time() for item in state["invitations"].values()):
                raise FileExistsError("A pending invitation already exists for this username")
            if any(item["secret_sha256"] == digest for item in state["invitations"].values()):
                raise ValueError("Generate a new invitation secret")
            invitation = {"invitation_id": secrets.token_hex(16), "username": username,
                          "display_name": str(display_name).strip()[:160] or username,
                          "invited_by": actor, "created_at": time.time(),
                          "expires_at": time.time() + duration, "status": "pending", "secret_sha256": digest}
            if first_admin:
                invitation["first_admin"] = True
            state["invitations"][invitation["invitation_id"]] = invitation
            self.state.replace(state)
        return self._public(invitation)

    def list_invitations(self):
        items = []
        for item in self._read()["invitations"].values():
            row = self._public(item)
            if row["status"] == "pending" and row["expires_at"] <= time.time():
                row["status"] = "expired"
            items.append(row)
        return sorted(items, key=lambda row: row["created_at"], reverse=True)

    def revoke_invitation(self, invitation_id):
        with self.state.transaction():
            state = self._read()
            row = state["invitations"].get(invitation_id)
            if row is None:
                raise KeyError("Invitation not found")
            if row["status"] == "pending":
                row["status"] = "revoked"
                self.state.replace(state)
            return self._public(row)

    def create_account(self, actor, username, *, display_name="", recovery_email="", password="", secret_sha256="", token_name="API", expires_in_days=90, status="active"):
        username = validate_user_id(username)
        if status not in {"active", "disabled"}:
            raise ValueError("New accounts must be active or disabled")
        if password:
            self.credentials.validate_password(password)
        if secret_sha256:
            self._digest(secret_sha256)
            self._duration(expires_in_days, 365)
        if not password and not secret_sha256:
            raise ValueError("A password or machine credential is required")
        from management_portal import permission_system

        with self.access._writing() as connection, self.state.transaction():
            if username in self.credentials.load():
                raise FileExistsError("Username already exists")
            first = not self.has_accounts()
            self.access.upsert_user(username, actor=actor, display_name=display_name, email=recovery_email,
                                    status="disabled", require_new=True)
            if first:
                # The first account of a new Portal is its first Portal 管理员 (decision_b820faf42218 §3.1).
                self.access.set_category(username, "portal_admin", actor=actor)
            if password:
                self.credentials.set_password(username, password)
            user = self.access.upsert_user(username, actor=actor, status="active")
            token = self.create_token(username, secret_sha256, name=token_name, expires_in_days=expires_in_days) if secret_sha256 else None
            if status != "active":
                user = self.access.upsert_user(username, actor=actor, status=status, revoke_sessions=True)
            # Every way an account is made places it (and its personal space) in the permission trees, in its transaction.
            permission_system.sync_user(username, connection=connection)
        return {"user": user, "credential": token}

    @staticmethod
    def _invitation_digest(invitation_secret):
        if not isinstance(invitation_secret, str) or not 32 <= len(invitation_secret) <= 256:
            raise PermissionError("Invitation is invalid or expired")
        return hashlib.sha256(invitation_secret.encode()).hexdigest()

    def preview_invitation(self, invitation_secret):
        """What a pending invitation offers, shown to whoever holds its secret before they accept."""
        digest = self._invitation_digest(invitation_secret)
        row = next((r for r in self._read()["invitations"].values() if secrets.compare_digest(r["secret_sha256"], digest)), None)
        if not row or row["status"] != "pending" or row["expires_at"] <= time.time():
            raise PermissionError("Invitation is invalid or expired")
        offer = {"username": row["username"], "display_name": row["display_name"], "expires_at": row["expires_at"]}
        if row.get("first_admin"):
            offer["first_admin"] = True
        return offer

    def accept_invitation(self, invitation_secret, *, password="", secret_sha256="", token_name="API", expires_in_days=90):
        digest = self._invitation_digest(invitation_secret)
        with self.access._transaction(), self.state.transaction():
            state = self._read()
            row = next((r for r in state["invitations"].values() if secrets.compare_digest(r["secret_sha256"], digest)), None)
            if not row or row["status"] != "pending" or row["expires_at"] <= time.time():
                raise PermissionError("Invitation is invalid or expired")
            if row.get("first_admin") and self.has_accounts():
                raise PermissionError("This Portal already has accounts; its administrators grant administrator rights")
            result = self.create_account(row["invited_by"], row["username"], display_name=row["display_name"],
                                         password=password, secret_sha256=secret_sha256,
                                         token_name=token_name, expires_in_days=expires_in_days)
            # Account creation may also insert a credential in this namespace.
            state = self._read()
            state["invitations"][row["invitation_id"]].update(status="accepted", accepted_at=time.time())
            self.state.replace(state)
            self.access.audit(row["invited_by"], "user_invitation_accepted", target=row["username"],
                              details={"invitation_id": row["invitation_id"]})
            # The first account a Portal makes is its first Portal 管理员 (create_account), whichever invitation made it.
            return {**result, "first_admin": self.access.user(row["username"]).get("category") == "portal_admin"}

    def create_token(self, username, secret_sha256, *, name="API", expires_in_days=90):
        digest = self._digest(secret_sha256)
        duration = self._duration(expires_in_days, 365)
        with self.access._transaction(), self.state.transaction():
            self._active(username)
            state = self._read()
            if any(r["secret_sha256"] == digest for r in state["tokens"].values()):
                raise ValueError("Generate a new credential secret")
            row = {"credential_id": secrets.token_hex(16), "username": username, "name": str(name).strip()[:120] or "API",
                   "secret_sha256": digest, "created_at": time.time(), "expires_at": time.time() + duration,
                   "status": "active"}
            state["tokens"][row["credential_id"]] = row
            self.state.replace(state)
            return self._public(row)

    def list_tokens(self, username):
        self._active(username)
        items = []
        for record in self._read()["tokens"].values():
            if record["username"] != username:
                continue
            row = self._public(record)
            if row["status"] == "active":
                if row["expires_at"] <= time.time():
                    row["status"] = "expired"
            items.append(row)
        return items

    def revoke_token(self, username, credential_id):
        with self.state.transaction():
            state = self._read()
            row = state["tokens"].get(credential_id)
            if row is None or row["username"] != username:
                raise KeyError("Credential not found")
            row["status"] = "revoked"
            self.state.replace(state)
            return self._public(row)

    def authenticate(self, token):
        if not isinstance(token, str) or not token.startswith("dbu_") or not 36 <= len(token) <= 256:
            return ""
        digest = hashlib.sha256(token.encode()).hexdigest()
        row = next((r for r in self._read()["tokens"].values() if secrets.compare_digest(r["secret_sha256"], digest)), None)
        if not row or row["status"] != "active" or row["expires_at"] <= time.time():
            return ""
        user = self.access.user(row["username"])
        if not user["configured"] or user["status"] != "active":
            return ""
        return row["username"]
