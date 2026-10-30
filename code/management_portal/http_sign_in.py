"""Signing in with a second factor: login challenges and their cookie, authenticator
enrollment, and the login pages.
"""
from __future__ import annotations

import base64
import hmac
import io
import secrets
import threading
import time
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

from management_portal import http_auth, web_accounts
from management_portal.portal_session_auth import login_challenge_cookie_name
from management_portal.portal_two_factor import generate_totp_secret, match_totp, otpauth_uri


ROOT = Path(__file__).resolve().parent


_LOGIN_CHALLENGE_COOKIE = login_challenge_cookie_name()

_LOGIN_CHALLENGE_MAX_AGE = 5 * 60
_LOGIN_CHALLENGE_MAX_ATTEMPTS = 5
_TWO_FACTOR_SETUP_MAX_AGE = 10 * 60

_LOGIN_CHALLENGES: dict[str, dict] = {}
_LOGIN_CHALLENGES_LOCK = threading.RLock()
_TWO_FACTOR_SETUPS: dict[str, dict] = {}
_TWO_FACTOR_SETUPS_LOCK = threading.RLock()


def _login_challenge_cookie_value(token: str, *, secure: bool = False) -> str:
    suffix = "; Secure" if secure else ""
    return (
        f"{_LOGIN_CHALLENGE_COOKIE}={token}; HttpOnly; Path=/login; "
        f"Max-Age={_LOGIN_CHALLENGE_MAX_AGE}; SameSite=Strict{suffix}"
    )


def _set_login_challenge_cookie(handler: SimpleHTTPRequestHandler, token: str) -> None:
    handler.send_header(
        "Set-Cookie",
        _login_challenge_cookie_value(token, secure=http_auth._request_is_https(handler)),
    )


def _clear_login_challenge_cookie(handler: SimpleHTTPRequestHandler) -> None:
    secure = "; Secure" if http_auth._request_is_https(handler) else ""
    handler.send_header(
        "Set-Cookie",
        f"{_LOGIN_CHALLENGE_COOKIE}=; HttpOnly; Path=/login; Max-Age=0; SameSite=Strict{secure}",
    )


def _prune_ephemeral_auth_state(now: float | None = None) -> None:
    current = time.time() if now is None else float(now)
    with _LOGIN_CHALLENGES_LOCK:
        expired = [
            token for token, record in _LOGIN_CHALLENGES.items()
            if float(record.get("expires_at") or 0) <= current
        ]
        for token in expired:
            _LOGIN_CHALLENGES.pop(token, None)
    with _TWO_FACTOR_SETUPS_LOCK:
        expired = [
            token for token, record in _TWO_FACTOR_SETUPS.items()
            if float(record.get("expires_at") or 0) <= current
        ]
        for token in expired:
            _TWO_FACTOR_SETUPS.pop(token, None)


def _start_login_challenge(username: str) -> str:
    _prune_ephemeral_auth_state()
    token = secrets.token_urlsafe(32)
    with _LOGIN_CHALLENGES_LOCK:
        _LOGIN_CHALLENGES[token] = {
            "username": username,
            "expires_at": time.time() + _LOGIN_CHALLENGE_MAX_AGE,
            "attempts": 0,
        }
    return token


def _login_challenge_username(token: str) -> str:
    _prune_ephemeral_auth_state()
    with _LOGIN_CHALLENGES_LOCK:
        record = _LOGIN_CHALLENGES.get(str(token or ""))
        return str(record.get("username") or "") if isinstance(record, dict) else ""


def _verify_login_challenge(token: str, code: str) -> tuple[str, str] | None:
    _prune_ephemeral_auth_state()
    clean_token = str(token or "")
    with _LOGIN_CHALLENGES_LOCK:
        record = _LOGIN_CHALLENGES.get(clean_token)
        if not isinstance(record, dict):
            return None
        if int(record.get("attempts") or 0) >= _LOGIN_CHALLENGE_MAX_ATTEMPTS:
            _LOGIN_CHALLENGES.pop(clean_token, None)
            return None
        record["attempts"] = int(record.get("attempts") or 0) + 1
        username = str(record.get("username") or "")
        factor = web_accounts._verify_second_factor(username, code)
        if factor:
            _LOGIN_CHALLENGES.pop(clean_token, None)
            return username, factor
        if int(record.get("attempts") or 0) >= _LOGIN_CHALLENGE_MAX_ATTEMPTS:
            _LOGIN_CHALLENGES.pop(clean_token, None)
    return None


def _begin_two_factor_setup(username: str) -> dict:
    _prune_ephemeral_auth_state()
    secret = generate_totp_secret()
    token = secrets.token_urlsafe(32)
    uri = otpauth_uri(username, secret)
    with _TWO_FACTOR_SETUPS_LOCK:
        _TWO_FACTOR_SETUPS[token] = {
            "username": username,
            "secret": secret,
            "expires_at": time.time() + _TWO_FACTOR_SETUP_MAX_AGE,
        }
    return {
        "setup_token": token,
        "secret": secret,
        "otpauth_uri": uri,
        "qr_code_data_url": _totp_qr_data_url(uri),
        "expires_in": _TWO_FACTOR_SETUP_MAX_AGE,
    }


def _confirm_two_factor_setup(username: str, setup_token: str, code: str) -> list[str]:
    _prune_ephemeral_auth_state()
    clean_token = str(setup_token or "")
    with _TWO_FACTOR_SETUPS_LOCK:
        record = _TWO_FACTOR_SETUPS.get(clean_token)
        if not isinstance(record, dict) or not hmac.compare_digest(
            str(record.get("username") or ""), username
        ):
            raise PermissionError("Two-factor setup has expired; start again.")
        secret = str(record.get("secret") or "")
        if not web_accounts._AUTH_ATTEMPTS.allow("factor", username):
            raise PermissionError("Too many verification attempts; try again later.")
        counter = match_totp(secret, code)
        if counter is None:
            raise PermissionError("The verification code is invalid.")
        _TWO_FACTOR_SETUPS.pop(clean_token, None)
    return web_accounts._PORTAL_TWO_FACTOR.enable(username, secret, last_counter=counter)


def _totp_qr_data_url(uri: str) -> str:
    import qrcode
    import qrcode.image.svg

    image = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=3)
    buffer = io.BytesIO()
    image.save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _prepare_login_html(error: bool = False) -> bytes:
    html = (ROOT / "login.html").read_text(encoding="utf-8")
    if error:
        html = html.replace(
            "<!--ERROR-->",
            '<div class="portalAsyncState loginError" data-state="error" data-density="inline" role="alert"><span class="portalAsyncStateIcon" aria-hidden="true"></span><span class="portalAsyncStateMessage">密码错误，请重试</span></div>',
        )
    else:
        html = html.replace("<!--ERROR-->", "")
    return html.encode("utf-8")


def _prepare_login_verify_html(error: bool = False) -> bytes:
    html = (ROOT / "login_verify.html").read_text(encoding="utf-8")
    if error:
        html = html.replace(
            "<!--ERROR-->",
            '<div class="portalAsyncState loginError" data-state="error" data-density="inline" role="alert"><span class="portalAsyncStateIcon" aria-hidden="true"></span><span class="portalAsyncStateMessage">验证码无效或挑战已过期，请重试</span></div>',
        )
    else:
        html = html.replace("<!--ERROR-->", "")
    return html.encode("utf-8")
