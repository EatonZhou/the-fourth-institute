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


def _page_subject(media, address, sha256, actor, task='', *, creating=False):
    ref = {'address': address, 'sha256': sha256}
    context = media.context(**ref, actor=actor)
    if creating:
        media.writable(context, actor)
    manifest = context['manifest']
    if manifest.get('quality') != 'high':
        raise ValueError('仅high解释页需要独立审核')
    return {'kind': 'explanation', 'ref': address, 'version': sha256,
            # A directory owner describes storage, not authorship. Direct
            # Resident submission binds the authenticated creator; an unbound
            # User stays awaiting_author rather than trusting payload identities.
            'digest': sha256, 'quality': 'high', 'author': actor,
            'assets': media.review_assets(context),
            'task': task or manifest.get('summary', '')}


def _reviews(request, parsed, actor):
    try:
        owner = ReviewService()
        if request.command == 'GET':
            query = parse_qs(parsed.query)
            identifier = query.get('id', [''])[0]
            if not identifier:
                subject = _page_subject(Media(), query.get('address', [''])[0], query.get('sha256', [''])[0], actor)
                value = owner.package_projection(subject['ref'], subject['version'], actor)
            else:
                value = owner.read(identifier, actor)
        else:
            body = http_request_body._read_bounded_json_object(request, max_bytes=16384)
            if parsed.path.endswith('/verdict'):
                value = owner.verdict(body.get('review_id'), actor, digest=body.get('digest'),
                    status=body.get('status'), evidence=body.get('evidence'), feedback=body.get('feedback', ''))
            elif parsed.path.endswith('/cancel'):
                value = owner.cancel(body.get('review_id'), actor)
            elif parsed.path.endswith('/resume'):
                value = owner.resume(body.get('review_id'), actor, body.get('reviewer', ''))
            else:
                subject = _page_subject(Media(), body.get('address'), body.get('sha256'), actor, body.get('task', ''), creating=True)
                subject['previous_review_id'] = body.get('previous_review_id')
                binding = owner.package_binding(subject['ref'], subject['version'], actor)
                if binding and binding['author'] != actor:
                    raise PermissionError('该固定包已关联正式作者；合法读者可读质量状态，不能冒充作者重新送审')
                value = owner.submit(subject, actor, body.get('reviewer', ''))
        return http_responses._serve_json(request, 200, {'review': value})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _review_file(request, parsed, actor):
    try:
        query = parse_qs(parsed.query)
        with ReviewService().asset(query.get('id', [''])[0], query.get('sha256', [''])[0], actor,
                                   group=query.get('group', ['subject'])[0]) as (data, entry, check_live):
            # This protects acceptance/body construction. The ASGI adapter
            # buffers this response before network transmission, so an already
            # accepted response can finish after cancellation; new requests fail.
            response = plan_media_response(request, size=len(data), mtime_ns=0, download=True)
            response.send_headers(request, entry['content_type'], extra_headers={
                'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store',
                'Content-Security-Policy': "sandbox; default-src 'none'"})
            if response.status in (200, 206):
                for offset in range(response.start, response.start + response.length, 256 * 1024):
                    try:
                        check_live()
                    except PermissionError:
                        request.close_connection = True
                        return
                    request.wfile.write(data[offset:min(offset + 256 * 1024, response.start + response.length)])
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _prepare(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=16384)
        if set(body) - {'kind', 'quality', 'task'}:
            raise ValueError('创作准备只接受kind、quality、task；身份与Family由服务端核验')
        value = ReviewService().prepare(actor, kind=body.get('kind'),
            quality=body.get('quality'), task=body.get('task'))
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
            route('POST', '/api/home-explanation/reviews/prepare', _prepare, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews/verdict', _reviews, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews/cancel', _reviews, 'home-explanation'),
            route('POST', '/api/home-explanation/reviews/resume', _reviews, 'home-explanation'),
            route('GET', '/api/home-explanation/reviews/file', _review_file, 'home-explanation'),
            route('GET', '/api/home-explanation/media/file', _file, 'home-explanation'))
