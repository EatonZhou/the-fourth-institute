"""Projects page and index HTTP routes."""

from pathlib import Path
from urllib.parse import parse_qs, unquote

from management_portal import resource_views
from management_portal.http_routes import RouteDefinition
from management_portal.research_maps import list_research_maps

PROJECT_BOARD_HTML = Path(__file__).resolve().parents[1] / "project_board_lan" / "projects_board.html"


class ProjectsHttp:
    def __init__(self, *, session_actor, resource_service, project_html, document,
                 respond, error, empty):
        self.session_actor = session_actor
        self.resource_service = resource_service
        self.project_html = project_html
        self.document = document
        self.respond = respond
        self.error = error
        self.empty = empty

    def get(self, request, parsed, _route_actor):
        subpath = parsed.path[len('/projects'):] or '/'
        actor = self.session_actor(request) or ''
        resource_service = self.resource_service()
        if subpath in {'', '/'} or (subpath.startswith('/') and '/' not in unquote(subpath[1:]).strip('/') and (not subpath.startswith('/api/'))):
            return self.document(request, 200, self.project_html())
        if subpath == '/api/projects':
            try:
                payload = resource_views.project_index(actor=actor, service=resource_service)
            except Exception as exc:
                return self.error(request, exc)
            return self.respond(request, 200, payload)
        if subpath == '/api/research-maps':
            try:
                project_id = (parse_qs(parsed.query).get('project') or [''])[0].strip()
                maps = list_research_maps(project_id, actor=actor, service=resource_service)
            except Exception as exc:
                return self.error(request, exc)
            return self.respond(request, 200, {'project_id': project_id, 'maps': maps})
        return self.empty(request, 404)


NAVIGATION = ({"id": "projects", "label": "Project", "href": "/projects/", "order": 20},)


def routes(portal):
    """The Project Board, Project Pages and their API (``web.routes``); ``portal`` is the Web shell."""
    http = ProjectsHttp(
        session_actor=lambda request: portal._get_session_username(request),
        resource_service=lambda: portal.get_resource_service(),
        project_html=lambda: PROJECT_BOARD_HTML.read_bytes(),
        document=lambda *args: portal._serve_authenticated_document(*args),
        respond=lambda *args: portal._serve_json(*args),
        error=lambda request, exc: portal._serve_exception_json(request, exc),
        empty=lambda *args: portal._send_empty(*args),
    )
    return (
        RouteDefinition('GET', '/projects', http.get, 'projects', page=True),
        RouteDefinition('GET', '/projects/', http.get, 'projects', page=True),
        RouteDefinition('GET', '/projects/', http.get, 'projects', match='prefix_fallback'),
    )
