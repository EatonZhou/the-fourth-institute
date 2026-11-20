"""Presentation conversion and authorized HTTP content boundaries.

:design: decision_61e50306cc79
"""
from io import BytesIO
import asyncio
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlsplit
import zipfile

import pytest

from management_portal import presentation_preview as preview
from management_portal.http_agent_workbench import AgentWorkbenchHttp
from management_portal.http_articles import ArticlesHttp
from management_portal.http_resources import ResourceHttp


def sample_pptx():
    """Two slides with text and a colored shape; no user data or external URLs."""
    output = BytesIO()
    p = 'http://schemas.openxmlformats.org/presentationml/2006/main'
    a = 'http://schemas.openxmlformats.org/drawingml/2006/main'
    r = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                         '<Default Extension="xml" ContentType="application/xml"/>'
                         '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                         + ''.join(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' for i in (1, 2)) + '</Types>')
        archive.writestr('_rels/.rels', f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{r}/officeDocument" Target="ppt/presentation.xml"/></Relationships>')
        archive.writestr('ppt/presentation.xml', f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId id="256" r:id="rId1"/><p:sldId id="257" r:id="rId2"/></p:sldIdLst><p:sldSz cx="9144000" cy="5143500"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        archive.writestr('ppt/_rels/presentation.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="{r}/slide" Target="slides/slide{i}.xml"/>' for i in (1, 2)) + '</Relationships>')
        for i, title in ((1, 'Portal Preview 中文幻灯片'), (2, 'Second slide')):
            archive.writestr(f'ppt/slides/slide{i}.xml', f'''<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
<p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="400000" y="400000"/><a:ext cx="8300000" cy="1200000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:solidFill><a:srgbClr val="DBEAFE"/></a:solidFill></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="zh-CN" sz="2800"><a:latin typeface="Noto Sans"/><a:ea typeface="Noto Sans CJK SC"/></a:rPr><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp>
</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>''')
    return output.getvalue()


@pytest.fixture
def converter(tmp_path, monkeypatch):
    from tests.cases.instance_test_support import select_instance
    select_instance(monkeypatch, tmp_path / 'instance')
    monkeypatch.setattr(preview, 'libreoffice', lambda: 'fixture-soffice')
    def convert(command, **options):
        Path(options['cwd'], 'slides.pdf').write_bytes(b'%PDF-1.4\nfixture')
        return SimpleNamespace(returncode=0)
    run = Mock(side_effect=convert)
    monkeypatch.setattr(preview, 'run_process', run)
    return run


@pytest.mark.parametrize('applications', ['/Applications', '/service-home/Applications'])
def test_discovers_macos_application_outside_path(tmp_path, monkeypatch, applications):
    monkeypatch.delenv('PORTAL_LIBREOFFICE', raising=False)
    monkeypatch.setattr(preview.sys, 'platform', 'darwin')
    monkeypatch.setattr(preview.Path, 'home', lambda: Path('/service-home'))
    monkeypatch.setattr(preview, 'instance_runtime_root', lambda: tmp_path)
    executable = f'{applications}/LibreOffice.app/Contents/MacOS/soffice'
    monkeypatch.setattr(preview.shutil, 'which', lambda command: executable if command == executable else None)

    assert preview.libreoffice() == executable
    monkeypatch.setenv('PORTAL_LIBREOFFICE', '/custom/soffice')
    assert preview.libreoffice() == '/custom/soffice'


def test_cache_uses_content_and_releases_conversion_slot(converter):
    first = preview.presentation_pdf(b'first', 'slides.pptx')
    assert preview.presentation_pdf(b'first', 'renamed.pptx') == first
    assert converter.call_count == 1
    assert preview.presentation_pdf(b'changed', 'slides.pptx') != first
    assert converter.call_count == 2
    assert not list(first.parent.glob('convert-*'))


def test_cached_pdf_does_not_require_converter(converter, monkeypatch):
    first = preview.presentation_pdf(b'unchanged', 'slides.pptx')
    original = first.stat()
    unavailable = Mock(side_effect=preview.PresentationPreviewError('converter unavailable'))
    monkeypatch.setattr(preview, 'libreoffice', unavailable)
    for filename in ('slides.pptx', 'renamed.PPTX'):
        assert preview.presentation_pdf(b'unchanged', filename) == first
        assert first.read_bytes().startswith(b'%PDF-')
    unavailable.assert_not_called()
    assert converter.call_count == 1
    assert first.stat().st_mtime_ns == original.st_mtime_ns
    # A changed document or a different source format must not reuse stale PDF.
    for data, filename in ((b'changed', 'slides.pptx'), (b'unchanged', 'slides.ppt')):
        with pytest.raises(preview.PresentationPreviewError, match='converter unavailable'):
            preview.presentation_pdf(data, filename)
    assert first.exists()
    assert not preview._conversion_slot.locked()


def test_presentation_cache_is_not_deleted_by_age(converter):
    import os
    import time
    first = preview.presentation_pdf(b'old', 'slides.pptx')
    old = time.time() - 8 * 86400
    os.utime(first, (old, old))
    preview.presentation_pdf(b'new', 'slides.pptx')
    assert first.exists()


def test_video_deck_above_64_mib_can_preview(tmp_path, converter):
    # Embedded videos can make an otherwise ordinary presentation exceed 64 MiB.
    source = tmp_path / 'deck-with-videos.pptx'
    size = 65 * 1024 * 1024
    with source.open('wb') as stream:
        stream.write(b'PK\x03\x04')
        stream.truncate(size)
    data = preview.read_presentation(source)
    assert len(data) == size
    pdf = preview.presentation_pdf(data, source.name)
    assert pdf.read_bytes().startswith(b'%PDF-')
    assert converter.call_count == 1
    assert source.stat().st_size == size
    with source.open('rb') as stream:
        assert stream.read(4) == b'PK\x03\x04'


def test_oversized_source_rejected_before_reading_or_converting(tmp_path, converter):
    source = tmp_path / 'oversized.pptx'
    with source.open('wb') as stream:
        stream.truncate(preview.MAX_SOURCE_BYTES + 1)
    with pytest.raises(preview.PresentationPreviewError, match='预览上限'):
        preview.read_presentation(source)
    converter.assert_not_called()


def test_conversion_errors_limits_and_cleanup(converter, monkeypatch):
    with pytest.raises(preview.PresentationPreviewError, match='PPT'):
        preview.presentation_pdf(b'file', 'slides.docx')
    monkeypatch.setattr(preview, 'MAX_SOURCE_BYTES', 4)
    with pytest.raises(preview.PresentationPreviewError, match='预览上限'):
        preview.presentation_pdf(b'large', 'slides.ppt')
    converter.side_effect = subprocess.TimeoutExpired('soffice', 60)
    with pytest.raises(preview.PresentationPreviewError, match='超时'):
        preview.presentation_pdf(b'file', 'slides.ppt')
    converter.side_effect = lambda *args, **kwargs: SimpleNamespace(returncode=1)
    with pytest.raises(preview.PresentationPreviewError, match='损坏或已加密'):
        preview.presentation_pdf(b'file', 'slides.ppt')
    assert not preview._conversion_slot.locked()


def test_resource_preview_authorizes_each_request_and_download_stays_original(tmp_path, monkeypatch):
    handler = ResourceHttp.__new__(ResourceHttp)
    service = Mock()
    handler.resource_service = lambda: service
    service.read_content.return_value = (b'original', 'application/vnd.ms-powerpoint', {'filename': 'slides.ppt'})
    pdf = tmp_path / 'cached.pdf'
    convert = Mock(return_value=pdf)
    monkeypatch.setattr(preview, 'presentation_pdf', convert)
    handler.media_file = Mock(return_value='pdf')
    handler.resource_error = lambda _request, error: error
    url = urlsplit('/api/resources/content?address=workspace_0123456789ab/file/slides.ppt&preview=pdf')
    assert handler.content(None, url, 'viewer') == 'pdf'
    service.read_content.assert_called_once_with('workspace_0123456789ab/file/slides.ppt', actor='viewer', max_bytes=preview.MAX_SOURCE_BYTES)
    service.read_content.side_effect = PermissionError('revoked')
    assert isinstance(handler.content(None, url, 'viewer'), PermissionError)
    assert convert.call_count == 1
    original = tmp_path / 'slides.ppt'
    original.write_bytes(b'original')
    service.open_content.return_value = {'resolved_path': str(original)}
    handler.download_file = Mock(return_value='original')
    assert handler.content(None, urlsplit(url.geturl() + '&download=1'), 'viewer') == 'original'
    handler.download_file.assert_called_once_with(None, original)


@pytest.mark.parametrize('suffix', ['.ppt', '.pptx'])
def test_article_preview_authorizes_caches_and_preserves_original(tmp_path, monkeypatch, converter, suffix):
    from management_portal import article_registry, article_files
    article_id = 'article_0123456789ab'
    root = tmp_path / 'article'
    root.mkdir()
    source = root / ('幻灯片' + suffix)
    source.write_bytes(b'original presentation')
    monkeypatch.setattr(article_registry, '_article_source_context', lambda _id: ({}, {}, root))
    handler = ArticlesHttp.__new__(ArticlesHttp)
    handler.session_actor = lambda _request: 'viewer'
    handler.require_resource = Mock()
    handler.respond = lambda _request, status, data: (status, data)
    handler.exception_response = lambda _request, error: (403, str(error))
    handler.serve_media = lambda _request, path, media: (path, media)
    handler.serve_download = lambda _request, path: ('download', path)
    handler.media_content_type = lambda _path: 'application/vnd.ms-powerpoint'
    params = {'article_id': article_id, 'action': 'raw'}
    metadata = article_files.describe_article_file(article_id, source.name)
    url = metadata['rawUrl'] + '&preview=pdf'
    read = lambda target=url: handler.workspace_read(None, urlsplit(target), 'ignored', params)
    pdf, media = read()
    assert media == 'application/pdf' and pdf.read_bytes().startswith(b'%PDF-')
    assert pdf != source and source.read_bytes() == b'original presentation'
    assert read() == (pdf, media)
    assert converter.call_count == 1
    assert handler.require_resource.call_args_list == [((article_id, 'viewer'),)] * 2
    assert read(url + '&download=1') == ('download', source)
    assert read(metadata['downloadUrl']) == ('download', source)
    assert read(metadata['rawUrl']) == (source, 'application/vnd.ms-powerpoint')
    assert converter.call_count == 1
    # Authorization runs even with a ready PDF; revoked users never reach the cache.
    handler.require_resource.side_effect = PermissionError('revoked')
    assert read()[0] == 403
    assert converter.call_count == 1
    handler.require_resource.side_effect = None
    # The existing Article resolver still rejects traversal and symlink escapes.
    outside = tmp_path / ('outside' + suffix)
    outside.write_bytes(b'outside')
    (root / ('escape' + suffix)).symlink_to(outside)
    for relative in ('../' + outside.name, 'escape' + suffix):
        assert read(f'/api/articles/{article_id}/raw?path={relative}&preview=pdf')[0] == 400
    assert read(f'/api/articles/{article_id}/raw?path=missing.ppt&preview=pdf')[0] == 404
    assert converter.call_count == 1
    source.write_bytes(b'changed presentation')
    assert read()[0] != pdf
    assert converter.call_count == 2
    source.write_bytes(b'broken presentation')
    converter.side_effect = lambda *a, **kw: SimpleNamespace(returncode=1)
    status, error = read()
    assert status == 400 and '损坏或已加密' in error['error']
    assert read(metadata['downloadUrl']) == ('download', source)


def test_local_file_permission_checked_before_conversion(tmp_path, monkeypatch):
    handler = AgentWorkbenchHttp.__new__(AgentWorkbenchHttp)
    api = Mock()
    handler.workspace_api = lambda: api
    handler.session_actor = lambda request: 'viewer'
    handler.resolved_path = lambda value, _description: Path(value)
    handler.link_permission_denied = lambda request: 'denied'
    handler.error = lambda request, error: error
    handler.download_file = Mock(return_value='original')
    handler.media_file = Mock(return_value='pdf')
    original = tmp_path / 'slides.pptx'
    original.write_bytes(b'original')
    api.resolve_workspace_link_file_path.return_value = original
    convert = Mock(return_value=tmp_path / 'preview.pdf')
    monkeypatch.setattr(preview, 'presentation_pdf', convert)
    url = urlsplit('/api/agent-workbench/link-file-raw?path=/slides.pptx&preview=pdf')
    assert handler.get_link_file_raw(None, url, 'viewer') == 'pdf'
    api.resolve_workspace_link_file_path.assert_called_once_with('/slides.pptx', allow_outside_roots=False, actor='viewer')
    assert handler.get_link_file_raw(None, urlsplit(url.geturl() + '&download=1'), 'viewer') == 'original'
    api.resolve_workspace_link_file_path.side_effect = PermissionError('denied')
    assert handler.get_link_file_raw(None, url, 'viewer') == 'denied'
    assert convert.call_count == 1


def test_upload_preview_uses_authenticated_metadata(tmp_path, monkeypatch):
    from management_portal import asgi
    from management_portal import resources
    from starlette.requests import Request
    source = tmp_path / 'presentation.pptx'
    source.write_bytes(b'original')
    meta = {'filename': source.name, 'content_size': 8, 'content_mtime_ns': 10,
            'media_type': 'application/octet-stream', 'target_kind': 'file'}
    read = Mock(return_value=(b'original', meta['media_type'], meta))
    service = SimpleNamespace(resolve=Mock(return_value=meta), read_content=read, read_content_range=read)
    monkeypatch.setattr(resources, 'get_resource_service', lambda: service)
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('viewer', 'web'))
    convert = Mock(return_value=tmp_path / 'result.pdf')
    monkeypatch.setattr(preview, 'presentation_pdf', convert)
    scope = {'type': 'http', 'method': 'GET', 'scheme': 'http', 'path': '/api/uploads/upload_0123456789abcdef01234567',
             'headers': [], 'query_string': b'preview=pdf', 'server': ('testserver', 80),
             'path_params': {'upload_id': 'upload_0123456789abcdef01234567'}}
    request = Request(scope)
    response = asyncio.run(asgi.upload_content(request))
    assert response.status_code == 200
    assert response.media_type == 'application/pdf'
    convert.assert_called_once_with(b'original', 'presentation.pptx')
    async def download():
        response = await asgi.upload_content(Request({**scope, 'query_string': b'preview=pdf&download=1'}))
        return b''.join([part async for part in response.body_iterator])
    assert asyncio.run(download()) == b'original'
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('', 'web'))
    assert asyncio.run(asgi.upload_content(request)).status_code == 401
    assert read.call_count == 2


@pytest.mark.skipif(os.environ.get('PORTAL_TEST_PRESENTATIONS') != '1', reason='requires LibreOffice and Poppler')
def test_real_pptx_and_legacy_ppt(tmp_path, monkeypatch):
    executable = preview.libreoffice()
    from tests.cases.instance_test_support import select_instance
    select_instance(monkeypatch, tmp_path / 'instance')
    monkeypatch.setattr(preview, 'libreoffice', lambda: executable)
    convert = Mock(wraps=preview.run_process)
    monkeypatch.setattr(preview, 'run_process', convert)
    data = sample_pptx()
    source = tmp_path / 'slides.pptx'
    source.write_bytes(data)
    pdf = preview.presentation_pdf(data, source.name)
    assert b'Pages:           2' in subprocess.check_output(['pdfinfo', str(pdf)])
    text = subprocess.check_output(['pdftotext', str(pdf), '-']).decode()
    assert 'Portal Preview' in text and '中文幻灯片' in text and 'Second slide' in text
    before = pdf.stat().st_mtime_ns
    assert preview.presentation_pdf(data, 'renamed.pptx') == pdf
    assert pdf.stat().st_mtime_ns == before
    assert convert.call_count == 1
    preview.run_process([executable, '-env:UserInstallation=' + (tmp_path / 'export-profile').as_uri(),
                         '--headless', '--convert-to', 'ppt:MS PowerPoint 97', '--outdir', str(tmp_path), str(source)],
                        capture_output=True, timeout=60, check=True)
    legacy = preview.presentation_pdf(source.with_suffix('.ppt').read_bytes(), 'slides.ppt')
    assert b'Pages:           2' in subprocess.check_output(['pdfinfo', str(legacy)])
    assert 'Second slide' in subprocess.check_output(['pdftotext', str(legacy), '-']).decode()
    calls = convert.call_count
    assert preview.presentation_pdf(source.with_suffix('.ppt').read_bytes(), 'renamed.ppt') == legacy
    assert convert.call_count == calls


def test_browser_contract():
    subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'presentation_preview.cjs')], check=True, timeout=30)


@pytest.mark.parametrize('mode', [[], ['--article']], ids=['shared-preview', 'article-file'])
def test_chromium_renders_converted_pdf_pages_zoom_and_original_download(mode):
    """共享 Preview 在 Chromium 中逐页渲染演示文稿转换出的 PDF，可缩放且下载原文件

    用两页 PDF 夹具代替 LibreOffice 输出，经支持 Range 的本地内容路由读取；核对页数、首尾页文字、滚动渲染、放大和原 slides.pptx 下载。

    :module: workbench.preview
    """
    pdf = Path(__file__).with_name('fixtures') / 'paper-synthetic-counts.pdf'
    result = subprocess.run(
        ['node', str(Path(__file__).with_name('javascript') / 'presentation_preview.browser.cjs'),
         str(pdf), '2', 'Synthetic Counts Study', 'TAU SYNTHETIC SAMPLE - continued', *mode],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
