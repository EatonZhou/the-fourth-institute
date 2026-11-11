"""Authenticated Reader translation API. Client identity is never a payload field."""
from urllib.parse import parse_qs
import time
from management_portal.http_routes import RouteDefinition
from management_portal.paper_translation_kimi_api import TranslationConfigurationUnavailable
from management_portal.paper_reader_annotations import Conflict


def routes(*, service, get_auth, authenticate, read_json, respond):
    def handle(operation):
        def route(request, parsed, _actor):
            started = time.monotonic()
            def error(status, code, message): return respond(request, status, {'error':dict(code=code, message=message)})
            try:
                context = get_auth(request)
                if not context: return error(401, 'authentication_required', 'Authentication required')
                principal = authenticate(context)
                if request.headers.get('X-Reader-Principal') != principal:
                    return error(409, 'principal_changed', 'Account changed; reopen Reader')
                if operation != 'get':
                    from management_portal.http_auth import _same_origin_request
                    if request.headers.get('Sec-Fetch-Site') == 'cross-site' or not _same_origin_request(request):
                        raise PermissionError('cross-origin mutation')
                    if request.headers.get('Content-Type', '').split(';')[0].lower().strip() != 'application/json':
                        raise ValueError('JSON content type required')
                    length = int(request.headers.get('Content-Length') or '0')
                    if not 0 < length <= 32768: return error(413, 'request_too_large', 'Request exceeds 32768 bytes')
                    payload = read_json(request)
                    if not isinstance(payload, dict): raise ValueError('JSON object required')
                    if operation == 'cancel':
                        if set(payload) != {'task_id'}: raise ValueError('only task_id accepted')
                        value = service.cancel(context, payload['task_id'])
                    else:
                        if not {'operation_id', 'text', 'source_language', 'target_language', 'mode', 'document'} <= set(payload):
                            raise ValueError('required translation fields missing')
                        if set(payload) - {'operation_id', 'text', 'source_language', 'target_language', 'mode', 'document', 'refresh'}:
                            raise ValueError('unknown translation fields')
                        operation_id = payload.pop('operation_id')
                        refresh = payload.pop('refresh', False)
                        value = service.request(context, operation_id=operation_id, request=payload, refresh=refresh)
                else:
                    params = parse_qs(parsed.query)
                    if set(params) != {'task_id'} or len(params['task_id']) != 1: raise ValueError('task_id required')
                    value = service.get(context, params['task_id'][0])
                value['task_total_ms'] = value.get('total_ms')
                value['total_ms'] = (time.monotonic() - started) * 1000
                return respond(request, 200, value)
            except PermissionError: return error(403, 'access_denied', 'Translation access denied')
            except Conflict: return error(409, 'operation_conflict', 'Operation or document changed')
            except TranslationConfigurationUnavailable: return error(503, 'service_unavailable', 'Selected translation API configuration unavailable')
            except KeyError: return error(404, 'not_found', 'Document or translation task missing')
            except (ValueError, TypeError): return error(400, 'invalid_request', 'Invalid translation request')
            except Exception: return error(503, 'service_unavailable', 'Translation service temporarily unavailable')
        return route
    path = '/paper/api/reader/translation'
    return tuple(RouteDefinition(method, endpoint, handle(operation), 'paper-translation', phase='dual-auth')
        for method, endpoint, operation in [('POST', path, 'request'), ('GET', path, 'get'), ('POST', path + '/cancel', 'cancel')])


def production_routes():
    from management_portal import http_auth, http_request_body, http_responses
    from management_portal.page_api_auth import authorize
    from management_portal.paper_translation_service import production_service
    return routes(service=production_service(), get_auth=http_auth._get_agent_api_auth,
        authenticate=authorize, read_json=http_request_body._read_request_json,
        respond=http_responses._serve_json)
