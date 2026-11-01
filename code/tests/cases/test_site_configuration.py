"""Site identity uses one fresh config, without changing authentication identity."""
import html
import json
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from management_common import site_config
from tests.cases.instance_test_support import write_instance


@pytest.fixture
def configured_site(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    write_instance(tmp_path)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(tmp_path))
    def write(name, **values):
        path.write_text(json.dumps({'site_name': name, **values}))
    return path, write


def test_default_fresh_read_and_deployment_keys(configured_site):
    path, write = configured_site
    assert site_config.site_name() == 'TFI Portal'
    write('  Institute One  ', deployment={'pulse': {'lineage_id': 'example'}})
    assert site_config.site_name() == 'Institute One'
    assert site_config.read_site_config()['deployment']['pulse']['lineage_id'] == 'example'
    write('Institute Two')
    assert site_config.site_name() == 'Institute Two'
    path.unlink()
    with pytest.raises(FileNotFoundError):
        site_config.site_name()


@pytest.mark.parametrize('value', ['', '  ', None, 23, [], 'Lab\nBcc: unwanted', 'Lab\x00'])
def test_invalid_display_identity_is_rejected(configured_site, value):
    _, write = configured_site
    write(value)
    with pytest.raises(ValueError, match='site_name'):
        site_config.site_name()


def test_explicit_manifest_selection_ignores_cwd_and_previous_locators(tmp_path, monkeypatch):
    root = write_instance(tmp_path / 'installation', site={'site_name': 'Portable Institute'})
    elsewhere = tmp_path / 'elsewhere'; elsewhere.mkdir()
    (elsewhere / 'site.json').write_text('{"site_name":"Unselected"}')
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    assert site_config.site_name() == 'Portable Institute'
    monkeypatch.delenv('PORTAL_INSTANCE_ROOT')
    with pytest.raises(ValueError, match='Select an instance'):
        site_config.site_name()


def test_browser_namespace_comes_from_manifest_and_survives_move(tmp_path, monkeypatch):
    import shutil
    root = write_instance(tmp_path / 'original')
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(root))
    before = site_config.browser_storage_namespace()
    target = tmp_path / 'moved'
    shutil.move(root, target)
    monkeypatch.setenv('PORTAL_INSTANCE_ROOT', str(target))
    (target / 'site.json').write_text('{"site_name":"Renamed"}')
    assert site_config.browser_storage_namespace() == before
    doc = json.loads((target / 'instance.json').read_text())
    doc['instance_id'] = 'another-instance'
    (target / 'instance.json').write_text(json.dumps(doc))
    assert site_config.browser_storage_namespace() != before


def test_anonymous_web_response_uses_reader_and_escapes_display_name(configured_site, monkeypatch):
    from management_portal import serve_management_portal as portal
    from management_portal import http_auth, http_documents, http_responses
    _, write = configured_site
    name = '研究\u2028 "A" <Lab> & </script><script>bad()</script>'
    write(name, deployment={'secret_not_public': 'not-in-bootstrap'})
    monkeypatch.setattr(http_auth, '_get_session_username', lambda _: '')
    monkeypatch.setattr(http_responses, '_serve_payload', lambda *args, **kwargs: args[2])
    doc = b'''<html><head><title data-portal-site-title='["Login"]'>Login</title></head><body>
    <a data-portal-site-name>Portal</a><p>User content</p></body></html>'''
    result = http_documents._serve_bytes(None,200,doc,'text/html').decode()
    assert 'not-in-bootstrap' not in result
    assert result.count('</script>') == 1
    assert html.unescape(re.search(r'<title[^>]*>(.*?)</title>',result,re.S).group(1)) == 'Login — '+name
    assert '<script>bad()</script>' not in result
    assert '<p>User content</p>' in result


def test_mail_totp_and_passkey_display_share_name_without_changing_identity(configured_site, monkeypatch):
    from management_portal.portal_password_reset import PortalSmtpMailer
    from management_portal.portal_two_factor import otpauth_uri, totp_code
    from management_portal.portal_passkeys import PortalPasskeyService
    _, write = configured_site
    write('Research Institute')
    messages=[]
    mailer=PortalSmtpMailer(host='mail.example', port=465, username='', password='', sender='sender@example.test', public_origin='https://site.example', security='ssl')
    monkeypatch.setattr(mailer, '_send', messages.append)
    mailer.send_reset('reader@example.test','sample_token')
    mailer.send_password_changed('reader@example.test')
    assert all('Research Institute' in m['Subject'] and 'Research Institute' in m.get_content() for m in messages)
    secret='JBSWY3DPEHPK3PXP'
    code=totp_code(secret,at_time=60)
    uri=otpauth_uri('reader',secret)
    assert parse_qs(urlsplit(uri).query)['issuer']==['Research Institute']
    assert parse_qs(urlsplit(otpauth_uri('reader',secret,issuer='Existing issuer')).query)['issuer']==['Existing issuer']
    service=PortalPasskeyService()
    service.store=SimpleNamespace(user_handle=lambda user:b'fixed-user-handle', credential_descriptors=lambda user:[])
    registered=service.begin_registration('reader', display_name='Reader', rp_id='site.example',origin='https://site.example')
    assert registered['options']['rp']=={'id':'site.example','name':'Research Institute'}
    record=service._consume(registered['ceremony_token'],'registration','reader')
    assert record['origin']=='https://site.example' and record['rp_id']=='site.example'
    write('Renamed Institute')
    assert totp_code(secret,at_time=60)==code
    assert parse_qs(urlsplit(otpauth_uri('reader',secret)).query)['issuer']==['Renamed Institute']


def test_knowledge_title_uses_actual_translated_template(configured_site):
    from management_portal.page_bootstrap import inject_site_name
    from management_portal.page_renderers import render_knowledge_html
    _, write = configured_site
    write('Research & Learning')
    result = inject_site_name(render_knowledge_html(resource_category='experiment')).decode()
    title = re.search(r'<title[^>]*>(.*?)</title>', result, re.S).group(1)
    assert html.unescape(title) == 'Experiment — Resource — Research & Learning'
    assert 'data-portal-site-title' in result
    assert 'data-portal-i18n-text="&quot;Knowledge Board&quot;"' not in result
