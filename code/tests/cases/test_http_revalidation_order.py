"""Conditional reads share entity-tag semantics before range selection.

:module: platform.core
:design: decision_f25266e5a0e7
:importance: P1
"""
import asyncio
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from management_portal import http_files, http_static_files, http_user_guide


def request(headers=None, *, method="GET", path="/file"):
    response = {}
    return SimpleNamespace(
        headers=headers or {}, command=method, path=path, wfile=BytesIO(),
        response=response, send_response=lambda status: response.update(status=status),
        send_header=lambda k, v: response.update({k: v}), end_headers=lambda: None,
    )


@pytest.mark.parametrize("validator", ["strong", "weak", "wildcard"])
def test_media_revalidation_precedes_even_unsatisfiable_range(tmp_path, validator):
    path = tmp_path / "media.pdf"
    path.write_bytes(b"fixture-media")
    initial = request()
    http_files._serve_media_file(initial, path, "application/pdf")
    tag = initial.response["ETag"]
    value = {"strong": tag, "weak": '"old,tag", W/' + tag, "wildcard": "*"}[validator]
    cached = request({"If-None-Match": value, "Range": "bytes=999-"})
    http_files._serve_media_file(cached, path, "application/pdf")
    assert cached.response["status"] == 304
    assert cached.response["ETag"] == tag
    assert "Content-Range" not in cached.response
    assert cached.wfile.getvalue() == b""


def test_authorized_media_cached_and_head_do_not_read_bytes():
    def forbidden_read(*a, **k):
        pytest.fail("validated metadata-only response must not fetch content")
    service = SimpleNamespace(read_content_range=forbidden_read)
    for handler, expected in [
        (request({"If-None-Match": 'W/"7-10"', "Range": "bytes=3-5"}), 304),
        (request({"Range": "bytes=999-"}, method="HEAD"), 200),
    ]:
        plan, headers, body = http_files.authorized_resource_response(
            handler, resource_service=service, address="fixture", actor="actor",
            resolved={"content_size": 16, "content_mtime_ns": 7, "filename": "fixture.pdf"},
            content_type="application/pdf",
        )
        assert plan.status == expected
        assert list(body) == []
        if expected == 200:
            assert headers["Content-Length"] == "16" and "Content-Range" not in headers


@pytest.mark.parametrize("validator", ["weak", "wildcard"])
def test_static_and_guide_use_same_revalidation(validator, tmp_path, monkeypatch):
    path = tmp_path / "app.js"
    path.write_bytes(b"const fixture = true;")
    monkeypatch.setattr(http_static_files.http_static_versions, "_directory", lambda: tmp_path / "versions")
    first = request(path="/static/app.js")
    http_static_files._serve_static_file(first, path, "application/javascript")
    tag = first.response["ETag"]
    cached = request({"If-None-Match": "*" if validator == "wildcard" else "W/" + tag}, path=first.path)
    http_static_files._serve_static_file(cached, path, "application/javascript")
    assert cached.response["status"] == 304 and cached.wfile.getvalue() == b""
    assert cached.response["Cache-Control"] == first.response["Cache-Control"]
    monkeypatch.setattr(http_user_guide, "guide_source", lambda *a: "fixture guide")
    monkeypatch.setattr(http_user_guide, "_language", lambda *a: "en")
    sent = []
    monkeypatch.setattr(http_user_guide.http_documents, "_serve_bytes", lambda r, status, data, ct, **kw: sent.append((status, data, kw)))
    http_user_guide.guide_markdown(request(), urlsplit("/guide/agent.md"), "")
    tag = sent[-1][2]["extra_headers"]["ETag"]
    http_user_guide.guide_markdown(request({"If-None-Match": "*" if validator == "wildcard" else "W/" + tag}), urlsplit("/guide/agent.md"), "")
    assert sent[-1][:2] == (304, b"")


def test_nonmatching_and_malformed_tags_keep_range_and_download_policy(tmp_path):
    path = tmp_path / "file.pdf"
    path.write_bytes(b"0123456789")
    initial = request()
    http_files._serve_media_file(initial, path, "application/pdf")
    tag = initial.response["ETag"]
    for value in ['"old"', '"unterminated, ' + tag, '*, "old"']:
        handler = request({"If-None-Match": value, "Range": "bytes=2-4"})
        http_files._serve_media_file(handler, path, "application/pdf")
        assert handler.response["status"] == 206 and handler.wfile.getvalue() == b"234"
    for if_range, expected in [(tag, b"234"), ("W/" + tag, b"0123456789"), ('"old"', b"0123456789")]:
        handler = request({"If-None-Match": '"old"', "If-Range": if_range, "Range": "bytes=2-4"})
        http_files._serve_media_file(handler, path, "application/pdf")
        assert handler.wfile.getvalue() == expected
    download = request({"If-None-Match": "*", "Range": "bytes=2-4"})
    http_files._serve_download_file(download, path)
    assert download.response["status"] == 206 and download.response["Cache-Control"] == "no-store"
    assert download.wfile.getvalue() == b"234"


@pytest.mark.parametrize("endpoint", ["upload_content", "resource_download"])
def test_asgi_head_preserves_method_for_shared_response_policy(monkeypatch, endpoint):
    from starlette.requests import Request
    from management_portal import asgi, resources
    from management_portal.resources import isa
    monkeypatch.setattr(asgi, "_instruction_principal", lambda r: ("actor", "fixture"))
    service = SimpleNamespace(resolve=lambda *a, **k: {"content_size": 16, "content_mtime_ns": 7, "filename": "fixture.pdf", "media_type": "application/pdf"},
                              read_content_range=lambda *a, **k: pytest.fail("HEAD read bytes"))
    monkeypatch.setattr(resources, "get_resource_service", lambda: service)
    monkeypatch.setattr(isa, "read_download", lambda *a: {"address": "fixture", "content_type": "application/pdf"})
    r = Request({"type": "http", "method": "HEAD", "scheme": "http", "server": ("fixture", 80),
                 "path": "/fixture", "query_string": b"", "headers": [(b"range", b"bytes=999-")],
                 "path_params": {"upload_id": "fixture", "token": "fixture"}})
    async def run():
        response = await getattr(asgi, endpoint)(r)
        assert response.status_code == 200
        assert response.headers["content-length"] == "16"
        assert [chunk async for chunk in response.body_iterator] == []
    asyncio.run(run())


def test_shared_validator_parses_whole_tag_list():
    from management_portal.http_validators import if_none_match_matches
    assert if_none_match_matches('"old,tag", W/"current,tag"', '"current,tag"')
    assert if_none_match_matches(', W/"current", ,', '"current"')
    for malformed in ['"unterminated, "current"', '*, "current"', 'junk, "current"', 'w/"current"', '"current" "other"', ',' + ' ' * 8192 + '!']:
        assert not if_none_match_matches(malformed, '"current"')
