"""HTTP request handling for Notice Board routes."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from management_portal import http_documents, http_responses, notice_board_backend
from management_portal.http_routes import RouteDefinition

# The Notice Board panel on Home with its dialogs, styles and script (web.home_panel).
HOME_PANEL = Path(__file__).with_name("notice_board_home.html")


class NoticeBoardHttp:
    def __init__(self, *, respond, serve_bytes):
        self.respond = respond
        self.serve_bytes = serve_bytes

    def get_image(self, request, parsed, actor):
        image_id = str((parse_qs(parsed.query).get("image_id") or [""])[0]).strip()
        try:
            data, content_type = notice_board_backend.notice_board.resolve_image(actor, image_id)
        except FileNotFoundError:
            return self.respond(request, 404, {"error": "Notice Board image not found"})
        return self.serve_bytes(request, 200, data, content_type)


def routes(portal):
    """Notice Board images (``web.routes``); posts go through the ISA."""
    http = NoticeBoardHttp(respond=http_responses._serve_json, serve_bytes=http_documents._serve_bytes)
    return (RouteDefinition("GET", "/api/notice-board/image", http.get_image, "portal-notice-board"),)

