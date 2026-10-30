"""Lineage HTTP routes."""

from urllib.parse import parse_qs

from management_portal.http_routes import RouteDefinition


class LineageHttp:
    def __init__(self, *, respond, exception_response, service_unavailable,
                 session_actor, service_status, lineage, unavailable_error):
        self.respond = respond
        self.exception_response = exception_response
        self.service_unavailable = service_unavailable
        self.session_actor = session_actor
        self.service_status = service_status
        self.lineage = lineage
        self.unavailable_error = unavailable_error

    @staticmethod
    def _param(qs, key):
        return str((qs.get(key) or [""])[0]).strip()

    def get_route(self, operation):
        def route(request, parsed, _route_actor):
            qs = parse_qs(parsed.query)
            actor = self.session_actor(request) or ""
            try:
                return operation(request, qs, actor)
            except self.unavailable_error as exc:
                return self.service_unavailable(request, exc)
            except Exception as exc:
                return self.exception_response(request, exc)
        return route

    def status(self, request, qs, actor):
        return self.respond(request, 200, self.service_status())

    def lineages(self, request, qs, actor):
        return self.respond(request, 200, self.lineage().list_lineages(actor))

    def node_message_counts(self, request, qs, actor):
        lineage_id = self._param(qs, "id")
        if not lineage_id:
            return self.respond(request, 400, {"error": "Lineage id required"})
        return self.respond(request, 200, self.lineage().get_node_message_counts(lineage_id, actor))

    def node_metadata(self, request, qs, actor):
        lineage_id = self._param(qs, "lineage_id")
        resident_id = self._param(qs, "resident_id")
        if not lineage_id or not resident_id:
            return self.respond(request, 400, {"error": "Lineage id and resident_id required"})
        return self.respond(request, 200, self.lineage().get_lineage_node_metadata(lineage_id, resident_id, actor))

    def routes(self, principal_route):
        """The Lineage API.

        ``principal_route`` declares a route Users and Residents both call.
        """
        return (
            RouteDefinition("GET", "/lineage/api/service-status", self.get_route(self.status), "lineage"),
            principal_route("GET", "/lineage/api/lineages", self.get_route(self.lineages), "lineage"),
            RouteDefinition("GET", "/lineage/api/node-message-counts", self.get_route(self.node_message_counts), "lineage"),
            RouteDefinition("GET", "/lineage/api/node-metadata", self.get_route(self.node_metadata), "lineage"),
        )
