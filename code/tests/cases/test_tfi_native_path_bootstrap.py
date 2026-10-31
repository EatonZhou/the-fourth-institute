"""Public path projection: production HTML injection through real JS consumers.

TFI_PATH_BOOTSTRAP_SOURCE may point at an isolated page_bootstrap.py candidate.
TFI_PATH_CONSUMER_ROOT may point at the previously frozen native-path candidate.
No host configuration, database, network, browser, or model is used.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPO = Path(os.environ.get('TFI_TEST_REPO', Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO))
from management_common import portal_paths, site_config

DOCUMENT = b'''<!doctype html><html><head><meta charset="utf-8">
<title data-portal-site-title='["Login"]'>Login</title></head><body>
<span data-portal-site-name>Portal</span><p>User text remains unchanged</p></body></html>'''


@pytest.fixture
def installation(monkeypatch, tmp_path):
    install = tmp_path / 'installation with spaces'
    install.mkdir()
    monkeypatch.setattr(portal_paths, 'PORTAL_ROOT', install)
    monkeypatch.setattr(portal_paths, 'CONFIG_PATH', tmp_path / 'paths.json')
    monkeypatch.setattr(site_config, 'PORTAL_ROOT', install)
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(tmp_path / 'site.json'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    for key in [*portal_paths.ENV_KEYS.values(), 'PORTAL_SHARE_ROOT']:
        monkeypatch.delenv(key, raising=False)
    paths = {key: 'data/' + key for key in portal_paths.KEYS}
    paths.update(secrets_dir='PRIVATE-SECRET-ROOT')
    def write_paths(**changes):
        paths.update(changes)
        portal_paths.CONFIG_PATH.write_text(json.dumps(paths))
    def write_site(value):
        (tmp_path / 'site.json').write_text(json.dumps(value))
    write_paths()
    source = Path(os.environ.get('TFI_PATH_BOOTSTRAP_SOURCE', REPO / 'management_portal/page_bootstrap.py'))
    spec = importlib.util.spec_from_file_location('private_native_path_bootstrap', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Candidate keeps deployment-relative static paths. Only the isolated test
    # supplies the production assets from the shared checkout.
    module._SITE_SCRIPT = REPO / 'management_portal/static/portal_site.js'
    module._SCRIPT = REPO / 'management_portal/static/portal_page_boot.js'
    return SimpleNamespace(module=module, install=install, write_paths=write_paths, write_site=write_site, tmp=tmp_path)


def injected(installation):
    data = installation.module.inject_page_bootstrap(DOCUMENT)
    start = data.index(b'<script id="portalSiteScript">') + len(b'<script id="portalSiteScript">')
    script = data[start:data.index(b'</script>', start)].decode()
    source = installation.module._SITE_SCRIPT.read_text().replace('</script', '<\\/script')
    assert script.startswith(source + '(') and script.endswith(');')
    return data, json.loads(script[len(source) + 1:-2]), script


def test_defaults_refresh_and_whitelisted_projection(installation, monkeypatch):
    data, config, _ = injected(installation)
    from management_common.site_config import browser_storage_namespace
    assert config == {'storage_namespace': browser_storage_namespace(), 'site_name': 'TFI Portal', 'paths': {
        'share_root': str(portal_paths.path('share_root')),
        'staging_root': str(portal_paths.path('staging_root')),
        'test_results_root': str(installation.install / 'test-results'),
    }, 'compatibility': {'browser_identifiers': {}, 'format_namespaces': []}}
    assert b'PRIVATE-ACCOUNT-ROOT' not in data and b'PRIVATE-SECRET-ROOT' not in data
    installation.write_site({'site_name': 'Custom site', 'private_key': 'PRIVATE-KEY', 'deployment': {
        'native_ui': {'test_results_root': 'runs/artifacts', 'private_key': 'PRIVATE-UI-KEY'},
        'native_shell': {'database_env_file': '/private/DBENV-MARKER'},
    }})
    elsewhere = installation.tmp / 'cwd'; elsewhere.mkdir(); monkeypatch.chdir(elsewhere)
    installation.write_paths(share_root='updated-share')
    data, config, _ = injected(installation)
    assert config['site_name'] == 'Custom site'
    assert config['paths']['share_root'] == str(installation.install / 'updated-share')
    assert config['paths']['test_results_root'] == str(installation.install / 'runs/artifacts')
    for hidden in [b'PRIVATE-', b'DBENV-MARKER', b'database_env_file', b'deployment', b'secrets_dir']:
        assert hidden not in data


INVALID = ['', ' ', None, False, 7, [], {}, '/', '///', '../private', '/data/../private',
           '/data/PRIVATE-MARKER\x00', '/data/PRIVATE-MARKER\n', '/data/PRIVATE-MARKER\t',
           '/data/PRIVATE-MARKER\x7f', '/data/PRIVATE-MARKER\x85', '/data\\PRIVATE-MARKER']


def test_old_browser_configuration_is_not_projected(installation):
    installation.write_site({'site_name': 'Current site', 'deployment': {
        'native_ui': {'test_results_root': 'current/reports'},
        'compatibility': {'format_namespaces': ['archive.'], 'domain_values': {
            'browser_identifiers': {'theme': 'archive.theme', 'page_state': 'archive.state',
                                    'private_token': 'PRIVATE-TOKEN'},
        }},
    }})
    data, config, _ = injected(installation)
    assert config['compatibility'] == {'browser_identifiers': {}, 'format_namespaces': []}
    assert config['site_name'] == 'Current site'
    assert config['paths']['test_results_root'] == str(installation.install / 'current/reports')
    assert b'archive.' not in data and b'PRIVATE-TOKEN' not in data


@pytest.mark.parametrize('key', ['share_root', 'staging_root', 'test_results_root'])
@pytest.mark.parametrize('invalid', INVALID)
def test_invalid_root_fails_without_exposing_value(installation, key, invalid):
    if key == 'test_results_root':
        installation.write_site({'deployment': {'native_ui': {key: invalid}}})
    else:
        installation.write_paths(**{key: invalid})
    with pytest.raises(ValueError) as caught:
        injected(installation)
    assert 'PRIVATE-MARKER' not in str(caught.value)


@pytest.mark.parametrize('value', [None, [], '', 7, {'native_ui': None}, {'native_ui': []}])
def test_malformed_deployment_does_not_silently_fallback(installation, value):
    installation.write_site({'deployment': value})
    with pytest.raises(ValueError):
        injected(installation)


def test_resolved_root_is_rejected_and_home_path_is_anchored(installation):
    root_link = installation.tmp / 'root-link'; root_link.symlink_to('/')
    installation.write_site({'deployment': {'native_ui': {'test_results_root': str(root_link)}}})
    with pytest.raises(ValueError):
        injected(installation)
    installation.write_site({'deployment': {'native_ui': {'test_results_root': '~/artifacts'}}})
    _, config, _ = injected(installation)
    assert config['paths']['test_results_root'] == str(installation.tmp / 'home/artifacts')


def test_script_context_and_site_fields_remain_safe(installation):
    name = 'Study "A" <Lab> & </script><script>globalThis.injectedAttack=1</script>\u2028\u2029 End'
    path = '/data/"<&>/run\u2028\u2029'
    installation.write_site({'site_name': name, 'deployment': {'native_ui': {'test_results_root': path}}})
    data, config, script = injected(installation)
    assert config['site_name'] == name
    assert config['paths']['test_results_root'] == path
    assert data.count(b'</script>') == 1
    serialized = script[len(installation.module._SITE_SCRIPT.read_text()) + 1:-2]
    assert all(value not in serialized for value in ['<', '>', '&', '\u2028', '\u2029'])
    assert all(value in serialized for value in ['\\u003c', '\\u003e', '\\u0026', '\\u2028', '\\u2029'])
    assert b'Login \xe2\x80\x94 Study &quot;A&quot; &lt;Lab&gt;' in data


def test_skipped_and_already_branded_documents_do_not_read_config(installation, monkeypatch):
    branded, _, _ = injected(installation)
    def unexpected():
        raise AssertionError('should not reread deployment configuration')
    monkeypatch.setattr(site_config, 'site_name', unexpected)
    monkeypatch.setattr(site_config, 'read_site_config', unexpected)
    monkeypatch.setattr(portal_paths, 'path_values', unexpected)
    for value in [branded, b'<html><head><title>User preview</title></head></html>', b'<title data-portal-site-title="[]">Title</title>']:
        assert installation.module.inject_page_bootstrap(value) == value


JS_CONSUMERS = r'''
const fs=require('node:fs'),assert=require('node:assert/strict');
const {createRequire}=require('node:module');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {JSDOM}=createRequire(input.repo+'/package.json')('jsdom');
const dom=new JSDOM(input.html,{url:'https://native-path.fixture.invalid/',runScripts:'outside-only'});
const w=dom.window;
w.eval(w.document.querySelector('#portalSiteScript').textContent);
w.PortalSite.apply();
assert.equal(Object.isFrozen(w.__PORTAL_SITE__),true);
assert.deepEqual(JSON.parse(JSON.stringify(w.__PORTAL_SITE__)),input.expected);
assert.equal(w.document.title,'Login — '+input.expected.site_name);
assert.equal(w.document.querySelector('[data-portal-site-name]').textContent,input.expected.site_name);
assert.equal(w.injectedAttack,undefined);
w.PortalI18n={t:value=>value};
for(const name of ['portal_ui_primitives.js','portal_preview_contract.js','portal_file_links.js','markdown.js']){
 const root=['portal_file_links.js','markdown.js'].includes(name)?input.consumers:input.repo;
 w.eval(fs.readFileSync(root+'/management_portal/static/'+name,'utf8'));
}
const p=input.expected.paths;
assert.equal(w.PortalFileLinks.normalizePath('/nas_ro/report.md'),p.share_root+'/report.md');
for(const root of Object.values(p)){
 const file=root+'/diagram.png';
 const container=w.document.createElement('div');
 container.innerHTML=w.MD.render('![fixture](<file://'+file+'>)');
 assert.equal(container.querySelector('img')?.getAttribute('src'),'/api/agent-workbench/link-file-raw?path='+encodeURIComponent(file));
}
// The projection selects a transport, not a new authorization or URL route.
const container=w.document.createElement('div');container.innerHTML=w.MD.render('![public](/share/public.png)');
assert.equal(container.querySelector('img').getAttribute('src'),'/share/public.png');
dom.window.close();
console.log('Production bootstrap reached the actual FileLinks/Markdown path readers.');
'''


def test_production_injection_reaches_actual_js_consumers(installation):
    installation.write_site({'site_name': 'Named "site" & literal', 'deployment': {'native_ui': {'test_results_root': 'reports/latest'}}})
    installation.write_paths(share_root='public files', staging_root='staging files')
    data, config, _ = injected(installation)
    expected = {'site_name': 'Named "site" & literal', 'paths': {
        'share_root': str(installation.install / 'public files'),
        'staging_root': str(installation.install / 'staging files'),
        'test_results_root': str(installation.install / 'reports/latest'),
    }}
    expected['compatibility'] = {'browser_identifiers': {}, 'format_namespaces': []}
    from management_common.site_config import browser_storage_namespace
    expected["storage_namespace"] = browser_storage_namespace()
    assert config == expected
    result = subprocess.run(['node', '-e', JS_CONSUMERS], input=json.dumps({
        'repo': str(REPO), 'consumers': os.environ.get('TFI_PATH_CONSUMER_ROOT', str(REPO)),
        'html': data.decode(), 'expected': expected,
    }), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'actual FileLinks/Markdown' in result.stdout


from tests.cases.compatibility_test_support import setUpModule, tearDownModule, site_environ
