"""Authenticated, explicit explanation-video generation and byte ranges."""
from urllib.parse import parse_qs

from management_portal import http_auth, http_request_body, http_responses
from management_portal.home_explanation_media import Media
from management_portal.home_explanation_review import ReviewService, AuthoringService
import json
from management_portal.http_media_range import plan_media_response


def _media(request, parsed, actor):
    try:
        if request.command == 'POST':
            body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
            state = Media().start(body.get('address'), body.get('sha256'), actor, body.get('quality', 'medium'), body.get('previous_review_id'))
            code = 202 if state and state.get('status') == 'generating' else 200
        else:
            query = parse_qs(parsed.query)
            state = Media().status(query.get('address', [''])[0], query.get('sha256', [''])[0], actor, query.get('quality', ['medium'])[0])
            code = 200
        return http_responses._serve_json(request, code, {'media': state})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _file(request, parsed, actor):
    try:
        query = parse_qs(parsed.query)
        with Media().file(query.get('address', [''])[0], query.get('sha256', [''])[0], query.get('file', [''])[0], actor, query.get('quality', ['medium'])[0]) as (stream, info, mime):
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
        state = Media().retry_review(body.get('address'), body.get('sha256'), actor, body.get('quality', 'medium'))
        return http_responses._serve_json(request, 200, {'media': state})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _cancel_media(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
        value = Media().cancel(body.get('address'), body.get('sha256'), actor, body.get('quality', 'medium'))
        return http_responses._serve_json(request, 200, {'media': value})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _reviews(request, parsed, actor):
    try:
        owner = ReviewService()
        if request.command == 'GET':
            value = owner.read(parse_qs(parsed.query).get('id', [''])[0], actor)
        else:
            body = http_request_body._read_bounded_json_object(request, max_bytes=16384)
            if parsed.path.endswith('/verdict'):
                value = owner.verdict(body.get('review_id'), actor, digest=body.get('digest'),
                    status=body.get('status'), evidence=body.get('evidence'), feedback=body.get('feedback', ''))
            elif parsed.path.endswith('/cancel'):
                value = owner.cancel(body.get('review_id'), actor)
            else:
                media = Media()
                ref = {'address': body.get('address'), 'sha256': body.get('sha256')}
                ctx = media.context(**ref, actor=actor)
                manifest = json.loads(media.read(ref, actor))
                if manifest.get('quality') != 'high':
                    raise ValueError('仅high解释页需要独立审核')
                assets = [ref] + [manifest[k] for k in ('interaction', 'supplement', 'video_plan', 'video') if manifest.get(k)]
                value = owner.submit({'kind': 'explanation', 'ref': ref['address'], 'version': ref['sha256'],
                    'digest': ref['sha256'], 'quality': 'high', 'author': ctx.get('owner_id') or actor,
                    'assets': assets, 'task': body.get('task', manifest.get('summary', '')),
                    'previous_review_id': body.get('previous_review_id')}, actor, body.get('reviewer', ''))
        return http_responses._serve_json(request, 200, {'review': value})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _authoring(request, parsed, actor):
    try:
        value = AuthoringService().read(parse_qs(parsed.query).get('id', [''])[0], actor)
        return http_responses._serve_json(request, 200, {'authoring': value})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _accept_authoring(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=16384)
        value = Media().accept_authoring(body.get('address'), body.get('sha256'), actor,
            body.get('quality'), body.get('authoring_id'))
        return http_responses._serve_json(request, 200, {'authoring': value})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def routes():
    route = http_auth._principal_route
    return (route('GET', '/api/home-explanation/media', _media, 'home-explanation'),
            route('POST', '/api/home-explanation/media', _media, 'home-explanation'),
            route('POST', '/api/home-explanation/media/review', _review, 'home-explanation'),
            route('POST', '/api/home-explanation/media/cancel', _cancel_media, 'home-explanation'),
            route('POST', '/api/home-explanation/media/authoring', _accept_authoring, 'home-explanation'),
            route('GET', '/api/home-explanation/reviews', _reviews, 'home-explanation'),
            route('GET', '/api/home-explanation/authoring', _authoring, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews', _reviews, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews/verdict', _reviews, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews/cancel', _reviews, 'home-explanation'),
            route('GET', '/api/home-explanation/media/file', _file, 'home-explanation'))
