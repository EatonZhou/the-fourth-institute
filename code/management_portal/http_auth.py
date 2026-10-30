"""Who is calling: the browser session cookie, User and Resident bearer tokens, the
dual-auth route wrapper, request origin checks, the Test API gate and sign-out.
"""
from __future__ import annotations

import hmac
import logging
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlparse

from management_portal import agent_token_auth, http_responses, page_api_auth, web_accounts
from management_portal.http_errors import failure_frames
from management_portal.http_routes import RouteDefinition
from management_portal.portal_session_auth import cookie_value, session_cookie_name
from management_portal.runtime_identity_headers import runtime_identity_header

LOGGER = logging.getLogger(__name__)


_SESSION_COOKIE = session_cookie_name()


def _get_session_token(handler: SimpleHTTPRequestHandler) -> str | None:
    return cookie_value(handler.headers.get("Cookie", ""), _SESSION_COOKIE) or None


def _get_session_username(handler: SimpleHTTPRequestHandler) -> str | None:
    token = _get_session_token(handler)
    if not token:
        return None
    username = web_accounts._verify_session_token(token)
    return username if username and web_accounts._portal_user_allowed(username) else None


def _check_session(handler: SimpleHTTPRequestHandler) -> bool:
    return _get_session_username(handler) is not None


def _get_bearer_token(handler: SimpleHTTPRequestHandler) -> str:
    auth = handler.headers.get("Authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _get_agent_api_auth(handler: SimpleHTTPRequestHandler, *, scope: str = "") -> dict | None:
    """Authorize agent API callers via human session or agent bearer token."""
    username = _get_session_username(handler)
    if username:
        return {"type": "human", "id": username}
    token = _get_bearer_token(handler)
    if token.startswith("dbu_"):
        username = web_accounts._USER_ENROLLMENT.authenticate(token)
        return {"type": "user", "id": username} if username and web_accounts._portal_user_allowed(username) else None
    agent = agent_token_auth.authenticate_bearer_token(token, scope=scope)
    if agent:
        from management_common.principal import actor_principal

        caller = str(agent["agent_id"])
        if actor_principal(caller).kind == "user" and not web_accounts._portal_user_allowed(caller):
            return None
        return {
            "type": "agent",
            "id": caller,
            "execution_id": runtime_identity_header(handler.headers, "X-Portal-Execution-ID"),
            "lineage_id": runtime_identity_header(handler.headers, "X-Portal-Lineage-ID"),
        }
    return None


def _get_page_api_actor(handler: SimpleHTTPRequestHandler) -> str | None:
    """Authenticate a page caller without converting a Resident into a User."""
    auth = _get_agent_api_auth(handler)
    return page_api_auth.authorize(auth) if auth else None


def _get_request_actor(handler: SimpleHTTPRequestHandler) -> str | None:
    """The principal already checked by this request's page entry."""
    return getattr(handler, "_portal_authenticated_actor", None) or _get_session_username(handler)


def _principal_route(method, path, handler, group, **options):
    """Declare a native surface whose domain supports both User and Resident."""
    def route(request, parsed, _actor):
        try:
            actor = _get_page_api_actor(request)
        except PermissionError as exc:
            return http_responses._serve_http_error(request, 403, str(exc))
        except Exception as exc:
            LOGGER.warning("Page identity verification failed for %s %s: %s; frames=%s", method, path,
                           type(exc).__name__, failure_frames(exc))
            return http_responses._serve_http_error(request, 503, "Page identity verification unavailable")
        if not actor:
            if method == "GET" and not http_responses._is_json_api_path(parsed.path) and not request.headers.get("Authorization"):
                request.send_response(302)
                request.send_header("Location", "/login")
                request.end_headers()
                return
            return http_responses._serve_http_error(request, 401, "Authentication required")
        request._portal_authenticated_actor = actor
        from management_common.resource_lifecycle import lifecycle_scope
        try:
            with lifecycle_scope(request.headers.get("X-Portal-Resource-Lifecycle", ""), actor=actor):
                return handler(request, parsed, actor)
        finally:
            # A keep-alive HTTP handler can serve a different principal next.
            request._portal_authenticated_actor = None

    return RouteDefinition(method, path, route, group, phase="dual-auth", **options)


def _request_scheme(handler: SimpleHTTPRequestHandler) -> str:
    # ASGI/uvicorn owns the trusted proxy boundary.
    asgi_scheme = str(getattr(handler, "_asgi_request_scheme", "") or "").lower()
    return asgi_scheme if asgi_scheme in {"http", "https"} else "http"


def _request_client_key(handler: SimpleHTTPRequestHandler) -> str:
    peer = str(getattr(handler, "client_address", ("", 0))[0] or "")
    return peer or "unknown"


def _request_is_https(handler: SimpleHTTPRequestHandler) -> bool:
    return _request_scheme(handler) == "https"


def _same_origin_request(handler: SimpleHTTPRequestHandler) -> bool:
    source = str(handler.headers.get("Origin") or "").strip()
    if not source:
        referer = str(handler.headers.get("Referer") or "").strip()
        if not referer:
            return True
        parsed_referer = urlparse(referer)
        source = f"{parsed_referer.scheme}://{parsed_referer.netloc}"
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    expected = f"{_request_scheme(handler)}://{str(handler.headers.get('Host') or '').strip()}"
    return hmac.compare_digest(source.rstrip("/").casefold(), expected.rstrip("/").casefold())


def _passkey_request_context(handler: SimpleHTTPRequestHandler) -> tuple[str, str]:
    host = str(handler.headers.get("Host") or "").strip()
    origin = f"{_request_scheme(handler)}://{host}"
    parsed = urlparse(origin)
    rp_id = str(parsed.hostname or "").strip().lower()
    if not rp_id or parsed.scheme not in {"http", "https"}:
        raise ValueError("Passkey origin is invalid.")
    if parsed.scheme != "https" and rp_id not in {"localhost", "127.0.0.1", "::1"}:
        raise PermissionError("Passkeys require HTTPS.")
    return rp_id, origin


def _session_cookie_value(token: str, *, secure: bool = False) -> str:
    suffix = "; Secure" if secure else ""
    return f"{_SESSION_COOKIE}={token}; HttpOnly; Path=/; Max-Age={web_accounts._SESSION_MAX_AGE}; SameSite=Lax{suffix}"


def _set_session_cookie(handler: SimpleHTTPRequestHandler, token: str) -> None:
    handler.send_header(
        "Set-Cookie",
        _session_cookie_value(token, secure=_request_is_https(handler)),
    )


def _clear_session_cookie(handler: SimpleHTTPRequestHandler) -> None:
    secure = "; Secure" if _request_is_https(handler) else ""
    handler.send_header(
        "Set-Cookie",
        f"{_SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax{secure}",
    )


def _request_cookie(handler: SimpleHTTPRequestHandler, name: str) -> str:
    return cookie_value(handler.headers.get("Cookie", ""), name)


def _test_lab_access(actor: str) -> dict[str, object]:
    return {"actor": str(actor or ""), "can_operate": True}


def _authorize_test_api(handler: SimpleHTTPRequestHandler) -> dict[str, object] | None:
    auth = _get_agent_api_auth(handler)
    if not auth:
        http_responses._serve_json(handler, 401, {"error": "agent token or human session required"})
        return None
    try:
        actor = page_api_auth.authorize(auth)
    except PermissionError as exc:
        http_responses._serve_json(handler, 403, {"error": str(exc)})
        return None
    except Exception as exc:
        LOGGER.warning("Test API identity verification failed: %s; frames=%s", type(exc).__name__, failure_frames(exc))
        http_responses._serve_json(handler, 503, {"error": "Test API 身份校验暂不可用"})
        return None
    return _test_lab_access(actor)


def _route_logout_get(request, _parsed, _actor: str):
    request.send_response(405)
    request.send_header("Allow", "POST")
    request.end_headers()


def _route_logout_post(request, _parsed, _actor: str):
    if not _same_origin_request(request):
        return http_responses._send_empty(request, 403)
    token = _get_session_token(request)
    username = web_accounts._verify_session_token(token or "") if token else None
    if username:
        web_accounts._PORTAL_ACCESS.upsert_user(
            username,
            actor=username,
            revoke_sessions=True,
            revoke_personal_tokens=False,
        )
    request.send_response(302)
    _clear_session_cookie(request)
    request.send_header("Location", "/login")
    request.end_headers()


def routes() -> tuple[RouteDefinition, ...]:
    """Signing out."""
    return (
        RouteDefinition("GET", "/logout", _route_logout_get, "portal-session", phase="public"),
        RouteDefinition("POST", "/logout", _route_logout_post, "portal-session", phase="public"),
    )
