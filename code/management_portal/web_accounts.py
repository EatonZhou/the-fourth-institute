"""The account authorities Portal Web uses and the account operations built on them.

Access, credentials, enrollment, second factors, passkeys and password resets each
have one store here; the session authority signs browser sessions.  Changing a
password, the administrator's access actions and deleting a User's own data run
against these stores.
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
from pathlib import Path

from management_common.portal_paths import path as portal_path
from management_portal import conversation_read_state, presentation_store
from management_portal.authentication_attempts import AuthenticationAttempts
from management_portal.portal_access_control import (
    get_portal_access_store,
    PortalCredentialStore,
    USERNAME_RE,
)
from management_portal.portal_passkeys import PortalPasskeyService
from management_portal.portal_password_reset import (
    PortalPasswordResetService,
    PortalPasswordResetStore,
    PortalSmtpMailer,
)
from management_portal.portal_session_auth import (
    PortalSessionAuthority,
    session_signing_key_path,
    two_factor_key_path,
)
from management_portal.portal_two_factor import PortalTwoFactorStore
from management_portal.shared_state import exclusive_file_lock
from management_portal.user_enrollment import UserEnrollment


def _env_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser()


_PORTAL_ALLOWED_USERS = {
    item.strip()
    for item in os.getenv("PORTAL_ALLOWED_USERS", "").split(",")
    if item.strip()
}

_SESSION_SIGNING_KEY_PATH = session_signing_key_path()
_TWO_FACTOR_KEY_PATH = two_factor_key_path()

_PORTAL_UPLOADS_ROOT = _env_path(
    "PORTAL_UPLOADS_ROOT",
    (portal_path("staging_root") / "shared" / "portal_uploads"),
)

_SESSION_MAX_AGE = int(os.getenv("MANAGEMENT_PORTAL_SESSION_MAX_AGE", str(30 * 24 * 3600)))

_SESSION_AUTHORITY: PortalSessionAuthority | None = None
_SESSION_AUTHORITY_CONFIG: tuple[str, int] | None = None
_SESSION_AUTHORITY_LOCK = threading.RLock()

_PORTAL_ACCESS = get_portal_access_store()
_PORTAL_CREDENTIALS = PortalCredentialStore()

_USER_ENROLLMENT = UserEnrollment(_PORTAL_ACCESS, _PORTAL_CREDENTIALS)
_PORTAL_TWO_FACTOR = PortalTwoFactorStore(_TWO_FACTOR_KEY_PATH)
_PORTAL_PASSKEYS = PortalPasskeyService()
try:
    _PORTAL_SMTP_MAILER = PortalSmtpMailer.from_environment()
except Exception as exc:
    print(f"Portal password reset email is disabled: {type(exc).__name__}", file=sys.stderr)
    _PORTAL_SMTP_MAILER = PortalSmtpMailer(
        host="",
        port=587,
        username="",
        password="",
        sender="",
        public_origin="",
    )
_PORTAL_PASSWORD_RESETS = PortalPasswordResetService(
    PortalPasswordResetStore(),
    _PORTAL_SMTP_MAILER,
)

_PORTAL_USER_UPLOAD_ROOTS = (
    _PORTAL_UPLOADS_ROOT / "resident_chat_images",
    _PORTAL_UPLOADS_ROOT / "resident_chat_attachments",
    _PORTAL_UPLOADS_ROOT / "resident_chat_files",
)


def _load_users() -> dict[str, str]:
    """Read the current Portal password authority."""
    return _PORTAL_CREDENTIALS.load()


_AUTH_ATTEMPTS = AuthenticationAttempts()


def _verify_credentials(username: str, password: str) -> bool:
    if not _AUTH_ATTEMPTS.allow("password", username):
        return False
    valid = _PORTAL_CREDENTIALS.verify(username, password)
    if valid:
        _AUTH_ATTEMPTS.succeeded("password", username)
    return valid


def _verify_second_factor(username: str, code: str) -> str | None:
    if not _AUTH_ATTEMPTS.allow("factor", username):
        return None
    factor = _PORTAL_TWO_FACTOR.verify(username, code)
    if factor:
        _AUTH_ATTEMPTS.succeeded("factor", username)
    return factor


def _change_portal_password(actor: str, payload: dict) -> dict:
    username = str(actor or "").strip()
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    confirmation = str(payload.get("new_password_confirmation") or "")
    if new_password != confirmation:
        raise ValueError("两次输入的新密码不一致")
    if new_password == current_password:
        raise ValueError("新密码不能与当前密码相同")
    _PORTAL_CREDENTIALS.validate_password(new_password)
    _require_account_reauthentication(username, payload)
    _PORTAL_CREDENTIALS.set_password(username, new_password)
    _PORTAL_PASSWORD_RESETS.invalidate_user(username)
    user = _PORTAL_ACCESS.upsert_user(
        username,
        actor=username,
        revoke_sessions=True,
    )
    _PORTAL_ACCESS.audit(username, "password_changed", target=username)
    return {"changed": True, "session_version": user["session_version"]}


def _ensure_session_signing_key() -> None:
    with exclusive_file_lock(
        _SESSION_SIGNING_KEY_PATH.with_suffix(
            f"{_SESSION_SIGNING_KEY_PATH.suffix}.lock"
        )
    ):
        try:
            key = _SESSION_SIGNING_KEY_PATH.read_bytes()
        except OSError:
            key = os.urandom(32)
            _SESSION_SIGNING_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            _SESSION_SIGNING_KEY_PATH.write_bytes(key)
            os.chmod(_SESSION_SIGNING_KEY_PATH, 0o600)
        if len(key) < 32:
            raise RuntimeError("Portal session signing key must be at least 32 bytes")


def _session_authority() -> PortalSessionAuthority:
    global _SESSION_AUTHORITY, _SESSION_AUTHORITY_CONFIG
    config = (str(_SESSION_SIGNING_KEY_PATH), id(_PORTAL_ACCESS))
    with _SESSION_AUTHORITY_LOCK:
        if _SESSION_AUTHORITY is None or _SESSION_AUTHORITY_CONFIG != config:
            _ensure_session_signing_key()
            _SESSION_AUTHORITY = PortalSessionAuthority(
                _SESSION_SIGNING_KEY_PATH,
                access_store=_PORTAL_ACCESS,
            )
            _SESSION_AUTHORITY_CONFIG = config
        return _SESSION_AUTHORITY


def _issue_session_token(username: str) -> str:
    from management_portal.personal_ama import provision_account

    token = _session_authority().issue(username, max_age=_SESSION_MAX_AGE)
    provision_account(username)
    return token


def _verify_session_token(token: str) -> str | None:
    return _session_authority().verify(token) or None


def _portal_user_allowed(username: str) -> bool:
    from management_common.principal import validate_user_id

    try:
        actor = validate_user_id(username)
    except ValueError:
        return False
    return bool(
        actor
        and _PORTAL_ACCESS.is_active(actor)
        and (not _PORTAL_ALLOWED_USERS or actor in _PORTAL_ALLOWED_USERS)
    )


def _delete_portal_user_personal_data(username: str) -> dict:
    """Remove a deleted User's own data; each feature removes its part (``user.deleted``) under its key."""
    from management_portal.user_avatar import user_avatar_store
    from management_portal import portal_features

    clean_username = str(username or "").strip()
    if not USERNAME_RE.fullmatch(clean_username):
        raise ValueError("invalid username")

    preferences_removed = presentation_store.delete_preferences(clean_username)

    conversation_read_state.delete_actor(clean_username)

    favorites_removed = presentation_store.delete_actor_favorites(clean_username)

    removed_upload_roots: list[str] = []
    for root in _PORTAL_USER_UPLOAD_ROOTS:
        target = root / clean_username
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            shutil.rmtree(target)
        else:
            continue
        removed_upload_roots.append(root.name)

    avatar_removed = user_avatar_store().delete(clean_username)

    return {
        "preferences": preferences_removed,
        "favorites": favorites_removed,
        "upload_roots": removed_upload_roots,
        "avatar": avatar_removed,
        **{feature: bool(forget(clean_username)) for feature, forget in portal_features.contributions("user.deleted")},
    }


def _require_authenticated_actor(actor: str) -> None:
    from .resources.access import require_identity

    require_identity(actor)


def _admin_access_payload(actor: str) -> dict:
    _require_authenticated_actor(actor)

    from management_portal.web_isa import with_user_allows

    credential_usernames = _load_users().keys()
    return {
        "password_reset": {"email_configured": _PORTAL_PASSWORD_RESETS.configured},
        "users": with_user_allows(actor, _PORTAL_ACCESS.list_users(credential_usernames)),
        "audit": _PORTAL_ACCESS.recent_audit(50),
    }


def _admin_access_action(actor: str, payload: dict) -> dict:
    action = str(payload.get("action") or "").strip().casefold()
    _require_authenticated_actor(actor)
    username = str(payload.get("username") or "").strip()
    if action == "create":
        result = _USER_ENROLLMENT.create_account(
            actor, username, password=str(payload.get("password") or ""),
            display_name=payload.get("display_name") or "", recovery_email=payload.get("email") or "",
            status=str(payload.get("status") or "active").strip().casefold(),
        )
        _PORTAL_ACCESS.audit(actor, "user_created", target=username)
        from management_portal.personal_ama import provision_account

        return {**result, "personal_ama": provision_account(username)}
    if action == "update":
        if username not in _load_users() and not _PORTAL_ACCESS.user(username)["configured"]:
            raise KeyError("user not found")
        previous_email = str(_PORTAL_ACCESS.user(username).get("email") or "")
        user = _PORTAL_ACCESS.upsert_user(
            username,
            actor=actor,
            display_name=payload.get("display_name"),
            email=payload.get("email"),
            status=payload.get("status"),


            revoke_sessions=bool(payload.get("revoke_sessions")),
        )
        if previous_email.casefold() != str(user.get("email") or "").casefold():
            _PORTAL_PASSWORD_RESETS.invalidate_user(username)
        return {"user": user}
    if action == "reset_password":
        if username.casefold() == actor.casefold():
            raise ValueError("修改自己的密码须在 Settings 中验证当前密码")
        if username not in _load_users() and not _PORTAL_ACCESS.user(username)["configured"]:
            raise KeyError("user not found")
        _PORTAL_CREDENTIALS.set_password(
            username,
            str(payload.get("password") or ""),
        )
        _PORTAL_PASSWORD_RESETS.invalidate_user(username)
        user = _PORTAL_ACCESS.upsert_user(
            username,
            actor=actor,
            revoke_sessions=True,
        )
        _PORTAL_ACCESS.audit(actor, "password_reset", target=username)
        return {"user": user}
    if action == "revoke_sessions":
        if username not in _load_users() and not _PORTAL_ACCESS.user(username)["configured"]:
            raise KeyError("user not found")
        return {
            "user": _PORTAL_ACCESS.upsert_user(
                username,
                actor=actor,
                revoke_sessions=True,
            )
        }
    if action == "delete":
        identity = _PORTAL_ACCESS.user(username)
        if identity["status"] == "deleted" or (
            username not in _load_users() and not identity["configured"]
        ):
            raise KeyError("user not found")
        if username.casefold() in {actor.casefold()}:
            raise ValueError("you cannot delete your own account")

        # Deletion is deletion: sign-in stops first, then the account is removed.
        _PORTAL_ACCESS.upsert_user(
            username,
            actor=actor,
            status="disabled",
            revoke_sessions=True,
        )
        _PORTAL_PASSWORD_RESETS.invalidate_user(username)
        deleted_data = _delete_portal_user_personal_data(username)
        deleted_data["credential"] = _PORTAL_CREDENTIALS.delete_user(username)
        deleted_data["two_factor"] = _PORTAL_TWO_FACTOR.delete_user(username)
        deleted_data["passkeys"] = _PORTAL_PASSKEYS.delete_user(username)
        # Retiring the account also takes the User out of the permission trees, in the same transaction.
        _PORTAL_ACCESS.retire_user(username, actor=actor)
        # The account's Ask Me Anything only its owner could open goes with it.
        try:
            from management_portal.society_service import get_society_service

            deleted_data["personal_ama"] = get_society_service().lineage.delete_personal_ama(username, actor)["deleted_lineage_ids"]
        except Exception as exc:  # noqa: BLE001 - the account is already gone; the audit records what is left
            deleted_data["personal_ama_error"] = str(exc) or type(exc).__name__
        retained = [
            "access audit and identity tombstone",
            "Lineage and Project resources",
            "conversation and execution history",
        ]
        _PORTAL_ACCESS.audit(
            actor,
            "user_deleted",
            target=username,
            details={"deleted_data": deleted_data, "retained": retained},
        )
        return {
            "username": username,
            "deleted": True,
            "deleted_data": deleted_data,
            "retained": retained,
        }
    raise ValueError("unknown access-control action")


def _require_account_reauthentication(username: str, payload: dict) -> None:
    if not _verify_credentials(username, str(payload.get("current_password") or "")):
        raise PermissionError("Current password is incorrect.")
    if _PORTAL_TWO_FACTOR.enabled(username):
        factor = _verify_second_factor(
            username,
            str(payload.get("verification_code") or ""),
        )
        if not factor:
            raise PermissionError("The authenticator or recovery code is invalid.")


def _password_reset_user(email: str) -> dict | None:
    normalized = str(email or "").strip().casefold()
    if not normalized:
        return None
    credential_users = _load_users()
    return next((
        user
        for user in _PORTAL_ACCESS.list_users(credential_users.keys())
        if user.get("status") == "active"
        and user.get("username") in credential_users
        and str(user.get("email") or "").strip().casefold() == normalized
    ), None)


def _require_active_actor(actor: str) -> None:
    if not str(actor or "").strip() or not _PORTAL_ACCESS.is_active(actor):
        raise PermissionError("需要有效登录身份")
