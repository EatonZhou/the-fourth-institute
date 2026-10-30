"""The Portal's overview APIs: health, component status, runtime configuration, the site
summary, the daily news and the user and context option lists.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler
from pathlib import Path

from management_common.portal_paths import path as portal_path
from management_common.site_registry import get_portal_summary, get_runtime_config
from management_portal import agent_context_selection, http_auth, http_instance, http_responses, web_clients
from management_portal.portal_access_control import list_users
from management_portal.resources import get_resource_service
from management_portal.society_service import SocietyServiceBusy, SocietyServiceClient
from management_portal.http_routes import RouteDefinition


DAILY_NEWS_ROOT = Path(os.getenv(
    "PORTAL_DAILY_NEWS_ROOT",
    str(portal_path("share_root") / "Agents" / "lineage_7e2464bbdb60" / "memory" / "reports"),
)).expanduser()


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


def _portal_health_payload(handler: SimpleHTTPRequestHandler | None = None) -> dict:
    port = 0
    if handler is not None:
        try:
            port = int(handler.server.server_address[1])
        except (AttributeError, IndexError, TypeError, ValueError):
            # A request object without a bound server address reports port 0.
            port = 0
    payload = {
        "ok": True,
        "instance": http_instance._PORTAL_INSTANCE,
        "build_id": http_instance._PORTAL_BUILD_ID,
        "port": port,
        "started_at": http_instance._SERVER_STARTED_AT,
        "now": time.time(),
    }
    try:
        from management_portal.execution.service import ExecutionService

        payload["execution_layer"] = ExecutionService().health()
    except Exception as exc:
        payload["execution_layer"] = {"ok": False, "error": str(exc)}
    try:
        payload["society_service"] = web_clients._SOCIETY_SERVICE.health()
    except Exception as exc:
        payload["society_service"] = {
            "ok": False,
            "error": str(exc),
        }
    return payload


def _society_service_status_payload() -> dict:
    try:
        if isinstance(web_clients._SOCIETY_SERVICE, SocietyServiceClient):
            health = web_clients._SOCIETY_SERVICE.health(timeout=2.25)
        else:
            health = web_clients._SOCIETY_SERVICE.health()
    except Exception as exc:
        busy = isinstance(exc, SocietyServiceBusy)
        return {
            "schema": "tfi.portal-component-status.v1",
            "component": "lineage",
            "service": "SocietyService",
            "state": "busy" if busy else "unavailable",
            "ready": False,
            "message": (
                "SocietyService 正忙，正在等待当前操作完成"
                if busy
                else "SocietyService 暂时不可用，正在重新连接"
            ),
            "retry_after_ms": 750,
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
        "health": health,
    }


def _resource_service_status_payload() -> dict:
    try:
        health = get_resource_service().status(timeout=2.25)
    except Exception as exc:
        return {
            "schema": "tfi.portal-component-status.v1",
            "component": "resource",
            "service": "ResourceService",
            "state": "unavailable",
            "ready": False,
            "message": "ResourceService 暂时不可用，正在重新连接",
            "retry_after_ms": 750,
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
        "health": health,
    }


def _runtime_config_payload(handler: SimpleHTTPRequestHandler) -> dict:
    payload = get_runtime_config()
    portal = payload.get("portal") if isinstance(payload.get("portal"), dict) else {}
    portal.update({
        "port": int(handler.server.server_address[1]),
        "instance": http_instance._PORTAL_INSTANCE,
        "build_id": http_instance._PORTAL_BUILD_ID,
    })
    payload["portal"] = portal
    return payload


def _route_health(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, _portal_health_payload(request))


def _route_favicon(request, _parsed, _actor: str):
    return http_responses._send_empty(request, 204)


def _route_users(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, list_users())


def _route_agent_context_options(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, agent_context_selection.list_options())


def _route_config(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, _runtime_config_payload(request))


def _route_summary(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, get_portal_summary())


def _route_daily_news(request, _parsed, _actor: str):
    return http_responses._serve_json(request, 200, _daily_news_payload())


def routes() -> tuple[RouteDefinition, ...]:
    """The overview API, health and the site icon."""
    return (
        RouteDefinition("GET", "/api/health", _route_health, "portal-public", phase="public"),
        RouteDefinition("GET", "/favicon.ico", _route_favicon, "portal-public", phase="public"),
        RouteDefinition("GET", "/api/users", _route_users, "portal-overview"),
        http_auth._principal_route("GET", "/api/agent-context-options", _route_agent_context_options, "portal-overview"),
        RouteDefinition("GET", "/api/config", _route_config, "portal-overview"),
        RouteDefinition("GET", "/api/summary", _route_summary, "portal-overview"),
        RouteDefinition("GET", "/api/daily-news", _route_daily_news, "portal-overview"),
    )
