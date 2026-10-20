"""Stable installation identity is projected, never build identity or secrets."""
import copy
import json
from pathlib import Path
from management_common import site_config
from management_portal.page_bootstrap import inject_site_name
from scripts import tfi_release_install as installer


def test_browser_namespace_tracks_installation_not_build_or_display_name(tmp_path, monkeypatch):
    path = tmp_path / 'site.json'
    monkeypatch.setenv('PORTAL_SITE_CONFIG', str(path))
    def write(**deployment):
        path.write_text(json.dumps({'site_name': 'Institute', 'deployment': deployment}))
    write(installation_id='one', source_root='/source/first', release_root='/release/first', secret='private')
    first = site_config.browser_storage_namespace()
    write(installation_id='one', source_root='/source/second', release_root='/release/second', secret='private')
    assert site_config.browser_storage_namespace() == first
    html = inject_site_name(b'<html><head><title data-portal-site-title="[]">TFI</title></head></html>')
    assert first.encode() in html
    assert b'private' not in html and b'/source/second' not in html
    write(installation_id='two', source_root='/source/second')
    assert site_config.browser_storage_namespace() != first
    write(source_root='/source/stable', release_root='/build/one')
    legacy = site_config.browser_storage_namespace()
    write(source_root='/source/stable', release_root='/build/two')
    assert site_config.browser_storage_namespace() == legacy
    write(source_root='/source/different', release_root='/build/two')
    assert site_config.browser_storage_namespace() != legacy


def test_installation_configuration_identity_survives_retry_and_run_change(tmp_path):
    def target(root):
        return {'install_root': str(root), 'run_id': 'first-run', 'service_prefix': 'test-only',
                'database_name': 'test_db', 'python': '/usr/bin/python3', 'pg_bin': '/usr/bin',
                'nats_server': '/usr/bin/nats-server', 'ports': {'web': 28086, 'postgres': 55432, 'nats': 14223},
                'data_roots': {key: str(root / 'data' / key) for key in installer.KEYS}}
    first = target(tmp_path / 'one')
    installer.prepare_configuration(first)
    path = Path(first['install_root']) / 'config/site.json'
    identity = json.loads(path.read_text())['deployment']['installation_id']
    repeated = copy.deepcopy(first); repeated['run_id'] = 'another-build'
    installer.prepare_configuration(repeated)
    assert json.loads(path.read_text())['deployment']['installation_id'] == identity
    second = target(tmp_path / 'two')
    installer.prepare_configuration(second)
    assert json.loads((Path(second['install_root']) / 'config/site.json').read_text())['deployment']['installation_id'] != identity
