"""The lab's RoboLab site in the site navigation (web.navigation).

RoboLab is served beside the Portal at ``/cloud-lab/`` and stays in Dev;
packages leave this module out (decision_15aab18c0ab3).
"""

import mimetypes
from pathlib import Path
from urllib.parse import unquote

from management_common.portal_paths import path as portal_path
from management_common.site_config import read_site_config
from management_portal.http_routes import RouteDefinition

NAVIGATION = ({"id": "robolab", "label": "RoboLab", "href": "/cloud-lab/", "order": 50},)


def frontend_root() -> Path:
    """The deployment's existing build; RoboLab is not part of release packages."""
    settings = read_site_config().get("deployment", {}).get("robolab", {})
    default = portal_path("share_root") / "Projects" / "RoboLab" / "src" / "frontend" / "dist"
    return Path(settings.get("frontend_root") or default).resolve()


def frontend_file(path: str) -> Path | None:
    root = frontend_root()
    relative = unquote(path.removeprefix("/cloud-lab/"))
    candidate = (root / (relative or "index.html")).resolve()
    if not candidate.is_relative_to(root):
        return None
    if candidate.is_file():
        return candidate
    # Browser history routes belong to the SPA; missing assets remain 404.
    if not Path(relative).suffix and (root / "index.html").is_file():
        return root / "index.html"
    return None


def routes(portal):
    """Serve the installed SPA even when the gateway routes directly to Portal.

    The deployment's reverse proxy continues to own /cloud-lab-api/ (including
    WebSockets); this adapter serves only RoboLab's existing static frontend.
    """
    def page(request, parsed, _actor):
        if parsed.path == "/cloud-lab":
            return portal._serve_payload(request, 308, b"", "text/plain", extra_headers={"Location": "/cloud-lab/"})
        target = frontend_file(parsed.path)
        if target is None:
            return portal._send_empty(request, 404)
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return portal._serve_static_file(request, target, content_type)

    return (
        RouteDefinition("GET", "/cloud-lab", page, "robolab", page=True),
        RouteDefinition("GET", "/cloud-lab/", page, "robolab", page=True),
        RouteDefinition("GET", "/cloud-lab/", page, "robolab", match="prefix_fallback"),
    )
