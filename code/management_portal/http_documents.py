"""Portal HTML documents: page templates and the bootstrap each served document receives
(script groups, language, identity, versioned asset URLs, frontend manifest).
"""
from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

from management_portal import (
    http_auth,
    http_responses,
    http_static_files,
    script_groups,
    user_preferences,
)
from management_portal.page_renderers import render_knowledge_html


ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = ROOT.parent


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
    cache_control: str = http_responses._SENSITIVE_RESPONSE_CACHE_CONTROL,
    extra_headers: dict[str, str] | None = None,
):
    if content_type.lower().startswith("text/html"):
        from management_portal.localization import normalize_language
        if script_groups.MARKER.encode("ascii") in data:
            data = script_groups.expand_document(data.decode("utf-8")).encode("utf-8")
        username = http_auth._get_request_actor(handler) or ""
        if username:
            language = normalize_language(user_preferences._portal_user_preferences(username).get("appearance", {}).get("language"))
            bootstrap = ("<script>window.__PORTAL_LANGUAGE__=" + json.dumps(language) + ";</script>").encode("utf-8")
            data = data.replace(b"<head>", b"<head>" + bootstrap, 1)
        data = _inject_portal_identity_bootstrap(
            data,
            http_auth._get_request_actor(handler) or "",
        )
        data = http_static_files._version_static_asset_urls(data)
        from management_portal import frontend_manifest
        from management_portal.page_bootstrap import inject_page_bootstrap
        data = inject_page_bootstrap(frontend_manifest.inject(data))
    return http_responses._serve_payload(
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
        cache_control=http_responses._AUTHENTICATED_DOCUMENT_CACHE_CONTROL,
        extra_headers=extra_headers,
    )


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
