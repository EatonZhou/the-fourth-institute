"""HTTP routes for the Resource catalog, content and indexing."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, unquote

from management_portal import semantic_search_jobs
from management_portal.society_service import SocietyServiceUnavailable
from management_portal.resources.content_payload import resolved_media_type
from management_portal.http_routes import RouteDefinition

ROOT = Path(__file__).resolve().parent


class ResourceHttp:
    def __init__(
        self, *, respond, exception, resource_error, read_json, reference_payload,
        lineage_unavailable, download_file, media_file, authorized_file, serve_bytes,
        download_disposition,
        auth_document, knowledge_html, portal_location, portal_not_found,
        status_payload, notify_index, resource_service,
    ):
        self.respond = respond
        self.exception = exception
        self.resource_error = resource_error
        self.read_json = read_json
        self.reference_payload = reference_payload
        self.lineage_unavailable = lineage_unavailable
        self.download_file = download_file
        self.media_file = media_file
        self.authorized_file = authorized_file
        self.serve_bytes = serve_bytes
        self.download_disposition = download_disposition
        self.auth_document = auth_document
        self.knowledge_html = knowledge_html
        self.portal_location = portal_location
        self.portal_not_found = portal_not_found
        self.status_payload = status_payload
        self.notify_index = notify_index
        self.resource_service = resource_service

    def semantic_searches(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resource_scope = str((qs.get('resource_scope') or [''])[0]).strip()
        actor = actor or ''
        try:
            return self.respond(request, 200, semantic_search_jobs.list_searches(semantic_search_jobs.resource_scope(resource_scope), actor))
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception(request, exc)

    def service_status(self, request, parsed, actor):
        return self.respond(request, 200, self.status_payload())

    def index_query(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        values = lambda key: [part.strip() for raw in qs.get(key) or [] for part in str(raw).split(',') if part.strip()]
        try:
            limit = int(str((qs.get('limit') or ['50'])[0]) or 50)
            offset = int(str((qs.get('offset') or ['0'])[0]) or 0)
            include_attributes = str((qs.get('include_attributes') or [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
            attribute_filters = {key.removeprefix('attribute.'): values(key) for key in qs if key.startswith('attribute.') and key.removeprefix('attribute.')}
            return self.respond(request, 200, self.resource_service().query(actor=actor or '', q=str((qs.get('q') or [''])[0]), resource_types=values('resource_type') + values('type'), facets=values('facet'), projects=values('project'), statuses=values('status'), attribute_filters=attribute_filters, limit=limit, offset=offset, include_attributes=include_attributes))
        except Exception as exc:
            return self.resource_error(request, exc)

    def index_suggest(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        try:
            limit = min(int(str((qs.get('limit') or ['30'])[0]) or 30), 100)
            return self.respond(request, 200, self.resource_service().suggest(
                actor=actor or '', q=str((qs.get('q') or [''])[0]), limit=limit,
                resource_type=str((qs.get('resource_type') or [''])[0]).strip(),
            ))
        except Exception as exc:
            return self.resource_error(request, exc)

    def index_status(self, request, parsed, actor):
        try:
            return self.respond(request, 200, self.resource_service().status())
        except Exception as exc:
            return self.resource_error(request, exc)

    def index_detail(self, request, parsed, actor, params):
        resource_id = unquote(params['resource_id']).strip()
        if not resource_id or '/' in resource_id:
            return self.respond(request, 400, {'error': 'resource_id required'})
        try:
            return self.respond(request, 200, self.resource_service().get(resource_id, actor=actor or ''))
        except Exception as exc:
            return self.resource_error(request, exc)

    def capabilities(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resource_type = str((qs.get('resource_type') or [''])[0]).strip()
        if not resource_type:
            return self.respond(request, 400, {'error': 'resource_type required'})
        try:
            return self.respond(request, 200, self.resource_service().capabilities(resource_type))
        except Exception as exc:
            return self.resource_error(request, exc)

    def resolve(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        address = str((qs.get('address') or [''])[0]).strip()
        actor = actor or ''
        if not address:
            return self.respond(request, 400, {'error': 'address required'})
        try:
            return self.respond(request, 200, self.resource_service().resolve(address, actor=actor))
        except Exception as exc:
            return self.resource_error(request, exc)

    def reference(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        address = str((qs.get('address') or [''])[0]).strip()
        actor = actor or ''
        if not address:
            return self.respond(request, 400, {'error': 'address required'})
        try:
            return self.respond(request, 200, self.reference_payload(address, actor))
        except SocietyServiceUnavailable as exc:
            return self.lineage_unavailable(request, exc)
        except Exception as exc:
            return self.resource_error(request, exc)

    def content(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        address = str((qs.get('address') or [''])[0]).strip()
        download = str((qs.get('download') or [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
        actor = actor or ''
        if not address:
            return self.respond(request, 400, {'error': 'address required'})
        try:
            resource_service = self.resource_service()
            if not download and (qs.get('preview') or [''])[0] == 'pdf':
                from .presentation_preview import MAX_SOURCE_BYTES, presentation_pdf
                data, _media_type, resolved = resource_service.read_content(
                    address, actor=actor, max_bytes=MAX_SOURCE_BYTES)
                converted = presentation_pdf(data, resolved.get('filename') or address)
                return self.media_file(request, converted, 'application/pdf')
            opened = resource_service.open_content(address, actor=actor)
            if isinstance(opened, tuple):
                data, content_type, content_resolution = opened
                content_resolution = content_resolution if isinstance(content_resolution, dict) else {}
                headers = None
                if download:
                    headers = {'Content-Disposition': self.download_disposition(content_resolution.get('filename') or address)}
                return self.serve_bytes(request, 200, data, content_type, extra_headers=headers)
            resolved = opened
            resolved_path = Path(str(resolved.get('resolved_path') or ''))
            provider_type = resolved_media_type(resolved, fallback='application/octet-stream')
            if resolved_path.is_file():
                if download:
                    return self.download_file(request, resolved_path)
                return self.media_file(request, resolved_path, provider_type)
            if resolved.get('target_kind') == 'file' and type(resolved.get('content_size')) is int and (type(resolved.get('content_mtime_ns')) is int) and callable(getattr(resource_service, 'read_content_range', None)):
                return self.authorized_file(request, resource_service=resource_service, address=address, actor=actor, resolved=resolved, content_type=provider_type, download=download)
            raise ValueError('ResourceService returned an unsupported content plan')
        except Exception as exc:
            return self.resource_error(request, exc)

    def knowledge_page(self, request, parsed, actor):
        return self.auth_document(request, 200, self.knowledge_html())

    def skill_page(self, request, parsed, actor, resource_category):
        qs = parse_qs(parsed.query)
        item_id = str((qs.get('item') or [''])[0]).strip()
        embedded_detail = str((qs.get('embedded') or [''])[0]).strip() == '1'
        if item_id and embedded_detail:
            return self.auth_document(request, 200, self.knowledge_html(resource_category=resource_category, hide_detail_graph=resource_category == 'article'))
        return self.auth_document(request, 200, (ROOT / 'resource_catalog.html').read_bytes())

    def device_page(self, request, parsed, actor):
        return self.auth_document(request, 200, (ROOT / 'resource_catalog.html').read_bytes())

    def resource_page(self, request, parsed, actor):
        address = str((parse_qs(parsed.query).get('address') or [''])[0]).strip()
        if not address:
            request.send_response(302)
            location = '/resource/knowledge'
            if parsed.query:
                location += f'?{parsed.query}'
            request.send_header('Location', location)
            request.end_headers()
            return
        location = self.portal_location(address, actor or '')
        if not location:
            return self.portal_not_found(request, address)
        request.send_response(302)
        request.send_header('Location', location)
        request.end_headers()
        return

    def semantic_search(self, request, parsed, actor, operation):
        payload = self.read_json(request)
        actor = actor or ''
        try:
            scope = semantic_search_jobs.resource_scope(payload.get('resource_scope'))
            if operation == 'start':
                result = semantic_search_jobs.start_search(scope, payload, actor)
                kind = 'created'
            elif operation == 'refresh':
                result = semantic_search_jobs.refresh_search(scope, payload, actor)
                kind = 'updated'
            else:
                result = semantic_search_jobs.delete_search(scope, payload, actor)
                kind = 'deleted'
            self.notify_index(resource_id=str(result.get('search_id') or ''), resource_type='search', kind=kind)
            return self.respond(request, 200, result)
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc) or 'semantic search not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception(request, exc)

    def index_refresh(self, request, _parsed, actor):
        payload = self.read_json(request)
        raw_adapters = payload.get('adapters')
        if isinstance(raw_adapters, str):
            adapters = [value.strip() for value in raw_adapters.split(',') if value.strip()]
        elif isinstance(raw_adapters, list):
            adapters = [str(value).strip() for value in raw_adapters if str(value).strip()]
        else:
            adapters = []
        try:
            return self.respond(request, 202, self.resource_service().refresh(adapters=adapters, path=str(payload.get('path') or '').strip(), wait=bool(payload.get('wait')), timeout=float(payload.get('timeout') or 180)))
        except Exception as exc:
            return self.resource_error(request, exc)

    def routes(self, principal_route):
        """The resource catalog, index and content API and the resource pages.

        ``principal_route`` declares a route Users and Residents both call.
        """
        return (
            RouteDefinition("GET", "/api/resource-semantic-searches", self.semantic_searches, "portal-catalog-get"),
            RouteDefinition("GET", "/resource/api/service-status", self.service_status, "portal-catalog-get"),
            RouteDefinition("GET", "/api/resource-index/query", self.index_query, "portal-catalog-get"),
            RouteDefinition("GET", "/api/resource-index/suggest", self.index_suggest, "portal-catalog-get"),
            RouteDefinition("GET", "/api/resource-index/status", self.index_status, "portal-catalog-get"),
            RouteDefinition("GET", "/api/resource-index/resources/{resource_id}", self.index_detail, "portal-resource-index", match="template"),
            principal_route("GET", "/api/resources/capabilities", self.capabilities, "portal-resources"),
            principal_route("GET", "/api/resources/resolve", self.resolve, "portal-resources"),
            principal_route("GET", "/api/resources/reference", self.reference, "portal-resources"),
            principal_route("GET", "/api/resources/content", self.content, "portal-resources"),
            RouteDefinition("GET", "/resource/knowledge", self.knowledge_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/knowledge/", self.knowledge_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/skill", (lambda request, parsed, actor: self.skill_page(request, parsed, actor, resource_category="skill")), "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/skill/", (lambda request, parsed, actor: self.skill_page(request, parsed, actor, resource_category="skill")), "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/experiment", (lambda request, parsed, actor: self.skill_page(request, parsed, actor, resource_category="experiment")), "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/experiment/", (lambda request, parsed, actor: self.skill_page(request, parsed, actor, resource_category="experiment")), "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/device", self.device_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/device/", self.device_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource", self.resource_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource/", self.resource_page, "portal-pages-get", page=True),
            RouteDefinition("GET", "/resource.html", self.resource_page, "portal-pages-get", page=True),
            RouteDefinition("POST", "/api/resource-semantic-search/start", (lambda request, parsed, actor: self.semantic_search(request, parsed, actor, operation="start")), "portal-terminal-post"),
            RouteDefinition("POST", "/api/resource-semantic-search/refresh", (lambda request, parsed, actor: self.semantic_search(request, parsed, actor, operation="refresh")), "portal-terminal-post"),
            RouteDefinition("POST", "/api/resource-semantic-search/delete", (lambda request, parsed, actor: self.semantic_search(request, parsed, actor, operation="delete")), "portal-terminal-post"),
            RouteDefinition("POST", "/api/resource-index/refresh", self.index_refresh, "portal-resources-post"),
        )
