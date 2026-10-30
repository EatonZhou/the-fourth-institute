"""Resource Backup page and API."""

from __future__ import annotations

from pathlib import Path

from management_portal import (
    http_documents, http_request_body, http_responses, resource_backup_backend,
)
from management_portal.http_routes import RouteDefinition

ROOT = Path(__file__).resolve().parent
# Backup is a maintenance page in the Resource menu, with no resource type of its own (web.navigation).
NAVIGATION = ({"id": "backup", "label": "Backup", "href": "/resource/backup", "section": "resource"},)


def routes(portal):
    """The Resource Backup page, overview and requests (``web.routes``); ``portal`` is the Web shell."""
    def overview(request, parsed, actor):
        try:
            return http_responses._serve_json(request, 200, resource_backup_backend.get_overview(actor or ''))
        except Exception as exc:
            return http_responses._serve_exception_json(request, exc)

    def backup_runs(request, _parsed, actor):
        payload = http_request_body._read_request_json(request)
        try:
            result = resource_backup_backend.request_backup(str(payload.get('profile') or ''), actor)
            return http_responses._serve_json(request, 202, result)
        except Exception as exc:
            return http_responses._serve_exception_json(request, exc)

    def verifications(request, _parsed, actor):
        payload = http_request_body._read_request_json(request)
        try:
            result = resource_backup_backend.request_verification(str(payload.get('mode') or ''), actor)
            return http_responses._serve_json(request, 202, result)
        except Exception as exc:
            return http_responses._serve_exception_json(request, exc)

    def page(request, parsed, actor):
        return http_documents._serve_authenticated_document(request, 200, (ROOT / 'resource_backup.html').read_bytes())

    return (
        RouteDefinition('GET', '/api/resource-backup/overview', overview, 'portal-catalog-get'),
        RouteDefinition('POST', '/api/resource-backup/runs', backup_runs, 'portal-resources-post'),
        RouteDefinition('POST', '/api/resource-backup/verifications', verifications, 'portal-resources-post'),
        *(RouteDefinition('GET', path, page, 'portal-pages-get', page=True)
          for path in ('/resource/backup', '/resource/backup/')),
    )
