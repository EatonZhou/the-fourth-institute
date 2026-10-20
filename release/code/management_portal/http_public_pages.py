"""Public Portal pages and allowlisted static assets."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import parse_qs


_APPLICATION_JS = {
    "portal_projections.js", "projection_host.js", "projection_sdk.js",
    "workbench_studio.js",
}
# These reviewed host dependencies have nested paths. This is deliberately an
# exact map, not a public mount of the projection library registry.
PUBLIC_SHARED_STATIC_ASSETS = {
    "/static/projection_libraries/portal-graph/1.0.0/geometry.js":
        "projection_libraries/portal-graph/1.0.0/geometry.js",
}


class PublicPagesHttp:
    def __init__(self, *, root, check_session, serve_bytes, serve_static,
                 send_empty, prepare_login, prepare_verify, request_cookie,
                 challenge_cookie, challenge_username, clear_challenge,
                 route_script, restart_notice, respond, media_content_type):
        self.root = root
        self.check_session = check_session
        self.serve_bytes = serve_bytes
        self.serve_static = serve_static
        self.send_empty = send_empty
        self.prepare_login = prepare_login
        self.prepare_verify = prepare_verify
        self.request_cookie = request_cookie
        self.challenge_cookie = challenge_cookie
        self.challenge_username = challenge_username
        self.clear_challenge = clear_challenge
        self.route_script = route_script
        self.restart_notice = restart_notice
        self.respond = respond
        self.media_content_type = media_content_type

    @staticmethod
    def _redirect(request, location):
        request.send_response(302)
        request.send_header("Location", location)
        request.end_headers()

    def login(self, request, parsed, _actor):
        if self.check_session(request):
            return self._redirect(request, "/")
        return self.serve_bytes(request, 200, self.prepare_login("error" in parse_qs(parsed.query)), "text/html; charset=utf-8")

    def login_verify(self, request, parsed, _actor):
        if self.check_session(request):
            return self._redirect(request, "/")
        token = self.request_cookie(request, self.challenge_cookie)
        if not self.challenge_username(token):
            request.send_response(302)
            self.clear_challenge(request)
            request.send_header("Location", "/login")
            request.end_headers()
            return
        return self.serve_bytes(request, 200, self.prepare_verify("error" in parse_qs(parsed.query)), "text/html; charset=utf-8")

    def forgot_password(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, (self.root / "forgot_password.html").read_bytes(),
                                "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})

    def reset_password(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, (self.root / "reset_password.html").read_bytes(),
                                "text/html; charset=utf-8", extra_headers={"Referrer-Policy": "no-referrer"})

    def resource_routes(self, request, _parsed, _actor):
        return self.serve_bytes(request, 200, self.route_script(), "text/javascript; charset=utf-8")

    def restart_notice_get(self, request, _parsed, _actor):
        return self.respond(request, 200, self.restart_notice())

    def static(self, request, parsed, _actor):
        filename = Path(parsed.path).name
        if parsed.path in PUBLIC_SHARED_STATIC_ASSETS:
            path = self.root / "static" / PUBLIC_SHARED_STATIC_ASSETS[parsed.path]
        else:
            path = self.root / "static" / filename
        if filename.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif filename.endswith(".json"):
            content_type = "application/json; charset=utf-8"
        elif filename in _APPLICATION_JS:
            content_type = "application/javascript; charset=utf-8"
        else:
            content_type = "text/javascript; charset=utf-8"
        return self.serve_static(request, path, content_type)

    def vendor(self, request, parsed, _actor):
        rel = parsed.path.removeprefix("/static/vendor/").strip("/")
        if not rel or "\\" in rel:
            return self.send_empty(request, 404)
        root = (self.root / "static" / "vendor").resolve(strict=False)
        path = (root / rel).resolve(strict=False)
        if root not in path.parents or not path.is_file():
            return self.send_empty(request, 404)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix in {".js", ".mjs"}:
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix in {".woff2", ".woff", ".ttf"}:
            content_type = {".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf"}[path.suffix]
        return self.serve_static(request, path, content_type)

    def asset(self, request, parsed, _actor):
        rel = parsed.path.removeprefix("/static/assets/").strip("/")
        if not rel or "/" in rel or "\\" in rel:
            return self.send_empty(request, 404)
        root = (self.root / "static" / "assets").resolve(strict=False)
        path = (root / rel).resolve(strict=False)
        if root not in path.parents or not path.is_file():
            return self.send_empty(request, 404)
        content_type = self.media_content_type(path)
        if not content_type:
            return self.send_empty(request, 404)
        return self.serve_static(request, path, content_type)
