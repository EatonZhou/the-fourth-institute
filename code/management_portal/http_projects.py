"""The Projects page (decision_46c37026e8ea).

One page shows the Project list (``/projects``) and a Project
(``/projects?project=<ID>``). Project management uses HATG instructions;
the private discussion list joins current resource links and the caller's
existing discussion bindings through their authorized readers.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from management_portal.http_routes import RouteDefinition

PAGE = Path(__file__).resolve().parent / "projects.html"
NAVIGATION = ({"id": "projects", "label": "Project", "href": "/projects", "order": 20},)
# Project links open the Project page instead of the knowledge filtered by it;
# a research direction opens at its place on its Project's research map.
RESOURCE_ROUTES = {"project": ("/projects", "project"), "direction": ("/projects", "direction")}


def routes(portal):
    """The Projects page (``web.routes``); ``portal`` is the Web shell."""
    # ResourceService reads RESOURCE_ROUTES from this module; only the Web shell
    # that serves the page loads the document stack.
    from management_portal import http_documents

    def page(request, _parsed, _actor):
        return http_documents._serve_authenticated_document(request, 200, PAGE.read_bytes())

    def discussions(request, parsed, actor):
        from . import http_responses, project_resource_discussions
        from .resources import get_resource_service
        query = parse_qs(parsed.query)
        try:
            result = project_resource_discussions.list_discussions(
                get_resource_service(), actor, str(query.get("project_id", [""])[0]),
                offset=int(query.get("offset", ["0"])[0]))
        except PermissionError as exc:
            return http_responses._serve_json(request, 403, {"error": str(exc)})
        except (KeyError, FileNotFoundError) as exc:
            return http_responses._serve_json(request, 404, {"error": str(exc)})
        except ValueError as exc:
            return http_responses._serve_json(request, 400, {"error": str(exc)})
        return http_responses._serve_json(request, 200, result)

    return (*tuple(RouteDefinition("GET", path, page, "portal-pages-get", page=True)
                   for path in ("/projects", "/projects/")),
            RouteDefinition("GET", "/projects/api/discussions", discussions, "projects"))
