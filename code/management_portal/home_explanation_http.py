"""Authenticated, explicit explanation-video generation and byte ranges."""
from urllib.parse import parse_qs

from management_portal import http_auth, http_request_body, http_responses
from management_portal.home_explanation_media import Media
from management_portal.http_media_range import plan_media_response


def _media(request, parsed, actor):
    try:
        if request.command == 'POST':
            body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
            state = Media().start(body.get('address'), body.get('sha256'), actor)
            code = 202 if state and state.get('status') == 'generating' else 200
        else:
            query = parse_qs(parsed.query)
            state = Media().status(query.get('address', [''])[0], query.get('sha256', [''])[0], actor)
            code = 200
        return http_responses._serve_json(request, code, {'media': state})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _file(request, parsed, actor):
    try:
        query = parse_qs(parsed.query)
        with Media().file(query.get('address', [''])[0], query.get('sha256', [''])[0], query.get('file', [''])[0], actor) as (stream, info, mime):
            response = plan_media_response(request, size=info.st_size, mtime_ns=info.st_mtime_ns, download=True)
            response.send_headers(request, mime, extra_headers={'X-Content-Type-Options': 'nosniff'})
            if response.status in (200, 206):
                stream.seek(response.start)
                remaining = response.length
                while remaining:
                    data = stream.read(min(remaining, 256*1024))
                    if not data:
                        break
                    request.wfile.write(data)
                    remaining -= len(data)
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _review(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
        state = Media().retry_review(body.get('address'), body.get('sha256'), actor)
        return http_responses._serve_json(request, 200, {'media': state})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def routes():
    route = http_auth._principal_route
    return (route('GET', '/api/home-explanation/media', _media, 'home-explanation'),
            route('POST', '/api/home-explanation/media', _media, 'home-explanation'),
            route('POST', '/api/home-explanation/media/review', _review, 'home-explanation'),
            route('GET', '/api/home-explanation/media/file', _file, 'home-explanation'))
