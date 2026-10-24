#!/usr/bin/env python3
from __future__ import annotations
from management_common.portal_paths import path as portal_path
from management_portal.runtime_identity_headers import runtime_identity_header
from management_portal.http_timing import timed_call


import base64
import gzip
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from contextvars import copy_context
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent
DAILY_NEWS_ROOT = Path(os.getenv(
    "PORTAL_DAILY_NEWS_ROOT",
    str(portal_path("share_root") / "Agents" / "lineage_7e2464bbdb60" / "memory" / "reports"),
)).expanduser()
import knowledge_board_lan.knowledge_backend as knowledge_backend
from management_common.site_registry import get_portal_summary, get_runtime_config
from management_portal.watchboards import service as watchboard_applications
from management_portal.resources import (
    ResourceService,
    get_resource_service,
)
from management_portal.resources.errors import (
    resource_error_http_response,
)
from management_portal.resources.portal import canonical_portal_location, portal_route_script
from management_portal.society_service import (
    SocietyServiceBusy,
    SocietyServiceClient,
    SocietyServiceUnavailable,
    get_society_service,
)
from management_portal.society_service import workspace_contracts
from management_portal.conversation_completion import completion_signature
from management_portal import agent_context_selection, conversation_read_state
from management_portal.execution import get_execution_service
from management_portal.component_lifecycle import read_component_state
from management_portal.http_errors import instruction_error_payload as _hatg_instruction_error_payload
from management_common.operation_errors import OperationError
from management_portal.http_errors import exception_http_error, normalize_json_error
from management_portal.http_media_range import plan_media_response
from management_portal.portal_access_control import (
    PortalCredentialStore,
    list_users,
    USERNAME_RE,
    get_portal_access_store,
)
from management_portal.portal_session_auth import (
    session_cookie_name,
    login_challenge_cookie_name,
    session_signing_key_path,
    two_factor_key_path,
    PortalSessionAuthority,
    cookie_value,
)
from management_portal.portal_two_factor import (
    PortalTwoFactorStore,
    generate_totp_secret,
    match_totp,
    otpauth_uri,
)
from management_portal.portal_passkeys import PortalPasskeyService
from management_portal.portal_password_reset import (
    PortalPasswordResetService,
    PortalPasswordResetStore,
    PortalSmtpMailer,
)
from management_portal.shared_state import atomic_replace_json, exclusive_file_lock
from management_portal import presentation_store
from management_portal.http_routes import (
    ROUTE_NOT_FOUND,
    RouteDefinition,
    RouteDispatcher,
)
from management_portal.resident_discovery_client import (
    ResidentDiscoveryClient,
    ResidentDiscoveryUnavailable,
)
from management_portal.http_agent_status import AgentStatusHttp
from management_portal.http_admin import AdminHttp
from management_portal.http_portal_session import PortalSessionHttp
from management_portal.http_public_pages import PUBLIC_SHARED_STATIC_ASSETS, PublicPagesHttp
from management_portal.http_principal_backpack import PrincipalBackpackHttp
from management_portal import principal_backpack
from management_portal import user_avatar
from management_portal.http_resources import ResourceHttp
from management_portal.http_script_runs import ScriptRunHttp
from management_portal.http_account import AccountHttp
from management_portal.http_ui_state import UiStateHttp
from management_portal.http_portal_pages import PortalPagesHttp
from management_portal.http_agent_workbench import AgentWorkbenchHttp
from management_portal.http_knowledge import KnowledgeHttp
from management_portal.http_skills import SkillsHttp
from management_portal.http_lineage import LineageHttp
from management_portal.http_terminal import TerminalHttp
from management_portal.http_public_auth import PublicAuthHttp

from management_portal import (
    agent_conversation_index_backend,
    agent_status_backend,
    scheduled_triggered_monitor,
    agent_token_auth,
    page_api_auth,
    knowledge_archive_backend,
    knowledge_item_chat_backend,
    mailbox_sources,
    local_terminal_backend,
    conversation_index_projection,
    portal_features,
    resident_sidebar_backend,
    resident_recommendation_backend,
    resident_chat_projection,
    resident_workspace_adapter,
    resource_views,
)
from management_portal.page_renderers import render_knowledge_html
from management_portal.notifications import publish_local_projection_change, publish_projection_change

# ── Auth / instance identity ───────────────────────────────────────────────────
def _env_path(name: str, default: Path) -> Path:
    return Path(os.getenv(name, str(default))).expanduser()


def _daily_news_payload() -> dict:
    requested_date = datetime.now().astimezone().date().isoformat()
    requested_path = DAILY_NEWS_ROOT / f"{requested_date}__daily_news.md"
    report_path = requested_path
    if not report_path.is_file():
        try:
            candidates = sorted(
                DAILY_NEWS_ROOT.glob("????-??-??__daily_news.md"),
                reverse=True,
            )
        except OSError as exc:
            return {
                "status": "error",
                "date": requested_date,
                "path": str(requested_path),
                "content": "",
                "error": str(exc),
            }
        report_path = next(
            (
                candidate
                for candidate in candidates
                if candidate.name[:10] <= requested_date
            ),
            requested_path,
        )
    try:
        content = report_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            "status": "missing",
            "date": requested_date,
            "path": str(requested_path),
            "content": "",
        }
    except OSError as exc:
        return {
            "status": "error",
            "date": requested_date,
            "path": str(report_path),
            "content": "",
            "error": str(exc),
        }
    try:
        updated_at = datetime.fromtimestamp(report_path.stat().st_mtime).astimezone().isoformat()
    except OSError:
        updated_at = ""
    return {
        "status": "ok",
        "date": report_path.name[:10],
        "requested_date": requested_date,
        "stale": report_path != requested_path,
        "path": str(report_path),
        "updated_at": updated_at,
        "content": content,
    }


def _read_release_manifest() -> dict:
    try:
        payload = json.loads((WORKSPACE_ROOT / "release-manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


_RELEASE_MANIFEST = _read_release_manifest()
_PORTAL_INSTANCE = os.getenv("PORTAL_INSTANCE", "dev").strip() or "dev"


def _resolve_portal_build_id(configured: object, manifest: object) -> str:
    configured_id = str(configured or "").strip()
    release = manifest if isinstance(manifest, dict) else {}
    manifest_id = str(
        release.get("source_commit") or ""
    ).strip()
    if configured_id and configured_id != "release":
        return configured_id
    return manifest_id or configured_id or "development"


_PORTAL_BUILD_ID = _resolve_portal_build_id(
    os.getenv("PORTAL_BUILD_ID", ""),
    _RELEASE_MANIFEST,
)
_PORTAL_ALLOWED_USERS = {
    item.strip()
    for item in os.getenv("PORTAL_ALLOWED_USERS", "").split(",")
    if item.strip()
}
_PORTAL_SERVICE_NAME = os.getenv("PORTAL_SERVICE_NAME", "management-portal.service").strip() or "management-portal.service"
_SESSION_SIGNING_KEY_PATH = session_signing_key_path()
_TWO_FACTOR_KEY_PATH = two_factor_key_path()

_PORTAL_UPLOADS_ROOT = _env_path(
    "PORTAL_UPLOADS_ROOT",
    (portal_path("staging_root") / "shared" / "portal_uploads"),
)
_RESTART_NOTICE_PATH = _env_path("PORTAL_RESTART_NOTICE_PATH", ROOT / "restart_notice.json")
_SESSION_COOKIE = session_cookie_name()
_LOGIN_CHALLENGE_COOKIE = login_challenge_cookie_name()
_SESSION_MAX_AGE = int(os.getenv("MANAGEMENT_PORTAL_SESSION_MAX_AGE", str(30 * 24 * 3600)))
_LOGIN_CHALLENGE_MAX_AGE = 5 * 60
_LOGIN_CHALLENGE_MAX_ATTEMPTS = 5
_TWO_FACTOR_SETUP_MAX_AGE = 10 * 60
_SESSION_AUTHORITY: PortalSessionAuthority | None = None
_SESSION_AUTHORITY_CONFIG: tuple[str, int] | None = None
_SESSION_AUTHORITY_LOCK = threading.RLock()
_AGENT_WORKBENCH_LINEAGE_FAVORITE_RE = re.compile(
    r"^lineage:lineage_[0-9a-f]{12}$"
)
_PORTAL_MAX_FAVORITE_LINEAGES = 120
_RESTART_NOTICE_LOCK = threading.RLock()
_PORTAL_ACCESS = get_portal_access_store()
_PORTAL_CREDENTIALS = PortalCredentialStore()
from management_portal.user_enrollment import UserEnrollment
from management_portal import http_user_enrollment
from management_portal import http_user_guide

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
_LOGIN_CHALLENGES: dict[str, dict] = {}
_LOGIN_CHALLENGES_LOCK = threading.RLock()
_TWO_FACTOR_SETUPS: dict[str, dict] = {}
_TWO_FACTOR_SETUPS_LOCK = threading.RLock()
_SERVER_STARTED_AT = time.time()
_SOCIETY_SERVICE = get_society_service()
resident_api = _SOCIETY_SERVICE.resident
workspace_api = _SOCIETY_SERVICE.workspace
lineage_domain = _SOCIETY_SERVICE.lineage
execution_api = get_execution_service()
_ACTIVE_AGENTS_CACHE_TTL_SECONDS = 5.0
_ACTIVE_AGENTS_CACHE_LOCK = threading.Lock()
_ACTIVE_AGENTS_CACHE: dict[str, tuple[float, dict]] = {}
_ACTIVE_AGENTS_INFLIGHT: dict[str, Future[dict]] = {}


_NO_COLD_READ_VALUE = object()


class _CoalescingReadCache:
    """Small process-local cache for identical, immutable read projections."""

    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._lock = threading.Lock()
        self._values: dict[object, tuple[float, object]] = {}
        self._inflight: dict[object, Future] = {}
        self._generation = 0

    def _refresh(
        self,
        key: object,
        builder,
        refresh: Future,
        generation: int,
        *,
        stale: object,
        has_stale: bool,
    ):
        try:
            result = builder()
        except BaseException as exc:
            with self._lock:
                if self._inflight.get(key) is refresh:
                    self._inflight.pop(key, None)
            if has_stale:
                refresh.set_result(stale)
                return stale
            refresh.set_exception(exc)
            raise
        with self._lock:
            if self._inflight.get(key) is refresh:
                self._inflight.pop(key, None)
                if generation == self._generation:
                    self._values[key] = (
                        time.monotonic() + self.ttl_seconds,
                        result,
                    )
        refresh.set_result(result)
        return result

    def get(
        self,
        key: object,
        builder,
        *,
        timeout: float = 90.0,
        cold_value: object = _NO_COLD_READ_VALUE,
        stale_transform=None,
        allow_stale: bool = True,
    ):
        now = time.monotonic()
        owns_refresh = False
        missing = object()
        stale: object = missing
        with self._lock:
            cached = self._values.get(key)
            if cached and now < cached[0]:
                return cached[1]
            if cached and allow_stale:
                stale = cached[1]
            refresh = self._inflight.get(key)
            if refresh is None:
                refresh = Future()
                self._inflight[key] = refresh
                generation = self._generation
                owns_refresh = True
        if not owns_refresh:
            if stale is not missing:
                return stale_transform(stale) if stale_transform else stale
            if cold_value is not _NO_COLD_READ_VALUE:
                return cold_value
            return refresh.result(timeout=timeout)

        if stale is not missing:
            threading.Thread(
                target=self._refresh,
                args=(key, builder, refresh, generation),
                kwargs={"stale": stale, "has_stale": True},
                name="portal-read-cache-refresh",
                daemon=True,
            ).start()
            return stale_transform(stale) if stale_transform else stale
        if cold_value is not _NO_COLD_READ_VALUE:
            threading.Thread(
                target=self._refresh,
                args=(key, builder, refresh, generation),
                kwargs={"stale": cold_value, "has_stale": True},
                name="portal-read-cache-cold-refresh",
                daemon=True,
            ).start()
            return cold_value
        return self._refresh(
            key,
            builder,
            refresh,
            generation,
            stale=missing,
            has_stale=False,
        )

    def invalidate(self, *, preserve_stale: bool = False) -> None:
        with self._lock:
            self._generation += 1
            if preserve_stale:
                self._values = {
                    key: (0.0, value)
                    for key, (_expires_at, value) in self._values.items()
                }
            else:
                self._values.clear()

    def expire(self, key: object) -> None:
        """Refresh one viewer; a pre-change rebuild cannot replace their next read."""
        with self._lock:
            if key in self._values:
                self._values[key] = (0.0, self._values[key][1])
                self._inflight.pop(key, None)

    def discard(self, matches) -> None:
        """Forget matching entries outright; their next read is built from committed state."""
        with self._lock:
            for store in (self._values, self._inflight):
                for key in [key for key in store if matches(key)]:
                    del store[key]


_SCHEDULED_TRIGGERED_READ_CACHE = _CoalescingReadCache(30.0)
_AGENT_CONVERSATION_READ_CACHE = _CoalescingReadCache(2.0)
_PORTAL_USER_UPLOAD_ROOTS = (
    _PORTAL_UPLOADS_ROOT / "resident_chat_images",
    _PORTAL_UPLOADS_ROOT / "resident_chat_attachments",
    _PORTAL_UPLOADS_ROOT / "resident_chat_files",
)
_MULTIPART_FORM_OVERHEAD_BYTES = 65_536
_MULTIPART_HARD_LIMIT_BYTES = 256 * 1024 * 1024
_JSON_REQUEST_MAX_BYTES = 4 * 1024 * 1024
_FILE_SAVE_JSON_MAX_BYTES = 2 * 1024 * 1024


class InvalidRequestBody(ValueError):
    pass


class RequestBodyTooLarge(InvalidRequestBody):
    pass


def _multipart_body_limit(payload_max_bytes: int) -> int:
    return min(
        _MULTIPART_HARD_LIMIT_BYTES,
        max(0, int(payload_max_bytes)) + _MULTIPART_FORM_OVERHEAD_BYTES,
    )


def _cached_agent_status_summary(actor: str) -> dict:
    # The backend keeps the stored summary and rebuilds it in milliseconds. A
    # stale-while-revalidate cache here would answer every poll with the
    # summary the previous poll caused, long enough for quota readings to expire.
    return agent_status_backend.get_agent_status_summary(str(actor or ""))


def _cached_scheduled_triggered_status(actor: str, *, fresh: bool = False) -> dict:
    context = copy_context()
    def build() -> dict:
        try:
            return scheduled_triggered_monitor.get_scheduled_triggered_status(str(actor or ""))
        except Exception as exc:
            return {"status": "error", "loaded": False, "items": [], "error": str(exc)}

    if fresh:
        # An explicit post-mutation read must neither return a cached plan nor
        # join a rebuild that began before the instruction committed.
        _SCHEDULED_TRIGGERED_READ_CACHE.discard(lambda key: key == str(actor or ""))
        return _SCHEDULED_TRIGGERED_READ_CACHE.get(
            str(actor or ""), lambda: context.run(build), allow_stale=False,
        )
    return _SCHEDULED_TRIGGERED_READ_CACHE.get(
        str(actor or ""),
        lambda: context.run(build),
        cold_value={"status": "loading", "loaded": False, "items": []},
        stale_transform=lambda value: {**value, "refreshing": True},
    )


def _invalidate_agent_conversation_cache() -> None:
    # Conversation mutations already carry an authoritative receipt to the
    # initiating browser.  Keep the last committed index available while one
    # background rebuild incorporates the mutation for other tabs.
    _AGENT_CONVERSATION_READ_CACHE.invalidate(preserve_stale=True)


def _forget_cached_inbox(actor: str) -> None:
    # The viewer re-reads the Inbox right after changing a read state; a copy
    # cached before the change would bring the unread mark back. Other
    # viewers' copies do not contain this viewer's read state.
    _AGENT_CONVERSATION_READ_CACHE.discard(lambda key: key[0] == actor)


def _cached_agent_conversation_payload(
    actor: str,
    *,
    resident_id: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> dict:
    # Event-driven targets must not spend their retry budget waiting for a
    # previously cached projection to expire. Browsers coalesce these reads.
    if resident_id:
        return _agent_conversation_payload(actor, resident_id=resident_id)
    key = (str(actor or ""), str(resident_id or ""), int(page_size), str(page_token or ""))
    return _AGENT_CONVERSATION_READ_CACHE.get(
        key,
        lambda: _agent_conversation_payload(
            actor,
            resident_id=resident_id,
            page_size=page_size,
            page_token=page_token,
        ),
        allow_stale=False,
    )


def _invalidate_active_agents_cache(actor: str) -> None:
    """Discard an actor snapshot and detach any older refresh in flight."""

    cache_key = str(actor or "")
    with _ACTIVE_AGENTS_CACHE_LOCK:
        _ACTIVE_AGENTS_CACHE.pop(cache_key, None)
        _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)


def _cached_active_agents(actor: str) -> dict:
    """Collapse refresh bursts without holding a lock during projection IO."""

    cache_key = str(actor or "")
    now = time.monotonic()
    owns_refresh = False
    missing = object()
    stale: object = missing
    with _ACTIVE_AGENTS_CACHE_LOCK:
        cached = _ACTIVE_AGENTS_CACHE.get(cache_key)
        if cached and now - cached[0] < _ACTIVE_AGENTS_CACHE_TTL_SECONDS:
            return cached[1]
        if cached:
            stale = cached[1]
        refresh = _ACTIVE_AGENTS_INFLIGHT.get(cache_key)
        if refresh is None:
            refresh = Future()
            _ACTIVE_AGENTS_INFLIGHT[cache_key] = refresh
            owns_refresh = True
    if not owns_refresh:
        if stale is not missing:
            return {**stale, "refreshing": True}
        return refresh.result(timeout=45.0)

    def build() -> dict:
        try:
            result = get_society_service().projection.collect_active_agents(
                actor=cache_key,
            )
        except Exception as exc:
            failed = {**stale, "refreshing": False, "error": str(exc)} if stale is not missing else None
            with _ACTIVE_AGENTS_CACHE_LOCK:
                if _ACTIVE_AGENTS_INFLIGHT.get(cache_key) is refresh:
                    if failed is not None:
                        _ACTIVE_AGENTS_CACHE[cache_key] = (time.monotonic(), failed)
                    _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)
            if stale is not missing:
                refresh.set_result(failed)
                return failed
            refresh.set_exception(exc)
            raise
        with _ACTIVE_AGENTS_CACHE_LOCK:
            if _ACTIVE_AGENTS_INFLIGHT.get(cache_key) is refresh:
                _ACTIVE_AGENTS_CACHE[cache_key] = (time.monotonic(), result)
                _ACTIVE_AGENTS_INFLIGHT.pop(cache_key, None)
        refresh.set_result(result)
        return result

    if stale is not missing:
        threading.Thread(
            target=build,
            name="active-agents-cache-refresh",
            daemon=True,
        ).start()
        return {**stale, "refreshing": True}
    return build()


def _resident_sidebar_snapshot(
    actor: str,
) -> dict:
    return resident_sidebar_backend.cached_snapshot(actor)


def _mailbox_source_entries(actor: str) -> list[dict]:
    """The features' Mailbox items: Notice Board mentions, Chatrooms (``mailbox_sources``)."""
    return mailbox_sources.entries(actor)


def _agent_conversation_entries(
    actor: str,
    resident_ids: list[str] | None = None,
) -> list[dict]:
    resident_entries = agent_conversation_index_backend.build_entries(
        actor,
        resident_ids=resident_ids,
    )
    other_entries = _mailbox_source_entries(actor) if resident_ids is None else []
    return agent_conversation_index_backend.normalized_entries(
        [*resident_entries, *other_entries]
    )


def _load_users() -> dict[str, str]:
    """Read the current Portal password authority."""
    return _PORTAL_CREDENTIALS.load()


from management_portal.authentication_attempts import AuthenticationAttempts
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


def _get_session_token(handler: SimpleHTTPRequestHandler) -> str | None:
    return cookie_value(handler.headers.get("Cookie", ""), _SESSION_COOKIE) or None


def _get_session_username(handler: SimpleHTTPRequestHandler) -> str | None:
    token = _get_session_token(handler)
    if not token:
        return None
    username = _verify_session_token(token)
    return username if username and _portal_user_allowed(username) else None


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


def _portal_bool_preference(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return default


def _portal_motion_preference(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"system", "full", "reduced"} else "system"


def _portal_auto_colors_preference(value: object) -> dict:
    value = value if isinstance(value, dict) else {}
    accent = str(value.get("accent") or "").strip().lower()
    quote_color = str(value.get("quote_color") or "").strip().lower()
    strength = value.get("bubble_intensity")
    # The persisted value is a color, never arbitrary CSS.
    return {
        "accent": accent if re.fullmatch(r"#[0-9a-f]{6}", accent) else "",
        "quote_color": quote_color if re.fullmatch(r"#[0-9a-f]{6}", quote_color) else "",
        "bubble_intensity": int(min(100, max(0, strength)) + .5)
        if type(strength) in (int, float) and float("-inf") < strength < float("inf") else 18,
    }


def _default_portal_user_preferences() -> dict:
    return {
        "appearance": {"motion": "system", "language": "auto"},
        "lineage": {
            "favorite_lineage_ids": [],
        },
        "resource_workspaces": {
            "article": {"last_item_id": ""},
        },
        "onboarding": {"welcome_dismissed": False},
    }


def _limited_preference_string_list(
    value: object,
    *,
    max_entries: int,
    max_item_length: int = 320,
) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_item in value:
        item = str(raw_item or "").strip()[:max_item_length]
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized[-max_entries:]


def _normalize_portal_user_preferences(value: object) -> dict:
    prefs = _default_portal_user_preferences()
    if not isinstance(value, dict):
        return prefs
    appearance = value.get("appearance") if isinstance(value.get("appearance"), dict) else {}
    prefs["appearance"]["motion"] = _portal_motion_preference(appearance.get("motion"))
    from management_portal.localization import normalize_language
    prefs["appearance"]["language"] = normalize_language(appearance.get("language"))
    if "auto_colors" in appearance:
        prefs["appearance"]["auto_colors"] = _portal_auto_colors_preference(appearance["auto_colors"])
    lineage = value.get("lineage") if isinstance(value.get("lineage"), dict) else {}
    prefs["lineage"]["favorite_lineage_ids"] = _limited_preference_string_list(
        lineage.get("favorite_lineage_ids"),
        max_entries=_PORTAL_MAX_FAVORITE_LINEAGES,
        max_item_length=160,
    )
    resource_workspaces = (
        value.get("resource_workspaces")
        if isinstance(value.get("resource_workspaces"), dict)
        else {}
    )
    article_workspace = (
        resource_workspaces.get("article")
        if isinstance(resource_workspaces.get("article"), dict)
        else {}
    )
    last_article_id = str(article_workspace.get("last_item_id") or "").strip()
    prefs["resource_workspaces"]["article"]["last_item_id"] = (
        last_article_id
        if re.fullmatch(r"article_[0-9a-f]{12}", last_article_id)
        else ""
    )
    onboarding = value.get("onboarding") if isinstance(value.get("onboarding"), dict) else {}
    prefs["onboarding"]["welcome_dismissed"] = _portal_bool_preference(onboarding.get("welcome_dismissed"), False)
    return prefs


def _merge_portal_user_preferences(current: dict, patch: object) -> dict:
    next_preferences = _normalize_portal_user_preferences(current)
    patch_preferences = (
        patch.get("preferences")
        if isinstance(patch, dict) and isinstance(patch.get("preferences"), dict)
        else patch
    )
    if not isinstance(patch_preferences, dict):
        return next_preferences
    appearance = (
        patch_preferences.get("appearance")
        if isinstance(patch_preferences.get("appearance"), dict)
        else {}
    )
    if "motion" in appearance:
        next_preferences["appearance"]["motion"] = _portal_motion_preference(
            appearance.get("motion")
        )
    if "language" in appearance:
        from management_portal.localization import normalize_language
        next_preferences["appearance"]["language"] = normalize_language(appearance.get("language"))
    if "auto_colors" in appearance:
        colors = appearance["auto_colors"]
        next_preferences["appearance"]["auto_colors"] = _portal_auto_colors_preference({
            **next_preferences["appearance"].get("auto_colors", {}),
            **(colors if isinstance(colors, dict) else {"accent": "", "quote_color": "", "bubble_intensity": 18}),
        })
    onboarding = patch_preferences.get("onboarding") if isinstance(patch_preferences.get("onboarding"), dict) else {}
    if "welcome_dismissed" in onboarding:
        next_preferences["onboarding"]["welcome_dismissed"] = _portal_bool_preference(
            onboarding.get("welcome_dismissed"), next_preferences["onboarding"]["welcome_dismissed"],
        )
    lineage = (
        patch_preferences.get("lineage")
        if isinstance(patch_preferences.get("lineage"), dict)
        else {}
    )
    if "favorite_lineage_ids" in lineage:
        next_preferences["lineage"]["favorite_lineage_ids"] = (
            _limited_preference_string_list(
                lineage.get("favorite_lineage_ids"),
                max_entries=_PORTAL_MAX_FAVORITE_LINEAGES,
                max_item_length=160,
            )
        )
    resource_workspaces = (
        patch_preferences.get("resource_workspaces")
        if isinstance(patch_preferences.get("resource_workspaces"), dict)
        else {}
    )
    article_workspace = (
        resource_workspaces.get("article")
        if isinstance(resource_workspaces.get("article"), dict)
        else {}
    )
    if "last_item_id" in article_workspace:
        last_article_id = str(article_workspace.get("last_item_id") or "").strip()
        next_preferences["resource_workspaces"]["article"]["last_item_id"] = (
            last_article_id
            if re.fullmatch(r"article_[0-9a-f]{12}", last_article_id)
            else ""
        )
    return next_preferences










def _portal_user_preferences(actor: str) -> dict:
    actor_id = str(actor or "").strip()
    return _normalize_portal_user_preferences(presentation_store.preferences(actor_id)) if actor_id else _default_portal_user_preferences()


def _set_portal_user_preferences(actor: str, patch: object) -> dict:
    actor_id = str(actor or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后修改用户设置")
    return presentation_store.update_preferences(actor_id, lambda current: _merge_portal_user_preferences(current, patch))


def _agent_conversation_payload(
    actor: str,
    *,
    resident_id: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> dict:
    actor_id = str(actor or "").strip()
    read_state = timed_call("portal_conv_read_state", conversation_read_state.load, actor_id)
    target_ids = [resident_id] if resident_id else None
    target_state = ""
    resident_cursor = 0
    if resident_id:
        try:
            timed_call("portal_conv_identity", resident_api.resolve_resident_identity, resident_id, actor_id)
            target_state = "present"
            resident_cursor = timed_call("portal_conv_cursor", agent_conversation_index_backend.canonical_resident_cursor,
                actor_id,
                resident_id,
            )
        except KeyError:
            target_state = "deleted"
        except PermissionError:
            target_state = "forbidden"
    page = (
        timed_call("portal_conv_entries", agent_conversation_index_backend.build_entries_page,
            actor_id, page_size=page_size, page_token=page_token,
        )
        if not resident_id else {}
    )
    if page and not page_token:
        # The features' items (Notice Board mentions, Chatrooms) are not Resident
        # activity pages; the first page carries all of them so each reaches the Inbox once.
        page["entries"] = agent_conversation_index_backend.normalized_entries(
            [*page["entries"], *timed_call("portal_conv_entries", _mailbox_source_entries, actor_id)]
        )
        pinned_keys = set(read_state.get("pinned_keys") or [])
        if pinned_keys:
            # This request already authorized and assembled the page's entries.
            # Only pins outside that snapshot need their own source read.
            page_keys = {entry["key"] for entry in page["entries"]}
            pinned_residents = [key.removeprefix("resident:") for key in pinned_keys
                                if key.startswith("resident:") and key not in page_keys]
            pinned_entries = timed_call("portal_conv_entries", _agent_conversation_entries, actor_id, pinned_residents) if pinned_residents else []
            page["entries"] = agent_conversation_index_backend.normalized_entries([*page["entries"], *pinned_entries])
            available_keys = {entry["key"] for entry in page["entries"]}
            read_state = timed_call("portal_conv_read_state", conversation_read_state.retain_pins, actor_id, available_keys)
    elif page and page_token:
        # Pinned targets were already returned on page one, even when old.
        pinned_keys = set(read_state.get("pinned_keys") or [])
        page["entries"] = [entry for entry in page["entries"] if entry["key"] not in pinned_keys]
    entries = page["entries"] if page else (
        timed_call("portal_conv_entries", _agent_conversation_entries, actor_id, target_ids)
        if target_state == "present" else []
    )
    payload = timed_call("portal_conv_decorate", agent_conversation_index_backend.decorate_entries,
        entries,
        read_state,
    )
    if page:
        payload.update({key: value for key, value in page.items() if key != "entries"})
    if resident_id:
        target_entries = [
            entry
            for entry in entries
            if str(entry.get("resident_id") or "") == resident_id
        ]
        payload["resident_id"] = resident_id
        payload["target_state"] = target_state
        payload["resident_cursor"] = resident_cursor
        payload["projection_cursor"] = max(
            (int(entry.get("cursor") or 0) for entry in target_entries),
            default=0,
        )
        payload["target_present"] = bool(target_entries)
    if not resident_id and not page_token:
        # Use the same viewer receipts and head entries for rows and badges.
        # Older pages contribute only unread markers; never hydrate their states.
        summary = {item["key"]: item for item in timed_call("portal_conv_summary", agent_conversation_index_backend.unread_resident_entries, actor_id, read_state)}
        for item in payload["entries"]:
            summary[item["key"]] = item
        payload["mailbox_summary"] = timed_call("portal_conv_summary", agent_conversation_index_backend.mailbox_summary, list(summary.values()))
        payload["unread_count"] = payload["mailbox_summary"]["unread_count"]
    payload.update({"ready": True, "refreshing": False, "error": ""})
    return payload


def _conversation_item(key: str, state: dict) -> dict:
    """What a browser keeps of a conversation's row: it holds the highest revision."""
    return {"key": key, "unread": state["unread"], "pinned": state["pinned"],
            "read_revision": state["read_revision"], "dismissed_through": state["dismissed_through"]}


def _set_agent_conversation_read_state(
    actor: str,
    key: str,
    *,
    unread: bool,
    expected_revision: object = None,
) -> dict:
    """Mark one conversation read or unread; ``expected_revision`` refuses a change seen out of date."""
    actor_id = str(actor or "").strip()
    key = str(key or "").strip()
    if not actor_id or not key:
        raise PermissionError("需要登录后修改对话状态")
    if len(key) > 512:
        raise ValueError("invalid conversation key")
    source = mailbox_sources.source_of(key)
    if source is not None and source.set_unread is not None:
        # A Chatroom moves its members' read positions itself, in their rows.
        item = source.set_unread(actor_id, key.removeprefix(source.prefix), unread,
                                 expected_revision=expected_revision)
        _invalidate_agent_conversation_cache()
        return {"key": key, "unread": item["unread"], "read_revision": item["read_revision"]}
    state = conversation_read_state.set_read(
        actor_id, key, unread_value=unread, expected_revision=expected_revision,
    )
    _forget_cached_inbox(actor_id)
    _invalidate_agent_conversation_cache()
    _publish_conversation_read_changes(actor_id, {key})
    return _conversation_item(key, state)


def _publish_conversation_read_changes(actor: str, keys: set[str], *, bulk: bool = False) -> None:
    if not keys and not bulk:
        return
    publish_projection_change("agent-conversation-index", "current", actor=actor, fields=["unread"], details={"keys": sorted(keys)})
    if bulk:
        publish_projection_change("resident-view-state", "current", actor=actor, fields=["unread"])
    else:
        for key in keys:
            if re.fullmatch(r"resident:resident_[0-9a-f]{12}", key):
                publish_projection_change("resident-view-state", key.removeprefix("resident:"),
                                                actor=actor, fields=["unread"])


def _clear_agent_conversations(actor: str, *, mode: str, entries: list[dict]) -> dict:
    """Read the listed unread conversations, or read and hide every listed unpinned one.

    The server lists ``entries`` at the request: a hidden conversation returns
    once its activity passes the ``updated_at`` it had then.
    """
    actor_id = str(actor or "").strip()
    clean_mode = str(mode or "").strip().lower()
    if not actor_id:
        raise PermissionError("需要登录后清理对话")
    if clean_mode not in {"unread", "all"}:
        raise ValueError("mode must be unread or all")
    # Chatrooms keep their members' read positions themselves; hiding them
    # until their next message works like any other conversation.
    own_read_state = mailbox_sources.own_read_state_prefixes()
    rooms = [entry for entry in entries if str(entry.get("key") or "").startswith(own_read_state) and entry.get("unread")]
    for entry in rooms:
        source = mailbox_sources.source_of(str(entry["key"]))
        source.set_unread(actor_id, str(entry["key"]).removeprefix(source.prefix), False)
    if clean_mode == "unread":
        changed = conversation_read_state.read_through(actor_id, [
            str(entry["key"]) for entry in entries
            if entry.get("unread") and not str(entry["key"]).startswith(own_read_state)
        ])
        cleared_count = len(changed) + len(rooms)
    else:
        changed = conversation_read_state.dismiss(actor_id, {
            str(entry["key"]): entry.get("updated_at") or "" for entry in entries if not entry.get("pinned")
        })
        cleared_count = len(changed)
    _forget_cached_inbox(actor_id)
    _invalidate_agent_conversation_cache()
    _publish_conversation_read_changes(actor_id, set(changed), bulk=True)
    return {"clear_mode": clean_mode, "cleared_count": cleared_count,
            "items": [_conversation_item(key, state) for key, state in changed.items()]}








def _is_canonical_lineage_favorite(selection_id: object) -> bool:
    value = str(selection_id or "").strip()
    return bool(
        _AGENT_WORKBENCH_LINEAGE_FAVORITE_RE.fullmatch(value)
        or (value.startswith("project:") and 0 < len(value[8:]) <= 256
            and value[8:] == value[8:].strip()
            and not any(ord(char) < 32 for char in value))
    )


def _normalize_lineage_favorites(items: object) -> list[str]:
    if not isinstance(items, list):
        raise ValueError("favorites must be an array")
    normalized: list[str] = []
    represented: set[str] = set()
    for raw_item in items:
        item = str(raw_item or "").strip()
        if not _is_canonical_lineage_favorite(item):
            raise ValueError("favorite address does not match current contract")
        if item in represented:
            continue
        represented.add(item)
        normalized.append(item)
    return normalized


def _lineage_favorite_payload(actor: str) -> dict[str, list[str]]:
    actor_id = str(actor or "").strip()
    return {"favorites": _normalize_lineage_favorites(presentation_store.favorite_ids(actor_id, "lineage")) if actor_id else []}


def _set_lineage_favorite(actor: str, selection_id: str, favorite: bool) -> dict[str, list[str]]:
    actor_id = str(actor or "").strip()
    item_id = str(selection_id or "").strip()
    if not actor_id:
        raise PermissionError("需要登录后操作")
    if not _is_canonical_lineage_favorite(item_id):
        raise ValueError("selection_id must be a canonical lineage or project address")
    return {"favorites": presentation_store.set_favorite(actor_id, "lineage", item_id, favorite)}


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


def _restart_notice_now() -> float:
    return time.time()


def _read_restart_notice_raw() -> dict:
    try:
        payload = json.loads(_RESTART_NOTICE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_restart_notice(payload: dict) -> None:
    atomic_replace_json(_RESTART_NOTICE_PATH, payload, indent=2, sort_keys=False)


def _restart_notice_public_payload() -> dict:
    now = _restart_notice_now()
    with _RESTART_NOTICE_LOCK:
        payload = _read_restart_notice_raw()
    restart_at = float(payload.get("restart_at") or 0)
    expires_at = float(payload.get("expires_at") or 0)
    active = bool(
        payload.get("notice_id")
        and restart_at
        and restart_at > _SERVER_STARTED_AT
        and now < restart_at
        and now <= expires_at
    )
    if not active:
        return {"active": False, "now": now, "server_started_at": _SERVER_STARTED_AT}
    return {
        "active": True,
        "notice_id": str(payload.get("notice_id") or ""),
        "message": str(payload.get("message") or "系统将在倒计时结束后重启。"),
        "start_at": float(payload.get("start_at") or now),
        "restart_at": restart_at,
        "expires_at": expires_at,
        "restart_requested": bool(payload.get("restart_requested")),
        "scheduled_by": str(payload.get("scheduled_by") or ""),
        "now": now,
        "server_started_at": _SERVER_STARTED_AT,
    }


def _portal_health_payload(handler: SimpleHTTPRequestHandler | None = None) -> dict:
    port = 0
    if handler is not None:
        try:
            port = int(handler.server.server_address[1])
        except Exception:
            port = 0
    payload = {
        "ok": True,
        "instance": _PORTAL_INSTANCE,
        "build_id": _PORTAL_BUILD_ID,
        "built_at": str(_RELEASE_MANIFEST.get("built_at") or ""),
        "source_commit": str(_RELEASE_MANIFEST.get("source_commit") or ""),
        "port": port,
        "started_at": _SERVER_STARTED_AT,
        "now": time.time(),
    }
    try:
        from management_portal.execution.service import ExecutionService

        payload["execution_layer"] = ExecutionService().health()
    except Exception as exc:
        payload["execution_layer"] = {"ok": False, "error": str(exc)}
    try:
        payload["society_service"] = _SOCIETY_SERVICE.health()
    except Exception as exc:
        payload["society_service"] = {
            "ok": False,
            "error": str(exc),
        }
    return payload


def _society_service_status_payload() -> dict:
    lifecycle = read_component_state(_PORTAL_INSTANCE, "lineage")
    try:
        if isinstance(_SOCIETY_SERVICE, SocietyServiceClient):
            health = _SOCIETY_SERVICE.health(timeout=2.25)
        else:
            health = _SOCIETY_SERVICE.health()
    except Exception as exc:
        announced_restart = lifecycle.get("state") == "restarting"
        busy = isinstance(exc, SocietyServiceBusy) and not announced_restart
        return {
            "schema": "tfi.portal-component-status.v1",
            "component": "lineage",
            "service": "SocietyService",
            "state": (
                "restarting" if announced_restart else "busy" if busy else "unavailable"
            ),
            "ready": False,
            "message": (
                "SocietyService 正在重启"
                if announced_restart
                else "SocietyService 正忙，正在等待当前操作完成"
                if busy
                else "SocietyService 暂时不可用，正在重新连接"
            ),
            "retry_after_ms": 750,
            "operation_id": str(lifecycle.get("operation_id") or ""),
            "updated_at": float(lifecycle.get("updated_at") or 0),
            "detail": str(exc),
        }
    return {
        "schema": "tfi.portal-component-status.v1",
        "component": "lineage",
        "service": "SocietyService",
        "state": "ready",
        "ready": True,
        "message": "SocietyService 已就绪",
        "retry_after_ms": 0,
        "operation_id": str(lifecycle.get("operation_id") or ""),
        "updated_at": float(lifecycle.get("updated_at") or 0),
        "health": health,
    }


def _resource_service_status_payload() -> dict:
    lifecycle = read_component_state(_PORTAL_INSTANCE, "resource")
    try:
        health = get_resource_service().status(timeout=2.25)
    except Exception as exc:
        announced_restart = lifecycle.get("state") == "restarting"
        return {
            "schema": "tfi.portal-component-status.v1",
            "component": "resource",
            "service": "ResourceService",
            "state": "restarting" if announced_restart else "unavailable",
            "ready": False,
            "message": (
                "ResourceService 正在重启"
                if announced_restart
                else "ResourceService 暂时不可用，正在重新连接"
            ),
            "retry_after_ms": 750,
            "operation_id": str(lifecycle.get("operation_id") or ""),
            "updated_at": float(lifecycle.get("updated_at") or 0),
            "detail": str(exc),
        }
    return {
        "schema": "tfi.portal-component-status.v1",
        "component": "resource",
        "service": "ResourceService",
        "state": "ready",
        "ready": True,
        "message": "ResourceService 已就绪",
        "retry_after_ms": 0,
        "operation_id": str(lifecycle.get("operation_id") or ""),
        "updated_at": float(lifecycle.get("updated_at") or 0),
        "health": health,
    }


def _serve_society_service_unavailable(
    handler: SimpleHTTPRequestHandler,
    error: SocietyServiceUnavailable | None = None,
) -> None:
    failure = error if error is not None else SocietyServiceUnavailable("SocietyService 暂时不可用")
    return _serve_json(
        handler,
        failure.http_status,
        _hatg_instruction_error_payload(failure),
    )


def _serve_resource_error(handler: SimpleHTTPRequestHandler, error: Exception) -> None:
    status, payload = resource_error_http_response(error)
    return _serve_json(handler, status, payload)


def _runtime_config_payload(handler: SimpleHTTPRequestHandler) -> dict:
    payload = get_runtime_config()
    portal = payload.get("portal") if isinstance(payload.get("portal"), dict) else {}
    portal.update({
        "port": int(handler.server.server_address[1]),
        "instance": _PORTAL_INSTANCE,
        "build_id": _PORTAL_BUILD_ID,
    })
    payload["portal"] = portal
    return payload


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


def _notification_target(subject: str) -> tuple[str, str]:
    parts = str(subject or "").split(".")
    if len(parts) < 4 or parts[0] != "portal":
        return "", ""
    domain = parts[1]
    if domain in {"execution", "resident", "lineage"}:
        return domain, parts[3]
    if domain == "projection" and len(parts) >= 5:
        return domain, parts[4]
    if domain == "resource" and len(parts) >= 5:
        return domain, parts[4]
    return domain, ""


def _portal_change_payload(
    actor: str,
    notifications: list,
    domains: set[str],
) -> dict:
    snapshot_required = False
    candidates: dict[str, set[str]] = {
        "execution": set(),
        "resident": set(),
        "lineage": set(),
        "projection": set(),
    }
    projection_candidates: dict[str, set[str]] = {}
    projection_fields: dict[str, dict[str, set[str]]] = {}
    projection_details: dict[str, dict[str, dict[str, object]]] = {}
    resource_projection_candidates: dict[str, set[str]] = {}
    resource_projection_snapshots: set[str] = set()
    resident_events: list[dict[str, object]] = []
    resident_changes: dict[str, set[str]] = {}
    changed_domains: set[str] = set()
    for notification in notifications:
        subject = str(notification.subject or "")
        if subject == "portal.gateway.reconnected":
            snapshot_required = True
            continue
        domain, target_id = _notification_target(subject)
        if domain not in domains:
            continue
        if domain == "execution":
            event_actor = str(notification.payload.get("actor") or "")
            if not event_actor or event_actor.casefold() != actor.casefold():
                continue
            if target_id:
                candidates[domain].add(target_id)
            changed_domains.add(domain)
            continue
        if domain == "projection":
            event_actor = str(notification.payload.get("actor") or "")
            event_actors = {
                str(item).strip().casefold()
                for item in (
                    notification.payload.get("actors")
                    if isinstance(notification.payload.get("actors"), list)
                    else []
                )
                if str(item).strip()
            }
            if event_actors and actor.casefold() not in event_actors:
                continue
            if not event_actors and event_actor and event_actor.casefold() != actor.casefold():
                continue
            parts = subject.split(".")
            projection = parts[3] if len(parts) >= 5 else ""
            if projection == "active-agents" and target_id == "snapshot":
                _invalidate_active_agents_cache(actor)
            if target_id:
                candidates[domain].add(target_id)
                if projection:
                    projection_candidates.setdefault(projection, set()).add(
                        target_id
                    )
                    fields = notification.payload.get("fields")
                    if isinstance(fields, list):
                        target_fields = projection_fields.setdefault(
                            projection,
                            {},
                        ).setdefault(target_id, set())
                        target_fields.update(
                            str(field).strip()
                            for field in fields
                            if str(field).strip()
                        )
                    details = notification.payload.get("details")
                    if isinstance(details, dict) and details:
                        target_details = projection_details.setdefault(
                            projection,
                            {},
                        ).setdefault(target_id, {})
                        target_details.update(details)
            changed_domains.add(domain)
            continue
        if domain in {"resident", "lineage"}:
            if target_id:
                candidates[domain].add(target_id)
            if domain == "resident":
                parts = subject.split(".")
                if target_id and len(parts) == 5:
                    resident_changes.setdefault(target_id, set()).add(parts[4])
                event = notification.payload.get("event")
                if (
                    notification.payload.get("kind") == "conversation_event"
                    and isinstance(event, dict)
                    and str(event.get("resident_id") or "") == target_id
                ):
                    resident_events.append(dict(event))
            continue
        if domain == "resource":
            parts = subject.split(".")
            if len(parts) >= 5 and parts[3] == "projection":
                adapter = parts[4]
                resource_ids = {
                    str(item or "").strip()
                    for item in notification.payload.get("resource_ids") or []
                    if str(item or "").strip()
                }
                if notification.payload.get("snapshot_required") or not resource_ids:
                    resource_projection_snapshots.add(adapter)
                else:
                    resource_projection_candidates.setdefault(adapter, set()).update(
                        resource_ids
                    )
                continue
            changed_domains.add(domain)
            if len(parts) >= 5 and parts[3] in {"resident", "lineage"}:
                candidates[parts[3]].add(parts[4])

    collaboration_ids = sorted(candidates["resident"] | candidates["lineage"])
    if collaboration_ids:
        try:
            from management_portal.permission_system import readable_ids
            for domain, instruction in (("resident", "GetResident"), ("lineage", "GetLineage")):
                candidates[domain] = readable_ids(instruction, domain + "_id", candidates[domain], actor=actor)
        except Exception:
            candidates["resident"] = set()
            candidates["lineage"] = set()
            snapshot_required = True
        for domain in ("resident", "lineage"):
            if candidates[domain] and domain in domains:
                changed_domains.add(domain)
        # Deletion or a permission change can remove an identity from the
        # authorization results above. Existing Editors still need to revalidate their own
        # targets; disclose no removed/private identity to other subscribers.
        if "resident" in domains and any(
            "identity" in scopes and resident_id not in candidates["resident"]
            for resident_id, scopes in resident_changes.items()
        ):
            snapshot_required = True

    try:
        from management_portal.permission_system import readable_ids
        conversation_ids = readable_ids("GetConversation", "resident_id",
                                        [event.get("resident_id") for event in resident_events], actor=actor)
    except Exception:
        conversation_ids = set()
        snapshot_required = True
    visible_resident_events = [
        event for event in resident_events
        if str(event.get("resident_id") or "") in candidates["resident"]
        and str(event.get("resident_id") or "") in conversation_ids
        and "resident" in domains
    ]

    resource_projection_changes: dict[str, list[str]] = {}
    resource_projection_ids = sorted({
        resource_id
        for resource_ids in resource_projection_candidates.values()
        for resource_id in resource_ids
    })
    if resource_projection_ids:
        try:
            from management_portal.permission_system import readable_ids
            readable = readable_ids("ResolveResource", "address", resource_projection_ids, actor=actor)
        except Exception:
            readable = set()
            resource_projection_snapshots.update(resource_projection_candidates)
        for adapter, resource_ids in resource_projection_candidates.items():
            visible_ids = sorted(resource_ids & readable)
            if visible_ids:
                resource_projection_changes[adapter] = visible_ids
    if resource_projection_changes or resource_projection_snapshots:
        changed_domains.add("resource")

    if "agent-conversation-index" in projection_candidates or snapshot_required:
        _forget_cached_inbox(actor)

    changed = {
        domain: sorted(resource_ids)
        for domain, resource_ids in candidates.items()
        if resource_ids and domain in domains
    }
    return {
        "domains": sorted(changed_domains),
        "changed": changed,
        "projections": {
            projection: sorted(target_ids)
            for projection, target_ids in sorted(projection_candidates.items())
            if target_ids
        },
        "projection_changes": {
            projection: {
                target_id: sorted(
                    projection_fields.get(projection, {}).get(target_id, set())
                )
                for target_id in sorted(target_ids)
            }
            for projection, target_ids in sorted(projection_candidates.items())
            if target_ids
        },
        "projection_details": {
            projection: {
                target_id: dict(details)
                for target_id, details in sorted(target_details.items())
                if details
            }
            for projection, target_details in sorted(projection_details.items())
            if target_details
        },
        "resource_projection_changes": {
            adapter: resource_ids
            for adapter, resource_ids in sorted(
                resource_projection_changes.items()
            )
        },
        "resource_projection_snapshots": sorted(resource_projection_snapshots),
        "resident_events": visible_resident_events,
        "resident_changes": {
            resident_id: sorted(scopes)
            for resident_id, scopes in sorted(resident_changes.items())
            if resident_id in candidates["resident"] and "resident" in domains
        },
        "snapshot_required": snapshot_required,
    }


def _publish_restart_notice(
    payload: dict,
    *,
    scheduled_by: str,
    restart_requested: bool,
) -> dict:
    seconds = int(payload.get("seconds") or 5)
    seconds = max(3, min(seconds, 300))
    now = _restart_notice_now()
    message = str(payload.get("message") or "系统将在倒计时结束后重启。").strip()[:240]
    notice = {
        "notice_id": secrets.token_hex(8),
        "message": message,
        "start_at": now,
        "restart_at": now + seconds,
        "expires_at": now + seconds + 20,
        "restart_requested": bool(restart_requested),
        "scheduled_by": str(scheduled_by or "portal"),
        "created_at": int(now),
    }
    with _RESTART_NOTICE_LOCK:
        _write_restart_notice(notice)
    public_notice = _restart_notice_public_payload()
    publish_local_projection_change(
        "restart-notice",
        "current",
        fields=["notice"],
        details={"notice": public_notice},
    )
    return public_notice


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
        username = _USER_ENROLLMENT.authenticate(token)
        return {"type": "user", "id": username} if username and _portal_user_allowed(username) else None
    agent = agent_token_auth.authenticate_bearer_token(token, scope=scope)
    if agent:
        from management_common.principal import actor_principal

        caller = str(agent["agent_id"])
        if actor_principal(caller).kind == "user" and not _portal_user_allowed(caller):
            return None
        return {
            "type": "agent",
            "id": caller,
            "execution_id": runtime_identity_header(handler.headers, "X-Portal-Execution-ID"),
            "lineage_id": runtime_identity_header(handler.headers, "X-Portal-Lineage-ID"),
        }
    return None


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


def _session_cookie_value(token: str, *, secure: bool = False) -> str:
    suffix = "; Secure" if secure else ""
    return f"{_SESSION_COOKIE}={token}; HttpOnly; Path=/; Max-Age={_SESSION_MAX_AGE}; SameSite=Lax{suffix}"


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


def _login_challenge_cookie_value(token: str, *, secure: bool = False) -> str:
    suffix = "; Secure" if secure else ""
    return (
        f"{_LOGIN_CHALLENGE_COOKIE}={token}; HttpOnly; Path=/login; "
        f"Max-Age={_LOGIN_CHALLENGE_MAX_AGE}; SameSite=Strict{suffix}"
    )


def _set_login_challenge_cookie(handler: SimpleHTTPRequestHandler, token: str) -> None:
    handler.send_header(
        "Set-Cookie",
        _login_challenge_cookie_value(token, secure=_request_is_https(handler)),
    )


def _clear_login_challenge_cookie(handler: SimpleHTTPRequestHandler) -> None:
    secure = "; Secure" if _request_is_https(handler) else ""
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
        factor = _verify_second_factor(username, code)
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
        if not _AUTH_ATTEMPTS.allow("factor", username):
            raise PermissionError("Too many verification attempts; try again later.")
        counter = match_totp(secret, code)
        if counter is None:
            raise PermissionError("The verification code is invalid.")
        _TWO_FACTOR_SETUPS.pop(clean_token, None)
    return _PORTAL_TWO_FACTOR.enable(username, secret, last_counter=counter)


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


def _read_bounded_request_body(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int,
    media_type: str,
) -> bytes:
    transfer_encoding = str(handler.headers.get("Transfer-Encoding") or "").strip()
    if transfer_encoding and transfer_encoding.casefold() != "identity":
        raise InvalidRequestBody("chunked request bodies are not supported")
    content_type = str(handler.headers.get("Content-Type") or "")
    actual_media_type = content_type.partition(";")[0].strip().casefold()
    if actual_media_type != media_type:
        raise InvalidRequestBody(f"Content-Type must be {media_type}")
    raw_length = str(handler.headers.get("Content-Length") or "").strip()
    try:
        length = int(raw_length)
    except (TypeError, ValueError) as exc:
        raise InvalidRequestBody("invalid Content-Length") from exc
    if length < 0:
        raise InvalidRequestBody("invalid Content-Length")
    if length > max_bytes:
        raise RequestBodyTooLarge("request body is too large")
    raw = handler.rfile.read(length)
    if len(raw) != length:
        raise InvalidRequestBody("incomplete request body")
    if not raw:
        raise InvalidRequestBody("request body must not be empty")
    return raw


def _read_request_json(handler: SimpleHTTPRequestHandler) -> dict:
    return _read_bounded_json_object(handler)


def _read_bounded_form_object(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int = 16_384,
) -> dict[str, str]:
    raw = _read_bounded_request_body(
        handler,
        max_bytes=max_bytes,
        media_type="application/x-www-form-urlencoded",
    )
    try:
        text = raw.decode("utf-8")
        return dict(
            parse_qsl(
                text,
                keep_blank_values=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=16,
            )
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise InvalidRequestBody("request body must be valid form data") from exc


def _read_bounded_json_object(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int = _JSON_REQUEST_MAX_BYTES,
) -> dict:
    raw = _read_bounded_request_body(
        handler,
        max_bytes=max_bytes,
        media_type="application/json",
    )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestBody("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestBody("request JSON must be an object")
    return payload


def _read_request_multipart(
    handler: SimpleHTTPRequestHandler,
    *,
    max_bytes: int,
) -> dict:
    content_type = str(handler.headers.get("Content-Type") or "")
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid Content-Length") from exc
    if not content_type.casefold().startswith("multipart/form-data"):
        return {}
    if length < 0:
        raise ValueError("invalid Content-Length")
    effective_max_bytes = min(int(max_bytes), _MULTIPART_HARD_LIMIT_BYTES)
    if effective_max_bytes < 0:
        raise ValueError("invalid multipart request size limit")
    if length > effective_max_bytes:
        raise RequestBodyTooLarge("multipart request body is too large")
    body = handler.rfile.read(length)
    if len(body) != length:
        raise ValueError("incomplete multipart request body")
    message = BytesParser(policy=email_policy).parsebytes(
        b"Content-Type: "
        + content_type.encode("latin-1")
        + b"\r\nMIME-Version: 1.0\r\n\r\n"
        + body
    )
    if not message.is_multipart():
        raise ValueError("invalid multipart request body")
    payload: dict[str, object] = {}
    for field in message.iter_parts():
        if field.get_content_disposition() != "form-data":
            continue
        key = str(field.get_param("name", header="content-disposition") or "")
        if not key:
            continue
        filename = str(field.get_filename() or "")
        data = field.get_payload(decode=True) or b""
        if filename or key == "file":
            payload["filename"] = filename or str(payload.get("filename") or "attachment")
            payload["content_type"] = str(
                field.get_content_type()
                or payload.get("content_type")
                or "application/octet-stream"
            )
            payload["data_bytes"] = data
            continue
        charset = field.get_content_charset() or "utf-8"
        payload[key] = data.decode(charset, errors="replace")
    return payload


_RESPONSE_COMPRESSION_MIN_BYTES = 1024
_STATIC_CACHE_CONTROL = "public, max-age=0, must-revalidate"
_STATIC_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
_STATIC_PAYLOAD_CACHE_LIMIT = 512
_STATIC_PAYLOAD_CACHE_LOCK = threading.RLock()
_STATIC_PAYLOAD_CACHE: OrderedDict[
    tuple[str, int, int, bool], tuple[bytes, str]
] = OrderedDict()
_STATIC_PAYLOAD_BUILD_LOCKS: dict[
    tuple[str, int, int, bool], threading.Lock
] = {}
_SENSITIVE_RESPONSE_CACHE_CONTROL = "no-store"
_AUTHENTICATED_DOCUMENT_CACHE_CONTROL = "private, no-cache"
_STATIC_HTML_REFERENCE_RE = re.compile(
    rb'((?:src|href)=["\'])(/static/[^"\']+)(["\'])'
)


def _version_static_asset_urls(data: bytes) -> bytes:
    version = quote(str(_PORTAL_BUILD_ID or "").strip(), safe="").encode("ascii")
    if not version:
        return data

    def replace(match: re.Match[bytes]) -> bytes:
        url = match.group(2)
        separator = b"&" if b"?" in url else b"?"
        return match.group(1) + url + separator + b"portal_v=" + version + match.group(3)

    return _STATIC_HTML_REFERENCE_RE.sub(replace, data)


def _static_cache_control(handler: SimpleHTTPRequestHandler) -> str:
    requested = str(
        (parse_qs(urlparse(handler.path).query).get("portal_v") or [""])[0]
    ).strip()
    # Dev serves a mutable working tree even while its Web build ID is unchanged.
    if (
        _PORTAL_INSTANCE == "release"
        and requested
        and requested == str(_PORTAL_BUILD_ID or "").strip()
    ):
        return _STATIC_IMMUTABLE_CACHE_CONTROL
    return _STATIC_CACHE_CONTROL


def _response_body_allowed(handler: SimpleHTTPRequestHandler) -> bool:
    return str(getattr(handler, "command", "") or "").upper() != "HEAD"


def _compressible_content_type(content_type: str) -> bool:
    mime = str(content_type or "").partition(";")[0].strip().casefold()
    return (
        mime.startswith("text/")
        or mime in {
            "application/javascript",
            "application/json",
            "application/manifest+json",
            "application/wasm",
            "application/xhtml+xml",
            "application/xml",
            "image/svg+xml",
        }
        or mime.endswith("+json")
        or mime.endswith("+xml")
    )


def _accepts_gzip(handler: SimpleHTTPRequestHandler) -> bool:
    accepted = str(handler.headers.get("Accept-Encoding") or "").casefold()
    for token in accepted.split(","):
        encoding, *parameters = token.split(";")
        if encoding.strip() != "gzip":
            continue
        quality = 1.0
        for parameter in parameters:
            key, separator, value = parameter.strip().partition("=")
            if separator and key == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        return quality > 0
    return False


def _encode_response_payload(
    handler: SimpleHTTPRequestHandler,
    data: bytes,
    content_type: str,
) -> tuple[bytes, str]:
    if (
        len(data) >= _RESPONSE_COMPRESSION_MIN_BYTES
        and _compressible_content_type(content_type)
        and _accepts_gzip(handler)
    ):
        return gzip.compress(data, compresslevel=4, mtime=0), "gzip"
    return data, ""


def _serve_payload(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    content_type: str,
    *,
    cache_control: str = _SENSITIVE_RESPONSE_CACHE_CONTROL,
    etag: str = "",
    extra_headers: dict[str, str] | None = None,
) -> None:
    if any(str(name).casefold() == "cache-control" for name in (extra_headers or {})):
        raise ValueError("Cache-Control must be selected through the response policy")
    encoded, content_encoding = _encode_response_payload(handler, data, content_type)
    return _serve_encoded_payload(
        handler,
        status,
        encoded,
        content_type,
        cache_control=cache_control,
        content_encoding=content_encoding,
        etag=etag,
        extra_headers=extra_headers,
    )


def _serve_encoded_payload(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    content_type: str,
    *,
    cache_control: str,
    content_encoding: str = "",
    etag: str = "",
    extra_headers: dict[str, str] | None = None,
) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", cache_control)
        if _compressible_content_type(content_type):
            handler.send_header("Vary", "Accept-Encoding")
        if content_encoding:
            handler.send_header("Content-Encoding", content_encoding)
        if etag:
            handler.send_header("ETag", etag)
        for name, value in (extra_headers or {}).items():
            handler.send_header(str(name), str(value))
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        if _response_body_allowed(handler):
            handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        return


def _cached_static_payload(
    path: Path,
    *,
    mtime_ns: int,
    size: int,
    gzip_variant: bool,
) -> tuple[bytes, str]:
    """Read and encode one immutable file version once across HTTP workers."""

    key = (str(path), int(mtime_ns), int(size), bool(gzip_variant))
    with _STATIC_PAYLOAD_CACHE_LOCK:
        cached = _STATIC_PAYLOAD_CACHE.get(key)
        if cached is not None:
            _STATIC_PAYLOAD_CACHE.move_to_end(key)
            return cached
        build_lock = _STATIC_PAYLOAD_BUILD_LOCKS.setdefault(key, threading.Lock())

    with build_lock:
        try:
            with _STATIC_PAYLOAD_CACHE_LOCK:
                cached = _STATIC_PAYLOAD_CACHE.get(key)
                if cached is not None:
                    _STATIC_PAYLOAD_CACHE.move_to_end(key)
                    return cached
            data = path.read_bytes()
            payload = (
                gzip.compress(data, compresslevel=4, mtime=0),
                "gzip",
            ) if gzip_variant else (data, "")
            with _STATIC_PAYLOAD_CACHE_LOCK:
                _STATIC_PAYLOAD_CACHE[key] = payload
                _STATIC_PAYLOAD_CACHE.move_to_end(key)
                while len(_STATIC_PAYLOAD_CACHE) > _STATIC_PAYLOAD_CACHE_LIMIT:
                    _STATIC_PAYLOAD_CACHE.popitem(last=False)
            return payload
        finally:
            with _STATIC_PAYLOAD_CACHE_LOCK:
                if _STATIC_PAYLOAD_BUILD_LOCKS.get(key) is build_lock:
                    _STATIC_PAYLOAD_BUILD_LOCKS.pop(key, None)


def _static_fallback_file(path: str) -> Path | None:
    """A static file outside the public route list, served with the same caching.

    The plain file handler sends only Last-Modified.  A browser then caches the
    file by heuristics (a tenth of its age, ~17 hours for a week-old file) and
    keeps running a stale copy after it changes, e.g. an old
    portal_user_avatar.js without the functions newer pages call.
    """
    if not path.startswith("/static/"):
        return None
    static_root = ROOT / "static"
    candidate = (static_root / unquote(path.removeprefix("/static/"))).resolve()
    if static_root not in candidate.parents or not candidate.is_file():
        return None
    return candidate


def _serve_static_file(
    handler: SimpleHTTPRequestHandler,
    path: Path,
    content_type: str,
) -> None:
    try:
        stat = path.stat()
    except OSError:
        return _send_empty(handler, 404)
    gzip_variant = (
        stat.st_size >= _RESPONSE_COMPRESSION_MIN_BYTES
        and _compressible_content_type(content_type)
        and _accepts_gzip(handler)
    )
    variant = "-gzip" if gzip_variant else ""
    etag = f'"{stat.st_mtime_ns:x}-{stat.st_size:x}{variant}"'
    cache_control = _static_cache_control(handler)
    if etag in {
        token.strip()
        for token in str(handler.headers.get("If-None-Match") or "").split(",")
    }:
        handler.send_response(304)
        handler.send_header("Cache-Control", cache_control)
        handler.send_header("ETag", etag)
        if _compressible_content_type(content_type):
            handler.send_header("Vary", "Accept-Encoding")
        handler.end_headers()
        return
    try:
        data, content_encoding = _cached_static_payload(
            path,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            gzip_variant=gzip_variant,
        )
    except OSError:
        return _send_empty(handler, 404)
    return _serve_encoded_payload(
        handler,
        200,
        data,
        content_type,
        cache_control=cache_control,
        content_encoding=content_encoding,
        etag=etag,
    )


def _serve_json(
    handler: SimpleHTTPRequestHandler,
    status: int,
    payload: dict | list,
    *,
    extra_headers: dict[str, str] | None = None,
):
    data = json.dumps(normalize_json_error(status, payload), ensure_ascii=False).encode("utf-8")
    return _serve_payload(
        handler,
        status,
        data,
        "application/json; charset=utf-8",
        extra_headers=extra_headers,
    )


def _serve_exception_json(handler: SimpleHTTPRequestHandler, error: Exception):
    status, payload = exception_http_error(error)
    return _serve_json(handler, status, payload)


def _is_json_api_path(path: str) -> bool:
    return path.startswith("/api/") or path.endswith("/api") or "/api/" in path


def _serve_http_error(handler: SimpleHTTPRequestHandler, status: int, message: str):
    return _serve_json(handler, status, {"error": message})


def _serve_unavailable_json(handler: SimpleHTTPRequestHandler):
    return _serve_exception_json(
        handler,
        OperationError(
            "Portal service temporarily unavailable",
            code="operation.unavailable",
            http_status=503,
        ),
    )


def _serve_link_file_permission_denied(handler: SimpleHTTPRequestHandler):
    return _serve_json(handler, 403, {"error": "file access denied"})


def _serve_agent_workbench_error(
    handler: SimpleHTTPRequestHandler,
    error: Exception,
    *,
    public_error: str | None = None,
):
    if isinstance(error, ResidentDiscoveryUnavailable):
        error = OperationError(str(error), code="operation.unavailable", http_status=503)
    status, payload = exception_http_error(error)
    if public_error is not None:
        payload.update(error=public_error, message=public_error, details={})
    return _serve_json(handler, status, payload)


def _notify_resource_index(
    *,
    path: str = "",
    resource_id: str = "",
    resource_type: str = "",
    kind: str = "unknown",
    wait: bool = False,
    timeout: float = 60.0,
) -> dict[str, object]:
    try:
        return get_resource_service().notify_source_changed(
            path=path,
            resource_id=resource_id,
            resource_type=resource_type,
            kind=kind,
            wait=wait,
            timeout=timeout,
        )
    except Exception as exc:
        # Source writes remain authoritative even when an optional index hint
        # cannot be queued. Low-frequency reconciliation will recover it.
        print(
            json.dumps(
                {
                    "event": "resource_change_notification_failed",
                    "path": path,
                    "resource_id": resource_id,
                    "resource_type": resource_type,
                    "kind": kind,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
            flush=True,
        )
        return {"accepted": False, "error": str(exc)}


def _inject_portal_identity_bootstrap(data: bytes, username: str) -> bytes:
    marker = b'<script src="/static/portal_identity.js"></script>'
    clean_username = str(username or "").strip()
    if not clean_username or marker not in data:
        return data
    identity_json = json.dumps(
        {"username": clean_username},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    identity_json = (
        identity_json.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    bootstrap = (
        '<script>window.__PORTAL_IDENTITY_BOOTSTRAP__='
        f'Object.freeze({identity_json});</script>\n    '
    ).encode("utf-8")
    return data.replace(marker, bootstrap + marker, 1)


def _serve_bytes(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    content_type: str,
    *,
    cache_control: str = _SENSITIVE_RESPONSE_CACHE_CONTROL,
    extra_headers: dict[str, str] | None = None,
):
    if content_type.lower().startswith("text/html"):
        from management_portal.localization import normalize_language
        username = _get_session_username(handler) or ""
        if username:
            language = normalize_language(_portal_user_preferences(username).get("appearance", {}).get("language"))
            bootstrap = ("<script>window.__PORTAL_LANGUAGE__=" + json.dumps(language) + ";</script>").encode("utf-8")
            data = data.replace(b"<head>", b"<head>" + bootstrap, 1)
        data = _inject_portal_identity_bootstrap(
            data,
            _get_session_username(handler) or "",
        )
        data = _version_static_asset_urls(data)
        from management_portal import frontend_manifest
        from management_portal.page_bootstrap import inject_page_bootstrap
        data = inject_page_bootstrap(frontend_manifest.inject(data))
    return _serve_payload(
        handler,
        status,
        data,
        content_type,
        cache_control=cache_control,
        extra_headers=extra_headers,
    )


def _serve_authenticated_document(
    handler: SimpleHTTPRequestHandler,
    status: int,
    data: bytes,
    *,
    extra_headers: dict[str, str] | None = None,
) -> None:
    """Serve a private Portal shell that must be revalidated before reuse."""
    return _serve_bytes(
        handler,
        status,
        data,
        "text/html; charset=utf-8",
        cache_control=_AUTHENTICATED_DOCUMENT_CACHE_CONTROL,
        extra_headers=extra_headers,
    )


def _society_service_resolved_path(value: object, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError(f"SocietyService returned an invalid {label}")
    raw_value = os.fspath(value)
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValueError(f"SocietyService returned an empty {label}")
    return Path(raw_value).expanduser().resolve(strict=False)


def _serve_media_file(handler: SimpleHTTPRequestHandler, path: Path, content_type: str) -> None:
    try:
        stat = path.stat()
    except OSError:
        return _send_empty(handler, 404)
    response = plan_media_response(handler, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    response.send_headers(handler, content_type)
    if response.status not in {200, 206} or not _response_body_allowed(handler):
        return
    try:
        with path.open("rb") as f:
            f.seek(response.start)
            remaining = response.length
            while remaining > 0:
                chunk = f.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return


def _serve_authorized_resource_file(
    handler: SimpleHTTPRequestHandler,
    *,
    resource_service: object,
    address: str,
    actor: str,
    resolved: dict[str, object],
    content_type: str,
    download: bool,
) -> None:
    size = resolved.get("content_size")
    mtime_ns = resolved.get("content_mtime_ns")
    if type(size) is not int or size < 0 or type(mtime_ns) is not int or mtime_ns < 0:
        raise ValueError("resource provider did not return stable content metadata")
    response = plan_media_response(handler, size=size, mtime_ns=mtime_ns, download=download)
    # Files written by Residents never run in the Portal origin: only
    # browser-safe media display inline, everything else downloads.
    safe_headers = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"}
    inline = content_type == "application/pdf" or content_type.startswith(("image/", "audio/", "video/"))
    if response.status in {304, 416}:
        response.send_headers(handler, content_type, extra_headers=safe_headers)
        return
    chunk_size = 1024 * 1024

    def read_chunk(chunk_start: int, chunk_end: int) -> bytes:
        data, _provider_type, current = resource_service.read_content_range(
            address,
            actor=actor,
            start=chunk_start,
            end=chunk_end,
        )
        current = current if isinstance(current, dict) else {}
        if (
            current.get("content_size") != size
            or current.get("content_mtime_ns") != mtime_ns
        ):
            raise RuntimeError("resource changed while its content was being read")
        expected = chunk_end - chunk_start + 1
        if len(data) != expected:
            raise RuntimeError("resource provider returned an incomplete content range")
        return data

    first_end = min(response.end, response.start + chunk_size - 1)
    first_chunk = read_chunk(response.start, first_end) if response.length else b""
    response.send_headers(
        handler,
        content_type,
        content_disposition=(
            _download_content_disposition(resolved.get("filename")) if download or not inline else ""
        ),
        extra_headers=safe_headers,
    )
    if not _response_body_allowed(handler):
        return
    try:
        if first_chunk:
            handler.wfile.write(first_chunk)
        offset = first_end + 1
        while offset <= response.end:
            chunk_end = min(response.end, offset + chunk_size - 1)
            handler.wfile.write(read_chunk(offset, chunk_end))
            offset = chunk_end + 1
    except (BrokenPipeError, ConnectionResetError):
        return


def _download_content_disposition(filename: object) -> str:
    filename = str(filename or "download").replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        "_" if ord(char) < 32 or ord(char) == 127 else char
        for char in filename
    ) or "download"
    ascii_filename = "".join(
        char
        if char.isascii() and (char.isalnum() or char in {".", "_", "-"})
        else "_"
        for char in filename
    ) or "download"
    return (
        f"attachment; filename=\"{ascii_filename}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )


def _serve_download_file(handler: SimpleHTTPRequestHandler, path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return _send_empty(handler, 404)
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    try:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Content-Length", str(size))
        handler.send_header(
            "Content-Disposition",
            _download_content_disposition(path.name),
        )
        handler.end_headers()
        if not _response_body_allowed(handler):
            return
        with path.open("rb") as f:
            while True:
                chunk = f.read(1024 * 256)
                if not chunk:
                    break
                handler.wfile.write(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return


def _media_content_type(path: Path) -> str:
    content_type = mimetypes.guess_type(str(path))[0] or ""
    if content_type == "application/pdf" or content_type.startswith(("audio/", "image/", "video/")):
        return content_type
    return ""


def _send_empty(handler: SimpleHTTPRequestHandler, status: int):
    try:
        handler.send_response(status)
        handler.end_headers()
    except (BrokenPipeError, ConnectionResetError):
        return


def _resource_portal_location(address: object, actor: object = "") -> str:
    clean_address = unquote(str(address or "")).strip().strip("/")
    if not clean_address:
        return ""
    fallback = (
        ResourceService.portal_url_for_address(clean_address)
        if "/" not in clean_address else ""
    )
    try:
        reference = get_resource_service().reference(
            clean_address,
            actor=str(actor or ""),
        )
    except (KeyError, FileNotFoundError, PermissionError, RuntimeError, ValueError):
        return fallback
    return canonical_portal_location(clean_address, reference.get("url")) or fallback


def _resource_reference_payload(address: str, actor: str) -> dict:
    reference = dict(get_resource_service().reference(address, actor=actor))
    if str(reference.get("resource_type") or "").strip() != "resident":
        return reference
    resident_id = str(reference.get("resource_id") or "").strip()
    if not resident_id:
        return reference
    try:
        navigation = conversation_index_projection.get_resident_navigation(
            resident_id,
            actor=actor,
        )
    except SocietyServiceUnavailable:
        raise
    except (KeyError, PermissionError, RuntimeError, ValueError):
        return reference
    navigation_url = (
        str(navigation.get("url") or "").strip()
        if isinstance(navigation, dict)
        else ""
    )
    if navigation_url:
        reference["url"] = navigation_url
    return reference


# Page routes check only who calls and that the named resource exists;
# permissions belong to the permission system (decision_b820faf42218).
def _require_resource(resource_id: str, actor: str) -> dict:
    if not str(actor or "").strip():
        raise PermissionError("需要登录")
    return get_resource_service().get(resource_id, actor=actor)


def _require_active_actor(actor: str) -> None:
    if not str(actor or "").strip() or not _PORTAL_ACCESS.is_active(actor):
        raise PermissionError("需要有效登录身份")


def _filter_resource_graph(payload: dict, actor: str) -> dict:
    def node_resource_id(node: dict) -> str:
        return str(
            node.get("resource_id")
            or node.get("kb_id")
            or node.get("id")
            or ""
        ).strip()

    raw_nodes = [
        dict(node)
        for node in payload.get("nodes") or []
        if isinstance(node, dict)
    ]
    target_ids = {node_resource_id(node) for node in raw_nodes}
    target_ids.discard("")

    readable_ids: set[str] = set()
    offset = 0
    page_size = 5000
    while target_ids - readable_ids:
        page = get_resource_service().query(
            facets=["knowledge"],
            actor=str(actor or ""),
            limit=page_size,
            offset=offset,
            include_attributes=False,
        )
        resources = [
            item
            for item in page.get("resources") or []
            if isinstance(item, dict)
        ]
        readable_ids.update(
            resource_id
            for item in resources
            if (
                resource_id := str(item.get("resource_id") or "").strip()
            ) in target_ids
        )
        if not resources:
            break
        offset += len(resources)
        try:
            total = int(page.get("total"))
        except (TypeError, ValueError):
            total = offset
        if offset >= total:
            break

    graph = dict(payload)
    nodes = [
        node
        for node in raw_nodes
        if node_resource_id(node) in readable_ids
    ]
    node_ids = {node_resource_id(node) for node in nodes}
    edges = [
        dict(edge)
        for edge in payload.get("edges") or []
        if isinstance(edge, dict)
        and str(edge.get("source") or "") in node_ids
        and str(edge.get("target") or "") in node_ids
    ]
    if any("degree" in node for node in nodes):
        degree = {resource_id: 0 for resource_id in node_ids}
        for edge in edges:
            source = str(edge.get("source") or "")
            target = str(edge.get("target") or "")
            degree[source] += 1
            if target != source:
                degree[target] += 1
        for node in nodes:
            node["degree"] = degree[node_resource_id(node)]
        nodes.sort(
            key=lambda node: (
                -int(node.get("degree") or 0),
                -int(node.get("importance") or 0),
                str(node.get("title") or "").lower(),
            )
        )
    graph["nodes"] = nodes
    graph["edges"] = edges
    if "relation_counts" in graph:
        counts: dict[str, int] = {}
        for edge in edges:
            relation = str(edge.get("relation") or "")
            if relation:
                counts[relation] = counts.get(relation, 0) + 1
        graph["relation_counts"] = counts
    unresolved_by_source = graph.pop("_unresolved_links_by_source", None)
    if isinstance(unresolved_by_source, dict):
        unresolved_links = 0
        for resource_id in node_ids:
            try:
                unresolved_links += max(
                    0,
                    int(unresolved_by_source.get(resource_id) or 0),
                )
            except (TypeError, ValueError):
                continue
        graph["unresolved_links"] = unresolved_links
    elif len(nodes) != len(raw_nodes) and "unresolved_links" in graph:
        graph["unresolved_links"] = 0
    return graph


def _test_lab_access(actor: str) -> dict[str, object]:
    return {"actor": str(actor or ""), "can_operate": True}


def _authorize_test_api(handler: SimpleHTTPRequestHandler) -> dict[str, object] | None:
    auth = _get_agent_api_auth(handler)
    if not auth:
        _serve_json(handler, 401, {"error": "agent token or human session required"})
        return None
    try:
        actor = page_api_auth.authorize(auth)
    except PermissionError as exc:
        _serve_json(handler, 403, {"error": str(exc)})
        return None
    except Exception:
        _serve_json(handler, 503, {"error": "Test API 身份校验暂不可用"})
        return None
    return _test_lab_access(actor)


def _serve_resource_portal_not_found(
    handler: SimpleHTTPRequestHandler,
    address: object,
):
    clean_address = unquote(str(address or "")).strip().strip("/")
    return _serve_json(
        handler,
        404,
        {
            "error": (
                f"resource has no Portal detail surface: {clean_address}"
                if clean_address
                else "resource has no Portal detail surface"
            )
        },
    )


def _send_options(handler: SimpleHTTPRequestHandler):
    try:
        handler.send_response(204)
        handler.send_header("Allow", "GET, POST, HEAD, OPTIONS")
        handler.send_header("Access-Control-Allow-Origin", handler.headers.get("Origin") or "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, HEAD, OPTIONS")
        requested_headers = handler.headers.get("Access-Control-Request-Headers")
        if requested_headers:
            handler.send_header("Access-Control-Allow-Headers", requested_headers)
        handler.send_header("Access-Control-Max-Age", "600")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    except (BrokenPipeError, ConnectionResetError):
        return


def _prepare_knowledge_html(
    *,
    resource_category: str = "knowledge",
    hide_detail_graph: bool = False,
) -> bytes:
    return render_knowledge_html(
        resource_category=resource_category,
        hide_detail_graph=hide_detail_graph,
        portal_root=WORKSPACE_ROOT,
    )


def _prepare_lineage_html() -> bytes:
    return (ROOT / "lineage.html").read_bytes()


def _prepare_hatg_panel_template() -> bytes:
    return (ROOT / "hatg-panel.html").read_bytes()


def _prepare_settings_html() -> bytes:
    return (ROOT / "settings.html").read_bytes()


def _prepare_portal_management_html() -> bytes:
    return (ROOT / "portal_management.html").read_bytes()


def _route_health(request, _parsed, _actor: str):
    return _serve_json(request, 200, _portal_health_payload(request))


def _route_favicon(request, _parsed, _actor: str):
    return _send_empty(request, 204)


def _route_logout_get(request, _parsed, _actor: str):
    request.send_response(405)
    request.send_header("Allow", "POST")
    request.end_headers()


def _route_logout_post(request, _parsed, _actor: str):
    if not _same_origin_request(request):
        return _send_empty(request, 403)
    token = _get_session_token(request)
    username = _verify_session_token(token or "") if token else None
    if username:
        _PORTAL_ACCESS.upsert_user(
            username,
            actor=username,
            revoke_sessions=True,
            revoke_personal_tokens=False,
        )
    request.send_response(302)
    _clear_session_cookie(request)
    request.send_header("Location", "/login")
    request.end_headers()


def _route_users(request, _parsed, _actor: str):
    return _serve_json(request, 200, list_users())


def _route_agent_context_options(request, _parsed, _actor: str):
    return _serve_json(request, 200, agent_context_selection.list_options())


def _route_config(request, _parsed, _actor: str):
    return _serve_json(request, 200, _runtime_config_payload(request))


def _route_summary(request, _parsed, _actor: str):
    return _serve_json(request, 200, get_portal_summary())


def _route_daily_news(request, _parsed, _actor: str):
    return _serve_json(request, 200, _daily_news_payload())


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
    return _serve_json(request, 200, {"paths": _PORTAL_HTTP_ROUTES.page_paths()})


def _route_not_found(request, _parsed, _actor: str):
    if request.command == "POST" and urlparse(request.path).path.startswith("/test/api/"):
        try:
            _read_bounded_json_object(request)
        except ValueError as exc:
            return _serve_json(request, 400, {"error": str(exc)})
        except Exception as exc:
            return _serve_exception_json(request, exc)
        return _serve_json(request, 404, {"error": "unknown Test Lab action"})
    if _is_json_api_path(urlparse(request.path).path):
        return _serve_http_error(request, 404, "API route not found")
    return _send_empty(request, 404)


_UI_STATE_HTTP = UiStateHttp(respond=_serve_json, read_json=_read_request_json)
_AGENT_STATUS_HTTP = AgentStatusHttp(
    respond=lambda *args: _serve_json(*args),
    unavailable=lambda request: _serve_unavailable_json(request),
    resident_api=resident_api,
    cached_summary=_cached_agent_status_summary,
    cached_active=_cached_active_agents,
    cached_scheduled_triggered=_cached_scheduled_triggered_status,
    invalidate_scheduled_triggered=_SCHEDULED_TRIGGERED_READ_CACHE.expire,
)

_ADMIN_HTTP = AdminHttp(
    respond=lambda *args: _serve_json(*args),
    session_actor=lambda request: _get_session_username(request),
    access_payload=lambda actor: _admin_access_payload(actor),
    read_json=lambda request: _read_request_json(request),
    is_admin=lambda actor: _PORTAL_ACCESS.user(actor).get("category") == "portal_admin",
)

_PORTAL_SESSION_HTTP = PortalSessionHttp(
    respond=lambda *args: _serve_json(*args),
    exception_response=lambda *args: _serve_exception_json(*args),
    read_json=lambda request: _read_request_json(request),
    session_actor=lambda request: _get_session_username(request),
    access=lambda: _PORTAL_ACCESS,
    avatar=lambda actor: user_avatar.get_user_avatar(actor),
    instance=_PORTAL_INSTANCE,
    build_id=_PORTAL_BUILD_ID,
    release_manifest=_RELEASE_MANIFEST,
    preferences=lambda actor: _portal_user_preferences(actor),
    set_preferences=lambda actor, value: _set_portal_user_preferences(actor, value),
    conversation_payload=lambda actor, **kwargs: _cached_agent_conversation_payload(actor, **kwargs),
    workspace_adapter=resident_workspace_adapter,
    session_opened=lambda actor: _provision_open_session(actor),
)


def _provision_open_session(username: str) -> None:
    from management_portal.personal_ama import provision_session

    provision_session(username)


_PUBLIC_PAGES_HTTP = PublicPagesHttp(
    root=ROOT,
    check_session=lambda request: _check_session(request),
    serve_bytes=lambda *args, **kwargs: _serve_bytes(*args, **kwargs),
    serve_static=lambda *args: _serve_static_file(*args),
    send_empty=lambda *args: _send_empty(*args),
    prepare_login=lambda error: _prepare_login_html(error),
    prepare_verify=lambda error: _prepare_login_verify_html(error),
    request_cookie=lambda *args: _request_cookie(*args),
    challenge_cookie=_LOGIN_CHALLENGE_COOKIE,
    challenge_username=lambda token: _login_challenge_username(token),
    clear_challenge=lambda request: _clear_login_challenge_cookie(request),
    route_script=lambda: portal_route_script(),
    restart_notice=lambda: _restart_notice_public_payload(),
    respond=lambda *args: _serve_json(*args),
    media_content_type=lambda path: _media_content_type(path),
)

_BACKPACK_HTTP = PrincipalBackpackHttp(
    respond=lambda *args: _serve_json(*args),
    read_json=lambda request, **kwargs: _read_bounded_json_object(request, **kwargs),
    session_actor=lambda request: _get_session_username(request),
    backend=lambda: principal_backpack.PrincipalBackpack(
        access=_PORTAL_ACCESS,
        resources=get_resource_service(),
        credential_usernames=_load_users().keys(),
    ),
    known_users=lambda: _load_users(),
)


_SCRIPT_RUN_HTTP = ScriptRunHttp(
    respond=_serve_json, exception=_serve_exception_json, payload=_serve_payload,
)


_RESOURCE_HTTP = ResourceHttp(
    respond=_serve_json, exception=_serve_exception_json,
    resource_error=_serve_resource_error, read_json=_read_request_json,
    reference_payload=_resource_reference_payload,
    lineage_unavailable=_serve_society_service_unavailable,
    download_file=_serve_download_file, media_file=_serve_media_file,
    authorized_file=_serve_authorized_resource_file, serve_bytes=_serve_bytes,
    download_disposition=_download_content_disposition,
    auth_document=_serve_authenticated_document, knowledge_html=_prepare_knowledge_html,
    portal_location=lambda *args, **kwargs: _resource_portal_location(*args, **kwargs),
    portal_not_found=_serve_resource_portal_not_found,
    status_payload=_resource_service_status_payload,
    notify_index=lambda *args, **kwargs: _notify_resource_index(*args, **kwargs),
    resource_service=lambda: get_resource_service(),
)


_ACCOUNT_HTTP = AccountHttp(
    respond=_serve_json, exception=_serve_exception_json,
    serve_bytes=_serve_bytes,
    same_origin=lambda request: _same_origin_request(request),
    access=lambda: _PORTAL_ACCESS,
    two_factor=lambda: _PORTAL_TWO_FACTOR, passkeys=lambda: _PORTAL_PASSKEYS,
    change_password=_change_portal_password,
    read_bounded_json=_read_bounded_json_object,
    issue_token=lambda actor: _issue_session_token(actor),
    session_cookie=_session_cookie_value,
    request_is_https=_request_is_https,
    require_reauthentication=_require_account_reauthentication,
    passkey_context=_passkey_request_context,
    begin_two_factor=_begin_two_factor_setup,
    confirm_two_factor=_confirm_two_factor_setup,
    send_empty=_send_empty,
    user_allowed=lambda username: _portal_user_allowed(username),
)

_PORTAL_PAGES_HTTP = PortalPagesHttp(
    root=ROOT,
    document=_serve_authenticated_document,
    respond=_serve_json,
    exception=_serve_exception_json,
    send_empty=_send_empty,
    lineage_html=lambda: _prepare_lineage_html(),
    hatg_html=lambda: _prepare_hatg_panel_template(),
    settings_html=lambda: _prepare_settings_html(),
    management_html=lambda: _prepare_portal_management_html(),
    require_admin=lambda actor: _require_authenticated_actor(actor),
    workspace_adapter=resident_workspace_adapter,
    resource_location=lambda address, actor: _resource_portal_location(address, actor),
    resource_not_found=_serve_resource_portal_not_found,
)

_AGENT_WORKBENCH_HTTP = AgentWorkbenchHttp(
    respond=lambda *args, **kwargs: _serve_json(*args, **kwargs),
    error=lambda *args, **kwargs: _serve_agent_workbench_error(*args, **kwargs),
    session_actor=lambda request: _get_session_username(request),
    lineage_unavailable=_serve_society_service_unavailable,
    link_permission_denied=_serve_link_file_permission_denied,
    media_file=lambda *args, **kwargs: _serve_media_file(*args, **kwargs),
    download_file=lambda *args, **kwargs: _serve_download_file(*args, **kwargs),
    send_empty=_send_empty,
    media_content_type=_media_content_type,
    resolved_path=_society_service_resolved_path,
    lineage_favorite_payload=lambda actor: _lineage_favorite_payload(actor),
    forget_favorite=lambda actor, selection: _set_lineage_favorite(actor, selection, False),
    resident_sidebar_snapshot=lambda actor: _resident_sidebar_snapshot(actor),
    resident_api=lambda: resident_api,
    execution_api=lambda: execution_api,
    workspace_api=lambda: workspace_api,
    watchboard_applications=lambda: watchboard_applications,
    resident_sidebar_backend=lambda: resident_sidebar_backend,
    resident_chat_projection=lambda: resident_chat_projection,
    local_terminal_backend=lambda: local_terminal_backend,
    knowledge_backend=lambda: knowledge_backend,
    read_json=_read_bounded_json_object,
    read_multipart=_read_request_multipart,
    multipart_body_limit=_multipart_body_limit,
    body_too_large=RequestBodyTooLarge,
    json_max_bytes=_JSON_REQUEST_MAX_BYTES,
    file_save_json_max_bytes=_FILE_SAVE_JSON_MAX_BYTES,
    chat_image_max_bytes=workspace_contracts.CHAT_IMAGE_MAX_BYTES,
    chat_attachment_max_bytes=workspace_contracts.CHAT_ATTACHMENT_MAX_BYTES,
    discovery_client=lambda: ResidentDiscoveryClient(),
    recommendation_backend=lambda: resident_recommendation_backend,
)


_KNOWLEDGE_HTTP = KnowledgeHttp(
    respond=lambda *args, **kwargs: _serve_json(*args, **kwargs),
    exception_response=lambda *args, **kwargs: _serve_exception_json(*args, **kwargs),
    resource_error=lambda *args, **kwargs: _serve_resource_error(*args, **kwargs),
    media_file=lambda *args, **kwargs: _serve_media_file(*args, **kwargs),
    send_empty=lambda *args, **kwargs: _send_empty(*args, **kwargs),
    session_actor=lambda request: _get_session_username(request),
    require_resource=lambda *args: _require_resource(*args),
    filter_graph=lambda *args: _filter_resource_graph(*args),
    notify_resource_index=lambda **kwargs: _notify_resource_index(**kwargs),
    resource_service=lambda: get_resource_service(),
    resource_views=lambda: resource_views,
    knowledge_backend=lambda: knowledge_backend,
    knowledge_item_chat_backend=lambda: knowledge_item_chat_backend,
    knowledge_archive_backend=lambda: knowledge_archive_backend,
    read_json=lambda request: _read_request_json(request),
)

_SKILLS_HTTP = SkillsHttp()


_LINEAGE_HTTP = LineageHttp(
    respond=lambda *args: _serve_json(*args),
    exception_response=lambda request, exc: _serve_exception_json(request, exc),
    service_unavailable=lambda request, exc: _serve_society_service_unavailable(request, exc),
    session_actor=lambda request: _get_session_username(request),
    service_status=lambda: _society_service_status_payload(),
    lineage=lambda: lineage_domain,
    unavailable_error=SocietyServiceUnavailable,
)

_TERMINAL_HTTP = TerminalHttp(
    session_actor=lambda request: _get_session_username(request),
    read_json=lambda request: _read_request_json(request),
    respond=lambda *args: _serve_json(*args),
    exception_response=lambda request, exc: _serve_exception_json(request, exc),
)

_PUBLIC_AUTH_HTTP = PublicAuthHttp(
    same_origin=lambda request: _same_origin_request(request),
    empty=lambda *args: _send_empty(*args),
    respond=lambda *args: _serve_json(*args),
    bounded_json=lambda request, **kwargs: _read_bounded_json_object(request, **kwargs),
    bounded_form=lambda request: _read_bounded_form_object(request),
    password_reset_user=lambda email: _password_reset_user(email),
    password_resets=lambda: _PORTAL_PASSWORD_RESETS,
    access=lambda: _PORTAL_ACCESS,
    credentials=lambda: _PORTAL_CREDENTIALS,
    request_client_key=lambda request: _request_client_key(request),
    verify_credentials=lambda *args: _verify_credentials(*args),
    load_users=lambda: _load_users(),
    user_allowed=lambda username: _portal_user_allowed(username),
    two_factor=lambda: _PORTAL_TWO_FACTOR,
    start_login_challenge=lambda username: _start_login_challenge(username),
    issue_session_token=lambda username: _issue_session_token(username),
    set_login_challenge_cookie=lambda *args: _set_login_challenge_cookie(*args),
    set_session_cookie=lambda *args: _set_session_cookie(*args),
    request_cookie=lambda *args: _request_cookie(*args),
    login_challenge_cookie=_LOGIN_CHALLENGE_COOKIE,
    verify_login_challenge=lambda *args: _verify_login_challenge(*args),
    clear_login_challenge_cookie=lambda request: _clear_login_challenge_cookie(request),
    login_challenge_username=lambda token: _login_challenge_username(token),
)


def _feature_route_definitions() -> tuple[RouteDefinition, ...]:
    """The routes of the installed features (``web.routes``, ``portal_features.py``)."""
    shell = sys.modules[__name__]
    return tuple(route for _feature, routes in portal_features.contributions("web.routes") for route in routes(shell))


def _portal_route_definitions() -> tuple[RouteDefinition, ...]:
    return (
        RouteDefinition("GET", "/api/health", _route_health, "portal-public", phase="public"),
        RouteDefinition("GET", "/favicon.ico", _route_favicon, "portal-public", phase="public"),
        RouteDefinition("GET", "/logout", _route_logout_get, "portal-session", phase="public"),
        RouteDefinition("POST", "/logout", _route_logout_post, "portal-session", phase="public"),
        RouteDefinition("GET", "/api/users", _route_users, "portal-overview"),
        RouteDefinition("GET", "/api/agent-context-options", _route_agent_context_options, "portal-overview"),
        RouteDefinition("GET", "/api/config", _route_config, "portal-overview"),
        RouteDefinition("GET", "/api/summary", _route_summary, "portal-overview"),
        RouteDefinition("GET", "/api/daily-news", _route_daily_news, "portal-overview"),
        RouteDefinition("GET", "/api/workbenches", _route_workbenches_get, "workbenches"),
        RouteDefinition("GET", "/api/workbenches/", _route_workbenches_get, "workbenches", match="prefix"),
        RouteDefinition("POST", "/api/workbenches", _route_workbenches_post, "workbenches"),
        RouteDefinition("POST", "/api/workbenches/", _route_workbenches_post, "workbenches", match="prefix"),
        RouteDefinition("GET", "/api/agent-workbench/agent-backends", _AGENT_WORKBENCH_HTTP.agent_backends, "agent-workbench-runtime"),
        RouteDefinition("GET", "/api/agent-workbench/agent-runtime-catalog", _AGENT_WORKBENCH_HTTP.runtime_catalog, "agent-workbench-runtime"),
        RouteDefinition("GET", "/api/agent-workbench/favorites", _AGENT_WORKBENCH_HTTP.get_favorites, "agent-workbench-favorites"),
        RouteDefinition("GET", "/api/agent-workbench/residents", _AGENT_WORKBENCH_HTTP.get_residents, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/shortcuts", _AGENT_WORKBENCH_HTTP.get_resident_shortcuts, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/navigation-target", _AGENT_WORKBENCH_HTTP.get_residents_navigation_target, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/projection", _AGENT_WORKBENCH_HTTP.get_residents_projection, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/watchboard", _AGENT_WORKBENCH_HTTP.get_residents_watchboard, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/session-restart-options", _AGENT_WORKBENCH_HTTP.get_residents_session_restart_options, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/residents/forward-targets", _AGENT_WORKBENCH_HTTP.get_residents_forward_targets, "agent-workbench-residents"),
        RouteDefinition("GET", "/api/agent-workbench/chat-attachments/image", _AGENT_WORKBENCH_HTTP.get_chat_attachments_image, "agent-workbench-chat-attachments"),
        RouteDefinition("GET", "/api/agent-workbench/local-terminal/read", _AGENT_WORKBENCH_HTTP.get_local_terminal_read, "agent-workbench-local-terminal"),
        RouteDefinition("GET", "/api/agent-workbench/link-file", _AGENT_WORKBENCH_HTTP.get_link_file, "agent-workbench-link-file"),
        RouteDefinition("GET", "/api/agent-workbench/link-file-raw", _AGENT_WORKBENCH_HTTP.get_link_file_raw, "agent-workbench-link-file-raw"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/contexts", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_contexts), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/recommend", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_recommend), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/recommend/latest", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_recommend_latest), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/recommend/result", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_recommend_result), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/routing-evals/residents/search", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_routing_residents_search), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/routing-evals/submit", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_routing_submit), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/resident-discovery/search", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.discovery_search), "agent-workbench-resident-discovery"),
        RouteDefinition("POST", "/api/agent-workbench/local-terminal/start", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.post_local_terminal_start), "agent-workbench-local-terminal"),
        RouteDefinition("POST", "/api/agent-workbench/local-terminal/input", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.post_local_terminal_input), "agent-workbench-local-terminal"),
        RouteDefinition("POST", "/api/agent-workbench/local-terminal/control", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.post_local_terminal_control), "agent-workbench-local-terminal"),
        RouteDefinition("POST", "/api/agent-workbench/residents/watchboard-application", _AGENT_WORKBENCH_HTTP.post_route(_AGENT_WORKBENCH_HTTP.post_residents_watchboard_application), "agent-workbench-residents"),
        RouteDefinition("GET", "/lineage/api/service-status", _LINEAGE_HTTP.get_route(_LINEAGE_HTTP.status), "lineage"),
        RouteDefinition("GET", "/lineage/api/lineages", _LINEAGE_HTTP.get_route(_LINEAGE_HTTP.lineages), "lineage"),
        RouteDefinition("GET", "/lineage/api/node-message-counts", _LINEAGE_HTTP.get_route(_LINEAGE_HTTP.node_message_counts), "lineage"),
        RouteDefinition("GET", "/lineage/api/node-metadata", _LINEAGE_HTTP.get_route(_LINEAGE_HTTP.node_metadata), "lineage"),
        RouteDefinition("GET", "/knowledge", _KNOWLEDGE_HTTP.get_home, "knowledge"),
        RouteDefinition("GET", "/knowledge/", _KNOWLEDGE_HTTP.get_home, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/index", _KNOWLEDGE_HTTP.get_api_index, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/item", _KNOWLEDGE_HTTP.get_api_item, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/semantic-searches", _KNOWLEDGE_HTTP.get_api_semantic_searches, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/graph", _KNOWLEDGE_HTTP.get_api_graph, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/detail", _KNOWLEDGE_HTTP.get_api_detail, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/item-graph", _KNOWLEDGE_HTTP.get_api_item_graph, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/item-chat", _KNOWLEDGE_HTTP.get_api_item_chat, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/item-chat/attachment-image", _KNOWLEDGE_HTTP.get_api_item_chat_attachment_image, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/archive/jobs", _KNOWLEDGE_HTTP.get_api_archive_jobs, "knowledge"),
        RouteDefinition("GET", "/knowledge/api/archive/job", _KNOWLEDGE_HTTP.get_api_archive_job, "knowledge"),
        RouteDefinition("POST", "/knowledge/api/detail/save", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_detail_save), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/detail/delete", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_detail_delete), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/item-chat/prepare", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_chat_prepare), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/item-chat", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_chat), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/semantic-search/start", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_search_start), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/semantic-search/refresh", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_search_refresh), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/semantic-search/delete", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_search_delete), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/create", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_create), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/chat", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_chat), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/candidates", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_candidates), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/drafts", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_drafts), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/apply", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_apply), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/draft/save", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_draft_save), "knowledge"),
        RouteDefinition("POST", "/knowledge/api/archive/delete", _KNOWLEDGE_HTTP.post_route(_KNOWLEDGE_HTTP.post_archive_delete), "knowledge"),
        RouteDefinition("GET", "/skills", _SKILLS_HTTP.home, "skills", page=True),
        RouteDefinition("GET", "/skills/", _SKILLS_HTTP.home, "skills", page=True),
        RouteDefinition("GET", "/api/restart-notice", _PUBLIC_PAGES_HTTP.restart_notice_get, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/forgot-password", _PUBLIC_PAGES_HTTP.forgot_password, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/login", _PUBLIC_PAGES_HTTP.login, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/join", http_user_enrollment.join_page, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/api/auth/setup-status", http_user_enrollment.setup_status, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/guide", http_user_guide.guide_page, "portal-public-get", phase="public", page=True),
        RouteDefinition("GET", "/guide/human.md", http_user_guide.guide_markdown, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/guide/agent.md", http_user_guide.guide_markdown, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/guide/admin.md", http_user_guide.guide_markdown, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/guide/portal_user.py", http_user_guide.client_script, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/llms.txt", http_user_guide.llms_txt, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/login/verify", _PUBLIC_PAGES_HTTP.login_verify, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/reset-password", _PUBLIC_PAGES_HTTP.reset_password, "portal-public-get", phase="public"),
        RouteDefinition("GET", "/static/portal_resource_routes.js", _PUBLIC_PAGES_HTTP.resource_routes, "portal-public-get", phase="public"),
        *(
            RouteDefinition("GET", path, _PUBLIC_PAGES_HTTP.static, "portal-public-get", phase="public")
            for path in (
                '/static/hatg_mailbox_panel.js',
                '/static/hatg_graph_model.js',
                '/static/hatg_lineage_state.js',
                '/static/hatg_family_blocks.js',
                '/static/hatg_node_status.js',
                '/static/hatg_function_block_menu.js',
                '/static/hatg_program_inspector.js',
                '/static/hatg_relations.js',
                '/static/hatg_layout.js',
                '/static/hatg_resident_dialogs.js',
                '/static/hatg_lineage_dialogs.js',
                '/static/hatg_seeds.js',
                '/static/hatg_leader.js',
                '/static/hatg_graph_search.js',
                '/static/hatg_panel.css',
                '/static/hatg_panel.js',
                '/static/portal_script_runs.js',
                '/static/hatg_panel_features.js',
                '/static/hatg_panel_state.js',
                '/static/hatg_workspace_projection.js',
                '/static/home.js',
                '/static/home_workbench.css',
                '/static/lineage_element_fields.js',
                '/static/lineage_function_blocks.css',
                '/static/lineage_graph.js',
                '/static/lineage_invocation_inspector.js',
                '/static/lineage_primitive_definitions.json',
                '/static/lineage_primitive_inspector.js',
                '/static/lineage_program.js',
                '/static/lineage_template_graph.js',
                '/static/lineage_templates.js',
                '/static/lineage_workbench.css',
                '/static/lineage_workbench.js',
                '/static/lineage_workbench_controller.js',
                '/static/lineage_workbench_state.js',
                '/static/lineage_workbench_watchboard.js',
                '/static/markdown.js',
                '/static/portal_agent_inbox.js',
                '/static/portal_mailbox_todos.js',
                '/static/portal_api.js',
                '/static/portal_board_shell.js',
                '/static/portal_button_tooltips.js',
                '/static/portal_model_api_settings.js',
                '/static/portal_resident_runtime_controller.js',
                '/static/portal_chat_workspace.js',
                '/static/portal_resident_avatar.css',
                '/static/portal_resident_avatar.js',
                '/static/portal_resident_menu.js',
                '/static/portal_resident_menu.css',
                '/static/portal_workspace_menu.js',
                '/static/portal_workspace_menu.css',
                '/static/portal_chatroom_view.js',
                '/static/portal_command_palette.js',
                '/static/portal_component_recovery.js',
                '/static/portal_components.css',
                '/static/portal_conversation_store.js',
                '/static/portal_demo_frame.js',
                '/static/portal_demo_player.js',
                '/static/portal_design_system.css',
                '/static/portal_detail_surface.js',
                '/static/portal_editor_open_service.js',
                '/static/portal_events.js',
                '/static/portal_file_links.js',
                '/static/portal_form_controls.js',
                '/static/portal_hatg_loader.js',
                '/static/portal_home_components.js',
                '/static/portal_text.js',
                '/static/portal_i18n.js',
                '/static/portal_identity.js',
                '/static/portal_image_editor.css',
                '/static/portal_image_editor.js',
                '/static/portal_instruction_client.js',
                '/static/portal_user_enrollment.js',
                '/static/portal_sha256.js',
                '/static/portal_user_enrollment.css',
                '/static/portal_lineage_catalogs.js',
                '/static/portal_society_service.js',
                '/static/portal_live_tour.css',
                '/static/portal_live_tour.js',
                '/static/portal_management.js',
                '/static/portal_mermaid.mjs',
                '/static/portal_mermaid_realm.mjs',
                '/static/portal_nav.js',
                '/static/portal_overlay.js',
                '/static/portal_page_state.js',
                '/static/portal_personal_workspaces.js',
                '/static/portal_passkeys.js',
                '/static/portal_pdf_preview.js',
                '/static/portal_preview_contract.js',
                '/static/portal_preview_renderers.js',
                '/static/portal_preview_runtime.js',
                '/static/portal_projections.js',
                '/static/portal_refresh_coordinator.js',
                '/static/portal_resident_discovery_service.js',
                '/static/portal_resource_chips.js',
                '/static/portal_resource_header.js',
                '/static/portal_state.js',
                '/static/portal_theme.js',
                '/static/portal_themes.css',
                '/static/portal_translations.js',
                '/static/portal_ui.js',
                '/static/portal_ui_primitives.js',
                '/static/portal_workbench.css',
                '/static/portal_workbench.js',
                '/static/portal_document_statistics.js',
                '/static/portal_workspace_surface.js',
                '/static/projection_host.js',
                '/static/projection_sdk.js',
                '/static/resident_projection.js',
                '/static/resident_protocol.js',
                '/static/settings.css',
                '/static/settings.js',
                '/static/development_workspace.js',
                '/static/workbench_studio.css',
                '/static/workbench_studio.js',
                '/static/portal_workbench_catalog.css',
                '/static/portal_workbench_catalog.js',
            )
        ),
        *(RouteDefinition("GET", path, _PUBLIC_PAGES_HTTP.static, "portal-public-get", phase="public")
          for path in PUBLIC_SHARED_STATIC_ASSETS),
        RouteDefinition("GET", "/static/assets/", _PUBLIC_PAGES_HTTP.asset, "portal-public-get", match="prefix", phase="public"),
        RouteDefinition("GET", "/static/vendor/", _PUBLIC_PAGES_HTTP.vendor, "portal-public-get", match="prefix", phase="public"),
        RouteDefinition("GET", "/workbenches/create", _PORTAL_PAGES_HTTP.workbenches_create, "portal-portal-get"),
        RouteDefinition("GET", "/workbenches/edit", _PORTAL_PAGES_HTTP.workbenches_edit, "portal-portal-get"),
        RouteDefinition("GET", "/workbenches", _PORTAL_PAGES_HTTP.workbenches, "portal-portal-get", page=True),
        RouteDefinition("GET", "/workbenches/", _PORTAL_PAGES_HTTP.workbenches, "portal-portal-get", page=True),
        RouteDefinition("GET", "/workbenches/view", _PORTAL_PAGES_HTTP.workbenches, "portal-portal-get", page=True),
        RouteDefinition("GET", "/development", _PORTAL_PAGES_HTTP.workbenches, "portal-portal-get", page=True),
        RouteDefinition("GET", "/development/native-views", _PORTAL_PAGES_HTTP.development_native_views, "portal-portal-get"),
        RouteDefinition("GET", "/api/account/avatar", _ACCOUNT_HTTP.avatar_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/account/avatar/image", _ACCOUNT_HTTP.avatar_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/account/security", _ACCOUNT_HTTP.security_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/me", _PORTAL_SESSION_HTTP.me, "portal-portal-get"),
        RouteDefinition("GET", "/api/navigation/pages", _route_navigation_pages, "portal-portal-get"),
        RouteDefinition("GET", "/__portal_router/preference", _PORTAL_SESSION_HTTP.router_preference, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/access-control", _ADMIN_HTTP.access_control_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/network", _ADMIN_HTTP.network_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/instances", _ADMIN_HTTP.instances_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/paths", _ADMIN_HTTP.paths_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/embeddings", _ADMIN_HTTP.embeddings_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/wall-scheduling", _ADMIN_HTTP.wall_scheduling_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/feature-switch", _ADMIN_HTTP.feature_switch_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/admin/paths/migration", _ADMIN_HTTP.paths_status_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/preferences", _PORTAL_SESSION_HTTP.preferences_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/resident-invocations/diagnostics", _PORTAL_SESSION_HTTP.resident_invocations_diagnostics, "portal-portal-get"),
        RouteDefinition("GET", "/api/ui-state/associations", _UI_STATE_HTTP.list_associations, "portal-ui-state"),
        RouteDefinition("GET", "/api/agent-conversations", _PORTAL_SESSION_HTTP.conversations_get, "portal-portal-get"),
        RouteDefinition("GET", "/api/terminal/read", _TERMINAL_HTTP.read, "portal-terminal"),
        RouteDefinition("GET", "/api/agent-status/account-residents", _AGENT_STATUS_HTTP.account_residents, "portal-agent-status"),
        RouteDefinition("GET", "/api/agent-status", _AGENT_STATUS_HTTP.summary, "portal-agent-status"),
        RouteDefinition("GET", "/api/agent-status/usage", _AGENT_STATUS_HTTP.usage, "portal-agent-status"),
        RouteDefinition("GET", "/api/agent-status/active-agents", _AGENT_STATUS_HTTP.active_agents, "portal-agent-status"),
        RouteDefinition("GET", "/api/agent-status/scheduled-triggered", _AGENT_STATUS_HTTP.scheduled_triggered, "portal-agent-status"),
        RouteDefinition("GET", "/api/principal-backpack/catalog", _BACKPACK_HTTP.catalog_get, "portal-principal-backpack"),
        RouteDefinition("GET", "/api/principal-backpack/detail", _BACKPACK_HTTP.detail_get, "portal-principal-backpack"),
        RouteDefinition("GET", "/api/principal-backpack/", _BACKPACK_HTTP.not_found, "portal-principal-backpack", match="prefix_fallback"),
        RouteDefinition("GET", "/api/resource-semantic-searches", _RESOURCE_HTTP.semantic_searches, "portal-catalog-get"),
        RouteDefinition("GET", "/resource/api/service-status", _RESOURCE_HTTP.service_status, "portal-catalog-get"),
        RouteDefinition("GET", "/api/resource-index/query", _RESOURCE_HTTP.index_query, "portal-catalog-get"),
        RouteDefinition("GET", "/api/projects/catalog", _RESOURCE_HTTP.project_catalog, "portal-catalog-get"),
        RouteDefinition("GET", "/api/resource-index/suggest", _RESOURCE_HTTP.index_suggest, "portal-catalog-get"),
        RouteDefinition("GET", "/api/resource-index/status", _RESOURCE_HTTP.index_status, "portal-catalog-get"),
        RouteDefinition("GET", "/api/resource-index/resources/{resource_id}", _RESOURCE_HTTP.index_detail, "portal-resource-index", match="template"),
        RouteDefinition("GET", "/api/resources/capabilities", _RESOURCE_HTTP.capabilities, "portal-resources"),
        RouteDefinition("GET", "/api/searches/diagnostics", _PORTAL_SESSION_HTTP.search_diagnostics, "portal-searches"),
        RouteDefinition("GET", "/api/resources/resolve", _RESOURCE_HTTP.resolve, "portal-resources"),
        RouteDefinition("GET", "/api/resources/reference", _RESOURCE_HTTP.reference, "portal-resources"),
        RouteDefinition("GET", "/api/script-runs/artifact", _SCRIPT_RUN_HTTP.artifact, "portal-script-runs"),
        RouteDefinition("GET", "/api/resources/content", _RESOURCE_HTTP.content, "portal-resources"),
        RouteDefinition("GET", "/r/", _PORTAL_PAGES_HTTP.resource_redirect, "portal-pages-get", match="prefix"),
        RouteDefinition("GET", "/resource/knowledge", _RESOURCE_HTTP.knowledge_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/knowledge/", _RESOURCE_HTTP.knowledge_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/skill", (lambda request, parsed, actor: _RESOURCE_HTTP.skill_page(request, parsed, actor, resource_category="skill")), "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/skill/", (lambda request, parsed, actor: _RESOURCE_HTTP.skill_page(request, parsed, actor, resource_category="skill")), "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/experiment", (lambda request, parsed, actor: _RESOURCE_HTTP.skill_page(request, parsed, actor, resource_category="experiment")), "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/experiment/", (lambda request, parsed, actor: _RESOURCE_HTTP.skill_page(request, parsed, actor, resource_category="experiment")), "portal-pages-get", page=True),
        RouteDefinition("GET", "/wall", _PORTAL_PAGES_HTTP.wall, "portal-pages-get", page=True),
        RouteDefinition("GET", "/wall/", _PORTAL_PAGES_HTTP.wall, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/device", _RESOURCE_HTTP.device_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/device/", _RESOURCE_HTTP.device_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource", _RESOURCE_HTTP.resource_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource/", _RESOURCE_HTTP.resource_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "/resource.html", _RESOURCE_HTTP.resource_page, "portal-pages-get", page=True),
        RouteDefinition("GET", "", _PORTAL_PAGES_HTTP.home, "portal-pages-get"),
        RouteDefinition("GET", "/", _PORTAL_PAGES_HTTP.home, "portal-pages-get", page=True),
        RouteDefinition("GET", "/index.html", _PORTAL_PAGES_HTTP.home, "portal-pages-get", page=True),
        RouteDefinition("GET", "/lineage", _PORTAL_PAGES_HTTP.lineage, "portal-pages-get", page=True),
        RouteDefinition("GET", "/lineage/", _PORTAL_PAGES_HTTP.lineage, "portal-pages-get", page=True),
        RouteDefinition("GET", "/lineage.html", _PORTAL_PAGES_HTTP.lineage, "portal-pages-get", page=True),
        RouteDefinition("GET", "/hatg-panel.html", _PORTAL_PAGES_HTTP.hatg_panel, "portal-pages-get", page=True),
        RouteDefinition("GET", "/chat-workspace/api/session", _PORTAL_PAGES_HTTP.chat_workspace_session, "portal-pages-get"),
        RouteDefinition("GET", "/principal-backpack", _PORTAL_PAGES_HTTP.principal_backpack, "portal-pages-get", page=True),
        RouteDefinition("GET", "/principal-backpack/", _PORTAL_PAGES_HTTP.principal_backpack, "portal-pages-get", page=True),
        RouteDefinition("GET", "/principal_backpack.html", _PORTAL_PAGES_HTTP.principal_backpack, "portal-pages-get", page=True),
        RouteDefinition("GET", "/settings", _PORTAL_PAGES_HTTP.settings, "portal-pages-get", page=True),
        RouteDefinition("GET", "/settings/", _PORTAL_PAGES_HTTP.settings, "portal-pages-get", page=True),
        RouteDefinition("GET", "/settings.html", _PORTAL_PAGES_HTTP.settings, "portal-pages-get", page=True),
        RouteDefinition("GET", "/portal-management", _PORTAL_PAGES_HTTP.portal_management, "portal-pages-get", page=True),
        RouteDefinition("GET", "/portal-management/", _PORTAL_PAGES_HTTP.portal_management, "portal-pages-get", page=True),
        RouteDefinition("GET", "/portal_management.html", _PORTAL_PAGES_HTTP.portal_management, "portal-pages-get", page=True),
        RouteDefinition("POST", "/api/auth/password-reset/request", _PUBLIC_AUTH_HTTP.password_reset_request, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/invitations/accept", http_user_enrollment.accept_invitation, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/invitations/preview", http_user_enrollment.preview_invitation, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/password-reset/confirm", _PUBLIC_AUTH_HTTP.password_reset_confirm, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/passkeys/options", _ACCOUNT_HTTP.public_passkey_options, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/auth/passkeys/verify", _ACCOUNT_HTTP.public_passkey_verify, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/login", _PUBLIC_AUTH_HTTP.login, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/login/verify", _PUBLIC_AUTH_HTTP.login_verify, "portal-public-post", phase="public"),
        RouteDefinition("POST", "/api/account/password", _ACCOUNT_HTTP.password_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/passkeys/registration/options", _ACCOUNT_HTTP.passkey_options_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/passkeys/registration/verify", _ACCOUNT_HTTP.passkey_verify_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/passkeys/delete", _ACCOUNT_HTTP.passkey_delete_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/two-factor/setup", _ACCOUNT_HTTP.two_factor_setup_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/two-factor/confirm", _ACCOUNT_HTTP.two_factor_confirm_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/two-factor/recovery-codes", _ACCOUNT_HTTP.two_factor_recovery_codes_post, "portal-account-post"),
        RouteDefinition("POST", "/api/account/two-factor/disable", _ACCOUNT_HTTP.two_factor_disable_post, "portal-account-post"),
        RouteDefinition("POST", "/api/terminal/start", _TERMINAL_HTTP.start, "portal-terminal"),
        RouteDefinition("POST", "/api/terminal/input", _TERMINAL_HTTP.input, "portal-terminal"),
        RouteDefinition("POST", "/api/terminal/control", _TERMINAL_HTTP.control, "portal-terminal"),
        RouteDefinition("POST", "/api/resource-semantic-search/start", (lambda request, parsed, actor: _RESOURCE_HTTP.semantic_search(request, parsed, actor, operation="start")), "portal-terminal-post"),
        RouteDefinition("POST", "/api/resource-semantic-search/refresh", (lambda request, parsed, actor: _RESOURCE_HTTP.semantic_search(request, parsed, actor, operation="refresh")), "portal-terminal-post"),
        RouteDefinition("POST", "/api/resource-semantic-search/delete", (lambda request, parsed, actor: _RESOURCE_HTTP.semantic_search(request, parsed, actor, operation="delete")), "portal-terminal-post"),
        RouteDefinition("POST", "/api/resource-index/refresh", _RESOURCE_HTTP.index_refresh, "portal-resources-post"),
        RouteDefinition("POST", "/api/principal-backpack/catalog", _BACKPACK_HTTP.write_not_allowed, "portal-principal-backpack"),
        RouteDefinition("POST", "/api/principal-backpack/detail", _BACKPACK_HTTP.write_not_allowed, "portal-principal-backpack"),
        RouteDefinition("POST", "/api/preferences", _PORTAL_SESSION_HTTP.preferences_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/network", _ADMIN_HTTP.network_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/instances", _ADMIN_HTTP.instances_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/paths", _ADMIN_HTTP.paths_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/embeddings", _ADMIN_HTTP.embeddings_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/wall-scheduling", _ADMIN_HTTP.wall_scheduling_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/feature-switch", _ADMIN_HTTP.feature_switch_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/admin/embeddings/install", _ADMIN_HTTP.embeddings_install_post, "portal-portal-post"),
        RouteDefinition("POST", "/api/agent-status/refresh", _AGENT_STATUS_HTTP.refresh, "portal-agent-status"),
        RouteDefinition("POST", "/api/agent-status/usage/refresh", _AGENT_STATUS_HTTP.refresh_usage, "portal-agent-status"),
        RouteDefinition("POST", "/chat-workspace/api/prepare", _PORTAL_SESSION_HTTP.chat_workspace_prepare, "portal-portal-post"),
        RouteDefinition("POST", "/api/ui-state/associations/put", _UI_STATE_HTTP.put, "portal-ui-state"),
        RouteDefinition("POST", "/api/ui-state/associations/remove", _UI_STATE_HTTP.remove, "portal-ui-state"),
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
        return _send_options(self)

    def do_HEAD(self):
        return self.do_GET()

    def do_GET(self):
        try:
            return self._do_GET()
        except Exception as exc:
            if not _is_json_api_path(urlparse(self.path).path):
                raise
            self.log_error("GET %s failed: %r", urlparse(self.path).path, exc)
            return _serve_exception_json(self, exc)

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
            test_access = _authorize_test_api(self)
            if test_access is None:
                return

        # ── Auth gate ──
        if test_access is None and not _check_session(self):
            if _is_json_api_path(parsed.path):
                return _serve_http_error(self, 401, "Authentication required")
            self.send_response(302)
            self.send_header("Location", "/login")
            self.end_headers()
            return

        # ── Protected routes ──
        route_actor = str((test_access or {}).get("actor") or _get_session_username(self) or "")
        self._test_api_access = test_access
        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "GET", parsed.path, self, parsed, route_actor, phase="protected"
        )
        return routed

    def _handle_get_fallback(self, parsed, route_actor):
        if _is_json_api_path(parsed.path):
            return _serve_http_error(self, 404, "API route not found")
        resource_path_address = unquote(parsed.path.lstrip("/")).strip("/")
        resource_root_id = resource_path_address.partition("/")[0]
        try:
            get_resource_service().validate_id(resource_root_id)
        except ValueError:
            pass
        else:
            location = _resource_portal_location(
                resource_path_address,
                _get_session_username(self) or "",
            )
            if not location:
                return _serve_resource_portal_not_found(
                    self,
                    resource_path_address,
                )
            self.send_response(302)
            self.send_header("Location", location)
            self.end_headers()
            return
        static_file = _static_fallback_file(parsed.path)
        if static_file is not None:
            return _serve_static_file(self, static_file, self.guess_type(str(static_file)))
        return super().do_GET()


    def do_POST(self):
        try:
            from management_common.resource_lifecycle import lifecycle_scope

            with lifecycle_scope(self.headers.get("X-Portal-Resource-Lifecycle", ""),
                                 actor=_get_session_username(self) or ""):
                return Handler._do_POST(self)
        except RequestBodyTooLarge as exc:
            return _serve_json(self, 413, {"error": str(exc)})
        except InvalidRequestBody as exc:
            return _serve_json(self, 400, {"error": str(exc)})
        except Exception as exc:
            self.log_error("POST %s failed: %r", urlparse(self.path).path, exc)
            return _serve_exception_json(self, exc)

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
        if _get_session_username(self) and not _same_origin_request(self):
            return _serve_http_error(self, 403, "Request origin is not allowed")

        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "POST", parsed.path, self, parsed, "", phase="dual-auth"
        )
        if routed is not ROUTE_NOT_FOUND:
            return routed

        test_access = None
        if parsed.path.startswith("/test/api/"):
            test_access = _authorize_test_api(self)
            if test_access is None:
                return

        # ── Auth gate ──
        if test_access is None and not _check_session(self):
            return _serve_http_error(self, 401, "Authentication required")

        # ── Protected routes ──
        route_actor = str((test_access or {}).get("actor") or _get_session_username(self) or "")
        routed = _PORTAL_HTTP_ROUTES.dispatch(
            "POST", parsed.path, self, parsed, route_actor, phase="protected"
        )
        return routed
