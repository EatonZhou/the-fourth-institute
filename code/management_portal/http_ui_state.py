"""HTTP request handling for UI association routes."""

from __future__ import annotations

from urllib.parse import parse_qs

from management_portal import portal_ui_state
from management_portal.http_routes import RouteDefinition


class UiStateHttp:
    def __init__(self, *, respond, read_json):
        self.respond = respond
        self.read_json = read_json

    def _store(self):
        return portal_ui_state.get_portal_ui_state_store()

    def list_associations(self, request, parsed, actor):
        namespace = str((parse_qs(parsed.query).get("namespace") or [""])[0]).strip()
        try:
            store = self._store()
        except Exception:
            return self.respond(request, 500, {"error": "UI association storage is unavailable."})
        return self.respond(
            request, 200, {"namespace": namespace, "items": store.list(actor, namespace)}
        )

    def put(self, request, _parsed, actor):
        payload = self.read_json(request)
        try:
            store = self._store()
        except Exception:
            return self.respond(request, 500, {"error": "UI association storage is unavailable."})
        association = store.put(actor, payload.get("namespace"), payload.get("key"), payload.get("value"))
        return self.respond(request, 200, {"association": association})

    def remove(self, request, _parsed, actor):
        payload = self.read_json(request)
        try:
            store = self._store()
        except Exception:
            return self.respond(request, 500, {"error": "UI association storage is unavailable."})
        removed = store.remove(actor, payload.get("namespace"), payload.get("key"))
        return self.respond(request, 200, {"removed": removed})

    def routes(self, principal_route):
        """The UI association API.

        ``principal_route`` declares a route Users and Residents both call.
        """
        return (
            principal_route("GET", "/api/ui-state/associations", self.list_associations, "portal-ui-state"),
            principal_route("POST", "/api/ui-state/associations/put", self.put, "portal-ui-state"),
            principal_route("POST", "/api/ui-state/associations/remove", self.remove, "portal-ui-state"),
        )
