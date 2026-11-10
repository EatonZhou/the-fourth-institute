"""Independent dual-auth Reader routes; installed only by explicit route assembly."""
from urllib.parse import parse_qs
from management_portal.http_routes import RouteDefinition
from management_portal.paper_reader_annotations import Conflict, MAX_BYTES


def routes(*, service, get_auth, authenticate, read_json, respond):
    def handle(operation):
        def route(request, parsed, _actor):
            try:
                auth = get_auth(request)
                if not auth: return respond(request, 401, {'error': 'authentication required'})
                actor = authenticate(auth)
                if not actor or request.headers.get('X-Reader-Principal') != actor:
                    return respond(request, 409, {'error': 'principal changed; reopen Reader'})
                if operation in ('open', 'commit'):
                    from management_portal.http_auth import _same_origin_request
                    if request.headers.get('Sec-Fetch-Site') == 'cross-site' or not _same_origin_request(request):
                        raise PermissionError('cross-origin mutation denied')
                    if request.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                        raise ValueError('Content-Type must be application/json')
                    length = int(request.headers.get('Content-Length') or '0')
                    if not 0 <= length <= MAX_BYTES: return respond(request, 413, {'error': 'request limit'})
                    payload = read_json(request)
                    if not isinstance(payload, dict): raise ValueError('JSON object required')
                else:
                    payload = {k: values[0] for k, values in parse_qs(parsed.query).items()}
                if operation == 'open':
                    if set(payload) != {'paper_id'}: raise ValueError('only paper_id accepted')
                    value = service.open(actor, payload['paper_id'])
                elif operation == 'commit': value = service.commit(actor, payload)
                elif operation == 'read': value = service.read(actor, payload)
                else:
                    with service.pdf(actor, payload) as stream:
                        # Versioned response; no cookie-independent cache reuse.
                        request.send_response(200); request.send_header('Content-Type', 'application/pdf')
                        request.send_header('Cache-Control', 'private, no-store'); request.end_headers()
                        while chunk := stream.read(1024 * 1024): request.wfile.write(chunk)
                    return
                return respond(request, 200, value)
            except PermissionError: return respond(request, 403, {'error': 'Paper access denied'})
            except (KeyError, FileNotFoundError): return respond(request, 404, {'error': 'Paper or version missing'})
            except Conflict as exc: return respond(request, 409, {'error': str(exc)})
            except ValueError as exc: return respond(request, 400, {'error': str(exc)})
        return route
    return tuple(RouteDefinition(method, '/paper/api/reader/' + path, handle(op), 'paper-reader', phase='dual-auth')
                 for method, path, op in [('POST', 'open', 'open'), ('GET', 'annotations', 'read'), ('POST', 'annotations', 'commit'), ('GET', 'pdf', 'pdf')])


def production_routes(portal=None, *, get_auth=None, read_json=None, respond=None):
    from management_portal import http_auth, http_request_body, http_responses
    from management_portal.page_api_auth import authorize
    from management_portal.paper_reader_service import production_service
    return (*routes(service=production_service(), get_auth=get_auth or http_auth._get_agent_api_auth,
        authenticate=authorize, read_json=read_json or http_request_body._read_request_json,
        respond=respond or http_responses._serve_json),
        RouteDefinition('GET', '/paper/reader-assets/', reader_asset, 'paper-reader-assets', match='prefix'))


def reader_asset(request, parsed, actor):
    from pathlib import Path
    import mimetypes
    from urllib.parse import unquote
    from management_portal.http_files import _serve_media_file
    from management_portal.http_responses import _send_empty
    root = Path(__file__).parent / 'paper_reader_app/zotero_prototype/upstream'
    path = (root / unquote(parsed.path.removeprefix('/paper/reader-assets/'))).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file(): return _send_empty(request, 404)
    return _serve_media_file(request, path, mimetypes.guess_type(path)[0] or 'application/octet-stream')
