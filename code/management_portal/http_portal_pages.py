"""Authenticated Portal documents and page redirects."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode

from management_common.operation_errors import OperationError
from management_portal import portal_features, script_groups
from management_portal.http_errors import instruction_error_payload
from management_portal.http_routes import RouteDefinition


def wall_page_location(query):
    """Compatibility location for former WALL documents; no domain migration."""
    parameters = {key: values[-1] for key, values in parse_qs(query, keep_blank_values=True).items()
                  if key in {"view", "project", "block", "semantic", "case", "run"}}
    if parameters.get("view") not in {"wall", "test"}:
        parameters["view"] = "test" if "case" in parameters or "run" in parameters else "wall"
    return "/development?" + urlencode(parameters)


_HOME_SCRIPT = b'<script src="/static/home.js"></script>'


def home_page(root: Path) -> bytes:
    """Home with the installed features' panels (web.home_panel) before Home's own script."""
    page = (root / "index.html").read_bytes()
    panels = b"".join(Path(fragment).read_bytes().strip() + b"\n    "
                      for _feature, fragment in portal_features.contributions("web.home_panel"))
    return page.replace(_HOME_SCRIPT, panels + _HOME_SCRIPT, 1)


class PortalPagesHttp:
    def __init__(
        self, *, root: Path, document, respond, exception, send_empty,
        lineage_html, hatg_html, settings_html, management_html,
        require_admin, workspace_adapter, resource_location, resource_not_found,
    ):
        self.root = root
        self.document = document
        self.respond = respond
        self.exception = exception
        self.send_empty = send_empty
        self.lineage_html = lineage_html
        self.hatg_html = hatg_html
        self.settings_html = settings_html
        self.management_html = management_html
        self.require_admin = require_admin
        self.workspace_adapter = workspace_adapter
        self.resource_location = resource_location
        self.resource_not_found = resource_not_found

    @staticmethod
    def _redirect(request, location):
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()

    def workbenches_create(self, request, parsed, actor):
        parameters = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        parameters["panel"] = "studio"
        return self._redirect(request, "/workbenches?" + urlencode(parameters))

    def workbenches_edit(self, request, parsed, actor):
        parameters = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        parameters["panel"] = "settings"
        return self._redirect(request, "/workbenches?" + urlencode(parameters))

    def workbenches(self, request, parsed, actor):
        return self.document(request, 200, (self.root / "workbenches.html").read_bytes(), extra_headers={"Content-Security-Policy": "frame-src 'self'; object-src 'none'"})

    def wall(self, request, parsed, actor):
        return self._redirect(request, wall_page_location(parsed.query))

    def development_native_views(self, request, parsed, actor):
        """The installed features' views of the development projection page (``web.native_view``).

        Without ``view`` the reply lists them; with ``view`` it carries that
        view's markup, with the script groups its assets name expanded
        (``script_groups.py``).  Authenticated markup only: domain APIs retain
        their own authorization.
        """
        views = {view["id"]: view for _feature, view in portal_features.contributions("web.native_view")}
        requested = (parse_qs(parsed.query).get("view") or [""])[-1]
        if not requested:
            return self.respond(request, 200, {"views": [
                {key: view[key] for key in ("id", "label", "icon", "title")} for view in views.values()]})
        if requested not in views:
            return self.respond(request, 404, {"error": f"没有安装视图 {requested}"})
        template = Path(views[requested]["template"]).read_text(encoding="utf-8")
        return self.respond(request, 200, {"html": script_groups.expand_document(template)})

    def home(self, request, parsed, actor):
        return self.document(request, 200, home_page(self.root))


    def lineage(self, request, parsed, actor):
        return self.document(request, 200, self.lineage_html())

    def hatg_panel(self, request, parsed, actor):
        return self.document(request, 200, self.hatg_html())

    def chat_workspace_session(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        surface = str((qs.get("surface") or [""])[0]).strip()
        workspace_key = str((qs.get("workspace_key") or [""])[0]).strip()
        context = {
            "project_id": str((qs.get("project_id") or [""])[0]).strip(),
            "paper_id": str((qs.get("paper_id") or [""])[0]).strip(),
            "query": str((qs.get("query") or [""])[0]).strip(),
        }
        try:
            return self.respond(request, 200, self.workspace_adapter.get_workspace(actor or "", surface, workspace_key, context))
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        except OperationError as exc:
            return self.respond(request, exc.http_status, instruction_error_payload(exc))
        except ValueError as exc:
            return self.respond(request, 400, {"error": str(exc)})
        except Exception as exc:
            return self.exception(request, exc)

    def principal_backpack(self, request, parsed, actor):
        return self.document(request, 200, (self.root / "principal_backpack.html").read_bytes())

    def settings(self, request, parsed, actor):
        return self.document(request, 200, self.settings_html())

    def portal_management(self, request, parsed, actor):
        try:
            self.require_admin(actor or "")
        except PermissionError as exc:
            return self.respond(request, 403, {"error": str(exc)})
        return self.document(request, 200, self.management_html())

    def resource_redirect(self, request, parsed, actor):
        address = unquote(parsed.path[len("/r/"):]).strip("/")
        if not address:
            return self.send_empty(request, 404)
        location = self.resource_location(address, actor or "")
        if not location:
            return self.resource_not_found(request, address)
        return self._redirect(request, location)

    def routes(self, principal_route):
        """The Portal's authenticated documents and page redirects.

        ``principal_route`` declares a route Users and Residents both call.
        """
        return (
            principal_route("GET", "/workbenches/create", self.workbenches_create, "portal-portal-get"),
            principal_route("GET", "/workbenches/edit", self.workbenches_edit, "portal-portal-get"),
            principal_route("GET", "/workbenches", self.workbenches, "portal-portal-get", page=True),
            principal_route("GET", "/workbenches/", self.workbenches, "portal-portal-get", page=True),
            principal_route("GET", "/workbenches/view", self.workbenches, "portal-portal-get", page=True),
            principal_route("GET", "/development", self.workbenches, "portal-portal-get", page=True),
            principal_route("GET", "/development/native-views", self.development_native_views, "portal-portal-get"),
            RouteDefinition("GET", "/r/", self.resource_redirect, "portal-pages-get", match="prefix"),
            RouteDefinition("GET", "/wall", self.wall, "portal-pages-get", page=True),
            RouteDefinition("GET", "/wall/", self.wall, "portal-pages-get", page=True),
            RouteDefinition("GET", "", self.home, "portal-pages-get"),
            RouteDefinition("GET", "/", self.home, "portal-pages-get", page=True),
            RouteDefinition("GET", "/index.html", self.home, "portal-pages-get", page=True),
            RouteDefinition("GET", "/lineage", self.lineage, "portal-pages-get", page=True),
            RouteDefinition("GET", "/lineage/", self.lineage, "portal-pages-get", page=True),
            RouteDefinition("GET", "/lineage.html", self.lineage, "portal-pages-get", page=True),
            RouteDefinition("GET", "/hatg-panel.html", self.hatg_panel, "portal-pages-get", page=True),
            RouteDefinition("GET", "/chat-workspace/api/session", self.chat_workspace_session, "portal-pages-get"),
            RouteDefinition("GET", "/principal-backpack", self.principal_backpack, "portal-pages-get", page=True),
            RouteDefinition("GET", "/principal-backpack/", self.principal_backpack, "portal-pages-get", page=True),
            RouteDefinition("GET", "/principal_backpack.html", self.principal_backpack, "portal-pages-get", page=True),
            RouteDefinition("GET", "/settings", self.settings, "portal-pages-get", page=True),
            RouteDefinition("GET", "/settings/", self.settings, "portal-pages-get", page=True),
            RouteDefinition("GET", "/settings.html", self.settings, "portal-pages-get", page=True),
            RouteDefinition("GET", "/portal-management", self.portal_management, "portal-pages-get", page=True),
            RouteDefinition("GET", "/portal-management/", self.portal_management, "portal-pages-get", page=True),
            RouteDefinition("GET", "/portal_management.html", self.portal_management, "portal-pages-get", page=True),
        )
