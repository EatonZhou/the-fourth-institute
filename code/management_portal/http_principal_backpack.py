"""Principal Backpack HTTP operations, one operation per registered route."""

from __future__ import annotations

import logging
from urllib.parse import parse_qs

from management_portal.hatg.contracts import InstructionError
from management_portal.resources.errors import ResourceOperationError, ResourceServiceUnavailable
from management_portal.http_routes import RouteDefinition


_LOG = logging.getLogger(__name__)


class PrincipalBackpackHttp:
    def __init__(self, *, respond, read_json, session_actor, backend, known_users):
        self.respond = respond
        self.read_json = read_json
        self.session_actor = session_actor
        self.backend = backend
        self.known_users = known_users

    def _run(self, request, operation, action):
        principal = self.session_actor(request) or ""
        try:
            result = action(self.backend(), principal)
            return self.respond(request, 200, result)
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except InstructionError as exc:
            return self.respond(request, exc.http_status, {"error": str(exc), "code": exc.code, "details": exc.details})
        except ResourceOperationError as exc:
            return self.respond(request, exc.http_status, {"error": str(exc), "code": exc.code, "details": exc.details})
        except (KeyError, FileNotFoundError):
            return self.respond(request, 404, {"error": "身份不存在或不可读取"})
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except ResourceServiceUnavailable:
            _LOG.exception("Principal backpack resource service unavailable: %s", operation)
            message = "资源服务暂不可用，请稍后重试。"
            return self.respond(request, 503, {"error": message, "code": "resource_service_unavailable"})
        except Exception:
            _LOG.exception("Principal backpack request failed: %s", operation)
            return self.respond(request, 503, {"error": "背包数据暂不可用，请稍后重试"})


    def write_not_allowed(self, request, _parsed, _actor):
        return self.respond(request, 405, {"error": "此背包接口不接受写入"})

    def not_found(self, request, _parsed, _actor):
        return self.respond(request, 404, {"error": "未知背包接口"})




    def catalog_get(self, request, parsed, _actor):
        def action(backend, principal):
            query = parse_qs(parsed.query, keep_blank_values=True)
            return backend.catalog(principal, query=(query.get("q") or [""])[0],
                                   offset=int((query.get("offset") or ["0"])[0]),
                                   limit=int((query.get("limit") or ["60"])[0]))
        return self._run(request, "catalog", action)

    def detail_get(self, request, parsed, _actor):
        def action(backend, principal):
            query = parse_qs(parsed.query, keep_blank_values=True)
            return backend.detail(principal, (query.get("principal") or [principal])[0])
        return self._run(request, "detail", action)

    def routes(self):
        """The principal backpack API."""
        return (
            RouteDefinition("GET", "/api/principal-backpack/catalog", self.catalog_get, "portal-principal-backpack"),
            RouteDefinition("GET", "/api/principal-backpack/detail", self.detail_get, "portal-principal-backpack"),
            RouteDefinition("GET", "/api/principal-backpack/", self.not_found, "portal-principal-backpack", match="prefix_fallback"),
            RouteDefinition("POST", "/api/principal-backpack/catalog", self.write_not_allowed, "portal-principal-backpack"),
            RouteDefinition("POST", "/api/principal-backpack/detail", self.write_not_allowed, "portal-principal-backpack"),
        )
