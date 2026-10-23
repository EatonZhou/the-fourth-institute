"""HTTP operations for the Agent Workbench surface."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs

from management_portal.society_service import SocietyServiceUnavailable
from management_portal.society_service.errors import SocietyServiceBusy
from management_portal.resources.errors import ResourceServiceUnavailable


class AgentWorkbenchHttp:
    def __init__(
        self, *, respond, error, session_actor, lineage_unavailable,
        link_permission_denied, media_file,
        download_file, send_empty, media_content_type, resolved_path,
        lineage_favorite_payload, resident_sidebar_snapshot,
        resident_api, execution_api, workspace_api, watchboard_applications, resident_sidebar_backend,
        resident_chat_projection, local_terminal_backend, knowledge_backend,
        read_json, read_multipart, multipart_body_limit, body_too_large,
        json_max_bytes, file_save_json_max_bytes, chat_image_max_bytes,
        chat_attachment_max_bytes, discovery_client, recommendation_backend,
        forget_favorite=None,
    ):
        self.respond = respond
        self.error = error
        self.session_actor = session_actor
        self.lineage_unavailable = lineage_unavailable
        self.link_permission_denied = link_permission_denied
        self.media_file = media_file
        self.download_file = download_file
        self.send_empty = send_empty
        self.media_content_type = media_content_type
        self.resolved_path = resolved_path
        self.lineage_favorite_payload = lineage_favorite_payload
        self.forget_favorite = forget_favorite
        self.resident_sidebar_snapshot = resident_sidebar_snapshot
        self.resident_api = resident_api
        self.execution_api = execution_api
        self.workspace_api = workspace_api
        self.watchboard_applications = watchboard_applications
        self.resident_sidebar_backend = resident_sidebar_backend
        self.resident_chat_projection = resident_chat_projection
        self.local_terminal_backend = local_terminal_backend
        self.knowledge_backend = knowledge_backend
        self.read_json = read_json
        self.read_multipart = read_multipart
        self.multipart_body_limit = multipart_body_limit
        self.body_too_large = body_too_large
        self.json_max_bytes = json_max_bytes
        self.file_save_json_max_bytes = file_save_json_max_bytes
        self.chat_image_max_bytes = chat_image_max_bytes
        self.chat_attachment_max_bytes = chat_attachment_max_bytes
        self.discovery_client = discovery_client
        self.recommendation_backend = recommendation_backend

    def post_route(self, operation, *, file_save=False, attachment=None):
        """Bind a POST operation to its body contract at route registration."""
        def route(request, parsed, actor):
            try:
                content_type = str(request.headers.get("Content-Type") or "").casefold()
                if attachment and content_type.startswith("multipart/form-data"):
                    size = self.chat_image_max_bytes if attachment == "image" else self.chat_attachment_max_bytes
                    payload = self.read_multipart(request, max_bytes=self.multipart_body_limit(size))
                else:
                    size = self.file_save_json_max_bytes if file_save else self.json_max_bytes
                    payload = self.read_json(request, max_bytes=size)
            except self.body_too_large as exc:
                return self.respond(request, 413, {"error": str(exc)})
            return operation(request, payload, self.session_actor(request) or "")
        return route

    def _discovery_result(self, request, operation):
        try:
            result = operation()
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def discovery_contexts(self, request, payload, actor):
        resident_ids = payload.get("resident_ids") if isinstance(payload.get("resident_ids"), list) else []
        return self._discovery_result(request, lambda: self.discovery_client().contexts(actor=actor, resident_ids=resident_ids))

    def discovery_routing_residents_search(self, request, payload, actor):
        return self._discovery_result(request, lambda: self.discovery_client().search_directory(
            actor=actor, query=str(payload.get("query") or ""), limit=int(payload.get("limit") or 30),
        ))

    def discovery_routing_submit(self, request, payload, actor):
        return self._discovery_result(request, lambda: self.discovery_client().submit_routing_eval_correction(
            actor=actor,
            query=str(payload.get("query") or ""),
            predicted=payload.get("predicted") if isinstance(payload.get("predicted"), list) else [],
            ground_truth_resident_id=str(payload.get("ground_truth_resident_id") or ""),
            ground_truth_action=str(payload.get("ground_truth_action") or ""),
            rationale=str(payload.get("rationale") or ""),
        ))

    def discovery_recommend_latest(self, request, payload, actor):
        return self._discovery_result(request, lambda: self.recommendation_backend().latest_recommendation(actor=actor))

    def discovery_recommend_result(self, request, payload, actor):
        return self._discovery_result(request, lambda: self.recommendation_backend().recommendation_result(
            actor=actor,
            resident_id=str(payload.get("resident_id") or ""),
            message_id=str(payload.get("message_id") or ""),
        ))

    def discovery_recommend(self, request, payload, actor):
        resident_ids = payload.get("resident_ids") if isinstance(payload.get("resident_ids"), list) else None
        return self._discovery_result(request, lambda: self.recommendation_backend().start_recommendation(
            actor=actor, query=str(payload.get("query") or ""), limit=int(payload.get("limit") or 5),
            resident_ids=resident_ids, client=self.discovery_client(),
        ))

    def discovery_search(self, request, payload, actor):
        resident_ids = payload.get("resident_ids") if isinstance(payload.get("resident_ids"), list) else None
        return self._discovery_result(request, lambda: self.discovery_client().search(
            actor=actor, query=str(payload.get("query") or ""), limit=int(payload.get("limit") or 8),
            resident_ids=resident_ids,
        ))

    def agent_backends(self, request, parsed, actor):
        return self._runtime_catalog(request, parsed, include_backends=True)

    def runtime_catalog(self, request, parsed, actor):
        return self._runtime_catalog(request, parsed, include_backends=False)

    def _runtime_catalog(self, request, parsed, *, include_backends):
        try:
            query = parse_qs(parsed.query)
            resident_id = str((query.get("resident_id") or [""])[0])
            runtime_route = self.resident_api().get_resident_runtime_route(
                resident_id, self.session_actor(request) or ""
            ) if resident_id else {}
            catalog = self.execution_api().runtime_catalog(
                account_id=str(runtime_route.get("account_id") or (query.get("account_id") or [""])[0]),
                backend=str(runtime_route.get("backend") or (query.get("backend") or [""])[0]),
                include_backends=include_backends,
            )
            return self.respond(request, 200, catalog)
        except Exception as exc:
            return self.error(request, exc)

    def get_favorites(self, request, parsed, actor):
        actor = self.session_actor(request) or ''
        try:
            from management_portal.principal_favorites import project_favorite_statuses
            forget = (lambda selection: self.forget_favorite(actor, selection)) if self.forget_favorite else None
            return self.respond(request, 200, project_favorite_statuses(
                actor, self.lineage_favorite_payload(actor), forget,
            ))
        except Exception as exc:
            return self.error(request, exc)

    def get_residents(self, request, parsed, actor):
        actor = self.session_actor(request) or ''
        try:
            return self.respond(request, 200, self.resident_sidebar_snapshot(actor))
        except SocietyServiceUnavailable as exc:
            return self.lineage_unavailable(request, exc)
        except (ResourceServiceUnavailable, TimeoutError) as exc:
            # The sidebar waits on the services a publication restarts; the
            # page retries quietly on the Lineage codes instead of showing a
            # failure in the Chat (10-12 "加载失败：HTTP 504").
            unavailable = SocietyServiceBusy if isinstance(exc, TimeoutError) else SocietyServiceUnavailable
            return self.lineage_unavailable(request, unavailable(f"Resident sidebar 暂时读取不到：{exc}"))
        except Exception as exc:
            return self.error(request, exc)

    def get_resident_shortcuts(self, request, parsed, actor):
        actor = self.session_actor(request) or ''
        try:
            return self.respond(request, 200, self.resident_sidebar_backend().shortcuts(actor))
        except SocietyServiceUnavailable as exc:
            return self.lineage_unavailable(request, exc)
        except (ResourceServiceUnavailable, TimeoutError) as exc:
            unavailable = SocietyServiceBusy if isinstance(exc, TimeoutError) else SocietyServiceUnavailable
            return self.lineage_unavailable(request, unavailable(f"Resident shortcuts 暂时读取不到：{exc}"))
        except Exception as exc:
            return self.error(request, exc)


    def get_residents_navigation_target(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resident_id = str((qs.get('id') or [''])[0]).strip()
        if not resident_id:
            return self.respond(request, 400, {'error': 'resident id required'})
        actor = self.session_actor(request) or ''
        try:
            result = self.resident_api().resolve_resident_identity(resident_id, actor)
        except KeyError:
            return self.respond(request, 404, {'error': 'resident not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_residents_projection(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resident_id = str((qs.get('id') or [''])[0]).strip()
        if not resident_id:
            return self.respond(request, 400, {'error': 'resident id required'})
        actor = self.session_actor(request) or ''
        try:
            result = self.resident_chat_projection().project_session(self.resident_api().get_resident_session_projection(resident_id, actor), actor)
        except KeyError:
            return self.respond(request, 404, {'error': 'resident not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_residents_watchboard(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resident_id = str((qs.get('id') or [''])[0]).strip()
        if not resident_id:
            return self.respond(request, 400, {'error': 'resident id required'})
        actor = self.session_actor(request) or ''
        try:
            result = self.watchboard_applications().project(self.resident_api().get_resident_watchboard(resident_id, actor), actor)
        except KeyError:
            return self.respond(request, 404, {'error': 'resident not found'})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_residents_session_restart_options(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        resident_id = str((qs.get('id') or [''])[0]).strip()
        if not resident_id:
            return self.respond(request, 400, {'error': 'resident id required'})
        actor = self.session_actor(request) or ''
        try:
            result = self.resident_api().get_resident_restart_options(resident_id, actor)
        except KeyError:
            return self.respond(request, 404, {'error': 'resident not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_residents_forward_targets(self, request, parsed, actor):
        actor = self.session_actor(request) or ''
        qs = parse_qs(parsed.query)
        try:
            result = self.resident_api().list_resident_forward_targets(actor, scope_id=str((qs.get('scope_id') or [''])[0]).strip(), lineage_id=str((qs.get('lineage_id') or [''])[0]).strip(), resident_id=str((qs.get('resident_id') or [''])[0]).strip(), preferred_lineage_id=str((qs.get('preferred_lineage_id') or [''])[0]).strip())
        except KeyError:
            return self.respond(request, 404, {'error': 'resident not found'})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_chat_attachments_image(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        file_id = str((qs.get('file_id') or [''])[0]).strip()
        actor = self.session_actor(request) or ''
        try:
            path, content_type = self.workspace_api().resolve_workspace_pasted_image(file_id, actor)
            return self.media_file(request, Path(path), content_type)
        except FileNotFoundError as exc:
            return self.error(request, exc)
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)

    def get_local_terminal_read(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        session_id = str((qs.get('session_id') or [''])[0]).strip()
        cursor = str((qs.get('cursor') or ['0'])[0]).strip()
        actor = self.session_actor(request) or ''
        try:
            result = self.local_terminal_backend().read_terminal(actor, session_id, cursor)
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_link_file(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        raw_path = str((qs.get('path') or [''])[0]).strip()
        meta_only = str((qs.get('meta') or [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
        if not raw_path:
            return self.respond(request, 400, {'error': 'path required'})
        actor = self.session_actor(request) or ''
        try:
            result = self.workspace_api().get_workspace_link_file(raw_path, actor, meta_only=meta_only, allow_outside_roots=False)
            if not meta_only and str(result.get('kind') or '') == 'markdown' and (str(result.get('access_source') or '') != 'shared'):
                try:
                    item = self.knowledge_backend().item_metadata_for_path(str(result.get('path') or ''))
                    kb_id = str(item.get('kb_id') or '')
                    result['knowledge_item'] = {'kb_id': kb_id, 'title': str(item.get('title') or ''), 'project': str(item.get('project') or ''), 'type': str(item.get('type') or '')}
                    if item.get('title'):
                        result['title'] = str(item.get('title') or '')
                except Exception:
                    pass
        except PermissionError:
            return self.link_permission_denied(request)
        except FileNotFoundError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def get_link_file_raw(self, request, parsed, actor):
        qs = parse_qs(parsed.query)
        raw_path = str((qs.get('path') or [''])[0]).strip()
        download = str((qs.get('download') or [''])[0]).strip().lower() in {'1', 'true', 'yes', 'on'}
        if not raw_path:
            return self.send_empty(request, 400)
        try:
            media_path = self.resolved_path(self.workspace_api().resolve_workspace_link_file_path(raw_path, allow_outside_roots=False, actor=self.session_actor(request) or ''), 'Lineage Workbench link file path')
            if media_path.is_dir():
                return self.send_empty(request, 415)
            if download:
                return self.download_file(request, media_path)
            if (qs.get('preview') or [''])[0] == 'pdf':
                from .presentation_preview import presentation_pdf, read_presentation
                converted = presentation_pdf(read_presentation(media_path), media_path.name)
                return self.media_file(request, converted, 'application/pdf')
            content_type = self.media_content_type(media_path)
            if not content_type:
                return self.send_empty(request, 415)
            return self.media_file(request, media_path, content_type)
        except PermissionError:
            return self.link_permission_denied(request)
        except (FileNotFoundError, ValueError) as exc:
            return self.error(request, exc)
        except Exception as exc:
            return self.error(request, exc)

    def post_local_terminal_start(self, request, payload, actor):
        try:
            result = self.local_terminal_backend().open_terminal(actor, session_id=str(payload.get('session_id') or ''), cols=payload.get('cols'), rows=payload.get('rows'), cwd=payload.get('cwd'))
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def post_local_terminal_input(self, request, payload, actor):
        try:
            result = self.local_terminal_backend().write_terminal(actor, str(payload.get('session_id') or ''), str(payload.get('input') or ''))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def post_local_terminal_control(self, request, payload, actor):
        try:
            result = self.local_terminal_backend().control_terminal(actor, str(payload.get('session_id') or ''), str(payload.get('action') or ''), payload.get('cols'), payload.get('rows'), payload.get('cursor'), payload.get('cwd'))
        except KeyError as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

    def post_residents_watchboard_application(self, request, payload, actor):
        from management_common.operation_errors import OperationError

        try:
            result = self.watchboard_applications().action(str(payload.get('resident_id') or ''), actor, str(payload.get('application_id') or ''), str(payload.get('operation') or ''), payload.get('args', {}))
        except OperationError as exc:
            return self.respond(request, exc.http_status, {'error': str(exc)})
        except PermissionError as exc:
            return self.respond(request, 403, {'error': str(exc)})
        except (KeyError, FileNotFoundError) as exc:
            return self.respond(request, 404, {'error': str(exc)})
        except ValueError as exc:
            return self.respond(request, 400, {'error': str(exc)})
        except Exception as exc:
            return self.error(request, exc)
        return self.respond(request, 200, result)

