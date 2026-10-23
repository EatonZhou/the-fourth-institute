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
    monkeypatch.setattr(preview, 'instance_root', lambda: tmp_path)
    monkeypatch.setattr(preview, 'libreoffice', lambda: 'fixture-soffice')
    def convert(command, **options):
        Path(options['cwd'], 'slides.pdf').write_bytes(b'%PDF-1.4\nfixture')
        return SimpleNamespace(returncode=0)
    run = Mock(side_effect=convert)
    monkeypatch.setattr(preview, 'run_process', run)
    return run


def test_cache_uses_content_and_releases_conversion_slot(converter):
    first = preview.presentation_pdf(b'first', 'slides.pptx')
    assert preview.presentation_pdf(b'first', 'renamed.pptx') == first
    assert converter.call_count == 1
    assert preview.presentation_pdf(b'changed', 'slides.pptx') != first
    assert converter.call_count == 2
    assert not list(first.parent.glob('convert-*'))


def test_conversion_errors_limits_and_cleanup(converter, monkeypatch):
    with pytest.raises(preview.PresentationPreviewError, match='PPT'):
        preview.presentation_pdf(b'file', 'slides.docx')
    monkeypatch.setattr(preview, 'MAX_SOURCE_BYTES', 4)
    with pytest.raises(preview.PresentationPreviewError, match='64 MiB'):
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
    from management_portal.resources import isa
    source = tmp_path / 'presentation.pptx'
    source.write_bytes(b'original')
    meta = {'ready': True, 'file_name': source.name, 'path': str(source), 'media_type': 'application/octet-stream'}
    read = Mock(return_value=meta)
    monkeypatch.setattr(isa, 'read_upload', read)
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('viewer', 'web'))
    convert = Mock(return_value=tmp_path / 'result.pdf')
    monkeypatch.setattr(preview, 'presentation_pdf', convert)
    request = SimpleNamespace(path_params={'upload_id': 'upload_0123456789abcdef01234567'}, query_params={'preview': 'pdf'})
    response = asyncio.run(asgi.upload_content(request))
    assert response.status_code == 200
    assert response.media_type == 'application/pdf'
    convert.assert_called_once_with(b'original', 'presentation.pptx')
    request.query_params['download'] = '1'
    response = asyncio.run(asgi.upload_content(request))
    assert response.body == b'original'
    monkeypatch.setattr(asgi, '_instruction_principal', lambda request: ('', 'web'))
    assert asyncio.run(asgi.upload_content(request)).status_code == 401
    assert read.call_count == 2


@pytest.mark.skipif(os.environ.get('PORTAL_TEST_PRESENTATIONS') != '1', reason='requires LibreOffice and Poppler')
def test_real_pptx_and_legacy_ppt(tmp_path, monkeypatch):
    executable = preview.libreoffice()
    monkeypatch.setattr(preview, 'instance_root', lambda: tmp_path)
    monkeypatch.setattr(preview, 'libreoffice', lambda: executable)
    data = sample_pptx()
    source = tmp_path / 'slides.pptx'
    source.write_bytes(data)
    pdf = preview.presentation_pdf(data, source.name)
    assert b'Pages:           2' in subprocess.check_output(['pdfinfo', str(pdf)])
    text = subprocess.check_output(['pdftotext', str(pdf), '-']).decode()
    assert 'Portal Preview' in text and '中文幻灯片' in text and 'Second slide' in text
    preview.run_process([executable, '-env:UserInstallation=' + (tmp_path / 'export-profile').as_uri(),
                         '--headless', '--convert-to', 'ppt:MS PowerPoint 97', '--outdir', str(tmp_path), str(source)],
                        capture_output=True, timeout=60, check=True)
    legacy = preview.presentation_pdf(source.with_suffix('.ppt').read_bytes(), 'slides.ppt')
    assert b'Pages:           2' in subprocess.check_output(['pdfinfo', str(legacy)])
    assert 'Second slide' in subprocess.check_output(['pdftotext', str(legacy), '-']).decode()


def test_browser_contract():
    subprocess.run(['node', str(Path(__file__).with_name('javascript') / 'presentation_preview.cjs')], check=True, timeout=30)
