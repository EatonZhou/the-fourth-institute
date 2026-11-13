"""Public path projection: production HTML injection through real JS consumers.

TFI_PATH_BOOTSTRAP_SOURCE may point at an isolated page_bootstrap.py candidate.
TFI_PATH_CONSUMER_ROOT may point at the previously frozen native-path candidate.
The paths come from the selected instance's manifest. No host configuration,
database, network, browser, or model is used.
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
from management_common import instance_manifest, site_config
from management_common.instance_manifest import AREAS
from tests.cases.instance_test_support import select_instance, write_instance

DOCUMENT = b'''<!doctype html><html><head><meta charset="utf-8">
<title data-portal-site-title='["Login"]'>Login</title></head><body>
<span data-portal-site-name>Portal</span><p>User text remains unchanged</p></body></html>'''


@pytest.fixture
def installation(monkeypatch, tmp_path):
    root = tmp_path / 'installation with spaces'
    manifest = select_instance(monkeypatch, root, site={})
    def write_site(value):
        (root / 'site.json').write_text(json.dumps(value))
    def write_areas(**areas):
        write_instance(root, areas=areas)
    source = Path(os.environ.get('TFI_PATH_BOOTSTRAP_SOURCE', REPO / 'management_portal/page_bootstrap.py'))
    spec = importlib.util.spec_from_file_location('private_native_path_bootstrap', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Candidate keeps deployment-relative static paths. Only the isolated test
    # supplies the production assets from the shared checkout.
    module._SITE_SCRIPT = REPO / 'management_portal/static/portal_site.js'
    module._SCRIPT = REPO / 'management_portal/static/portal_page_boot.js'
    return SimpleNamespace(module=module, root=root, manifest=manifest, write_site=write_site,
                           write_areas=write_areas, tmp=tmp_path)


def expected_paths(root):
    manifest = instance_manifest.load(root)
    return {**{area: str(manifest.area(area)) for area in AREAS},
            'test_results_root': str(manifest.directory('cache') / 'test-results')}


def injected(installation):
    data = installation.module.inject_page_bootstrap(DOCUMENT)
    start = data.index(b'<script id="portalSiteScript">') + len(b'<script id="portalSiteScript">')
    script = data[start:data.index(b'</script>', start)].decode()
    source = installation.module._SITE_SCRIPT.read_text().replace('</script', '<\\/script')
    assert script.startswith(source + '(') and script.endswith(');')
    return data, json.loads(script[len(source) + 1:-2]), script


def test_defaults_refresh_and_whitelisted_projection(installation, monkeypatch, tmp_path):
    data, config, _ = injected(installation)
    assert config == {'storage_namespace': site_config.browser_storage_namespace(), 'site_name': 'TFI Portal',
                      'paths': expected_paths(installation.root),
                      'compatibility': {'browser_identifiers': {}, 'format_namespaces': []}}
    assert b'PRIVATE-' not in data
    installation.write_site({'site_name': 'Custom site', 'private_key': 'PRIVATE-KEY', 'deployment': {
        'native_shell': {'database_env_file': '/private/DBENV-MARKER'},
    }})
    elsewhere = installation.tmp / 'cwd'; elsewhere.mkdir(); monkeypatch.chdir(elsewhere)
    installation.write_areas(projects=tmp_path / 'updated projects')
    data, config, _ = injected(installation)
    assert config['site_name'] == 'Custom site'
    assert config['paths']['projects'] == str(installation.tmp / 'updated projects')
    assert config['paths'] == expected_paths(installation.root)
    for hidden in [b'PRIVATE-', b'DBENV-MARKER', b'database_env_file', b'deployment', b'secrets']:
        assert hidden not in data


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
    assert config['paths'] == expected_paths(installation.root)
    assert b'archive.' not in data and b'PRIVATE-TOKEN' not in data and b'current/reports' not in data


@pytest.mark.parametrize('name', ['', ' ', None, False, 7, [], {}, 'bad\nname'])
def test_invalid_site_name_fails_without_falling_back(installation, name):
    installation.write_site({'site_name': name})
    with pytest.raises(ValueError):
        injected(installation)


def test_script_context_and_site_fields_remain_safe(installation, tmp_path):
    name = 'Study "A" <Lab> & </script><script>globalThis.injectedAttack=1</script>\u2028\u2029 End'
    path = tmp_path / 'data/"<&>/run\u2028\u2029'
    installation.write_site({'site_name': name})
    installation.write_areas(projects=path)
    data, config, script = injected(installation)
    assert config['site_name'] == name
    assert config['paths']['projects'] == str(path)
    assert data.count(b'</script>') == 1
    serialized = script[len(installation.module._SITE_SCRIPT.read_text()) + 1:-2]
    assert all(value not in serialized for value in ['<', '>', '&', '\u2028', '\u2029'])
    assert all(value in serialized for value in ['\\u003c', '\\u003e', '\\u0026', '\\u2028', '\\u2029'])
    assert b'Login \xe2\x80\x94 Study &quot;A&quot; &lt;Lab&gt;' in data


def test_skipped_and_already_branded_documents_do_not_read_config(installation, monkeypatch):
    branded, _, _ = injected(installation)
    def unexpected(*_args, **_kwargs):
        raise AssertionError('should not reread the instance')
    monkeypatch.setattr(site_config, 'site_name', unexpected)
    monkeypatch.setattr(site_config, 'read_site_config', unexpected)
    monkeypatch.setattr(instance_manifest, 'load', unexpected)
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
for(const root of Object.values(p)){
 const file=root+'/diagram.png';
 const container=w.document.createElement('div');
 container.innerHTML=w.MD.render('![fixture](<file://'+file+'>)');
 assert.equal(container.querySelector('img')?.getAttribute('src'),'/api/agent-workbench/link-file-raw?path='+encodeURIComponent(file));
}
// POSIX absolute paths use the authorized file transport even outside the
// projected roots.
const container=w.document.createElement('div');container.innerHTML=w.MD.render('![public](/share/public.png)');
assert.equal(container.querySelector('img').getAttribute('src'),'/api/agent-workbench/link-file-raw?path='+encodeURIComponent('/share/public.png'));
dom.window.close();
console.log('Production bootstrap reached the actual FileLinks/Markdown path readers.');
'''


def test_production_injection_reaches_actual_js_consumers(installation):
    installation.write_site({'site_name': 'Named "site" & literal'})
    installation.write_areas(projects=installation.tmp / 'public files', papers=installation.tmp / 'paper files')
    data, config, _ = injected(installation)
    expected = {'site_name': 'Named "site" & literal', 'paths': expected_paths(installation.root)}
    expected['compatibility'] = {'browser_identifiers': {}, 'format_namespaces': []}
    expected["storage_namespace"] = site_config.browser_storage_namespace()
    assert config == expected
    assert expected['paths']['projects'] == str(installation.tmp / 'public files')
    result = subprocess.run(['node', '-e', JS_CONSUMERS], input=json.dumps({
        'repo': str(REPO), 'consumers': os.environ.get('TFI_PATH_CONSUMER_ROOT', str(REPO)),
        'html': data.decode(), 'expected': expected,
    }), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert 'actual FileLinks/Markdown' in result.stdout
