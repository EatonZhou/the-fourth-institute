"""WALL API and page scripts."""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs

from management_common.operation_errors import OperationError
from management_portal import (
    http_auth, http_request_body, http_responses, page_api_auth, wall_backend,
)
from management_portal.http_routes import RouteDefinition

# WALL's view in the development projection page (web.native_view).
NATIVE_VIEW = {
    "id": "wall", "label": "WALL", "icon": "window", "title": "设计、改进与软件演化",
    "template": Path(__file__).with_name("wall.html"),
}


def api_actor(request, *, write=False):
    auth = http_auth._get_agent_api_auth(request)
    if not auth:
        raise PermissionError("WALL requires a human session or Resident credential")
    if auth.get("type") == "human" and write and not http_auth._same_origin_request(request):
        raise PermissionError("same-origin request required")
    return page_api_auth.authorize(auth)


def routes(portal):
    """The WALL API and its page scripts (``web.routes``); ``portal`` is the Web shell.

    The API checks its callers itself, so it runs before the session gate.
    """
    def get(request, parsed, actor):
        try:
            return http_responses._serve_json(request, 200, wall_backend.handle_get(
                parsed.path, parse_qs(parsed.query), api_actor(request)))
        except PermissionError as exc:
            return http_responses._serve_json(request, 403, {"error": str(exc)})
        except KeyError as exc:
            return http_responses._serve_json(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return http_responses._serve_json(request, 400, {"error": str(exc)})
        except Exception as exc:
            print(f"WALL GET failed: {exc}", file=sys.stderr)
            return http_responses._serve_json(request, 500, {"error": "WALL 暂时无法读取，请稍后重试"})

    def post(request, parsed, actor):
        try:
            payload = http_request_body._read_bounded_json_object(request, max_bytes=262_144)
            return http_responses._serve_json(request, 200, wall_backend.handle_post(
                parsed.path, payload, api_actor(request, write=True)))
        except PermissionError as exc:
            return http_responses._serve_json(request, 403, {"error": str(exc)})
        except KeyError as exc:
            return http_responses._serve_json(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return http_responses._serve_json(request, 400, {"error": str(exc)})
        except OperationError as exc:
            return http_responses._serve_exception_json(request, exc)
        except Exception as exc:
            print(f"WALL POST failed: {exc}", file=sys.stderr)
            return http_responses._serve_json(request, 500, {"error": "WALL 操作失败，请稍后重试"})

    return (
        RouteDefinition("GET", "/api/wall/", get, "wall", match="prefix", phase="public"),
        RouteDefinition("POST", "/api/wall/", post, "wall", match="prefix", phase="public"),
        *(RouteDefinition("GET", path, portal._PUBLIC_PAGES_HTTP.static, "portal-public-get", phase="public")
          for path in ("/static/wall.css", "/static/wall.js")),
    )
