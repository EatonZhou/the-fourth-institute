#!/usr/bin/env python3
"""Portal Web's HTTP shell: the request handler and the route table.

The shell wires each area's HTTP adapter (``http_*.py``) to the request,
response, identity and state modules, assembles the route table from the
adapters' ``routes``, the installed features' ``web.routes`` and its own
fallbacks, and dispatches each request through it: public routes, then the
routes Users and Residents both call, then the session gate and the
protected routes.
"""
from __future__ import annotations

import sys
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

import knowledge_board_lan.knowledge_backend as knowledge_backend
from management_portal import (
    http_auth,
    http_documents,
    http_files,
    http_instance,
    http_overview,
    http_request_body,
    http_responses,
    http_sign_in,
    http_static_files,
    http_user_enrollment,
    http_user_guide,
    knowledge_archive_backend,
    knowledge_item_chat_backend,
    local_terminal_backend,
    portal_features,
    principal_backpack,
    resident_chat_projection,
    resident_recommendation_backend,
    resident_sidebar_backend,
    resident_workspace_adapter,
    resource_views,
    user_avatar,
    user_preferences,
    web_accounts,
    web_clients,
    web_inbox,
    web_read_caches,
    web_resources,
)
from management_portal.http_account import AccountHttp
from management_portal.http_admin import AdminHttp
from management_portal.http_agent_status import AgentStatusHttp
from management_portal.http_agent_workbench import AgentWorkbenchHttp
from management_portal.http_knowledge import KnowledgeHttp
from management_portal.http_lineage import LineageHttp
from management_portal.http_portal_pages import PortalPagesHttp
from management_portal.http_portal_session import PortalSessionHttp
from management_portal.http_principal_backpack import PrincipalBackpackHttp
from management_portal.http_public_auth import PublicAuthHttp
from management_portal.http_public_pages import PublicPagesHttp
from management_portal.http_resources import ResourceHttp
from management_portal.http_routes import ROUTE_NOT_FOUND, RouteDefinition, RouteDispatcher
from management_portal.http_script_runs import ScriptRunHttp
from management_portal.http_skills import SkillsHttp
from management_portal.http_terminal import TerminalHttp
from management_portal.http_ui_state import UiStateHttp
from management_portal.resident_discovery_client import ResidentDiscoveryClient
from management_portal.resources import get_resource_service
from management_portal.resources.portal import portal_route_script
from management_portal.society_service import SocietyServiceUnavailable, workspace_contracts
from management_portal.watchboards import service as watchboard_applications


ROOT = Path(__file__).resolve().parent


def _resident_sidebar_snapshot(
    actor: str,
) -> dict:
    return resident_sidebar_backend.cached_snapshot(actor)


def _route_workbenches_get(request, parsed, actor: str):
    from management_portal.workbench_projections.http import handle

    return handle(request, parsed, actor)


def _route_workbenches_post(request, parsed, actor: str):
    from management_portal.workbench_projections.http import handle

    return handle(request, parsed, actor, write=True)


def _actor_method_route(method_name: str, **literal_params):
    def route(request, parsed, actor: str):
        return getattr(request, method_name)(parsed, actor, **literal_params)

    route.__name__ = f"_route_{method_name.removeprefix('_handle_')}"
    return route


def _route_navigation_pages(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, {"paths": _PORTAL_HTTP_ROUTES.page_paths()})


def _route_not_found(request, _parsed, _actor: str):
    if request.command == "POST" and urlparse(request.path).path.startswith("/test/api/"):
        try:
            http_request_body._read_bounded_json_object(request)
        except ValueError as exc:
            return http_responses._serve_json(request, 400, {"error": str(exc)})
        except Exception as exc:
            return http_responses._serve_exception_json(request, exc)
        return http_responses._serve_json(request, 404, {"error": "unknown Test Lab action"})
    if http_responses._is_json_api_path(urlparse(request.path).path):
        return http_responses._serve_http_error(request, 404, "API route not found")
    return http_responses._send_empty(request, 404)


_UI_STATE_HTTP = UiStateHttp(respond=http_responses._serve_json, read_json=http_request_body._read_request_json)
_AGENT_STATUS_HTTP = AgentStatusHttp(
    respond=lambda *args: http_responses._serve_json(*args),
    unavailable=lambda request: http_responses._serve_unavailable_json(request),
    resident_api=web_clients.resident_api,
    cached_summary=web_read_caches._cached_agent_status_summary,
    cached_active=web_read_caches._cached_active_agents,
    cached_scheduled_triggered=web_read_caches._cached_scheduled_triggered_status,
    invalidate_scheduled_triggered=web_read_caches._SCHEDULED_TRIGGERED_READ_CACHE.expire,
)

_ADMIN_HTTP = AdminHttp(
    respond=lambda *args: http_responses._serve_json(*args),
    session_actor=lambda request: http_auth._get_session_username(request),
    access_payload=lambda actor: web_accounts._admin_access_payload(actor),
    read_json=lambda request: http_request_body._read_request_json(request),
    is_admin=lambda actor: web_accounts._PORTAL_ACCESS.user(actor).get("category") == "portal_admin",
)

_PORTAL_SESSION_HTTP = PortalSessionHttp(
    respond=lambda *args: http_responses._serve_json(*args),
    exception_response=lambda *args: http_responses._serve_exception_json(*args),
    read_json=lambda request: http_request_body._read_request_json(request),
    session_actor=lambda request: http_auth._get_request_actor(request),
    access=lambda: web_accounts._PORTAL_ACCESS,
    avatar=lambda actor: user_avatar.get_user_avatar(actor),
    instance=http_instance._PORTAL_INSTANCE,
    build_id=http_instance._PORTAL_BUILD_ID,
    preferences=lambda actor: user_preferences._portal_user_preferences(actor),
    set_preferences=lambda actor, value: user_preferences._set_portal_user_preferences(actor, value),
    conversation_payload=lambda actor, **kwargs: web_inbox._cached_agent_conversation_payload(actor, **kwargs),
    workspace_adapter=resident_workspace_adapter,
    session_opened=lambda actor: _provision_open_session(actor),
)


def _provision_open_session(username: str) -> None:
    from management_portal.personal_ama import provision_session

    provision_session(username)


_PUBLIC_PAGES_HTTP = PublicPagesHttp(
    root=ROOT,
    check_session=lambda request: http_auth._check_session(request),
    serve_bytes=lambda *args, **kwargs: http_documents._serve_bytes(*args, **kwargs),
    serve_static=lambda *args: http_static_files._serve_static_file(*args),
    send_empty=lambda *args: http_responses._send_empty(*args),
    prepare_login=lambda error: http_sign_in._prepare_login_html(error),
    prepare_verify=lambda error: http_sign_in._prepare_login_verify_html(error),
    request_cookie=lambda *args: http_auth._request_cookie(*args),
    challenge_cookie=http_sign_in._LOGIN_CHALLENGE_COOKIE,
    challenge_username=lambda token: http_sign_in._login_challenge_username(token),
    clear_challenge=lambda request: http_sign_in._clear_login_challenge_cookie(request),
    route_script=lambda: portal_route_script(),
    respond=lambda *args: http_responses._serve_json(*args),
    media_content_type=lambda path: http_files._media_content_type(path),
)

_BACKPACK_HTTP = PrincipalBackpackHttp(
    respond=lambda *args: http_responses._serve_json(*args),
    read_json=lambda request, **kwargs: http_request_body._read_bounded_json_object(request, **kwargs),
    session_actor=lambda request: http_auth._get_session_username(request),
    backend=lambda: principal_backpack.PrincipalBackpack(
        access=web_accounts._PORTAL_ACCESS,
        resources=get_resource_service(),
        credential_usernames=web_accounts._load_users().keys(),
    ),
    known_users=lambda: web_accounts._load_users(),
)


_SCRIPT_RUN_HTTP = ScriptRunHttp(
    respond=http_responses._serve_json, exception=http_responses._serve_exception_json,
    payload=http_responses._serve_payload,
)


_RESOURCE_HTTP = ResourceHttp(
    respond=http_responses._serve_json, exception=http_responses._serve_exception_json,
    resource_error=http_responses._serve_resource_error, read_json=http_request_body._read_request_json,
    reference_payload=web_resources._resource_reference_payload,
    lineage_unavailable=http_responses._serve_society_service_unavailable,
    download_file=http_files._serve_download_file, media_file=http_files._serve_media_file,
    authorized_file=http_files._serve_authorized_resource_file, serve_bytes=http_documents._serve_bytes,
    download_disposition=http_files._download_content_disposition,
    auth_document=http_documents._serve_authenticated_document, knowledge_html=http_documents._prepare_knowledge_html,
    portal_location=lambda *args, **kwargs: web_resources._resource_portal_location(*args, **kwargs),
    portal_not_found=http_responses._serve_resource_portal_not_found,
    status_payload=http_overview._resource_service_status_payload,
    notify_index=lambda *args, **kwargs: web_resources._notify_resource_index(*args, **kwargs),
    resource_service=lambda: get_resource_service(),
)


_ACCOUNT_HTTP = AccountHttp(
    respond=http_responses._serve_json, exception=http_responses._serve_exception_json,
    serve_bytes=http_documents._serve_bytes,
    same_origin=lambda request: http_auth._same_origin_request(request),
    access=lambda: web_accounts._PORTAL_ACCESS,
    two_factor=lambda: web_accounts._PORTAL_TWO_FACTOR, passkeys=lambda: web_accounts._PORTAL_PASSKEYS,
    change_password=web_accounts._change_portal_password,
    read_bounded_json=http_request_body._read_bounded_json_object,
    issue_token=lambda actor: web_accounts._issue_session_token(actor),
    session_cookie=http_auth._session_cookie_value,
    request_is_https=http_auth._request_is_https,
    require_reauthentication=web_accounts._require_account_reauthentication,
    passkey_context=http_auth._passkey_request_context,
    begin_two_factor=http_sign_in._begin_two_factor_setup,
    confirm_two_factor=http_sign_in._confirm_two_factor_setup,
    send_empty=http_responses._send_empty,
    user_allowed=lambda username: web_accounts._portal_user_allowed(username),
)

_PORTAL_PAGES_HTTP = PortalPagesHttp(
    root=ROOT,
    document=http_documents._serve_authenticated_document,
    respond=http_responses._serve_json,
    exception=http_responses._serve_exception_json,
    send_empty=http_responses._send_empty,
    lineage_html=lambda: http_documents._prepare_lineage_html(),
    hatg_html=lambda: http_documents._prepare_hatg_panel_template(),
    settings_html=lambda: http_documents._prepare_settings_html(),
    management_html=lambda: http_documents._prepare_portal_management_html(),
    require_admin=lambda actor: web_accounts._require_authenticated_actor(actor),
    workspace_adapter=resident_workspace_adapter,
    resource_location=lambda address, actor: web_resources._resource_portal_location(address, actor),
    resource_not_found=http_responses._serve_resource_portal_not_found,
)

_AGENT_WORKBENCH_HTTP = AgentWorkbenchHttp(
    respond=lambda *args, **kwargs: http_responses._serve_json(*args, **kwargs),
    error=lambda *args, **kwargs: http_responses._serve_agent_workbench_error(*args, **kwargs),
    session_actor=lambda request: http_auth._get_request_actor(request),
    lineage_unavailable=http_responses._serve_society_service_unavailable,
    link_permission_denied=http_responses._serve_link_file_permission_denied,
    media_file=lambda *args, **kwargs: http_files._serve_media_file(*args, **kwargs),
    download_file=lambda *args, **kwargs: http_files._serve_download_file(*args, **kwargs),
    send_empty=http_responses._send_empty,
    media_content_type=http_files._media_content_type,
    resolved_path=http_files._society_service_resolved_path,
    lineage_favorite_payload=lambda actor: user_preferences._lineage_favorite_payload(actor),
    forget_favorite=lambda actor, selection: user_preferences._set_lineage_favorite(actor, selection, False),
    resident_sidebar_snapshot=lambda actor: _resident_sidebar_snapshot(actor),
    resident_api=lambda: web_clients.resident_api,
    execution_api=lambda: web_clients.execution_api,
    workspace_api=lambda: web_clients.workspace_api,
    watchboard_applications=lambda: watchboard_applications,
    resident_sidebar_backend=lambda: resident_sidebar_backend,
    resident_chat_projection=lambda: resident_chat_projection,
    local_terminal_backend=lambda: local_terminal_backend,
    knowledge_backend=lambda: knowledge_backend,
    read_json=http_request_body._read_bounded_json_object,
    read_multipart=http_request_body._read_request_multipart,
    multipart_body_limit=http_request_body._multipart_body_limit,
    body_too_large=http_request_body.RequestBodyTooLarge,
    json_max_bytes=http_request_body._JSON_REQUEST_MAX_BYTES,
    file_save_json_max_bytes=http_request_body._FILE_SAVE_JSON_MAX_BYTES,
    chat_image_max_bytes=workspace_contracts.CHAT_IMAGE_MAX_BYTES,
    chat_attachment_max_bytes=workspace_contracts.CHAT_ATTACHMENT_MAX_BYTES,
    discovery_client=lambda: ResidentDiscoveryClient(),
    recommendation_backend=lambda: resident_recommendation_backend,
)


_KNOWLEDGE_HTTP = KnowledgeHttp(
    respond=lambda *args, **kwargs: http_responses._serve_json(*args, **kwargs),
    exception_response=lambda *args, **kwargs: http_responses._serve_exception_json(*args, **kwargs),
    resource_error=lambda *args, **kwargs: http_responses._serve_resource_error(*args, **kwargs),
    media_file=lambda *args, **kwargs: http_files._serve_media_file(*args, **kwargs),
    send_empty=lambda *args, **kwargs: http_responses._send_empty(*args, **kwargs),
    session_actor=lambda request: http_auth._get_session_username(request),
    require_resource=lambda *args: web_resources._require_resource(*args),
    filter_graph=lambda *args: web_resources._filter_resource_graph(*args),
    notify_resource_index=lambda **kwargs: web_resources._notify_resource_index(**kwargs),
    resource_service=lambda: get_resource_service(),
    resource_views=lambda: resource_views,
    knowledge_backend=lambda: knowledge_backend,
    knowledge_item_chat_backend=lambda: knowledge_item_chat_backend,
    knowledge_archive_backend=lambda: knowledge_archive_backend,
    read_json=lambda request: http_request_body._read_request_json(request),
)

_SKILLS_HTTP = SkillsHttp()


_LINEAGE_HTTP = LineageHttp(
    respond=lambda *args: http_responses._serve_json(*args),
    exception_response=lambda request, exc: http_responses._serve_exception_json(request, exc),
    service_unavailable=lambda request, exc: http_responses._serve_society_service_unavailable(request, exc),
    session_actor=lambda request: http_auth._get_request_actor(request),
    service_status=lambda: http_overview._society_service_status_payload(),
    lineage=lambda: web_clients.lineage_domain,
    unavailable_error=SocietyServiceUnavailable,
)

_TERMINAL_HTTP = TerminalHttp(
    session_actor=lambda request: http_auth._get_session_username(request),
    read_json=lambda request: http_request_body._read_request_json(request),
    respond=lambda *args: http_responses._serve_json(*args),
    exception_response=lambda request, exc: http_responses._serve_exception_json(request, exc),
)

_PUBLIC_AUTH_HTTP = PublicAuthHttp(
    same_origin=lambda request: http_auth._same_origin_request(request),
    empty=lambda *args: http_responses._send_empty(*args),
    respond=lambda *args: http_responses._serve_json(*args),
    bounded_json=lambda request, **kwargs: http_request_body._read_bounded_json_object(request, **kwargs),
    bounded_form=lambda request: http_request_body._read_bounded_form_object(request),
    password_reset_user=lambda email: web_accounts._password_reset_user(email),
    password_resets=lambda: web_accounts._PORTAL_PASSWORD_RESETS,
    access=lambda: web_accounts._PORTAL_ACCESS,
    credentials=lambda: web_accounts._PORTAL_CREDENTIALS,
    request_client_key=lambda request: http_auth._request_client_key(request),
    verify_credentials=lambda *args: web_accounts._verify_credentials(*args),
    load_users=lambda: web_accounts._load_users(),
    user_allowed=lambda username: web_accounts._portal_user_allowed(username),
    two_factor=lambda: web_accounts._PORTAL_TWO_FACTOR,
    start_login_challenge=lambda username: http_sign_in._start_login_challenge(username),
    issue_session_token=lambda username: web_accounts._issue_session_token(username),
    set_login_challenge_cookie=lambda *args: http_sign_in._set_login_challenge_cookie(*args),
    set_session_cookie=lambda *args: http_auth._set_session_cookie(*args),
    request_cookie=lambda *args: http_auth._request_cookie(*args),
    login_challenge_cookie=http_sign_in._LOGIN_CHALLENGE_COOKIE,
    verify_login_challenge=lambda *args: http_sign_in._verify_login_challenge(*args),
    clear_login_challenge_cookie=lambda request: http_sign_in._clear_login_challenge_cookie(request),
    login_challenge_username=lambda token: http_sign_in._login_challenge_username(token),
)


def _feature_route_definitions() -> tuple[RouteDefinition, ...]:
    """The routes of the installed features (``web.routes``, ``portal_features.py``)."""
    shell = sys.modules[__name__]
    return tuple(route for _feature, routes in portal_features.contributions("web.routes") for route in routes(shell))


def _shell_routes() -> tuple[RouteDefinition, ...]:
    """The routes the shell answers itself: the projection host API and the page list."""
    return (
        http_auth._principal_route("GET", "/api/workbenches", _route_workbenches_get, "workbenches"),
        http_auth._principal_route("GET", "/api/workbenches/", _route_workbenches_get, "workbenches", match="prefix"),
        http_auth._principal_route("POST", "/api/workbenches", _route_workbenches_post, "workbenches"),
        http_auth._principal_route("POST", "/api/workbenches/", _route_workbenches_post, "workbenches", match="prefix"),
        http_auth._principal_route("GET", "/api/navigation/pages", _route_navigation_pages, "portal-portal-get"),
    )


def _portal_route_definitions() -> tuple[RouteDefinition, ...]:
    """The route table: each area's routes, the installed features' routes, then the fallbacks.

    Routes of one method and phase overlap only where an exact route precedes the
    prefix fallback covering it, and the fallbacks come last; ``RouteDispatcher``
    checks both, so the groups may come in any order before the fallbacks.
    """
    principal_route = http_auth._principal_route
    return (
        *http_overview.routes(),
        *http_auth.routes(),
        *_shell_routes(),
        *_AGENT_WORKBENCH_HTTP.routes(principal_route),
        *_LINEAGE_HTTP.routes(principal_route),
        *_KNOWLEDGE_HTTP.routes(),
        *_SKILLS_HTTP.routes(),
        *_PUBLIC_PAGES_HTTP.routes(),
        *http_user_enrollment.routes(),
        *http_user_guide.routes(),
        *http_static_files.routes(),
        *_PORTAL_PAGES_HTTP.routes(principal_route),
        *_ACCOUNT_HTTP.routes(principal_route),
        *_PORTAL_SESSION_HTTP.routes(principal_route),
        *_ADMIN_HTTP.routes(),
        *_UI_STATE_HTTP.routes(principal_route),
        *_TERMINAL_HTTP.routes(),
        *_AGENT_STATUS_HTTP.routes(principal_route),
        *_BACKPACK_HTTP.routes(),
        *_RESOURCE_HTTP.routes(principal_route),
        *_SCRIPT_RUN_HTTP.routes(),
        *_PUBLIC_AUTH_HTTP.routes(),
        *_feature_route_definitions(),
        RouteDefinition("GET", "/", _actor_method_route("_handle_get_fallback"), "portal-fallback", match="fallback"),
        RouteDefinition("POST", "/", _route_not_found, "portal-fallback", match="fallback"),
    )


_PORTAL_HTTP_ROUTES = RouteDispatcher(_portal_route_definitions())


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_request(self, code="-", size="-"):
        # Only failed requests are logged; successful ones carry nothing to act on.
        if isinstance(code, int) and code < 400:
            return
        super().log_request(code, size)

    def do_OPTIONS(self):
        return http_responses._send_options(self)

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        try:
            return self._do_GET()
        except Exception as exc:
            if not http_responses._is_json_api_path(urlparse(self.path).path):
                raise
            self.log_error("GET %s failed: %r", urlparse(self.path).path, exc)
            return http_responses._serve_exception_json(self, exc)

    def _do_GET(self):
        parsed = urlparse(self.path)

        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, self, parsed, "", phase="public"
        )
        if routed is not ROUTE_NOT_FOUND:
            return routed

        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, self, parsed, "", phase="dual-auth"
        )
        if routed is not ROUTE_NOT_FOUND:
            return routed

        test_access = None
        if parsed.path.startswith("/test/api/"):
            test_access = http_auth._authorize_test_api(self)
            if test_access is None:
                return

        # ── Auth gate ──
        if test_access is None and not http_auth._check_session(self):
            if http_responses._is_json_api_path(parsed.path):
                return http_responses._serve_http_error(self, 401, "Authentication required")
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # ── Protected routes ──
        route_actor = str((test_access or {}).get("actor") or http_auth._get_session_username(self) or "")
        self._test_api_access = test_access
        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, self, parsed, route_actor, phase="protected"
        )
        return routed

    def _handle_get_fallback(self, parsed, route_actor):
        if http_responses._is_json_api_path(parsed.path):
            return http_responses._serve_http_error(self, 404, "API route not found")
        resource_path_address = unquote(parsed.path.lstrip("/")).strip("/")
        resource_root_id = resource_path_address.partition("/")[0]
        try:
            get_resource_service().validate_id(resource_root_id)
        except ValueError:
            pass
        else:
            location = web_resources._resource_portal_location(
                resource_path_address,
                http_auth._get_session_username(self) or "",
            )
            if not location:
                return http_responses._serve_resource_portal_not_found(
                    self,
                    resource_path_address,
                )
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        static_file = http_static_files._static_fallback_file(parsed.path)
        if static_file is not None:
            return http_static_files._serve_static_file(self, static_file, self.guess_type(str(static_file)))
        return super().do_GET()


    def do_POST(self):
        try:
            from management_common.resource_lifecycle import lifecycle_scope

            with lifecycle_scope(self.headers.get("X-Portal-Resource-Lifecycle", ""),
                                 actor=http_auth._get_session_username(self) or ""):
                return Handler._do_POST(self)
        except http_request_body.RequestBodyTooLarge as exc:
            return http_responses._serve_json(self, 413, {"error": str(exc)})
        except http_request_body.InvalidRequestBody as exc:
            return http_responses._serve_json(self, 400, {"error": str(exc)})
        except Exception as exc:
            self.log_error("POST %s failed: %r", urlparse(self.path).path, exc)
            return http_responses._serve_exception_json(self, exc)

    def _do_POST(self):
        parsed = urlparse(self.path)

        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "POST", parsed.path, self, parsed, "", phase="public"
        )
        if routed is not ROUTE_NOT_FOUND:
            return routed

        # Browser session authentication is ambient authority. Enforce its
        # origin contract once before any protected or dual-auth POST route;
        # bearer-only callers remain independent of browser origin headers.
        if http_auth._get_session_username(self) and not http_auth._same_origin_request(self):
            return http_responses._serve_http_error(self, 403, "Request origin is not allowed")

        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "POST", parsed.path, self, parsed, "", phase="dual-auth"
        )
        if routed is not ROUTE_NOT_FOUND:
            return routed

        test_access = None
        if parsed.path.startswith("/test/api/"):
            test_access = http_auth._authorize_test_api(self)
            if test_access is None:
                return

        # ── Auth gate ──
        if test_access is None and not http_auth._check_session(self):
            return http_responses._serve_http_error(self, 401, "Authentication required")

        # ── Protected routes ──
        route_actor = str((test_access or {}).get("actor") or http_auth._get_session_username(self) or "")
        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "POST", parsed.path, self, parsed, route_actor, phase="protected"
        )
        return routed
