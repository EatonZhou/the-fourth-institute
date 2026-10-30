"""Knowledge HTTP endpoints and their request contract."""

from __future__ import annotations
from management_common.portal_paths import path as portal_path

from urllib.parse import parse_qs, parse_qsl, urlencode

from management_portal import semantic_search_jobs
from management_portal.http_routes import RouteDefinition


class KnowledgeHttp:
    def __init__(
        self, *, respond, exception_response, resource_error,
        media_file, send_empty, session_actor, require_resource,
        filter_graph, notify_resource_index, resource_service, resource_views,
        knowledge_backend, knowledge_item_chat_backend,
        knowledge_archive_backend, read_json,
    ):
        self.respond = respond
        self.exception_response = exception_response
        self.resource_error = resource_error
        self.media_file = media_file
        self.send_empty = send_empty
        self.session_actor = session_actor
        self.require_resource = require_resource
        self.filter_graph = filter_graph
        self.notify_resource_index = notify_resource_index
        self.resource_service = resource_service
        self.resource_views = resource_views
        self.knowledge_backend = knowledge_backend
        self.knowledge_item_chat_backend = knowledge_item_chat_backend
        self.knowledge_archive_backend = knowledge_archive_backend
        self.read_json = read_json

    def post_route(self, operation):
        def route(request, parsed, actor):
            payload = self.read_json(request)
            session_actor = self.session_actor(request) or ""
            try:
                kb_id = str(payload.get("kb_id") or "").strip()
                if kb_id:
                    self.require_resource(kb_id, session_actor)
                return operation(request, payload, session_actor)
            except self.knowledge_archive_backend().KnowledgeArchiveServiceUnavailable as exc:
                return self.respond(request, 503, {"error": str(exc), "service": "knowledge-archive-agent.service"})
            except KeyError as exc:
                return self.respond(request, 404, {"error": str(exc) or "not found"})
            except PermissionError as exc:
                return self.respond(request, 403, {"error": str(exc)})
            except ValueError as exc:
                return self.respond(request, 400, {"error": str(exc)})
            except Exception as exc:
                return self.exception_response(request, exc)
        return route

    def get_home(self, request, parsed, actor):
        query = parse_qsl(parsed.query, keep_blank_values=True)
        requested_type = next((str(value).strip() for key, value in query if key == 'type'), '')
        category = requested_type if requested_type in {'skill', 'experiment'} else 'knowledge'
        if category != 'knowledge':
            query = [(key, value) for key, value in query if key != 'type']
        location = f'/resource/{category}'
        if query:
            location += f'?{urlencode(query)}'
        request.send_response(302)
        request.send_header('Location', location)
        request.end_headers()
        return

    def get_api_index(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        active_only = str(qs.get('active_only', ['1'])[0]).strip() != '0'
        resource_surface = str(qs.get('surface', [''])[0]).strip()
        semantic_id = str(qs.get('semantic', [''])[0]).strip()
        offset = int(str(qs.get('offset', ['0'])[0]) or 0)
        raw_limit = str(qs.get('limit', [''])[0]).strip()
        limit = int(raw_limit) if raw_limit else None
        refresh = str(qs.get('refresh', [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
        try:
            wait_seconds = float(str(qs.get('wait', ['0'])[0]) or 0)
        except ValueError:
            wait_seconds = 0
        try:
            index = self.resource_views().knowledge_index(active_only=active_only, semantic_search_id=semantic_id, offset=offset, limit=limit, refresh=refresh, wait_seconds=wait_seconds, excluded_resource_types=('skill', 'experiment') if resource_surface == 'resource-knowledge' else (), actor=self.session_actor(request) or '', service=self.resource_service())
        except KeyError:
            return self.respond(request, 404, {'error': 'semantic search not found'})
        return self.respond(request, 200, index)

    def get_api_item(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        kb_id = str(qs.get('kb_id', [''])[0]).strip()
        try:
            resource_service = self.resource_service()
            resource_type = resource_service.validate_id(kb_id).resource_type
            if resource_type == 'article':
                self.require_resource(kb_id, self.session_actor(request) or '')
                item = self.knowledge_backend().resolve_item_record(kb_id, active_only=False)
                return self.respond(request, 200, self.knowledge_backend().public_item_record(item))
            return self.respond(request, 200, self.resource_views().knowledge_item(kb_id, service=resource_service, actor=self.session_actor(request) or ''))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc) or 'knowledge item not found'})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})

    def get_api_semantic_searches(self, request, parsed, actor):
        actor = self.session_actor(request) or ''
        return self.respond(request, 200, semantic_search_jobs.list_searches('knowledge', actor))

    def get_api_graph(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        refresh = str(qs.get('refresh', [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
        try:
            return self.respond(request, 200, self.filter_graph(self.knowledge_backend().build_knowledge_graph(refresh=refresh, actor=self.session_actor(request) or ''), self.session_actor(request) or ''))
        except Exception as exc:
            return self.exception_response(request, exc)

    def get_api_detail(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        address = str((qs.get('kb_id') or [''])[0]).strip()
        if not address:
            return self.respond(request, 400, {'error': 'knowledge address required'})
        try:
            return self.respond(request, 200, self.resource_views().knowledge_detail(address, service=self.resource_service(), actor=self.session_actor(request) or ''))
        except Exception as exc:
            return self.resource_error(request, exc)

    def get_api_item_graph(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        kb_id = str(qs.get('kb_id', [''])[0]).strip()
        if not kb_id:
            return self.respond(request, 400, {'error': 'kb_id required'})
        try:
            self.require_resource(kb_id, self.session_actor(request) or '')
            return self.respond(request, 200, self.filter_graph(self.knowledge_backend().build_item_graph(kb_id, actor=self.session_actor(request) or ''), self.session_actor(request) or ''))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc) or 'knowledge item not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def get_api_item_chat(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        kb_id = str(qs.get('kb_id', [''])[0]).strip()
        if not kb_id:
            return self.respond(request, 400, {'error': 'kb_id required'})
        actor = self.session_actor(request) or ''
        try:
            self.require_resource(kb_id, actor)
            return self.respond(request, 200, self.knowledge_item_chat_backend().knowledge_item_chat_backend.get_state(actor, kb_id))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc) or 'knowledge item not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def get_api_item_chat_attachment_image(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        raw_path = str(qs.get('path', [''])[0]).strip()
        try:
            media_path, content_type = self.knowledge_item_chat_backend().knowledge_item_chat_backend.resolve_attachment_image(raw_path, self.session_actor(request) or '')
            return self.media_file(request, media_path, content_type)
        except FileNotFoundError:
            return self.send_empty(request, 404)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def get_api_archive_jobs(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        actor = self.session_actor(request) or ''
        limit = int(str((qs.get('limit') or ['20'])[0]) or 20)
        try:
            return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.list_jobs(actor, limit=limit))
        except self.knowledge_archive_backend().KnowledgeArchiveServiceUnavailable as exc:
            return self.respond(request, 503, {'error': str(exc), 'service': 'knowledge-archive-agent.service'})

    def get_api_archive_job(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        actor = self.session_actor(request) or ''
        archive_id = str((qs.get('id') or [''])[0]).strip()
        if not archive_id:
            return self.respond(request, 400, {'error': 'archive id required'})
        try:
            return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.get_job(actor, archive_id))
        except self.knowledge_archive_backend().KnowledgeArchiveServiceUnavailable as exc:
            return self.respond(request, 503, {'error': str(exc), 'service': 'knowledge-archive-agent.service'})
        except KeyError:
            return self.respond(request, 404, {'error': 'archive job not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.exception_response(request, exc)

    def post_detail_save(self, request, payload, actor):
        address = str(payload.get('kb_id') or '').strip()
        result = self.knowledge_backend().update_item_text(address, str(payload.get('text') or ''), actor=actor)
        self.notify_resource_index(path=str(result.get('path') or ''), resource_id=address.partition('/')[0], kind='updated', wait=True)
        return self.respond(request, 200, result)

    def post_detail_delete(self, request, payload, actor):
        kb_id = str(payload.get('kb_id') or '').strip()
        result = self.knowledge_backend().delete_item(kb_id, actor=actor)
        self.notify_resource_index(path=str(result.get('path') or ''), resource_id=kb_id, resource_type=str(result.get('resource_type') or ''), kind='deleted', wait=True)
        return self.respond(request, 200, result)

    def post_chat_prepare(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_item_chat_backend().knowledge_item_chat_backend.prepare(actor, str(payload.get('kb_id') or ''), str(payload.get('message') or '')))

    def post_chat(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_item_chat_backend().knowledge_item_chat_backend.send_message(actor, str(payload.get('kb_id') or '').strip(), str(payload.get('content') or ''), payload.get('attachments') if isinstance(payload.get('attachments'), list) else [], payload.get('context_refs') if isinstance(payload.get('context_refs'), dict) else {}, message_id=str(payload.get('message_id') or '')))

    def post_search_start(self, request, payload, actor):
        result = semantic_search_jobs.start_search('knowledge', payload, actor)
        self.notify_resource_index(resource_id=str(result.get('search_id') or ''), resource_type='search', kind='created')
        return self.respond(request, 200, result)

    def post_search_refresh(self, request, payload, actor):
        result = semantic_search_jobs.refresh_search('knowledge', payload, actor)
        self.notify_resource_index(resource_id=str(result.get('search_id') or ''), resource_type='search', kind='updated')
        return self.respond(request, 200, result)

    def post_search_delete(self, request, payload, actor):
        result = semantic_search_jobs.delete_search('knowledge', payload, actor)
        self.notify_resource_index(resource_id=str(result.get('search_id') or ''), resource_type='search', kind='deleted')
        return self.respond(request, 200, result)

    def post_archive_create(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.create_job(actor, payload))

    def post_archive_chat(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.send_message(actor, str(payload.get('archive_id') or '').strip(), str(payload.get('content') or ''), message_id=str(payload.get('message_id') or '').strip(), input_media=payload.get('input_media') if isinstance(payload.get('input_media'), list) else []))

    def post_archive_candidates(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.update_candidates(actor, str(payload.get('archive_id') or '').strip(), payload.get('candidates') if isinstance(payload.get('candidates'), list) else []))

    def post_archive_drafts(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.generate_drafts(actor, str(payload.get('archive_id') or '').strip()))

    def post_archive_apply(self, request, payload, actor):
        result = self.knowledge_archive_backend().knowledge_archive_backend.apply_drafts(actor, str(payload.get('archive_id') or '').strip(), payload.get('draft_ids') if isinstance(payload.get('draft_ids'), list) else [])
        applied = result.get('applied') if isinstance(result, dict) else []
        paths = [str(item.get('path') or '').strip() for item in (applied if isinstance(applied, list) else []) if isinstance(item, dict)]
        for path in paths:
            if path:
                self.notify_resource_index(path=path, kind='created')
        self.notify_resource_index(path=str(portal_path("share_root") / "Projects"), kind='created', wait=True)
        return self.respond(request, 200, result)

    def post_archive_draft_save(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.update_draft_content(actor, str(payload.get('archive_id') or '').strip(), str(payload.get('draft_id') or '').strip(), str(payload.get('content') or '')))

    def post_archive_delete(self, request, payload, actor):
        return self.respond(request, 200, self.knowledge_archive_backend().knowledge_archive_backend.delete_job(actor, str(payload.get('archive_id') or '').strip()))

    def routes(self):
        """The Knowledge pages and API."""
        return (
            RouteDefinition("GET", "/knowledge", self.get_home, "knowledge"),
            RouteDefinition("GET", "/knowledge/", self.get_home, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/index", self.get_api_index, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/item", self.get_api_item, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/semantic-searches", self.get_api_semantic_searches, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/graph", self.get_api_graph, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/detail", self.get_api_detail, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/item-graph", self.get_api_item_graph, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/item-chat", self.get_api_item_chat, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/item-chat/attachment-image", self.get_api_item_chat_attachment_image, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/archive/jobs", self.get_api_archive_jobs, "knowledge"),
            RouteDefinition("GET", "/knowledge/api/archive/job", self.get_api_archive_job, "knowledge"),
            RouteDefinition("POST", "/knowledge/api/detail/save", self.post_route(self.post_detail_save), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/detail/delete", self.post_route(self.post_detail_delete), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/item-chat/prepare", self.post_route(self.post_chat_prepare), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/item-chat", self.post_route(self.post_chat), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/semantic-search/start", self.post_route(self.post_search_start), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/semantic-search/refresh", self.post_route(self.post_search_refresh), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/semantic-search/delete", self.post_route(self.post_search_delete), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/create", self.post_route(self.post_archive_create), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/chat", self.post_route(self.post_archive_chat), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/candidates", self.post_route(self.post_archive_candidates), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/drafts", self.post_route(self.post_archive_drafts), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/apply", self.post_route(self.post_archive_apply), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/draft/save", self.post_route(self.post_archive_draft_save), "knowledge"),
            RouteDefinition("POST", "/knowledge/api/archive/delete", self.post_route(self.post_archive_delete), "knowledge"),
        )
