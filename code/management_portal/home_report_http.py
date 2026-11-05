"""Authenticated report/media presentation routes for the optional Home widget."""
from __future__ import annotations

from urllib.parse import parse_qs

from management_portal import http_auth, http_request_body, http_responses
from management_portal.home_report import build_report, reauthorize, isa_reader
from management_portal.home_report_store import ReportStore, revision_number
from management_portal.home_report_persistent_media import PersistentMedia
from management_portal.home_report_media import MediaStore
from management_portal.media_tools import capabilities
from management_portal.home_report_quality import bind
from management_portal.resources.errors import ResourceVersionConflict
from management_portal.http_media_range import plan_media_response

_MEDIA = None
RESOURCE_ROUTES = {'report': ('/home-report', 'id')}


def media_store():
    global _MEDIA
    if _MEDIA is None:
        _MEDIA = MediaStore()
    return _MEDIA


def _get(request, parsed, actor):
    try:
        query = parse_qs(parsed.query)
        target = {key: query.get(key, [""])[0] for key in ("kind", "id", "focus")}
        if target['kind'] == 'report' or target['id'].startswith('report_'):
            report = ReportStore().response(target['id'], actor, query.get('revision', [None])[0])
            return http_responses._serve_json(request, 200, report)
        report = bind(build_report(target, actor=actor), query.get('quality', [None])[0], author=actor)
        available = capabilities()
        report["media_capability"] = {key: available[key] for key in ("available", "missing")}
        return http_responses._serve_json(request, 200, report)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _generate(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
        state = PersistentMedia(ReportStore()).start(body.get('report_id'), body.get('revision'), actor,
            retry=body.get('retry') is True, quality=body.get('quality', 'medium'))
        return http_responses._serve_json(request, 202, state)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _cancel_media(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request,max_bytes=4096)
        result = PersistentMedia(ReportStore()).cancel(body.get('report_id'),body.get('revision'),actor,quality=body.get('quality','medium'))
        return http_responses._serve_json(request,200,result)
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _validate(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=256000)
        if body.get('report_id'):
            record, number, snapshot = ReportStore().get(body['report_id'],actor,body.get('revision'))
            fresh = build_report(snapshot['target'],actor=actor)
            return http_responses._serve_json(request,200,{'readable':True,'source_changed':fresh['version'] != snapshot.get('source_version',snapshot['version']), 'latest_revision':record['revision']})
        sources = body.get("sources")
        allowed = {"GetMessage", "GetInputState", "GetLineage", "GetSharedState", "GetProject", "GetResearchMap", "ResolveResource"}
        if not isinstance(sources, list) or len(sources) > 400 or any(not isinstance(s, dict) or not isinstance(s.get("read"), dict) or s["read"].get("instruction") not in allowed for s in sources):
            raise ValueError("无效的报告来源")
        reauthorize({"sources": sources}, actor=actor)
        return http_responses._serve_json(request, 200, {"readable": True})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _origin(request, parsed, actor):
    try:
        key = parse_qs(parsed.query).get('origin_key',[''])[0]
        return http_responses._serve_json(request,200,ReportStore().origin(actor,key))
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _save(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request,max_bytes=8192)
        from management_portal import permission_system
        from management_portal.resources import get_resource_service
        store = ReportStore()
        operation = 'home-report-origin:' + store.origin_id(actor,body.get('origin_key'))
        def register(fields):
            arguments={'resource_type':'report','fields':fields}
            if not permission_system.allows(actor,'RegisterResource',arguments):
                raise PermissionError('无权创建汇报')
            # The Resource owner reserves the same ID across interrupted writes.
            # HTTP retries must not replay a permanently failed ISA response.
            return get_resource_service().register('report',fields,actor=actor,operation_id=operation)
        result = store.materialize(body,actor,register)
        if result.get('quality') == 'high':
            from management_portal.home_report_review import submit
            result['review'] = submit(store, result['report_id'], result['revision'], actor)
        return http_responses._serve_json(request,200,result)
    except ResourceVersionConflict as exc:
        return http_responses._serve_json(request,409,{'error':str(exc)})
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _refresh(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request,max_bytes=131072)
        changes = {key: body[key] for key in ('expected_revision', 'quality', 'content') if key in body}
        result = isa_reader(actor)('UpdateResource',{'resource_id':body.get('report_id'), 'changes':changes})
        resource = result.get('resource') or result
        store = ReportStore()
        result = store.response(body['report_id'],actor,resource.get('revision'))
        if result.get('quality') == 'high':
            from management_portal.home_report_review import submit
            result['review'] = submit(store, result['report_id'], result['revision'], actor)
        return http_responses._serve_json(request,200,result)
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _review(request, parsed, actor):
    try:
        from management_portal.home_report_review import submit
        body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
        store = ReportStore()
        media = None
        if body.get('video') is True:
            media = PersistentMedia(store).status(body.get('report_id'), body.get('revision'), actor, quality='high', include_review=False)
            if not media or media.get('status') != 'ready':
                raise ValueError('请先生成固定视频')
        result = submit(store, body.get('report_id'), body.get('revision'), actor, media=media)
        return http_responses._serve_json(request, 200, result)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _delete(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request,max_bytes=4096)
        # Same domain owner as UnregisterResource, with this form's CAS checked
        # inside the deletion lock rather than a preflight request race.
        result = ReportStore().delete(body.get('report_id'),actor,revision_number(body.get('expected_revision')))
        return http_responses._serve_json(request,200,result)
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _attach(request, parsed, actor):
    try:
        body=http_request_body._read_bounded_json_object(request,max_bytes=8192)
        store=ReportStore()
        result=store.attach(body.get('report_id'),actor,body.get('expected_revision'),body.get('address'))
        if result.get('quality') == 'high':
            from management_portal.home_report_review import submit
            result['review'] = submit(store, result['report_id'], result['revision'], actor)
        return http_responses._serve_json(request,200,result)
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _persistent(request, parsed, actor, params):
    try:
        store = ReportStore()
        identifier, number = params['id'], params['revision']
        if not params.get('file'):
            return http_responses._serve_json(request,200,PersistentMedia(store).status(identifier,number,actor,quality=params.get('quality','medium')))
        if params.get('quality'):
            path, mime = PersistentMedia(store).file(identifier,number,params['file'],actor,quality=params['quality'])
        else:
            path, mime = store.file(identifier,number,params['file'],actor)
        stat = path.stat()
        response = plan_media_response(request,size=stat.st_size,mtime_ns=stat.st_mtime_ns,download=True)
        response.send_headers(request,mime,extra_headers={'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
        if response.status in {200,206}:
            with path.open('rb') as stream:
                stream.seek(response.start)
                remaining = response.length
                while remaining:
                    chunk = stream.read(min(remaining,256*1024))
                    if not chunk:
                        break
                    request.wfile.write(chunk)
                    remaining -= len(chunk)
    except (BrokenPipeError,ConnectionResetError):
        return
    except Exception as exc:
        return http_responses._serve_exception_json(request,exc)


def _media(request, parsed, actor, params):
    try:
        job, filename = params["job"], params.get("file")
        if not filename:
            return http_responses._serve_json(request, 200, media_store().status(job, actor))
        path, content_type = media_store().file(job, filename, actor)
        stat = path.stat()
        response = plan_media_response(request, size=stat.st_size, mtime_ns=stat.st_mtime_ns, download=True)
        response.send_headers(request, content_type, extra_headers={"X-Content-Type-Options": "nosniff"})
        if response.status not in {200, 206}:
            return
        with path.open("rb") as stream:
            stream.seek(response.start)
            remaining = response.length
            while remaining:
                chunk = stream.read(min(remaining, 256 * 1024))
                if not chunk:
                    break
                request.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _page(request, parsed, actor):
    # Standalone QA/deep-link entry uses exactly the same component as Home.
    page = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>来源汇报 · TFI Portal</title><link rel="stylesheet" href="/static/home_report.css">
<link rel="stylesheet" href="/static/portal_components_markdown.css">
<script src="/static/portal_text.js"></script><script src="/static/portal_ui_primitives.js"></script>
<script src="/static/markdown.js"></script>
<main data-home-report-standalone></main><script src="/static/home_report.js"></script></html>'''
    return http_responses._serve_payload(request, 200, page.encode(), "text/html; charset=utf-8")


def routes():
    route = http_auth._principal_route
    return (route("GET", "/api/home-report", _get, "home-report"),
            route('GET','/api/home-report/origin',_origin,'home-report'),
            route('POST','/api/home-report/save',_save,'home-report'),
            route('POST','/api/home-report/refresh',_refresh,'home-report'),
            route('POST','/api/home-report/review',_review,'home-report'),
            route('POST','/api/home-report/delete',_delete,'home-report'),
            route('POST','/api/home-report/attachments',_attach,'home-report'),
            route('GET','/api/home-report/reports/{id}/{revision}/media',_persistent,'home-report',match='template'),
            route('GET','/api/home-report/reports/{id}/{revision}/media/{quality}',_persistent,'home-report',match='template'),
            route('GET','/api/home-report/reports/{id}/{revision}/media/{quality}/file/{file}',_persistent,'home-report',match='template'),
            route('GET','/api/home-report/reports/{id}/{revision}/file/{file}',_persistent,'home-report',match='template'),
            route("POST", "/api/home-report/validate", _validate, "home-report"),
            route("POST", "/api/home-report/media", _generate, "home-report"),
            route("POST", "/api/home-report/media/cancel", _cancel_media, "home-report"),
            route("GET", "/api/home-report/media/{job}", _media, "home-report", match="template"),
            route("GET", "/api/home-report/media/{job}/{file}", _media, "home-report", match="template"),
            route("GET", "/home-report", _page, "home-report", page=True))
