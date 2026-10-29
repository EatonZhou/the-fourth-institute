"""The Projects page (decision_46c37026e8ea).

One page shows the Project list (``/projects``) and a Project
(``/projects?project=<ID>``).  It reads and changes Projects only through
HATG instructions (ListProjects, GetProject, UpdateProject, RenameProject,
DeleteProject), so a Resident can do everything the page does.
"""
from __future__ import annotations

from pathlib import Path

from management_portal.http_routes import RouteDefinition

PAGE = Path(__file__).resolve().parent / "projects.html"
NAVIGATION = ({"id": "projects", "label": "Project", "href": "/projects", "order": 20},)
# Project links open the Project page instead of the knowledge filtered by it;
# a research direction opens at its place on its Project's research map.
RESOURCE_ROUTES = {"project": ("/projects", "project"), "direction": ("/projects", "direction")}


def routes(portal):
    """The Projects page (``web.routes``); ``portal`` is the Web shell."""
    def page(request, _parsed, _actor):
        return portal._serve_authenticated_document(request, 200, PAGE.read_bytes())

    return tuple(RouteDefinition("GET", path, page, "portal-pages-get", page=True)
                 for path in ("/projects", "/projects/"))
