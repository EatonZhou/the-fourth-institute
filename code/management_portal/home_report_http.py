"""Authenticated report/media presentation routes for the optional Home widget."""
from __future__ import annotations

from urllib.parse import parse_qs

from management_portal import http_auth, http_request_body, http_responses
from management_portal.home_report import build_report, reauthorize
from management_portal.home_report_media import MediaStore, capabilities
from management_portal.http_media_range import plan_media_response

_MEDIA = None


def media_store():
    global _MEDIA
    if _MEDIA is None:
        _MEDIA = MediaStore()
    return _MEDIA


def _get(request, parsed, actor):
    try:
        query = parse_qs(parsed.query)
        target = {key: query.get(key, [""])[0] for key in ("kind", "id", "focus")}
        report = build_report(target, actor=actor)
        available = capabilities()
        report["media_capability"] = {key: available[key] for key in ("available", "missing")}
        return http_responses._serve_json(request, 200, report)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _generate(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=4096)
        report = build_report(body.get("target"), actor=actor)
        if body.get("version") != report["version"]:
            return http_responses._serve_json(request, 409, {"error": "来源已更新，请刷新报告后生成视频"})
        state = media_store().start(report, actor, retry=body.get("retry") is True)
        return http_responses._serve_json(request, 202, state)
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


def _validate(request, parsed, actor):
    try:
        body = http_request_body._read_bounded_json_object(request, max_bytes=256000)
        sources = body.get("sources")
        allowed = {"GetMessage", "GetInputState", "GetLineage", "GetSharedState", "GetProject", "GetResearchMap", "ResolveResource"}
        if not isinstance(sources, list) or len(sources) > 400 or any(not isinstance(s, dict) or not isinstance(s.get("read"), dict) or s["read"].get("instruction") not in allowed for s in sources):
            raise ValueError("无效的报告来源")
        reauthorize({"sources": sources}, actor=actor)
        return http_responses._serve_json(request, 200, {"readable": True})
    except Exception as exc:
        return http_responses._serve_exception_json(request, exc)


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
<main data-home-report-standalone></main><script src="/static/home_report.js"></script></html>'''
    return http_responses._serve_payload(request, 200, page.encode(), "text/html; charset=utf-8")


def routes():
    route = http_auth._principal_route
    return (route("GET", "/api/home-report", _get, "home-report"),
            route("POST", "/api/home-report/validate", _validate, "home-report"),
            route("POST", "/api/home-report/media", _generate, "home-report"),
            route("GET", "/api/home-report/media/{job}", _media, "home-report", match="template"),
            route("GET", "/api/home-report/media/{job}/{file}", _media, "home-report", match="template"),
            route("GET", "/home-report", _page, "home-report", page=True))
