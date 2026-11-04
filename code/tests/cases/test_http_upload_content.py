"""Upload links use the same authorized file stream and headers as resource files."""
import asyncio
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from starlette.requests import Request

from management_portal import asgi, resources


UPLOAD = 'upload_0123456789abcdef01234567'


def request(*, headers=(), query=b''):
    return Request({'type': 'http', 'method': 'GET', 'scheme': 'http',
                    'path': f'/api/uploads/{UPLOAD}', 'headers': list(headers),
                    'server': ('testserver', 80), 'query_string': query,
                    'path_params': {'upload_id': UPLOAD}})


@pytest.fixture
def source(monkeypatch):
    body = b'PK\x03\x04' + b'x' * (2 * 1024 * 1024 + 7)
    resolved = {'filename': '资料 包.zip', 'content_size': len(body),
                'content_mtime_ns': 123456789, 'media_type': 'application/zip',
                'target_kind': 'file', 'exists': True}
    calls = []

    def resolve(address, *, actor):
        assert address == {'upload_id': UPLOAD} and actor == 'resident_83d75ad3a6c7'
        return dict(resolved)

    def read(address, *, start, end, actor):
        resolve(address, actor=actor)
        calls.append((start, end))
        return body[start:end + 1], 'application/zip', dict(resolved)

    service = SimpleNamespace(resolve=resolve, read_content_range=read)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: service)
    monkeypatch.setattr(asgi, '_instruction_principal', lambda _request: ('resident_83d75ad3a6c7', 'cli'))
    return SimpleNamespace(body=body, resolved=resolved, calls=calls, service=service)


async def collect(response):
    return b''.join([part async for part in response.body_iterator])


def test_zip_download_has_real_filename_and_bounded_reads(source):
    async def run():
        response = await asgi.upload_content(request())
        assert response.status_code == 200
        assert source.calls == [(0, 1024 * 1024 - 1)]
        assert response.headers['content-length'] == str(len(source.body))
        assert response.headers['content-type'] == 'application/zip'
        assert quote('资料 包.zip', safe='') in response.headers['content-disposition']
        assert response.headers['content-security-policy'] == 'sandbox'
        assert response.headers['x-content-type-options'] == 'nosniff'
        assert await collect(response) == source.body
        assert all(end - start + 1 <= 1024 * 1024 for start, end in source.calls)
    asyncio.run(run())


@pytest.mark.parametrize('value,status,expected', [
    ('bytes=0-3', 206, b'PK\x03\x04'), ('bytes=-3', 206, b'xxx'),
    ('bytes=9-2', 416, b''), ('bytes=0-1,3-4', 416, b''),
])
def test_upload_ranges(source, value, status, expected):
    async def run():
        response = await asgi.upload_content(request(headers=[(b'range', value.encode())]))
        assert response.status_code == status
        assert await collect(response) == expected
        assert 'content-range' in response.headers
        if status == 416:
            assert source.calls == []
    asyncio.run(run())


def test_conditionals_do_not_skip_identity_check(source, monkeypatch):
    etag = f'"{source.resolved["content_mtime_ns"]:x}-{len(source.body):x}"'
    response = asyncio.run(asgi.upload_content(request(headers=[(b'if-none-match', etag.encode())])))
    assert response.status_code == 304 and not source.calls
    monkeypatch.setattr(asgi, '_instruction_principal', lambda _request: ('', ''))
    response = asyncio.run(asgi.upload_content(request(headers=[(b'if-none-match', etag.encode())])))
    assert response.status_code == 401 and not source.calls


def test_later_ranges_reject_changed_file(source):
    async def run():
        response = await asgi.upload_content(request())
        source.resolved['content_mtime_ns'] += 1
        with pytest.raises(RuntimeError, match='resource changed'):
            await collect(response)
    asyncio.run(run())


def test_missing_file_is_reported_before_stream_headers(source):
    def missing(*args, **kwargs):
        raise FileNotFoundError('upload missing')
    source.service.read_content_range = missing
    response = asyncio.run(asgi.upload_content(request()))
    assert response.status_code == 404


def test_presentation_conversion_refusal_keeps_422(source, monkeypatch):
    from management_portal import presentation_preview

    source.service.read_content = lambda *_args, **_kwargs: (b'ppt', 'application/octet-stream',
                                                             {'filename': 'slides.pptx'})
    def refused(*_args):
        raise presentation_preview.PresentationPreviewError('converter unavailable')
    monkeypatch.setattr(presentation_preview, 'presentation_pdf', refused)
    response = asyncio.run(asgi.upload_content(request(query=b'preview=pdf')))
    assert response.status_code == 422 and b'converter unavailable' in response.body
